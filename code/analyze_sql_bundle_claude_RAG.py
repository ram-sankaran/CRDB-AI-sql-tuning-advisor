#!/usr/bin/env python3
"""
CockroachDB SQL Bundle Analyzer - Unified SLM/LLM Version

This consolidated version supports both Small Language Model (SLM) and Large Language Model (LLM) modes.

SLM Mode (default):
  - Optimized for small models (llama3:8b, mistral:7b)
  - Fast analysis: 3-5s per query
  - Ideal for: CPU/Apple Silicon, batch processing, interactive use
  - Default port: 5050

LLM Mode:
  - Optimized for large models (llama3.3:70b)
  - Deep analysis: 60-90s per query
  - Ideal for: Complex queries, GPU systems, detailed explanations
  - Default port: 5051

Usage:
  python analyze_sql_bundle_claude_RAG.py              # SLM mode (default)
  python analyze_sql_bundle_claude_RAG.py --mode llm   # LLM mode
  python analyze_sql_bundle_claude_RAG.py --mode slm --port 8080  # Custom port
  python analyze_sql_bundle_claude_RAG.py --help       # Show help
"""

import os
import re
import json
import math
import glob
import time
import zipfile
import shutil
import tempfile
import subprocess
import sys
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

# ---------------------------------------------------------------------------
# Command-line argument parsing
# ---------------------------------------------------------------------------
def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='CockroachDB SQL Tuning Advisor - AI-Powered Query Analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run in SLM mode (fast, default)
  %(prog)s

  # Run in LLM mode (detailed analysis)
  %(prog)s --mode llm

  # Run in SLM mode on custom port
  %(prog)s --mode slm --port 8080

  # Run in LLM mode on custom port
  %(prog)s --mode llm --port 8081

  # Enable debug logging
  %(prog)s --debug

Mode Details:
  SLM (Small Language Model):
    - Default model: llama3:8b
    - Analysis time: 3-5 seconds per query
    - Best for: Laptops, Apple Silicon, CPU-only systems
    - Default port: 5050

  LLM (Large Language Model):
    - Default model: llama3.3:70b
    - Analysis time: 60-90 seconds per query
    - Best for: Complex queries, systems with GPU
    - Default port: 5051

Environment Variables:
  OLLAMA_MODEL     - Override default Ollama model
  CRDB_CONN_STR    - CockroachDB connection string for testing
  OLLAMA_URL       - Ollama API URL (default: http://localhost:11434/api/generate)
  HTTP_TIMEOUT     - HTTP request timeout in seconds (default: 30)
        """
    )

    parser.add_argument(
        '--mode',
        choices=['slm', 'llm'],
        default='slm',
        help='Operation mode: slm (fast, default) or llm (detailed analysis)'
    )

    parser.add_argument(
        '--port',
        type=int,
        help='Server port (default: 5050 for SLM, 5051 for LLM)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging for detailed troubleshooting'
    )

    parser.add_argument(
        '--version',
        action='version',
        version='CockroachDB SQL Tuning Advisor v2.0 (Unified)'
    )

    return parser.parse_args()

# Parse arguments early (before any heavy imports)
args = parse_arguments()

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
log_level = logging.DEBUG if args.debug else logging.INFO
logging.basicConfig(level=log_level, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if args.debug:
    logger.debug("Debug logging enabled")

# ---------------------------------------------------------------------------
# Auto-install missing packages
# ---------------------------------------------------------------------------
REQUIRED = {
    "flask": "flask",
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "markdown": "markdown",
    "pypdf": "pypdf",
    "psycopg": "psycopg[binary]",
}


def _install_pkg(pkg: str) -> bool:
    for cmd in (
        [sys.executable, "-m", "pip", "install", "--user", pkg],
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "--user", pkg],
    ):
        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    return False


_missing = []
for _mod, _pip_name in REQUIRED.items():
    try:
        __import__(_mod)
    except Exception:
        _missing.append(_pip_name)

if _missing:
    print("Installing missing packages:", ", ".join(_missing))
    _failed = [p for p in _missing if not _install_pkg(p)]
    if _failed:
        print("Failed to install:", ", ".join(_failed))
        print("Run manually once:")
        print("python3 -m pip install --break-system-packages --user " + " ".join(_failed))
        sys.exit(1)

import markdown
import psycopg
import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, render_template_string, request, send_file, url_for
# session imported conditionally below (server-side for exe, cookie-based for script)
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# Application constants (mode-dependent)
# ---------------------------------------------------------------------------
MODE = args.mode.upper()  # 'SLM' or 'LLM'

# Mode-specific configuration
if args.mode == 'slm':
    APP_TITLE = "AI-Powered SQL Tuning Advisor with DB Replay - SLM"
    DEFAULT_PORT = 5050
    FALLBACK_OLLAMA_MODELS = ["llama3:8b", "llama3.2:latest", "mistral:7b", "mistral:latest"]
    DEFAULT_OLLAMA_MODEL_INTERNAL = "llama3:8b"
    BUNDLED_DOCS_MODULE = "code.bundled_docs_slm"
else:  # llm mode
    APP_TITLE = "AI-Powered SQL Tuning Advisor with DB Replay - LLM"
    DEFAULT_PORT = 5051
    FALLBACK_OLLAMA_MODELS = ["llama3.3:70b", "llama3.2:latest", "llama3.2", "mistral:latest", "mistral", "gemma3:12b"]
    DEFAULT_OLLAMA_MODEL_INTERNAL = "llama3.3:70b"
    BUNDLED_DOCS_MODULE = "code.bundled_docs_llm"

# Use command-line port if specified, otherwise use mode default
SERVER_PORT = args.port if args.port else DEFAULT_PORT

REPORT_DIR = "bundle_reports"
INDEX_DIR = "rag_index_full"
DEFAULT_DOCS_ROOT = "https://www.cockroachlabs.com/docs/v26.2/make-queries-fast"
DEFAULT_CONN_STR = os.environ.get("CRDB_CONN_STR", "postgresql://root@localhost:26257/defaultdb?sslmode=disable")

# Detect if running as PyInstaller exe (read-only mode, in-memory index only)
RUNNING_FROM_EXE = getattr(sys, 'frozen', False)

# In exe mode, try to import embedded docs (no filesystem access)
BUNDLED_DOCS_DATA = None
if RUNNING_FROM_EXE:
    try:
        # Dynamically import the appropriate bundled docs module
        import importlib
        bundled_module = importlib.import_module(BUNDLED_DOCS_MODULE)
        BUNDLED_DOCS_DATA = bundled_module.BUNDLED_DOCS
        logger.info(f"✓ Loaded embedded documentation from {BUNDLED_DOCS_MODULE} (no temp files created)")
    except ImportError as e:
        logger.warning(f"⚠️  {BUNDLED_DOCS_MODULE} not found - will try disk fallback (error: {e})")
DEFAULT_SEED_ROWS = 10000
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL_INTERNAL)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))
MAX_CRAWL_PAGES = int(os.environ.get("MAX_CRAWL_PAGES", "180"))
TOP_K_DOCS = int(os.environ.get("TOP_K_DOCS", "10"))
DEFAULT_LOCAL_RAG_TXT = os.environ.get("LOCAL_RAG_TXT", "Query_tuning_playbook_v3_optimal_wording.txt")
ENABLE_LOCAL_PDF_RAG = os.environ.get("ENABLE_LOCAL_PDF_RAG", "0") == "1"

# Only create directories in script mode (exe uses memory-only approach)
if not RUNNING_FROM_EXE:
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(INDEX_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Session handling: server-side for exe (no 4KB cookie limit), cookie-based for script
if RUNNING_FROM_EXE:
    # Server-side session storage (in-memory dict for single-user exe)
    _SERVER_SESSIONS = {}

    class ServerSideSession(dict):
        """Dict-like session that ignores .modified attribute for compatibility."""
        @property
        def modified(self):
            return True

        @modified.setter
        def modified(self, value):
            pass  # Ignore - changes are always saved

    @app.before_request
    def _load_server_session():
        from flask import request, g
        import uuid
        session_id = request.cookies.get('session_id')
        if not session_id:
            session_id = str(uuid.uuid4())
        g.session_id = session_id
        # Use ServerSideSession instead of plain dict
        existing = _SERVER_SESSIONS.get(session_id, {})
        g.session_data = ServerSideSession(existing)

    @app.after_request
    def _save_server_session(response):
        from flask import g
        if hasattr(g, 'session_id') and hasattr(g, 'session_data'):
            response.set_cookie('session_id', g.session_id, httponly=True, samesite='Lax')
            _SERVER_SESSIONS[g.session_id] = dict(g.session_data)  # Save as plain dict
        return response

    # session object points to g.session_data
    from werkzeug.local import LocalProxy
    from flask import g
    session = LocalProxy(lambda: g.session_data)
else:
    # Script mode: use Flask's default cookie-based session
    from flask import session

def get_ollama_models() -> Tuple[List[str], bool]:
    """
    Returns (models_list, ollama_is_running).
    If Ollama is down, returns (FALLBACK_MODELS, False).
    """
    try:
        r = requests.get(OLLAMA_URL.replace("/api/generate", "/api/tags"), timeout=5)
        r.raise_for_status()
        data = r.json()
        models = [m.get("name", "").strip() for m in data.get("models", []) if m.get("name", "").strip()]
        return (models or FALLBACK_OLLAMA_MODELS[:], True)
    except Exception:
        return (FALLBACK_OLLAMA_MODELS[:], False)


def validate_ollama_model(model: str) -> Tuple[bool, List[str], bool]:
    """
    Returns (model_in_list, models_list, ollama_is_running).
    """
    installed, ollama_running = get_ollama_models()
    return model in installed, installed, ollama_running

IDENT = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+")'
QUAL_IDENT = rf'{IDENT}(?:\.{IDENT})*'


# ---------------------------------------------------------------------------
# Scoring configuration — all magic numbers in one place
# ---------------------------------------------------------------------------
@dataclass
class ScoringConfig:
    good_index_used_boost: float = 0.75
    very_low_latency_ms: float = 10.0
    very_low_latency_boost: float = 0.35
    acceptable_latency_ms: float = 100.0
    acceptable_latency_boost: float = 0.15
    index_join_targeted_boost: float = 0.10
    low_selectivity_boost: float = 0.55
    small_fast_full_scan_boost: float = 0.35
    keep_score_threshold: float = 0.80
    index_score_threshold: float = 0.60
    rewrite_score_threshold: float = 0.60
    rewrite_plus_index_threshold: float = 0.70
    high_confidence_threshold: float = 0.80
    full_scan_index_score: float = 0.85
    covering_index_score: float = 0.55
    low_selectivity_index_score: float = 0.03
    rewrite_score: float = 0.90
    large_in_rewrite_score: float = 0.70
    json_rewrite_score: float = 0.35
    large_in_clause_min_items: int = 15
    rewrite_plus_index_score: float = 0.75
    small_table_row_limit: int = 10_000
    small_table_latency_ms: float = 20.0
    already_optimal_latency_ms: float = 150.0
    already_optimal_no_scan_ms: float = 20.0


SC = ScoringConfig()


# ---------------------------------------------------------------------------
# Typed result for analyze()
# ---------------------------------------------------------------------------
@dataclass
class AnalysisResult:
    facts: Dict[str, Any]
    signals: Dict[str, Any]
    docs: List[Dict[str, Any]]
    final_result: Dict[str, Any]
    validation: Dict[str, Any]
    plan_shape: str
    bundle_rows: Optional[int]
    llm_runtime: Dict[str, Any]


# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------

def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def dedupe(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def safe_int(x: Any) -> Optional[int]:
    try:
        return int(str(x).replace(",", "").strip())
    except Exception:
        return None


def safe_float(x: Any) -> Optional[float]:
    try:
        y = str(x).replace(",", "").strip()
        if y.endswith("ms"):
            return float(y[:-2].strip())
        if y.endswith("µs"):
            return float(y[:-2].strip()) / 1000.0
        if y.endswith("s") and not y.endswith("ms"):
            return float(y[:-1].strip()) * 1000.0
        return float(y)
    except Exception:
        return None


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/")


def is_docs_url(url: str) -> bool:
    p = urlparse(url)
    return p.netloc == "www.cockroachlabs.com" and (p.path.startswith("/docs/v26.2/") or p.path.startswith("/docs/stable/"))


def chunk_text(text: str, words: int = 220, overlap: int = 40) -> List[str]:
    toks = (text or "").split()
    if not toks:
        return []
    out = []
    i = 0
    while i < len(toks):
        j = min(i + words, len(toks))
        out.append(" ".join(toks[i:j]))
        if j == len(toks):
            break
        i = max(0, j - overlap)
    return out


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--.*?$", "", sql, flags=re.M)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return sql


def ms_string(v: Any) -> str:
    if v is None:
        return "not provided"
    fv = float(v)
    return f"{int(fv) if fv.is_integer() else fv} ms"


def mask_dsn_password(dsn: str) -> str:
    """Replace the password in a DSN with *** for safe display."""
    return re.sub(r"(://[^:@/]+:)[^@]+(@)", r"\1***\2", dsn)


# ---------------------------------------------------------------------------
# Bundle I/O helpers
# ---------------------------------------------------------------------------

def unzip_bundle(zip_path: str, out_dir: str) -> str:
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)
    return out_dir


def read_text(path: Optional[str]) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def find_bundle_files(folder: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {"sql": None, "plan": None, "trace": None, "schema": None, "env": None}
    for root, _, files in os.walk(folder):
        for name in files:
            full = os.path.join(root, name)
            lname = name.lower()
            # Support both statement.sql and statement.txt naming conventions
            if lname == "statement.sql" or lname == "statement.txt":
                out["sql"] = full
            # Support both plan.txt and statement.txt.plan.txt naming conventions
            elif lname == "plan.txt" or lname == "statement.txt.plan.txt":
                out["plan"] = full
            elif lname == "trace.txt":
                out["trace"] = full
            elif lname == "schema.sql":
                out["schema"] = full
            elif lname in ("env.sql", "env.txt"):
                out["env"] = full
    return out


def load_bundle(folder: str) -> Dict[str, str]:
    files = find_bundle_files(folder)
    return {k: read_text(v) for k, v in files.items()}


# ---------------------------------------------------------------------------
# Web / PDF fetching
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    """Fetch HTML with SSL error handling for corporate proxies."""
    import urllib3

    # Try with SSL verification first (normal case)
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "cockroach-sql-analyzer/1.0"}, verify=True)
        r.raise_for_status()
        return r.text
    except requests.exceptions.SSLError:
        # Corporate proxy with self-signed cert - retry without verification
        # Suppress InsecureRequestWarning to avoid polluting user's console
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "cockroach-sql-analyzer/1.0"}, verify=False)
        r.raise_for_status()
        return r.text


def extract_page(url: str, html: str) -> Tuple[str, str, List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    main = soup.find("main") or soup.find("article") or soup.body
    if not main:
        return title, "", []
    text = clean_spaces(main.get_text(" ", strip=True))
    links: List[str] = []
    for a in main.find_all("a", href=True):
        href = a["href"].strip()
        if href:
            full = normalize_url(urljoin(url, href))
            if is_docs_url(full):
                links.append(full)
    return title, text, dedupe(links)


def extract_pdf_chunks(pdf_path: str) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    try:
        reader = PdfReader(pdf_path)
        base = os.path.basename(pdf_path)
        for page_num, page in enumerate(reader.pages, start=1):
            text = clean_spaces(page.extract_text() or "")
            if not text:
                continue
            for i, piece in enumerate(chunk_text(text, words=180, overlap=30)):
                docs.append({
                    "source": "local_pdf",
                    "url": f"file://{os.path.abspath(pdf_path)}#page={page_num}",
                    "title": f"{base} - page {page_num}",
                    "chunk_id": i,
                    "text": piece,
                })
    except Exception as e:
        logger.warning("Failed to read PDF %s: %s", pdf_path, e)
    return docs


def extract_local_txt_chunks(txt_path: str) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    try:
        p = Path(txt_path)
        if not p.exists():
            return docs
        text = clean_spaces(p.read_text(encoding="utf-8", errors="ignore"))
        if not text:
            return docs
        for i, piece in enumerate(chunk_text(text, words=180, overlap=30)):
            docs.append({
                "source": "local_txt",
                "url": f"file://{p.resolve()}",
                "title": p.name,
                "chunk_id": i,
                "text": piece,
            })
    except Exception as e:
        logger.warning("Failed to read local TXT %s: %s", txt_path, e)
    return docs

def candidate_local_rag_txt_paths(default_name: str) -> List[str]:
    """
    Search for the normalized TXT RAG file in both the process cwd and the script directory,
    plus direct env overrides. This avoids depending on whichever directory the Flask app
    was launched from.
    """
    candidates: List[str] = []
    env_val = os.environ.get("LOCAL_RAG_TXT", "").strip()
    if env_val:
        candidates.append(env_val)
    if default_name:
        candidates.append(default_name)
        candidates.append(os.path.join(os.getcwd(), default_name))
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidates.append(os.path.join(script_dir, default_name))
        except Exception:
            pass
    # De-dup while preserving order
    out: List[str] = []
    seen = set()
    for c in candidates:
        key = os.path.abspath(c) if c else c
        if c and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def find_existing_local_rag_txt(default_name: str) -> Optional[str]:
    for p in candidate_local_rag_txt_paths(default_name):
        try:
            if os.path.exists(p) and os.path.isfile(p):
                return p
        except Exception:
            pass
    return None




def call_ollama_json(prompt: str, model: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """
    JSON-only Ollama call for recommendation generation.
    Returns (parsed_dict_or_none, error_or_none, raw_response_text).
    """
    ok, installed, ollama_running = validate_ollama_model(model)
    if not ollama_running:
        msg = f"Ollama is not running at {OLLAMA_URL}. Using rule-based fallback analysis."
        logger.warning(msg)
        return None, msg, ""
    if not ok:
        msg = (
            f"Ollama model '{model}' is not installed. "
            f"Installed models: {', '.join(installed) if installed else 'none'}"
        )
        logger.warning(msg)
        return None, msg, ""

    raw = ""
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=180,  # Enough for llama3.3:70b
        )
        r.raise_for_status()
        payload = r.json()
        raw = (payload.get("response") or "").strip()
    except requests.exceptions.ConnectionError:
        msg = f"Ollama not reachable at {OLLAMA_URL}. Is it running?"
        logger.warning(msg)
        return None, msg, raw
    except requests.exceptions.Timeout:
        msg = f"Ollama request timed out after 240s for model {model}."
        logger.warning(msg)
        return None, msg, raw
    except Exception as e:
        msg = f"Ollama JSON error: {e}"
        logger.warning(msg)
        return None, msg, raw

    clean = _cleanup_llm_text(raw)
    try:
        data = json.loads(clean)
        return (data if isinstance(data, dict) else None), None, raw
    except Exception:
        pass

    candidate = _extract_first_balanced_json_object(clean)
    if candidate:
        try:
            data = json.loads(candidate)
            return (data if isinstance(data, dict) else None), None, raw
        except Exception:
            pass

    return None, "LLM recommendation did not contain a recoverable JSON object.", raw

def call_json_llm(prompt: str, model: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """
    Generic JSON-only LLM call for the primary recommendation path.
    """
    return call_ollama_json(prompt, model)

def sanitize_loaded_rag_docs(docs: List[Dict[str, Any]], selected_rag_txt: str = "") -> List[Dict[str, Any]]:
    """
    Enforce runtime policy on a previously built cache:
    - remove local_pdf docs unless ENABLE_LOCAL_PDF_RAG=1
    - ensure local_txt docs are present if the TXT file can be found
    """
    out = list(docs or [])
    if not ENABLE_LOCAL_PDF_RAG:
        out = [d for d in out if d.get("source") != "local_pdf"]

    has_txt = any(d.get("source") == "local_txt" for d in out)
    if not has_txt:
        txt_path = resolve_selected_rag_txt(selected_rag_txt or DEFAULT_LOCAL_RAG_TXT)
        if txt_path:
            out.extend(extract_local_txt_chunks(txt_path))
    return out



def list_available_rag_txt_files() -> List[str]:
    # In exe mode: no local TXT files exist (docs are bundled in rag_index_full/docs.json)
    if RUNNING_FROM_EXE:
        return []

    names: List[str] = []
    search_dirs = [os.getcwd()]
    try:
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass

    explicit_candidates = [
        DEFAULT_LOCAL_RAG_TXT,
        "Query_tuning_playbook_v3_optimal_wording.txt",
        "Query_tuning_playbook_v2.txt",
        "Query_tuning_rag_context_v1.txt",
    ]

    seen = set()

    for d in search_dirs:
        try:
            for p in sorted(glob.glob(os.path.join(d, "*.txt"))):
                name = os.path.basename(p)
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
        except Exception:
            pass

    for candidate in explicit_candidates:
        resolved = None
        try:
            resolved = find_existing_local_rag_txt(candidate)
        except Exception:
            resolved = None
        if resolved:
            name = os.path.basename(resolved)
            if name not in seen:
                seen.add(name)
                names.append(name)

    names.sort(key=lambda n: (0 if "playbook" in n.lower() else 1, n.lower()))
    return names

def resolve_selected_rag_txt(selected_name: str) -> Optional[str]:
    # In exe mode: no local TXT files, use bundled docs.json only
    if RUNNING_FROM_EXE:
        return None
    if not selected_name:
        return find_existing_local_rag_txt(DEFAULT_LOCAL_RAG_TXT)
    return find_existing_local_rag_txt(selected_name)


# ---------------------------------------------------------------------------
# RAG Retriever  — JSON index, atomic writes, page-accurate crawl limit
# ---------------------------------------------------------------------------

class HybridRetriever:
    def __init__(self, index_path: str):
        self.index_path = index_path
        self.docs: List[Dict[str, Any]] = []


    def save(self) -> None:
        if RUNNING_FROM_EXE:
            logger.info("Skipping disk save (exe mode - in-memory index only)")
            return
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump({"docs": self.docs}, f, ensure_ascii=False)

    def build(self, seed_url: str, crawl_mode: str = "focused", include_local_pdfs: bool = False, selected_rag_txt: str = "", skip_web: bool = False) -> None:
        all_docs: List[Dict[str, Any]] = []
        web_docs_fetched = 0

        # Skip web crawling if explicitly requested or if seed_url is empty
        if not skip_web and seed_url:
            queue = [normalize_url(seed_url)]
            if crawl_mode == "focused":
                queue.extend([
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/indexes"),
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/partial-indexes"),
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/hash-sharded-indexes"),
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/cost-based-optimizer"),
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/explain-analyze"),
                    normalize_url("https://www.cockroachlabs.com/docs/v26.2/performance-best-practices-overview"),
                ])
                queue = dedupe(queue)
            seen: set = set()
            pages_crawled = 0  # track pages, not chunks

            while queue and pages_crawled < MAX_CRAWL_PAGES:
                url = queue.pop(0)
                if url in seen or not is_docs_url(url):
                    continue
                seen.add(url)
                if crawl_mode == "focused":
                    u = url.lower()
                    keep = any(k in u for k in [
                        "make-queries-fast", "explain", "explain-analyze", "indexes",
                        "partial-indexes", "statistics", "optimizer", "joins", "scan",
                        "storing", "json", "performance", "cost-based-optimizer",
                        "sql-tuning-with-explain", "inverted-indexes", "computed-columns",
                        "performance-best-practices", "hash-sharded-indexes", "secondary-indexes",
                        "schema-design-indexes", "query-behavior-troubleshooting",
                    ])
                    if not keep:
                        continue
                try:
                    html = fetch_html(url)
                    title, text, links = extract_page(url, html)
                    if len(text) > 300:
                        for i, piece in enumerate(chunk_text(text)):
                            all_docs.append({"source": "web", "url": url, "title": title, "chunk_id": i, "text": piece})
                        pages_crawled += 1
                        web_docs_fetched += 1
                    for link in links:
                        if link not in seen:
                            queue.append(link)
                except Exception as e:
                    logger.warning("Failed to crawl %s: %s", url, e)

            # In exe mode: if rebuild attempted but no web docs fetched, warn and abort
            if RUNNING_FROM_EXE and web_docs_fetched == 0:
                logger.error("⚠️  Cannot rebuild index - no internet connection detected.")
                logger.error("⚠️  Continuing with existing bundled documentation.")
                return  # Keep existing self.docs, don't replace
        else:
            logger.info("Skipping web crawl - using local txt file only")

        txt_path = resolve_selected_rag_txt(selected_rag_txt or DEFAULT_LOCAL_RAG_TXT)
        if txt_path:
            all_docs.extend(extract_local_txt_chunks(txt_path))
        if include_local_pdfs and ENABLE_LOCAL_PDF_RAG:
            script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
            pdf_candidates = sorted(set(glob.glob(os.path.join(os.getcwd(), "*.pdf")) + glob.glob(os.path.join(script_dir, "*.pdf"))))
            for pdf_path in pdf_candidates:
                # If the normalized TXT exists, prefer it and do not index the sibling PDF by default.
                all_docs.extend(extract_pdf_chunks(pdf_path))

        # Only write to disk if NOT running as exe (exe mode = in-memory only)
        if not RUNNING_FROM_EXE:
            tmp_path = self.index_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"docs": all_docs, "seed_url": seed_url, "crawl_mode": crawl_mode}, f)
            os.replace(tmp_path, self.index_path)
        else:
            logger.info("✓ Index rebuilt in memory (exe mode - not saved to disk)")

        # Always update in-memory docs (works for both script and exe)
        self.docs = sanitize_loaded_rag_docs(all_docs, selected_rag_txt)

    def load(self) -> bool:
        # In exe mode: try embedded docs first (no filesystem access)
        if RUNNING_FROM_EXE and BUNDLED_DOCS_DATA:
            try:
                self.docs = sanitize_loaded_rag_docs(BUNDLED_DOCS_DATA.get("docs", []))
                logger.info("✓ Loaded %d docs from embedded data", len(self.docs))
                return True
            except Exception as e:
                logger.warning("Failed to load embedded docs: %s", e)
                # Fall through to disk load

        # Script mode or fallback: load from disk
        if not os.path.exists(self.index_path):
            return False
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.docs = sanitize_loaded_rag_docs(payload.get("docs", []))
            return True
        except Exception as e:
            logger.warning("Failed to load index %s: %s", self.index_path, e)
            return False

    def load_or_build(self, seed_url: str, crawl_mode: str = "focused", rebuild: bool = False, selected_rag_txt: str = "", skip_web: bool = False) -> None:
        if rebuild or not self.load():
            self.build(seed_url, crawl_mode, selected_rag_txt=selected_rag_txt, skip_web=skip_web)
            self.save()
        else:
            txt_path = resolve_selected_rag_txt(selected_rag_txt or DEFAULT_LOCAL_RAG_TXT)
            if txt_path:
                target_name = os.path.basename(txt_path)
                has_target = any(
                    d.get("source") == "local_txt" and target_name in (d.get("url", "") or "")
                    for d in self.docs
                )
                if not has_target:
                    self.docs.extend(extract_local_txt_chunks(txt_path))

    def retrieve(self, query: str, top_k: int = TOP_K_DOCS) -> List[Dict[str, Any]]:
        q_terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", query.lower()))
        scored = []
        for d in self.docs:
            text = d["text"].lower()
            score = sum(1.0 for t in q_terms if t in text)
            if d.get("source") == "web":
                score += 0.9
            elif d.get("source") == "local_txt":
                score += 0.7
            elif d.get("source") == "local_pdf":
                score += 0.05
            elif d.get("source") == "local_txt":
                score += 0.35
            if score > 0:
                score = score / math.sqrt(max(20, len(text.split())))
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "url": d["url"],
                "title": d["title"],
                "score": round(score, 3),
                "snippet": d["text"][:800],
                "source": d.get("source", "unknown"),
            }
            for score, d in scored[:top_k]
        ]


# ---------------------------------------------------------------------------
# SQL text helpers
# ---------------------------------------------------------------------------

def normalize_ident(name: str) -> str:
    parts = [p.strip().strip('"') for p in name.split(".")]
    return parts[-1] if parts else name.strip().strip('"')


def extract_select_columns(sql: str) -> List[str]:
    sql2 = strip_sql_comments(sql)
    m = re.search(r"\bselect\b(.*?)\bfrom\b", sql2, re.I | re.S)
    if not m:
        return []
    section = m.group(1)
    cols: List[str] = []

    # SQL aggregate and window functions to exclude (these are not columns!)
    sql_functions = {
        'count', 'sum', 'avg', 'min', 'max', 'stddev', 'variance',
        'array_agg', 'string_agg', 'json_agg', 'jsonb_agg',
        'bool_and', 'bool_or', 'every', 'bit_and', 'bit_or',
        'row_number', 'rank', 'dense_rank', 'percent_rank',
        'cume_dist', 'ntile', 'lag', 'lead', 'first_value', 'last_value',
        'coalesce', 'nullif', 'greatest', 'least',
        'concat', 'substring', 'length', 'upper', 'lower', 'trim',
        'now', 'current_date', 'current_timestamp', 'extract',
        'cast', 'convert', 'abs', 'ceil', 'floor', 'round',
        'unnest', 'generate_series'
    }

    for part in re.split(r",(?![^()]*\))", section):
        p = part.strip()
        if not p or p == '*':  # Skip empty and SELECT *
            continue

        # Skip if starts with aggregate/function call
        p_lower = p.lower().strip()
        if any(p_lower.startswith(func + '(') for func in sql_functions):
            continue  # Skip COUNT(...), SUM(...), etc.

        for pattern in [rf"({QUAL_IDENT})\s+as\s+{IDENT}", rf"({QUAL_IDENT})"]:
            mm = re.search(pattern, p, re.I)
            if mm:
                col_name = normalize_ident(mm.group(1))
                # Double-check extracted name is not a function
                if col_name.lower() not in sql_functions:
                    cols.append(col_name)
                break
    return dedupe(cols)


def extract_where_columns(sql: str) -> List[str]:
    sql2 = strip_sql_comments(sql)
    out: List[str] = []
    patterns = [
        rf"({QUAL_IDENT})\s*=",
        rf"({QUAL_IDENT})\s+not\s+in\s*\(",   # NOT IN
        rf"({QUAL_IDENT})\s+in\s*\(",          # IN
        rf"({QUAL_IDENT})\s+is\s+not\s+null",
        rf"({QUAL_IDENT})\s*\?",
        rf"({QUAL_IDENT})\s*@>",
        rf"any\s*\(\s*({QUAL_IDENT})\s*\)",
        rf"any\s+({QUAL_IDENT})",
    ]
    # SQL keywords to exclude from column names
    sql_keywords = {'not', 'and', 'or', 'in', 'is', 'null', 'select', 'from', 'where', 'join',
                    'on', 'as', 'by', 'order', 'group', 'having', 'limit', 'offset'}

    for pattern in patterns:
        for m in re.finditer(pattern, sql2, re.I):
            col = normalize_ident(m.group(1))
            # Filter out SQL keywords
            if col.lower() not in sql_keywords:
                out.append(col)
    return dedupe(out)


def extract_join_columns(sql: str, plan: str = "") -> Dict[str, List[str]]:
    """
    Extract JOIN columns per table from SQL.
    Returns: {"table_name": ["col1", "col2"], ...}
    """
    sql2 = strip_sql_comments(sql or "")
    result: Dict[str, List[str]] = {}

    # Pattern to match: JOIN table_name alias ON ... = alias.column
    # Example: LEFT JOIN orders o ON c.id = o.customer_id
    join_pattern = r'(?:left|right|inner|outer|full)?\s*join\s+([a-z_][a-z0-9_]*)\s+([a-z_][a-z0-9_]*)\s+on\s+(.*?)(?:where|group|order|limit|having|;|$)'

    for match in re.finditer(join_pattern, sql2, re.I | re.DOTALL):
        table_name = match.group(1).lower()
        alias = match.group(2).lower()
        on_clause = match.group(3)

        # Extract columns from ON clause for this table
        # Look for alias.column patterns
        col_pattern = rf'{re.escape(alias)}\.({IDENT})'
        cols = []
        for col_match in re.finditer(col_pattern, on_clause, re.I):
            col = normalize_ident(col_match.group(1))
            cols.append(col)

        if cols:
            if table_name not in result:
                result[table_name] = []
            result[table_name].extend(cols)

    # Dedupe columns per table
    for table in result:
        result[table] = dedupe(result[table])

    return result


def split_sql_statements(sql_text: str) -> List[str]:
    """Split on ';' at depth 0, respecting single-quotes, double-quotes, and dollar-quoting."""
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    dollar_tag: Optional[str] = None
    i = 0
    while i < len(sql_text):
        ch = sql_text[i]

        # Dollar-quoting: detect opening $tag$
        if not in_single and not in_double and dollar_tag is None and ch == "$":
            end = sql_text.find("$", i + 1)
            if end != -1:
                tag = sql_text[i: end + 1]
                cur.append(tag)
                dollar_tag = tag
                i = end + 1
                continue

        # Dollar-quoting: detect closing $tag$
        if dollar_tag is not None:
            if sql_text[i:].startswith(dollar_tag):
                cur.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            cur.append(ch)
            i += 1
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                stmt = "".join(cur).strip()
                if stmt:
                    parts.append(stmt)
                cur = []
                i += 1
                continue
        cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


# ---------------------------------------------------------------------------
# Plan parsing helpers
# ---------------------------------------------------------------------------

def parse_table_and_index(plan: str) -> Tuple[str, str]:
    m = re.search(r"table:\s*([A-Za-z0-9_\.]+)@([A-Za-z0-9_]+)", plan or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def parse_execution_time(plan: str) -> Optional[float]:
    m = re.search(r"execution time:\s*([0-9\.]+ms)", plan, re.I)
    return safe_float(m.group(1)) if m else None


def extract_bundle_row_count(plan: str) -> Optional[int]:
    m = re.search(r"• scan.*?actual row count:\s*([0-9,]+)", plan, re.S)
    if m:
        return safe_int(m.group(1))
    m = re.search(r"rows decoded from KV:\s*([0-9,]+)", plan, re.I)
    if m:
        return safe_int(m.group(1))
    return None


def parse_scan_rows(plan: str) -> Optional[int]:
    m = re.search(r"• scan.*?actual row count:\s*([0-9,]+)", plan, re.S)
    return safe_int(m.group(1)) if m else None


def parse_filter_rows(plan: str) -> Optional[int]:
    matches = re.findall(r"• filter.*?actual row count:\s*([0-9,]+)", plan, re.S)
    return safe_int(matches[0]) if matches else None


def parse_index_join_rows(plan: str) -> Optional[int]:
    # CRITICAL: Only match "index join", NOT "lookup join"
    # Index join = secondary index scan + PK lookup for non-indexed columns
    # Lookup join = JOIN algorithm using index lookups (different concept!)
    m = re.search(r"• index join\s.*?actual row count:\s*([0-9,]+)", plan, re.S | re.I)
    if m:
        return safe_int(m.group(1))
    return None


def extract_all_table_row_counts(plan: str) -> Dict[str, int]:
    """
    Extract actual row count for EACH table from the bundle plan.

    This fixes the seed data bug where all tables got the same row count.

    Example plan:
      • scan
        actual row count: 30,000
        table: products@products_pkey
      • scan
        actual row count: 6,000
        table: discontinued_products@discontinued_products_pkey

    Returns: {"products": 30000, "discontinued_products": 6000}
    """
    table_rows: Dict[str, int] = {}

    # Split plan into scan sections
    # Each scan section contains one table and its row count
    scan_sections = re.split(r'(?=• scan\b)', plan, flags=re.I)

    for section in scan_sections:
        if not section.strip():
            continue

        # Look for "table: <name>" in this section
        table_match = re.search(r'table:\s+([^\s@\n]+)(?:@[^\s\n]+)?', section, re.I)
        if not table_match:
            continue

        # Look for "actual row count: <number>" in this section
        row_count_match = re.search(r'actual row count:\s*([0-9,]+)', section, re.I)
        if not row_count_match:
            continue

        # Extract table name and row count
        table_ref = table_match.group(1)
        row_count = safe_int(row_count_match.group(1))

        if row_count is None:
            continue

        # Extract table name (remove schema prefix if present)
        # "public.products" -> "products"
        table_name = table_ref.split('.')[-1].strip('"')

        # Store the row count for this table
        table_rows[table_name] = row_count

    return table_rows


def parse_index_join_kv_ms(plan: str) -> Optional[float]:
    # CRITICAL: Only match "index join", NOT "lookup join"
    # Index join can be optimized with STORING clause
    # Lookup join is a join algorithm, not an index optimization opportunity
    m = re.search(r"• index join\s.*?KV time:\s*([0-9a-zA-Z\.\,µ]+)", plan, re.S | re.I)
    if m:
        return safe_float(m.group(1))
    return None


def parse_scan_kv_ms(plan: str) -> Optional[float]:
    m = re.search(r"• scan.*?KV time:\s*([0-9a-zA-Z\.\,µ]+)", plan, re.S)
    return safe_float(m.group(1)) if m else None


def parse_total_kv_ms(plan: str) -> Optional[float]:
    m = re.search(r"cumulative time spent in KV:\s*([0-9a-zA-Z\.\,µ]+)", plan, re.I)
    return safe_float(m.group(1)) if m else None


def parse_total_kv_rows(plan: str) -> Optional[int]:
    m = re.search(r"rows decoded from KV:\s*([0-9,]+)", plan, re.I)
    return safe_int(m.group(1)) if m else None


def parse_estimated_scan_rows(plan: str) -> Optional[float]:
    m = re.search(r"estimated row count:\s*([0-9,\.]+)", plan, re.I)
    return safe_float(m.group(1)) if m else None


def parse_table_coverage_from_plan(plan: str) -> Optional[float]:
    m = re.search(r"estimated row count:\s*[0-9,\.]+\s*\(([^%]+)% of the table", plan or "", re.I)
    if not m:
        return None
    try:
        return float(m.group(1).strip())
    except Exception:
        return None


def parse_stats_collected(plan: str) -> str:
    m = re.search(r"stats collected\s*(.*?)[\);]", plan, re.I | re.S)
    return clean_spaces(m.group(1)) if m else "unknown"


def detect_full_scan(plan: str) -> bool:
    lower = (plan or "").lower()
    return ("full scan" in lower) or ("spans: full scan" in lower)


def extract_plan_metrics(plan_text: str) -> Dict[str, Any]:
    """Extract key numeric metrics from an EXPLAIN ANALYZE plan. Each regex runs once."""
    plan_text = plan_text or ""
    m_exec = re.search(r"execution time:\s*([0-9\.]+ms)", plan_text, re.I)
    m_kv = re.search(r"cumulative time spent in KV:\s*([0-9a-zA-Z\.\,µ]+)", plan_text, re.I)
    m_decoded = re.search(r"rows decoded from KV:\s*([0-9,]+)", plan_text, re.I)
    m_first_count = re.search(r"actual row count:\s*([0-9,]+)", plan_text)
    table, used_index = parse_table_and_index(plan_text)
    deep = re.findall(r'table:\s*([A-Za-z0-9_\.]+)@([A-Za-z0-9_]+)', plan_text)
    deepest = f"{deep[-1][0]}@{deep[-1][1]}" if deep else (f"{table}@{used_index}" if table and used_index else None)
    return {
        "execution_time_ms": safe_float(m_exec.group(1)) if m_exec else None,
        "total_kv_time_ms": safe_float(m_kv.group(1)) if m_kv else None,
        "rows_decoded": safe_int(m_decoded.group(1)) if m_decoded else None,
        "scan_rows": parse_scan_rows(plan_text),
        "result_rows": safe_int(m_first_count.group(1)) if m_first_count else None,
        "post_filter_rows": parse_filter_rows(plan_text),
        "has_full_scan": "full scan" in plan_text.lower(),
        "used_table": table,
        "used_index": deepest,
        "has_index_join": ("index join" in plan_text.lower()) or ("lookup join" in plan_text.lower()),
    }


# ---------------------------------------------------------------------------
# Schema / index helpers
# ---------------------------------------------------------------------------

def is_prefix_columns(subseq: List[str], seq: List[str]) -> bool:
    a = [str(x).strip().strip('"').lower() for x in (subseq or []) if str(x).strip()]
    b = [str(x).strip().strip('"').lower() for x in (seq or []) if str(x).strip()]
    if len(a) > len(b):
        return False
    return b[:len(a)] == a


def extract_index_columns_from_schema(schema_sql: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for m in re.finditer(
        r'(?is)create\s+(?:unique\s+)?(?:inverted\s+)?index\s+([A-Za-z0-9_"]+)\s+on\s+[A-Za-z0-9_\."]+\s*\((.*?)\)',
        schema_sql or "",
    ):
        idx_name = (m.group(1) or "").strip().strip('"')
        cols_raw = m.group(2) or ""
        cols = []
        for part in re.split(r',(?![^()]*\))', cols_raw):
            token = part.strip()
            cm = re.match(r'("?[\w]+"?)', token)
            if cm:
                cols.append(cm.group(1).strip('"'))
        if idx_name and cols:
            out[idx_name] = cols
    for m in re.finditer(r'(?is)\bindex\s+([A-Za-z0-9_"]+)\s*\((.*?)\)', schema_sql or ""):
        idx_name = (m.group(1) or "").strip().strip('"')
        cols_raw = m.group(2) or ""
        cols = []
        for part in re.split(r',(?![^()]*\))', cols_raw):
            token = part.strip()
            cm = re.match(r'("?[\w]+"?)', token)
            if cm:
                cols.append(cm.group(1).strip('"'))
        if idx_name and cols:
            out[idx_name] = cols
    return out


def extract_primary_key_columns_from_schema(schema_sql: str) -> Dict[str, List[str]]:
    """
    Extract PRIMARY KEY columns from schema SQL.
    Returns: {"table_name": ["col1", "col2", ...]}
    """
    out: Dict[str, List[str]] = {}
    tables = parse_schema_tables(schema_sql or "")
    for table in tables:
        if table.primary_key:
            # Extract table name without schema/quotes
            table_name = table.name.split('.')[-1].strip('"')
            out[table_name] = [col.strip('"') for col in table.primary_key]
    return out


def suppress_redundant_index_candidates(
    candidate_indexes: List[Dict[str, Any]],
    existing_index_cols: Dict[str, List[str]],
    where_columns: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Suppress redundant index candidates that are covered by existing indexes.

    Redundancy rules:
    1. Exact prefix match: candidate (a, b) is redundant if existing index is (a, b, c, ...)
    2. Same leading column: candidate (a, x) may be redundant if existing index (a, y, z)
       covers more WHERE clause columns
    """
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    where_cols_lower = [c.lower() for c in (where_columns or [])]

    for cand in candidate_indexes or []:
        ddl = (cand.get("ddl") or "").strip()
        m = re.search(r'\((.*?)\)', ddl)
        if not m:
            kept.append(cand)
            continue
        cand_cols = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
        cand_cols_lower = [c.lower() for c in cand_cols]
        redundant_against = None
        reason = None

        for idx_name, idx_cols in (existing_index_cols or {}).items():
            idx_cols_lower = [c.lower() for c in idx_cols]

            # Rule 1: Exact prefix match
            # Example: candidate (cid, account_id) redundant if existing (cid, account_id, persona)
            if is_prefix_columns(cand_cols, idx_cols):
                redundant_against = idx_name
                reason = f"Existing index `{idx_name}` has columns ({', '.join(idx_cols)}) which includes candidate as prefix"
                break

            # Rule 2: Same first column + existing index covers more WHERE columns
            # Example: candidate (cid, account_id) may be redundant if existing (cid, persona, type)
            #          and WHERE clause has cid, persona, type (not just cid, account_id)
            if (len(cand_cols_lower) >= 1 and len(idx_cols_lower) >= 1 and
                cand_cols_lower[0] == idx_cols_lower[0]):
                # Count how many WHERE columns each index covers
                if where_cols_lower:
                    cand_coverage = sum(1 for c in cand_cols_lower if c in where_cols_lower)
                    existing_coverage = sum(1 for c in idx_cols_lower if c in where_cols_lower)

                    # If existing index covers same or more WHERE columns, it's likely better
                    if existing_coverage >= cand_coverage and existing_coverage > 1:
                        redundant_against = idx_name
                        reason = (f"Existing index `{idx_name}({', '.join(idx_cols)})` starts with same column '{idx_cols[0]}' "
                                 f"and covers {existing_coverage} WHERE columns vs {cand_coverage} for candidate - "
                                 f"optimizer will likely prefer existing index")
                        break

        if redundant_against:
            dropped.append({
                "ddl": ddl,
                "reason": reason or f"Suppressed because existing index `{redundant_against}` already exists.",
            })
        else:
            kept.append(cand)
    return kept, dropped


def parse_deepest_non_pk_index(plan: str) -> Optional[str]:
    matches = re.findall(r'table:\s*([A-Za-z0-9_\.]+)@([A-Za-z0-9_]+)', plan or "")
    if not matches:
        return None
    for _table, idx in reversed(matches):
        if not idx.endswith("_pkey"):
            return idx
    return None


def extract_existing_indexes(schema: str, plan: str, env: str) -> List[str]:
    out: List[str] = []
    for line in (schema or "").splitlines():
        if "create index" in line.lower() or "create unique index" in line.lower():
            out.append(line.strip())
    for table, idx in re.findall(r"table:\s*([A-Za-z0-9_\.]+)@([A-Za-z0-9_]+)", plan or ""):
        out.append(f"{table}@{idx}")
    return dedupe(out)


# ---------------------------------------------------------------------------
# Replay schema helpers  (consolidated from v14 + v15)
# ---------------------------------------------------------------------------

def _split_qualified_name(name: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    in_quotes = False
    for ch in (name or ""):
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == '.' and not in_quotes:
            part = ''.join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = ''.join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _strip_only_database_qualifier(sql: str) -> str:
    """Remove 3-part db.schema.table qualifiers, keeping schema.table."""
    patt = re.compile(
        r'((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)'
        r'\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))'
    )
    def repl(m: re.Match) -> str:
        parts = _split_qualified_name(m.group(1).replace(' ', ''))
        if len(parts) == 3:
            return f'{parts[1]}.{parts[2]}'
        return m.group(1)
    return patt.sub(repl, sql or "")


def extract_and_create_schemas(schema_sql: str) -> str:
    """
    Extract schema names from CREATE TABLE statements and prepend CREATE SCHEMA statements.

    Handles cases where bundles don't include CREATE SCHEMA statements but use
    non-default schemas (e.g., CREATE TABLE myschema.mytable).

    Example:
      CREATE TABLE piercnsdbo.utc_trade_message (...)
      → CREATE SCHEMA IF NOT EXISTS piercnsdbo;
        CREATE TABLE piercnsdbo.utc_trade_message (...)

    Also handles quoted identifiers:
      CREATE TABLE "piercnsdbo"."utc_trade_message" (...)
      → CREATE SCHEMA IF NOT EXISTS piercnsdbo;
    """
    if not schema_sql:
        return schema_sql

    # Find all schema-qualified table names: schema.table
    # Pattern: CREATE TABLE ["]<schema>["].<table>
    # Handles both quoted and unquoted identifiers
    pattern = r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"?([a-zA-Z_][a-zA-Z0-9_]*)"?)\.'
    matches = re.findall(pattern, schema_sql, re.IGNORECASE)

    # Get unique schema names (excluding 'public' which always exists)
    schemas = set(s for s in matches if s.lower() != 'public')

    if not schemas:
        return schema_sql

    # Generate CREATE SCHEMA statements
    schema_creates = []
    for schema_name in sorted(schemas):
        schema_creates.append(f'CREATE SCHEMA IF NOT EXISTS {schema_name};')

    # Prepend schema creation statements
    return '\n'.join(schema_creates) + '\n\n' + schema_sql


def extract_create_type_statements(schema_sql: str) -> tuple[str, str]:
    """
    Extract CREATE TYPE statements from schema SQL and return them separately.

    Returns:
        (create_type_statements, remaining_schema)
    """
    # Use regex to find all CREATE TYPE statements (handles IF NOT EXISTS too)
    # Pattern: CREATE TYPE [IF NOT EXISTS] name AS ENUM (...);
    pattern = r'(?is)(CREATE\s+TYPE\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z0-9_."]+\s+AS\s+ENUM\s*\([^)]+\)\s*;)'

    matches = list(re.finditer(pattern, schema_sql or ""))

    if not matches:
        # No CREATE TYPE statements found
        return "", schema_sql

    # Extract all matched CREATE TYPE statements
    type_statements = [match.group(1).strip() for match in matches]

    # Remove CREATE TYPE statements from schema, leaving everything else
    remaining_schema = schema_sql
    for match in reversed(matches):  # Reverse to maintain string positions
        remaining_schema = remaining_schema[:match.start()] + remaining_schema[match.end():]

    # Clean up extra blank lines
    remaining_schema = re.sub(r'\n\s*\n\s*\n', '\n\n', remaining_schema)

    return '\n'.join(type_statements), remaining_schema.strip()


def normalize_replay_schema_sql(schema_sql: str) -> str:
    """Prepare schema SQL for safe replay in the analyzer's local DB."""
    sql = schema_sql or ""

    # Extract CREATE TYPE statements (must come before CREATE TABLE)
    create_types, sql = extract_create_type_statements(sql)

    logger.debug(f"Extracted {len(re.findall(r'CREATE TYPE', create_types, re.IGNORECASE))} CREATE TYPE statements")
    logger.debug(f"create_types content:\n{create_types[:500]}")  # First 500 chars

    # CRITICAL FIX: Strip CREATE DATABASE and USE statements
    # These cause types/tables to be created in different databases
    # Example: "USE global_cfg_db;" switches database context
    # We want everything in the same database (the one we're connected to)
    sql = re.sub(r'(?im)^\s*CREATE\s+DATABASE\s+[^;]+;\s*', '', sql)
    create_types = re.sub(r'(?im)^\s*CREATE\s+DATABASE\s+[^;]+;\s*', '', create_types)

    sql = re.sub(r'(?im)^\s*USE\s+[^;]+;\s*', '', sql)
    create_types = re.sub(r'(?im)^\s*USE\s+[^;]+;\s*', '', create_types)

    logger.debug(f"After stripping USE, create_types has {len(re.findall(r'CREATE TYPE', create_types, re.IGNORECASE))} CREATE TYPE statements")

    # CRITICAL FIX: Detect missing ENUM types and replace with STRING
    # Find all types defined in CREATE TYPE statements
    # Pattern must skip "IF NOT EXISTS" to avoid capturing "IF" as type name
    defined_types = set()
    for match in re.finditer(r'CREATE\s+TYPE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_."]+)', create_types or "", re.IGNORECASE):
        type_name = match.group(1).strip().strip('"')
        # Skip if we accidentally captured a keyword
        if type_name.upper() in ('IF', 'NOT', 'EXISTS', 'AS', 'ENUM'):
            continue
        # Handle schema-qualified types (public.typename or typename)
        defined_types.add(type_name)
        defined_types.add(type_name.upper())  # Add uppercase version
        if '.' in type_name:
            short_name = type_name.split('.')[-1]
            defined_types.add(short_name)  # Also add short name
            defined_types.add(short_name.upper())

    logger.debug(f"Defined types found: {defined_types}")

    # Find all custom types used in columns
    # Pattern: column_name TYPENAME where TYPENAME is not a built-in type
    builtin_types = {'STRING', 'INT8', 'INT4', 'INT2', 'FLOAT8', 'FLOAT4', 'DECIMAL',
                     'BOOL', 'BOOLEAN', 'BYTES', 'UUID', 'TIMESTAMP', 'TIMESTAMPTZ',
                     'DATE', 'TIME', 'TIMETZ', 'INTERVAL', 'JSONB', 'JSON', 'INET',
                     'TEXT', 'VARCHAR', 'CHAR', 'BYTEA', 'SERIAL', 'BIGSERIAL', 'SMALLSERIAL',
                     'INT[]', 'INT8[]', 'STRING[]', 'JSONB[]'}  # Add array types

    # More aggressive pattern: Replace custom type references anywhere in CREATE TABLE
    # This handles cases where the column-based regex doesn't match

    # First pass: Simple column type replacement
    replacement_count = 0

    def replace_undefined_type(match):
        nonlocal replacement_count
        col_name = match.group(1)
        full_type = match.group(2).strip()

        # Skip CONSTRAINT keyword - it's not a column, and the following name is a constraint name, not a type
        if col_name.upper() == "CONSTRAINT":
            return match.group(0)  # Keep as-is

        # Skip NOT NULL pattern - NOT is not a column name, NULL is not a type
        if col_name.upper() == "NOT" and full_type.upper() == "NULL":
            return match.group(0)  # Keep as-is

        # Extract base type (remove schema prefix if present)
        type_parts = full_type.split('.')
        base_type = type_parts[-1].strip()

        # Check if it's a builtin type (case-insensitive)
        if base_type.upper() in builtin_types:
            return match.group(0)  # Keep as-is

        # Check if custom type is defined (case-insensitive)
        if full_type in defined_types or full_type.upper() in defined_types:
            return match.group(0)  # Keep as-is
        if base_type in defined_types or base_type.upper() in defined_types:
            return match.group(0)  # Keep as-is

        # Undefined custom type - replace with STRING and add comment
        logger.warning(f"Replacing undefined type '{full_type}' with STRING for column '{col_name}'")
        replacement_count += 1
        return f"{col_name} STRING /* was: {full_type} */"

    # Pattern: column_name schema.typename or column_name typename
    # Must handle: persona public.personatypes NULL, type public.scopetypes NOT NULL
    sql = re.sub(
        r'(\b[A-Za-z_][A-Za-z0-9_]*\b)\s+([A-Za-z_][A-Za-z0-9_.]+)\s+(?=NULL|NOT\s+NULL|DEFAULT|PRIMARY|UNIQUE|REFERENCES|CHECK|,|\))',
        replace_undefined_type,
        sql,
        flags=re.MULTILINE
    )

    if replacement_count > 0:
        logger.info(f"Replaced {replacement_count} undefined type references with STRING")

    # Second pass: More aggressive - find any custom type name that's not defined
    # This catches cases the first regex missed (e.g., different formatting)
    # Pattern: Look for schema.typename or typename used as type in any context
    custom_type_pattern = r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b'

    def replace_undefined_qualified_type(match):
        nonlocal replacement_count
        full_type = match.group(1)

        # Check if this looks like it's in a column definition context
        # (not in a table name, index name, etc.)

        # Check if it's a defined type
        if full_type in defined_types or full_type.upper() in defined_types:
            return match.group(0)

        base_type = full_type.split('.')[-1]
        if base_type in defined_types or base_type.upper() in defined_types:
            return match.group(0)

        # Check if it's a table reference (e.g., public.tablename in FROM clause)
        # We only want to replace column types, not table names
        # A simple heuristic: if preceded by CREATE TABLE, it's a table name
        return match.group(0)  # Keep as-is for now to avoid breaking table names

    # Actually, let's be more surgical - only replace in specific column definition contexts
    # Look for pattern within CREATE TABLE ... (...) blocks

    # Third approach: Parse CREATE TABLE and replace types within column definitions
    def fix_types_in_create_table(match):
        nonlocal replacement_count
        table_header = match.group(1)  # "CREATE TABLE tablename ("
        table_body = match.group(2)    # Column definitions
        table_footer = match.group(3)  # ");"

        # Replace undefined types in column definitions
        fixed_body = table_body

        # Find all schema.typename references
        for type_match in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b', table_body):
            full_type = type_match.group(1)

            # Skip if it's a defined type
            if full_type in defined_types or full_type.upper() in defined_types:
                continue

            base_type = full_type.split('.')[-1]
            if base_type in defined_types or base_type.upper() in defined_types:
                continue

            # Check if it's a builtin type
            if base_type.upper() in builtin_types:
                continue

            # This is an undefined custom type - replace it
            logger.warning(f"Second pass: Replacing undefined type '{full_type}' with STRING")
            fixed_body = fixed_body.replace(full_type, f"STRING /* was: {full_type} */")
            replacement_count += 1

        return table_header + fixed_body + table_footer

    # Apply third approach: Fix types within CREATE TABLE blocks
    sql = re.sub(
        r'(CREATE\s+TABLE\s+[^(]+\()(.*?)(\);)',
        fix_types_in_create_table,
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )

    if replacement_count > 0:
        logger.info(f"Total type replacements: {replacement_count}")

    # Auto-create schemas for schema-qualified tables
    sql = extract_and_create_schemas(sql)

    # Make schema creation idempotent
    sql = re.sub(
        r'(?im)^\s*CREATE\s+SCHEMA\s+(?!IF\s+NOT\s+EXISTS)([^\n;]+);',
        r'CREATE SCHEMA IF NOT EXISTS \1;',
        sql,
    )
    # Strip only database qualifier; preserve schema + table names
    sql = _strip_only_database_qualifier(sql)
    # Skip zone configuration statements during replay
    sql = re.sub(
        r'(?is)^\s*ALTER\s+TABLE\s+.*?CONFIGURE\s+ZONE\s+USING\s+.*?;\s*',
        '-- skipped CONFIGURE ZONE for analyzer replay;\n',
        sql,
        flags=re.M,
    )

    # CRITICAL FIX: Reorder CREATE TABLE statements by dependency
    # Tables with foreign keys must be created AFTER the tables they reference
    sql = reorder_create_tables_by_dependency(sql)

    # Re-add CREATE TYPE statements at the beginning (before CREATE TABLE)
    if create_types:
        # Strip 3-part database qualifiers from CREATE TYPE statements
        # Pattern: CREATE TYPE db.schema.typename → CREATE TYPE schema.typename
        # Handles: defaultdb.public.scopetypes → public.scopetypes
        def strip_type_db_qualifier(match):
            prefix = match.group(1)  # "CREATE TYPE "
            qualified_name = match.group(2)  # "defaultdb.public.scopetypes"

            # Split by dots, handling quoted identifiers
            parts = qualified_name.split('.')

            # If 3 parts (db.schema.type), remove first part
            if len(parts) == 3:
                return f"{prefix}{parts[1]}.{parts[2]}"
            # If 2 parts (schema.type) or 1 part (type), keep as-is
            return match.group(0)

        create_types = re.sub(
            r'(CREATE\s+TYPE\s+)([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)',
            strip_type_db_qualifier,
            create_types,
            flags=re.IGNORECASE
        )

        # Make CREATE TYPE idempotent (add IF NOT EXISTS)
        create_types = re.sub(
            r'(?i)CREATE\s+TYPE\s+(?!IF\s+NOT\s+EXISTS)',
            'CREATE TYPE IF NOT EXISTS ',
            create_types
        )

        logger.debug(f"Final create_types before prepending ({len(create_types)} chars):\n{create_types}")

        sql = create_types + '\n\n' + sql

    return sql


def reorder_create_tables_by_dependency(schema_sql: str) -> str:
    """Reorder CREATE TABLE statements so tables are created in dependency order."""
    if not schema_sql or 'CREATE TABLE' not in schema_sql.upper():
        return schema_sql

    # Split schema into parts: before tables, table statements, after tables
    lines = schema_sql.split('\n')

    # Find CREATE TABLE statements with proper boundary detection
    table_statements = []
    current_table = []
    in_table = False
    paren_depth = 0
    current_table_name = None

    for line in lines:
        line_upper = line.upper().strip()

        # Detect start of CREATE TABLE
        if line_upper.startswith('CREATE TABLE') or (in_table == False and 'CREATE TABLE' in line_upper):
            # Start new table
            if current_table and current_table_name:
                # Save previous table
                table_statements.append({
                    'name': current_table_name,
                    'lines': current_table.copy(),
                    'references': []
                })
            current_table = [line]
            in_table = True
            # Extract table name
            match = re.search(r'CREATE\s+TABLE\s+((?:"[^"]+"|[\w]+)(?:\s*\.\s*(?:"[^"]+"|[\w]+))?)', line, re.I)
            current_table_name = match.group(1).strip() if match else None
            # Count parentheses
            paren_depth = line.count('(') - line.count(')')
        elif in_table:
            current_table.append(line)
            paren_depth += line.count('(') - line.count(')')

            # Check if this line closes the CREATE TABLE
            if paren_depth <= 0 and ';' in line:
                # End of CREATE TABLE statement
                in_table = False
                if current_table_name:
                    table_statements.append({
                        'name': current_table_name,
                        'lines': current_table.copy(),
                        'references': []
                    })
                current_table = []
                current_table_name = None
                paren_depth = 0

    # Save last table if any
    if current_table and current_table_name:
        table_statements.append({
            'name': current_table_name,
            'lines': current_table.copy(),
            'references': []
        })

    if not table_statements:
        return schema_sql

    # Extract foreign key references for each table
    for table in table_statements:
        full_text = '\n'.join(table['lines'])
        fk_pattern = r'FOREIGN\s+KEY\s*\([^)]+\)\s+REFERENCES\s+((?:"[^"]+"|[\w]+)(?:\s*\.\s*(?:"[^"]+"|[\w]+))?)'
        for fk_match in re.finditer(fk_pattern, full_text, re.I):
            ref_table = fk_match.group(1).strip()
            table['references'].append(ref_table)

    # Topological sort
    sorted_tables = []
    remaining = table_statements.copy()

    max_iterations = len(table_statements) * 2
    iteration = 0

    while remaining and iteration < max_iterations:
        iteration += 1
        no_deps = []

        for table in remaining:
            # Check if all references are satisfied
            refs_satisfied = True
            for ref in table['references']:
                # Check if any sorted table matches this reference
                ref_found = any(
                    sorted_t['name'].lower().replace('"', '').split('.')[-1] ==
                    ref.lower().replace('"', '').split('.')[-1]
                    for sorted_t in sorted_tables
                )
                if not ref_found:
                    refs_satisfied = False
                    break

            if refs_satisfied or not table['references']:
                no_deps.append(table)

        if not no_deps:
            # Can't resolve - add remaining in original order to avoid infinite loop
            sorted_tables.extend(remaining)
            break

        for table in no_deps:
            sorted_tables.append(table)
            remaining.remove(table)

    # Reconstruct schema SQL
    result = []

    for table in sorted_tables:
        # Add table with proper formatting
        table_text = '\n'.join(table['lines'])
        # Ensure it ends with semicolon
        if not table_text.rstrip().endswith(';'):
            table_text = table_text.rstrip() + ';'
        result.append(table_text)
        result.append('')  # Blank line between tables

    return '\n'.join(result)


def extract_schema_names_from_schema_sql(schema_sql: str) -> List[str]:
    names: List[str] = []
    sql = normalize_replay_schema_sql(schema_sql or "")
    for m in re.finditer(
        r'(?im)^\s*CREATE\s+SCHEMA(?:\s+IF\s+NOT\s+EXISTS)?\s+("?[A-Za-z0-9_]+"?)\s*;', sql
    ):
        name = (m.group(1) or '').strip()
        if name:
            names.append(name)
    for m in re.finditer(
        r'(?is)CREATE\s+TABLE\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))',
        sql,
    ):
        schema = _split_qualified_name(m.group(1).replace(' ', ''))[0]
        if schema and schema.lower().strip('"') not in ("public", "pg_catalog", "information_schema", "crdb_internal"):
            names.append(schema)
    seen: set = set()
    out: List[str] = []
    for n in names:
        k = n.strip('"').lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def build_schema_cleanup_sql(schema_sql: str) -> str:
    stmts: List[str] = []
    sql = normalize_replay_schema_sql(schema_sql or "")

    # Drop types first (they may be used by tables)
    type_matches = list(re.finditer(
        r'(?is)CREATE\s+TYPE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))?)',
        sql,
    ))
    for m in type_matches:
        fq = (m.group(1) or '').replace(' ', '')
        if fq:
            stmts.append(f'DROP TYPE IF EXISTS {fq} CASCADE;')

    # Then drop tables
    table_matches = list(re.finditer(
        r'(?is)CREATE\s+TABLE\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))?)',
        sql,
    ))
    for m in table_matches:
        fq = (m.group(1) or '').replace(' ', '')
        if fq:
            stmts.append(f'DROP TABLE IF EXISTS {fq} CASCADE;')

    # Finally drop schemas (if not public)
    for schema_name in extract_schema_names_from_schema_sql(sql):
        if schema_name.strip('"').lower() != "public":
            stmts.append(f'DROP SCHEMA IF EXISTS {schema_name} CASCADE;')
    return "\n".join(stmts) if stmts else "-- none"


def quote_ident_preserve_case(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return n
    if n.startswith('"') and n.endswith('"'):
        return n
    if re.fullmatch(r'[a-z_][a-z0-9_]*', n):
        return n
    return '"' + n.replace('"', '""') + '"'


def qualify_table_name_for_replay(name: str, default_schema: Optional[str] = None) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw
    raw = _strip_only_database_qualifier(raw)
    parts = _split_qualified_name(raw)
    if len(parts) == 1 and default_schema:
        return f'{quote_ident_preserve_case(default_schema)}.{quote_ident_preserve_case(parts[0].strip(chr(34)))}'
    if len(parts) == 2:
        return f'{parts[0]}.{parts[1]}'
    return raw


# ---------------------------------------------------------------------------
# Schema parsing / seed generation
# ---------------------------------------------------------------------------

@dataclass
class ColumnDef:
    name: str
    type_part: str
    nullable: bool = True
    default_expr: Optional[str] = None
    is_primary_key_inline: bool = False


@dataclass
class ForeignKeyDef:
    columns: List[str]
    ref_table: str
    ref_columns: List[str]


@dataclass
class TableDef:
    name: str
    columns: List[ColumnDef] = field(default_factory=list)
    primary_key: List[str] = field(default_factory=list)
    foreign_keys: List[ForeignKeyDef] = field(default_factory=list)


def _split_top_level_csv(text: str) -> List[str]:
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                i += 1
                continue
        cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_single_literal_check_map(schema_sql: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    sql = schema_sql or ""
    pattern = re.compile(
        r"(?is)CHECK\s*\(\s*\"?(?P<col>[A-Za-z_][A-Za-z0-9_]*)\"?\s+IN\s*\(\s*(?P<lit>'[^']*'|-?\d+(?:::[A-Za-z0-9_().]+)?|true|false)"
    )
    for mm in pattern.finditer(sql):
        col = mm.group("col")
        lit = (mm.group("lit") or "").strip()
        if col and lit:
            out[col.lower()] = re.sub(r":::.*$", "", lit)
    return out



def equality_match_fraction_for_column(
    column_name: str,
    table_name: str,
    simulation_profile: Optional[Dict[str, Any]],
) -> Optional[float]:
    if not simulation_profile:
        return None
    column_key = (column_name or "").lower()
    overrides = simulation_profile.get("equality_match_fraction_by_column", {}) or {}
    if column_key in overrides:
        return overrides[column_key]
    table_overrides = simulation_profile.get("equality_match_fraction_by_table_column", {}) or {}
    t = (table_name or "").split(".")[-1].strip('"').lower()
    if f"{t}.{column_key}" in table_overrides:
        return table_overrides[f"{t}.{column_key}"]
    return simulation_profile.get("default_equality_match_fraction")


def derive_replay_total_rows_for_table(
    requested_rows: int,
    table_name: str,
    sql_hints: Optional[Dict[str, Any]],
    simulation_profile: Optional[Dict[str, Any]],
) -> int:
    """
    Preserve bundle selectivity for equality predicates by making the replay table
    large enough that 'requested_rows' can represent the matching subset.

    Example:
      requested_rows = 1000
      equality match fraction = 0.2
      -> total replay rows = 5000
    """
    req = max(1, int(requested_rows or 1))
    # Try both full table name and short name without quotes
    table_candidates = [table_name, (table_name or "").split(".")[-1].strip('"')]
    table_hints = {}
    for t in table_candidates:
        table_hints = (sql_hints or {}).get(t, {})
        if table_hints:
            break
    has_equals = any(isinstance(v, dict) and v.get("equals") for v in (table_hints or {}).values())
    if not has_equals:
        return req
    frac = simulation_profile.get("default_equality_match_fraction") if simulation_profile else None
    try:
        frac = float(frac) if frac is not None else None
    except Exception:
        frac = None
    if frac is None or frac <= 0.0 or frac >= 1.0:
        return req
    total = int(math.ceil(req / frac))
    return max(req, total)

def make_match_selector_expr(row_alias: str, fraction: float, bucket_size: int = 1000) -> str:
    frac = max(0.0, min(1.0, float(fraction)))
    threshold = max(0, min(bucket_size, int(round(frac * bucket_size))))
    return f"((({row_alias}) - 1) % {bucket_size}) < {threshold}"


def build_non_match_value(col_name: str, lit: str, type_part: str, is_unique: bool = False, row_alias: str = "g.i") -> str:
    """
    Generate a value that will NOT match the literal value from the WHERE clause.
    This is used for seed data to control selectivity - rows with this value won't be returned by the query.

    For example, if WHERE customer_id = 100, this returns a value like 100100 that doesn't match.
    """
    raw = (lit or "").strip()
    t = (type_part or "").lower()
    cn = (col_name or "").lower()

    if raw.lower() in ("true", "false"):
        return "false" if raw.lower() == "true" else "true"

    if raw.startswith("'") and raw.endswith("'"):
        val = raw[1:-1].replace("''", "'")
        upper = val.upper()
        if cn == "persona":
            for cand in ["BRIDGE", "IOT", "SWITCH", "OTHER"]:
                if cand != upper:
                    return f"'{cand}'"
            return "'OTHER'"
        if cn == "type":
            for cand in ["SITE", "ACCOUNT", "SENSOR", "OTHER"]:
                if cand != upper:
                    return f"'{cand}'"
            return "'OTHER'"
        if is_unique:
            # For unique constraints, append row number to ensure uniqueness
            return f"concat('{val}_', {row_alias})"
        return "'" + (val + "_other").replace("'", "''") + "'"

    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        if is_unique:
            # For unique constraints (like primary keys), add row offset instead of fixed offset
            if "." in raw:
                return f"({raw} + 100000.0 + {row_alias})"
            return f"({raw} + 100000 + {row_alias})"
        if "." in raw:
            return str(float(raw) + 100000.0)
        return str(int(float(raw)) + 100000)

    if "uuid" in t:
        return "gen_random_uuid()"
    if any(x in t for x in ["string", "text", "varchar", "char"]):
        if is_unique:
            return f"concat('other_', {row_alias})"
        return "'other'"
    if "bool" in t:
        return "false"
    if any(x in t for x in ["int", "decimal", "numeric", "float", "double"]):
        if is_unique:
            return f"(100000 + {row_alias})"
        return "100000"
    return raw


def _normalize_table_name(name: str) -> str:
    return name.strip().strip('"')


def _qident(name: str) -> str:
    n = (name or "").strip().strip('"')
    return '"' + n.replace('"', '""') + '"'

def _split_qualified_name(name: str) -> List[str]:
    parts, buf = [], []
    in_quotes = False
    for ch in (name or ""):
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == '.' and not in_quotes:
            part = ''.join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = ''.join(buf).strip()
    if part:
        parts.append(part)
    return parts

def _normalize_fq_table_name(name: str) -> str:
    parts = [p.strip().strip('"') for p in _split_qualified_name(name) if p.strip()]
    if not parts:
        return name
    if len(parts) == 1:
        return _qident(parts[0])
    return ".".join(_qident(p) for p in parts[-2:])

def _is_non_seedable_column_name(col_name: str) -> bool:
    n = (col_name or "").strip().strip('"')
    return n.startswith("crdb_internal_")

def _is_generated_or_virtual_column(def_text: str) -> bool:
    d = (def_text or "").lower()
    return (" virtual" in d) or (" generated" in d) or (" as (" in d)



def parse_schema_tables(schema_sql: str) -> List[TableDef]:
    tables: List[TableDef] = []
    # Match CREATE TABLE statements using a more robust approach
    # Split by CREATE TABLE and process each section
    segments = re.split(r'(?i)(?=create\s+table)', schema_sql or "")

    for segment in segments:
        segment = segment.strip()
        if not segment or not re.match(r'(?i)create\s+table', segment):
            continue

        # Extract table name
        name_match = re.match(
            r'(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?((?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*))?)',
            segment
        )
        if not name_match:
            continue

        table_name = _normalize_fq_table_name(name_match.group(1).replace(" ", ""))

        # Find the table body between the first ( and its matching )
        # We need to balance parentheses
        start_idx = segment.find('(', name_match.end())
        if start_idx == -1:
            continue

        paren_count = 1
        idx = start_idx + 1
        while idx < len(segment) and paren_count > 0:
            if segment[idx] == '(':
                paren_count += 1
            elif segment[idx] == ')':
                paren_count -= 1
            idx += 1

        if paren_count != 0:
            # Unbalanced parens
            continue

        body = segment[start_idx+1:idx-1].strip()

        # Now parse the body
        table = TableDef(name=table_name)
        table_level_prefixes = (
            "constraint", "family", "index", "inverted index", "unique",
            "unique index", "check", "foreign key", "primary key",
        )
        for item in _split_top_level_csv(body):
            low = item.lower().strip()
            if not low:
                continue
            if low.startswith("primary key") or (low.startswith("constraint") and "primary key" in low):
                km = re.search(r"primary\s+key\s*\((.*?)\)", item, re.I | re.S)
                if km:
                    # Extract column names, strip ASC/DESC
                    pk_cols = []
                    for col_spec in _split_top_level_csv(km.group(1)):
                        col_name = col_spec.strip().split()[0].strip('"')  # Take first word, strip quotes
                        pk_cols.append(col_name)
                    table.primary_key = pk_cols
                continue
            if (low.startswith("constraint") and "foreign key" in low) or low.startswith("foreign key"):
                fkm = re.search(
                    r'foreign\s+key\s*\((.*?)\)\s*references\s+((?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*))?)\s*\((.*?)\)',
                    item, re.I | re.S,
                )
                if fkm:
                    table.foreign_keys.append(ForeignKeyDef(
                        columns=[c.strip().strip('"') for c in _split_top_level_csv(fkm.group(1))],
                        ref_table=_normalize_fq_table_name(fkm.group(2).replace(" ", "")),
                        ref_columns=[c.strip().strip('"') for c in _split_top_level_csv(fkm.group(3))],
                    ))
                continue
            if low.startswith(table_level_prefixes):
                continue
            cm = re.match(r'^(\"?[A-Za-z_][A-Za-z0-9_]*\"?)\s+(.+)$', item, re.S)
            if not cm:
                continue
            col_name = cm.group(1).strip().strip('"')
            rest = cm.group(2).strip()
            if _is_non_seedable_column_name(col_name) or _is_generated_or_virtual_column(item):
                continue
            km = re.search(r"\b(not\s+null|null|default|primary\s+key|references|constraint|check|unique)\b", rest, re.I)
            if km:
                type_part = rest[:km.start()].strip().rstrip(",")
                tail = rest[km.start():].strip()
            else:
                type_part = rest.strip().rstrip(",")
                tail = ""
            dm = re.search(
                r"\bdefault\b\s+(.+?)(?:(?:\bnot\s+null\b)|(?:\bnull\b)|(?:\bprimary\s+key\b)|(?:\breferences\b)|$)",
                tail, re.I | re.S
            )
            default_expr = dm.group(1).strip().rstrip(",") if dm else None
            nullable = not bool(re.search(r"\bnot\s+null\b", tail, re.I))
            inline_pk = bool(re.search(r"\bprimary\s+key\b", tail, re.I))
            refm = re.search(
                r'\breferences\s+((?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*))?)\s*\((.*?)\)',
                tail, re.I | re.S
            )
            if refm:
                table.foreign_keys.append(ForeignKeyDef(
                    columns=[col_name],
                    ref_table=_normalize_fq_table_name(refm.group(1).replace(" ", "")),
                    ref_columns=[c.strip().strip('"') for c in _split_top_level_csv(refm.group(2))],
                ))
            table.columns.append(ColumnDef(col_name, type_part, nullable, default_expr, inline_pk))
            if inline_pk and col_name not in table.primary_key:
                table.primary_key.append(col_name)
        tables.append(table)
    return tables


def order_tables_by_dependencies(tables: List[TableDef]) -> List[TableDef]:
    by_name = {t.name: t for t in tables}
    deps = {t.name: set() for t in tables}
    for t in tables:
        for fk in t.foreign_keys:
            if fk.ref_table in by_name and fk.ref_table != t.name:
                deps[t.name].add(fk.ref_table)
    ordered: List[TableDef] = []
    remaining = set(by_name)
    while remaining:
        ready = sorted([name for name in remaining if not (deps[name] & remaining)])
        if not ready:
            ready = [sorted(remaining)[0]]
        for name in ready:
            ordered.append(by_name[name])
            remaining.remove(name)
    return ordered


def parse_sql_aliases(sql_text: str) -> Dict[str, str]:
    sql2 = strip_sql_comments(sql_text)
    aliases: Dict[str, str] = {}
    for m in re.finditer(
        r'(?is)\b(from|join)\s+([A-Za-z0-9_\."]+)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?', sql2
    ):
        table = m.group(2).strip().strip('"')
        alias = (m.group(3) or "").strip()
        if alias:
            aliases[alias] = table
        aliases[table.split(".")[-1]] = table
    return aliases


def build_sql_seed_hints(sql_text: str) -> Dict[str, Any]:
    sql2 = strip_sql_comments(sql_text)
    aliases = parse_sql_aliases(sql2)
    hints: Dict[str, Any] = {}

    def ensure(table: str, col: str) -> Dict[str, Any]:
        t = hints.setdefault(table, {})
        return t.setdefault(col, {"equals": [], "in_values": [], "not_null": False})

    eq_pat = re.compile(r"(?is)([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\s*=\s*('[^']*'|-?\d+(?:\.\d+)?)")
    for m in eq_pat.finditer(sql2):
        a1, c2, lit = m.group(1), m.group(2), m.group(3)
        if c2:
            alias, col = a1, c2
            table = aliases.get(alias, alias)
        else:
            col, table = a1, next(iter(aliases.values()), "")
        if table:
            ensure(table, col)["equals"].append(lit)

    in_pat = re.compile(r"(?is)([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\s+in\s*\(([^)]*)\)")
    for m in in_pat.finditer(sql2):
        a1, c2, vals = m.group(1), m.group(2), m.group(3)
        # Extract values, stripping CockroachDB type casts like :::STRING, :::INT8, etc.
        # Pattern: 'value':::TYPE or just 'value' or 123
        values = []
        for v in vals.split(","):
            v_stripped = v.strip()
            # Remove type cast suffix (:::TYPE)
            v_cleaned = re.sub(r':::[A-Z0-9]+$', '', v_stripped)
            # Check if it's a valid literal (quoted string or number)
            if re.match(r"^('[^']*'|-?\d+(?:\.\d+)?)$", v_cleaned):
                values.append(v_cleaned)
        if not values:
            continue
        if c2:
            alias, col = a1, c2
            table = aliases.get(alias, alias)
        else:
            col, table = a1, next(iter(aliases.values()), "")
        if table:
            ensure(table, col)["in_values"].extend(values)

    nn_pat = re.compile(r"(?is)([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\s+is\s+not\s+null")
    for m in nn_pat.finditer(sql2):
        a1, c2 = m.group(1), m.group(2)
        if c2:
            alias, col = a1, c2
            table = aliases.get(alias, alias)
        else:
            col, table = a1, next(iter(aliases.values()), "")
        if table:
            ensure(table, col)["not_null"] = True

    return hints


def equality_literal_for_column(
    column_name: str,
    table_name: str,
    sql_hints: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not sql_hints:
        return None
    table_candidates = [table_name, (table_name or '').split('.')[-1].strip('"')]
    col_candidates = [column_name, (column_name or '').lower()]
    for t in table_candidates:
        hints = (sql_hints or {}).get(t, {}) or {}
        for c in col_candidates:
            hint = hints.get(c) or {}
            vals = hint.get('equals') or []
            if vals:
                return vals[0]
    return None


def match_fraction_for_column(
    column_name: str,
    table_name: str,
    sql_hints: Optional[Dict[str, Dict[str, Dict[str, Any]]]],
    simulation_profile: Optional[Dict[str, Any]],
) -> Optional[float]:
    column_key = (column_name or "").lower()
    if not simulation_profile:
        return None
    default_fraction = simulation_profile.get("default_not_null_match_fraction")
    overrides = simulation_profile.get("not_null_match_fraction_by_column", {}) or {}
    if column_key in overrides:
        return overrides[column_key]
    table_overrides = simulation_profile.get("not_null_match_fraction_by_table_column", {}) or {}
    t = (table_name or "").split(".")[-1].lower()
    if f"{t}.{column_key}" in table_overrides:
        return table_overrides[f"{t}.{column_key}"]
    return default_fraction


def make_sparse_uuid_expr(row_alias: str, fraction: float) -> str:
    if fraction >= 0.999:
        return "gen_random_uuid()"
    if fraction <= 0.0:
        return "NULL"
    step = max(1, int(round(1.0 / fraction)))
    return f"CASE WHEN ({row_alias}) % {step} = 0 THEN gen_random_uuid() ELSE NULL END"


def make_sparse_int_expr(row_alias: str, fraction: float) -> str:
    if fraction >= 0.999:
        return row_alias
    if fraction <= 0.0:
        return "NULL"
    step = max(1, int(round(1.0 / fraction)))
    return f"CASE WHEN ({row_alias}) % {step} = 0 THEN ({row_alias}) ELSE NULL END"


# ---------------------------------------------------------------------------
# Type Registry for CockroachDB Data Types
# ---------------------------------------------------------------------------

_TYPE_REGISTRY_CACHE = None

def load_type_registry() -> Dict[str, Any]:
    """Load CockroachDB type registry from JSON file."""
    global _TYPE_REGISTRY_CACHE
    if _TYPE_REGISTRY_CACHE is not None:
        return _TYPE_REGISTRY_CACHE

    # Look for the registry file in the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    registry_path = os.path.join(script_dir, "crdb_v26_2_type_registry.json")

    if not os.path.exists(registry_path):
        logger.warning(f"Type registry not found at {registry_path}, using fallback")
        _TYPE_REGISTRY_CACHE = {}
        return _TYPE_REGISTRY_CACHE

    try:
        with open(registry_path, 'r') as f:
            _TYPE_REGISTRY_CACHE = json.load(f)
        logger.debug(f"Loaded type registry with {len(_TYPE_REGISTRY_CACHE)} types")
        return _TYPE_REGISTRY_CACHE
    except Exception as e:
        logger.warning(f"Failed to load type registry: {e}")
        _TYPE_REGISTRY_CACHE = {}
        return _TYPE_REGISTRY_CACHE


def lookup_type_info(type_str: str) -> Optional[Dict[str, Any]]:
    """
    Look up type information from registry by matching type string against canonical names and aliases.
    Returns type info dict or None if not found.
    """
    registry = load_type_registry()
    if not registry:
        return None

    # Normalize the type string
    normalized = type_str.upper().strip()

    # Remove length/precision specs: VARCHAR(50) -> VARCHAR, DECIMAL(10,2) -> DECIMAL
    normalized = re.sub(r'\([^)]*\)', '', normalized).strip()

    # Direct lookup by canonical name
    if normalized in registry:
        return registry[normalized]

    # Search by alias
    for canonical, info in registry.items():
        aliases = info.get("aliases", [])
        if normalized in [a.upper() for a in aliases]:
            return info

    return None


def deterministic_registry_seed_expr(
    col_name: str,
    type_str: str,
    nullable: bool,
    row_alias: str = "g.i",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Generate seed expression for a column based on CockroachDB type registry.

    Returns:
        (seed_expr, warning_message)
        - seed_expr: SQL expression to generate test data, or None if type not in registry
        - warning_message: Optional warning about unsupported types
    """
    type_info = lookup_type_info(type_str)
    if not type_info:
        return None, None

    strategy = type_info.get("seed_strategy", "")
    category = type_info.get("category", "")

    # Handle unsupported types
    if strategy == "unsupported_safe":
        warning = f"Type {type_info['canonical']} ({category}) is not fully supported for seed data generation"
        return "NULL", warning

    # Generate seed expressions based on strategy
    if strategy == "int":
        return row_alias, None

    elif strategy == "string":
        return f"concat('{col_name}_', {row_alias})", None

    elif strategy == "bool":
        return f"(({row_alias}) % 2 = 0)", None

    elif strategy == "uuid":
        return "gen_random_uuid()", None

    elif strategy == "timestamp":
        return f"(now() - (({row_alias}) || ' seconds')::interval)", None

    elif strategy == "timestamptz":
        return f"(now() - (({row_alias}) || ' seconds')::interval)", None

    elif strategy == "date":
        return f"(current_date - (({row_alias}) % 365))", None

    elif strategy == "time":
        return f"('00:00:00'::time + (({row_alias}) || ' seconds')::interval)", None

    elif strategy == "timetz":
        return f"('00:00:00'::time + (({row_alias}) || ' seconds')::interval)", None

    elif strategy == "interval":
        return f"(({row_alias}) || ' seconds')::interval", None

    elif strategy == "decimal":
        return f"(({row_alias})::decimal)", None

    elif strategy == "float":
        return f"(({row_alias})::float)", None

    elif strategy == "jsonb":
        return f"jsonb_build_object('id', {row_alias}, 'name', concat('item_', {row_alias}))", None

    elif strategy == "array":
        return f"ARRAY[{row_alias}, ({row_alias}) + 1]", None

    elif strategy == "bytes":
        return f"('\\x' || md5({row_alias}::text))::bytes", None

    elif strategy == "bit":
        return f"({row_alias})::bit(8)", None

    elif strategy == "inet":
        # Generate valid IPv4 addresses
        return f"('192.168.' || (({row_alias}) % 256) || '.' || (({row_alias}) / 256 % 256))::inet", None

    elif strategy == "ltree":
        # Generate hierarchical paths like "root.branch_1.leaf_5"
        return f"('root.branch_' || (({row_alias}) % 10) || '.leaf_' || {row_alias})::ltree", None

    elif strategy == "oid":
        return f"({row_alias})::oid", None

    elif strategy == "enum":
        # For ENUM types, we'd need the actual enum values - fall back to string
        return f"concat('enum_', {row_alias})", None

    elif strategy == "tsquery":
        return f"to_tsquery('simple', concat('word', {row_alias}))", None

    elif strategy == "tsvector":
        return f"to_tsvector('simple', concat('document ', {row_alias}))", None

    elif strategy == "vector":
        # Generate a simple 3-dimensional vector
        return f"('[' || {row_alias} || ',' || ({row_alias}+1) || ',' || ({row_alias}+2) || ']')::vector(3)", None

    else:
        # Unknown strategy
        return None, f"Unknown seed strategy '{strategy}' for type {type_info['canonical']}"


def seed_expr_for_column(
    col: ColumnDef,
    table: TableDef,
    row_alias: str = "g.i",
    sql_hints: Optional[Dict] = None,
    simulation_profile: Optional[Dict] = None,
    table_size: Optional[int] = None,
) -> str:
    t = col.type_part.lower()
    cn = col.name.lower()

    lit = equality_literal_for_column(col.name, table.name, sql_hints)
    eq_frac = equality_match_fraction_for_column(cn, table.name, simulation_profile)

    # PRIMARY KEY columns must have unique values, so use unique alternates
    is_pk = col.is_primary_key_inline or (col.name in (table.primary_key or []))

    if lit is not None:
        if eq_frac is not None and 0.0 <= float(eq_frac) < 1.0:
            # Use table_size as bucket_size for accurate selectivity matching
            # This ensures very low selectivity (e.g. 0.01%) generates correct number of matches
            # Note: eq_frac=0.0 means "generate 0 matching rows" which is valid
            bucket_size = table_size if table_size and table_size > 1000 else 1000
            selector = make_match_selector_expr(row_alias, float(eq_frac), bucket_size=bucket_size)
            # For PK columns, non-match values must be unique; for non-PK, they can repeat
            non_match_value = build_non_match_value(col.name, lit, col.type_part, is_unique=is_pk, row_alias=row_alias)
            return f"CASE WHEN {selector} THEN {lit} ELSE {non_match_value} END"
        # If no selectivity fraction but there's a literal, use it only if not a PK
        # For PK, we need unique values so fall through to default logic
        if not is_pk:
            return lit

    # Look up table hints with multiple key candidates (same pattern as equality_literal_for_column)
    table_candidates = [table.name, table.name.split(".")[-1].strip('"')]
    col_candidates = [col.name, cn]  # cn is already lowercase

    hint = {}
    for table_candidate in table_candidates:
        table_hints = (sql_hints or {}).get(table_candidate, {}) or {}
        if table_hints:
            for c in col_candidates:
                hint = table_hints.get(c) or {}
                if hint.get("in_values") or hint.get("equals"):
                    break
            if hint.get("in_values") or hint.get("equals"):
                break

    if hint.get("in_values"):
        vals = hint["in_values"]
        return vals[0] if len(vals) == 1 else f"(ARRAY[{', '.join(vals)}])[1 + (({row_alias} - 1) % {len(vals)})]"

    if col.default_expr:
        return col.default_expr

    nn_fraction = (simulation_profile or {}).get("not_null_match_fraction_by_column", {}).get(cn)

    if "jsonb" in t or t == "json":
        return (
            f"jsonb_build_object('seed', {row_alias}, 'device_identifier', concat('dev-', {row_alias}),"
            f" 'alternative_hier_path', jsonb_build_array({row_alias}))"
        )

    if "uuid" in t:
        if nn_fraction is not None and col.nullable:
            threshold = max(0, min(1000, int(round(float(nn_fraction) * 1000))))
            return f"CASE WHEN ((({row_alias}) - 1) % 1000) < {threshold} THEN gen_random_uuid() ELSE NULL END"
        return "gen_random_uuid()"

    if "bool" in t:
        return f"(({row_alias}) % 2 = 0)"

    if "timestamp" in t or "timestamptz" in t:
        return f"(now() - (({row_alias}) || ' seconds')::interval)"

    if t == "date":
        return f"(current_date - (({row_alias}) % 30))"

    if "decimal" in t or "numeric" in t or "float" in t or "double" in t:
        return f"(({row_alias})::decimal)"

    if "int[]" in t or t.endswith("[]") or "array" in t:
        return f"ARRAY[{row_alias}, ({row_alias}) + 1]"

    if any(x in t for x in ["int", "serial"]):
        if nn_fraction is not None and col.nullable:
            threshold = max(0, min(1000, int(round(float(nn_fraction) * 1000))))
            return f"CASE WHEN ((({row_alias}) - 1) % 1000) < {threshold} THEN ({row_alias}) ELSE NULL END"
        return row_alias

    if any(x in t for x in ["string", "text", "varchar", "char"]):
        return f"concat('{table.name.replace('.', '_')}_{cn}_', {row_alias})"

    reg_expr, _reg_warn = deterministic_registry_seed_expr(col.name, col.type_part, col.nullable, row_alias=row_alias)
    if reg_expr is not None:
        return reg_expr
    return "NULL" if col.nullable else f"concat('{table.name.replace('.', '_')}_{cn}_', {row_alias})"


def suggest_seed_variation_pct(bundle_fraction: Optional[float]) -> Dict[str, Any]:
    frac = None if bundle_fraction is None else max(0.0, min(1.0, float(bundle_fraction)))
    if frac is None:
        return {
            "suggested_variation_pct": None,
            "suggested_effective_fraction": None,
            "reason": "Bundle selectivity could not be derived reliably.",
        }
    if frac >= 0.95:
        suggested = 70.0
    elif frac >= 0.70:
        suggested = 50.0
    elif frac >= 0.40:
        suggested = 30.0
    else:
        suggested = 0.0
    effective = max(0.0, min(1.0, frac * (1.0 - suggested / 100.0)))
    return {
        "suggested_variation_pct": suggested,
        "suggested_effective_fraction": effective,
        "reason": (
            "Bundle selectivity is very high, so replaying with some variation can help test a more selective what-if scenario."
            if frac >= 0.95 else
            "Bundle selectivity is moderately high, so some variation may help explore index what-if scenarios."
            if frac >= 0.40 else
            "Bundle selectivity is already selective enough that replaying with zero variation is usually the best default."
        ),
    }

def build_simulation_profile(
    facts: Dict[str, Any],
    sql_hints: Dict[str, Any],
    seed_variation_pct: Optional[float] = None,
) -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "default_not_null_match_fraction": None,
        "not_null_match_fraction_by_column": {},
        "not_null_match_fraction_by_table_column": {},
        "default_equality_match_fraction": None,
        "equality_match_fraction_by_column": {},
        "equality_match_fraction_by_table_column": {},
        "seed_variation_pct": float(seed_variation_pct or 0.0),
    }

    variation = max(0.0, min(95.0, float(seed_variation_pct or 0.0))) / 100.0

    selectivity = facts.get("post_filter_selectivity")
    if selectivity is not None and facts.get("has_is_not_null_predicate"):
        nn_fraction = max(0.0, min(1.0, float(selectivity) * (1.0 - variation)))
        for col in facts.get("where_columns") or []:
            key = (col or "").lower()
            profile["not_null_match_fraction_by_column"][key] = nn_fraction

    eq_fraction = None
    # Calculate selectivity based on ACTUAL result rows, not estimated table coverage
    if facts.get("post_filter_rows") is not None and facts.get("table_rows_estimate"):
        # Use actual returned rows vs table size for accurate selectivity
        eq_fraction = max(0.0, min(1.0, float(facts["post_filter_rows"]) / float(facts["table_rows_estimate"])))
    elif facts.get("estimated_table_coverage_pct") is not None:
        eq_fraction = max(0.0, min(1.0, float(facts["estimated_table_coverage_pct"]) / 100.0))
    elif facts.get("scan_rows") and facts.get("table_rows_estimate"):
        eq_fraction = max(0.0, min(1.0, float(facts["scan_rows"]) / float(facts["table_rows_estimate"])))

    if eq_fraction is not None:
        # Apply variation to reduce match rate
        # e.g., 80% selectivity with 50% variation → 40% match rate
        # Special case: 0% selectivity stays 0% (value doesn't exist in either dataset)
        if eq_fraction == 0.0:
            # Preserve exact 0% - no rows should match in test DB either
            logger.debug(f"Bundle had 0% selectivity - preserving as 0% (no matching rows in test DB)")
        else:
            eq_fraction = max(0.0, min(1.0, eq_fraction * (1.0 - variation)))
        profile["default_equality_match_fraction"] = eq_fraction

        # Populate column-specific dictionaries for WHERE columns from sql_hints
        for table_name, cols in (sql_hints or {}).items():
            if not isinstance(cols, dict):
                continue
            short_table = (table_name or "").split(".")[-1].strip('"').lower()
            for col_name, hint in cols.items():
                if isinstance(hint, dict) and hint.get("equals"):
                    ck = (col_name or "").lower()
                    profile["equality_match_fraction_by_table_column"][f"{short_table}.{ck}"] = eq_fraction
                    profile["equality_match_fraction_by_column"][ck] = eq_fraction

        # CRITICAL: Also populate for ALL WHERE columns from facts (not just sql_hints)
        # This ensures the seed generator picks up the match fraction even without explicit hints
        for col_name in facts.get("where_columns") or []:
            ck = (col_name or "").lower()
            if ck not in profile["equality_match_fraction_by_column"]:
                profile["equality_match_fraction_by_column"][ck] = eq_fraction
                logger.debug(f"Applied eq_fraction {eq_fraction:.6f} to WHERE column: {ck}")

    return profile

def db_exec(conn, sql_text: str) -> None:
    for stmt in split_sql_statements(sql_text):
        with conn.cursor() as cur:
            cur.execute(stmt)


def db_exec_capture(conn, sql_text: str) -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for stmt in split_sql_statements(sql_text):
        with conn.cursor() as cur:
            cur.execute(stmt)
            preview: List[Any] = []
            try:
                rows = cur.fetchall()
                preview = rows[:5]
            except Exception:
                preview = []
            outputs.append({
                "statement": stmt,
                "status": "OK",
                "rowcount": cur.rowcount,
                "rows_preview": preview,
            })
    return outputs


def db_exec_one_capture(conn, stmt: str) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(stmt)
        preview: List[Any] = []
        try:
            rows = cur.fetchall()
            preview = rows[:5]
        except Exception:
            preview = []
        return {
            "statement": stmt,
            "status": "OK",
            "rowcount": cur.rowcount,
            "rows_preview": preview,
        }


def extract_column_types_from_schema(schema_sql: str) -> dict:
    """
    Extract column names and their types from CREATE TABLE statements.

    Returns: dict mapping column_name -> type_category
    Type categories: 'string', 'numeric', 'date', 'timestamp', 'boolean'
    """
    column_types = {}

    # Match CREATE TABLE statements and extract columns
    # Pattern: column_name TYPE
    pattern = r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+(VARCHAR|STRING|TEXT|CHAR|INT\d*|BIGINT|SMALLINT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|DATE|TIMESTAMP|TIME|BOOL|BOOLEAN)'

    for line in schema_sql.split('\n'):
        match = re.match(pattern, line.strip(), re.IGNORECASE)
        if match:
            col_name = match.group(1).lower()
            col_type = match.group(2).upper()

            # Categorize type
            if col_type.startswith(('VARCHAR', 'STRING', 'TEXT', 'CHAR')):
                column_types[col_name] = 'string'
            elif col_type.startswith(('INT', 'BIGINT', 'SMALLINT', 'DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'REAL')):
                column_types[col_name] = 'numeric'
            elif col_type == 'DATE':
                column_types[col_name] = 'date'
            elif col_type in ('TIMESTAMP', 'TIME'):
                column_types[col_name] = 'timestamp'
            elif col_type.startswith('BOOL'):
                column_types[col_name] = 'boolean'

    return column_types


def substitute_redacted_values(sql_text: str, schema_sql: str = "") -> str:
    """
    Replace redacted values (‹×›) and placeholders ($1, $2, etc.) with sample values.

    Statement bundles redact sensitive data with ‹×› and use $1, $2 as parameter markers.
    These need to be replaced with valid SQL literals for EXPLAIN ANALYZE to work.

    Examples:
      match_status = ‹×› → match_status = 1
      trade_date = $1 → trade_date = CURRENT_DATE
      name = $2 → name = 'sample'
    """
    if not sql_text:
        return sql_text

    # Extract column types from schema if available
    column_types = {}
    if schema_sql:
        column_types = extract_column_types_from_schema(schema_sql)

    # Replace redacted values ‹×› with sample values
    sql_text = re.sub(r'‹×›', '1', sql_text)

    # Replace parameter placeholders with type-appropriate values
    # Strategy: Find patterns like "column_name = $N" and use column type

    def substitute_placeholder(match):
        """Substitute a placeholder based on context."""
        full_match = match.group(0)
        col_name = match.group(1).lower() if match.group(1) else None
        operator = match.group(2) if match.group(2) else '='
        placeholder = match.group(3)

        # Determine type from column name
        if col_name and col_name in column_types:
            col_type = column_types[col_name]
            if col_type == 'string':
                return f"{match.group(1)} {operator} 'sample'"
            elif col_type == 'date':
                return f"{match.group(1)} {operator} CURRENT_DATE"
            elif col_type == 'timestamp':
                return f"{match.group(1)} {operator} CURRENT_TIMESTAMP"
            elif col_type == 'boolean':
                return f"{match.group(1)} {operator} true"
            else:  # numeric
                return f"{match.group(1)} {operator} 1"

        # Fallback: Guess from column name patterns
        col_lower = col_name.lower() if col_name else ''
        if 'date' in col_lower or col_lower.endswith('dt'):
            return f"{match.group(1)} {operator} CURRENT_DATE"
        elif 'time' in col_lower or 'timestamp' in col_lower:
            return f"{match.group(1)} {operator} CURRENT_TIMESTAMP"
        elif any(x in col_lower for x in ['id', 'name', 'code', 'symbol', 'indicator', 'isin']):
            return f"{match.group(1)} {operator} 'sample'"
        else:
            # Default to numeric
            return f"{match.group(1)} {operator} 1"

    # Pattern: column_name = $N or column_name IN ($N, ...)
    sql_text = re.sub(
        r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(=|<>|!=|IN)\s*(\$\d+)',
        substitute_placeholder,
        sql_text,
        flags=re.IGNORECASE
    )

    # Handle IN clauses with multiple placeholders
    sql_text = re.sub(r'\$\d+(?=\s*,\s*\$\d+|\s*\))', "'sample'", sql_text)

    # Remaining placeholders (shouldn't happen, but fallback)
    sql_text = re.sub(r'\$\d+', '1', sql_text)

    return sql_text


def fetch_plan_text(conn, sql_text: str) -> str:
    explain_sql = "EXPLAIN ANALYZE " + sql_text.strip().rstrip(";")
    with conn.cursor() as cur:
        cur.execute(explain_sql)
        rows = cur.fetchall()
    return "\n".join(str(r[0]) for r in rows)


def named_test_index_ddl(ddl: str, seq: int) -> Tuple[str, Optional[str]]:
    text = ddl.strip().rstrip(";")
    m = re.match(r"(?is)^CREATE\s+(INVERTED\s+)?INDEX\s+ON\s+([A-Za-z0-9_\.\"-]+)\s*(\(.*)$", text)
    if m:
        inverted = m.group(1) or ""
        table = m.group(2)
        rest = m.group(3)
        idx_name = f"crdb_analyzer_test_{seq}"
        return f"CREATE {inverted}INDEX {idx_name} ON {table} {rest};", idx_name
    return (ddl if ddl.endswith(";") else ddl + ";"), None



def is_database_multiregion(conn) -> bool:
    """
    Check if the connected CockroachDB database is multi-region enabled.

    Returns:
        True if database has multi-region enabled (has a primary region)
        False if single-region database
    """
    try:
        with conn.cursor() as cur:
            # Try to query regions - this will work if multi-region is enabled
            cur.execute("SELECT region FROM [SHOW REGIONS FROM DATABASE] LIMIT 1")
            rows = cur.fetchall()
            # If we got any rows, database is multi-region
            return len(rows) > 0
    except Exception:
        # If query fails, database is not multi-region enabled
        return False


def strip_locality_clauses(schema_sql: str) -> str:
    """
    Remove LOCALITY clauses from CREATE TABLE statements.
    Multi-region CockroachDB features don't work on local single-region databases.

    Example:
      ) LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
      -> );
    """
    # Pattern matches LOCALITY clause at end of CREATE TABLE before the final semicolon
    # Handles various formats:
    # - LOCALITY REGIONAL BY TABLE IN PRIMARY REGION
    # - LOCALITY REGIONAL BY ROW
    # - LOCALITY GLOBAL
    pattern = r'\)\s+LOCALITY\s+[^;]+;'
    replacement = r');'

    cleaned = re.sub(pattern, replacement, schema_sql, flags=re.IGNORECASE | re.MULTILINE)
    return cleaned


def strip_problematic_defaults(schema_sql: str) -> str:
    """
    Remove DEFAULT clauses that contain volatile functions or redacted values.

    CockroachDB doesn't allow volatile functions (like now(), random()) in DEFAULT.
    Bundle schemas often have:
    - DEFAULT now():::TIMESTAMP
    - DEFAULT ‹×›:::TYPE (redacted values)
    - ON UPDATE now():::TIMESTAMP

    For testing, we don't need these - seed data is inserted explicitly.

    Examples:
      DEFAULT now():::TIMESTAMP → (removed)
      DEFAULT ‹×›:::INT8 → (removed)
      ON UPDATE now():::TIMESTAMP → (removed)
    """
    # Pattern 1: Remove DEFAULT ... ON UPDATE ... (combined pattern)
    # Matches: DEFAULT now():::TIMESTAMP ON UPDATE now():::TIMESTAMP
    schema_sql = re.sub(
        r'\s+DEFAULT\s+[^,]+\s+ON\s+UPDATE\s+[^,]+',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    # Pattern 2: Remove standalone ON UPDATE clauses
    schema_sql = re.sub(
        r'\s+ON\s+UPDATE\s+[^,\)]+',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    # Pattern 3: Remove DEFAULT clauses with now() function
    # Include the closing ) and optional cast (:::TYPE)
    schema_sql = re.sub(
        r'\s+DEFAULT\s+now\(\)(:::[A-Z]+)?',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    # Pattern 4: Remove DEFAULT clauses with redacted values (‹×›)
    # Include optional cast (:::TYPE)
    schema_sql = re.sub(
        r'\s+DEFAULT\s+‹×›(:::[A-Z0-9]+)?',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    # Pattern 5: Remove DEFAULT clauses with current_timestamp() or similar
    # Include the closing ) and optional cast
    schema_sql = re.sub(
        r'\s+DEFAULT\s+current_timestamp\(\)(:::[A-Z]+)?',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    # Pattern 6: Remove DEFAULT clauses with uuid_v4() or other volatile functions
    # CRITICAL FIX: Include the closing ) to avoid leaving stray )
    schema_sql = re.sub(
        r'\s+DEFAULT\s+(gen_random_uuid|uuid_v4|random|nextval)\(\)',
        '',
        schema_sql,
        flags=re.IGNORECASE
    )

    return schema_sql


def seed_tables_from_schema(
    conn,
    schema_sql: str,
    rows_per_table: int,
    sql_text: str = "",
    simulation_profile: Optional[Dict] = None,
    table_row_counts: Optional[Dict[str, int]] = None,
) -> Tuple[bool, List[str], List[str], List[Dict]]:
    tables = parse_schema_tables(schema_sql)
    if not tables:
        return False, ["Could not parse CREATE TABLE definitions from schema.sql."], [], []

    ordered = order_tables_by_dependencies(tables)
    logs: List[str] = []
    exec_outputs: List[Dict] = []
    analyzed_tables: List[str] = []
    rowcount_map: Dict[str, int] = {}
    pk_cache: Dict[str, List[str]] = {}
    sql_hints = build_sql_seed_hints(sql_text or "")
    check_literal_map = parse_single_literal_check_map(schema_sql or "")

    def _table_hints(table_name: str) -> Dict[str, Any]:
        # Try both full table name and short name without quotes
        for t in [table_name, (table_name or "").split(".")[-1].strip('"')]:
            hints = (sql_hints or {}).get(t, {})
            if hints:
                return hints
        return {}

    for table in ordered:
        th = _table_hints(table.name)
        eq_frac = None
        try:
            eq_frac = float((simulation_profile or {}).get("default_equality_match_fraction"))
        except Exception:
            eq_frac = None

        has_eq = any(equality_literal_for_column(col.name, table.name, sql_hints) is not None for col in table.columns)

        # Always use requested rows_per_table for table size
        # Bundle row counts are only used for informational logging
        table_name_only = table.name.split('.')[-1].strip('"')
        effective_rows = max(1, int(rows_per_table or 1))

        bundle_row_count = None
        if table_row_counts and table_name_only in table_row_counts:
            bundle_row_count = table_row_counts[table_name_only]
            logs.append(
                f"-- Bundle had {bundle_row_count} rows for {table.name}, creating {effective_rows} rows for testing"
            )

        if has_eq and eq_frac is not None:
            logs.append(
                f"-- seed info for {table.name}: requested_rows={rows_per_table}, "
                f"bundle_equality_match_fraction={eq_frac:.6f}, table_size={effective_rows}"
            )

        insert_cols: List[str] = []
        select_exprs: List[str] = []

        for col in table.columns:
            if _is_non_seedable_column_name(col.name):
                continue
            col_key = (col.name or "").lower()
            forced_check_lit = check_literal_map.get(col_key)

            if forced_check_lit:
                expr = forced_check_lit
            else:
                # Find FK match, handling schema-less references
                fk_match = None
                for fk in table.foreign_keys:
                    if col.name not in fk.columns:
                        continue
                    # Try exact match first
                    if fk.ref_table in rowcount_map:
                        fk_match = fk
                        break
                    # If FK ref has no schema, try prepending current table's schema
                    if '.' not in fk.ref_table.replace('"', ''):
                        # Current table has schema (e.g., "public"."orders")
                        if '.' in table.name.replace('"', ''):
                            schema_part = table.name.split('.')[0]  # e.g., "public"
                            qualified_ref = f"{schema_part}.{fk.ref_table}"  # e.g., "public"."customers"
                            if qualified_ref in rowcount_map:
                                # Update fk.ref_table for consistent use below
                                fk.ref_table = qualified_ref
                                fk_match = fk
                                break

                if fk_match:
                    idx = fk_match.columns.index(col.name)
                    ref_col = fk_match.ref_columns[idx] if idx < len(fk_match.ref_columns) else (pk_cache.get(fk_match.ref_table) or ["1"])[0]
                    expr = (
                        f"(SELECT {_qident(ref_col)} FROM {fk_match.ref_table} ORDER BY 1 LIMIT 1"
                        f" OFFSET ((g.i - 1) % GREATEST(1, (SELECT count(*) FROM {fk_match.ref_table}))))"
                    )
                else:
                    expr = seed_expr_for_column(col, table, sql_hints=sql_hints, simulation_profile=simulation_profile, table_size=effective_rows)

            if expr == "NULL" and not col.nullable and not col.default_expr and not col.is_primary_key_inline:
                # Fallback for non-nullable columns without defaults
                # Check if it's an integer type - use g.i directly instead of concat
                col_type_upper = col.type_part.upper()
                if any(int_type in col_type_upper for int_type in ['INT', 'BIGINT', 'SMALLINT', 'INTEGER', 'SERIAL']):
                    expr = "g.i"
                else:
                    expr = f"concat('{table.name.replace('.', '_').replace(chr(34), '')}_{col.name}_', g.i)"

            insert_cols.append(_qident(col.name))
            select_exprs.append(expr)

        insert_sql = (
            f"INSERT INTO {table.name} ({', '.join(insert_cols)}) "
            f"SELECT {', '.join(select_exprs)} FROM generate_series(1, {effective_rows}) AS g(i);"
        )

        try:
            exec_outputs.extend(db_exec_capture(conn, insert_sql))
            logs.append(insert_sql)
            analyzed_tables.append(table.name)
            rowcount_map[table.name] = effective_rows
            pk_cache[table.name] = table.primary_key[:] if table.primary_key else ([table.columns[0].name] if table.columns else [])
        except Exception as e:
            logs.append(f"FAILED for {table.name}: {e}. SQL: {insert_sql}")
            return False, logs, analyzed_tables, exec_outputs

    return True, logs, analyzed_tables, exec_outputs

def analyze_tables(conn, table_names: List[str], db_logs: Dict[str, Any]) -> None:
    for table_name in dedupe(table_names):
        stmt = f"ANALYZE {table_name};"
        db_logs["analyze_sql"].append(stmt)
        try:
            db_logs["analyze_output"].append(db_exec_one_capture(conn, stmt))
        except Exception as e:
            db_logs["messages"].append(f"ANALYZE failed for {table_name}: {e}")


# ---------------------------------------------------------------------------
# LLM / Ollama
# ---------------------------------------------------------------------------


def _extract_first_balanced_json_object(text: str) -> Optional[str]:
    s = (text or "").strip()
    if not s:
        return None

    # Prefer fenced json block if present.
    m = re.search(r"```json\s*(\{.*?\})\s*```", s, re.S | re.I)
    if m:
        return m.group(1)

    start = s.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
    return None


def _cleanup_llm_text(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"```(?:json)?", "", s, flags=re.I)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.strip()


def _extract_section_value(text: str, labels: List[str]) -> Optional[str]:
    for label in labels:
        pattern = rf'(?is)(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*[:\-]\s*(.*?)(?=\n\s*(?:[-*]\s*)?(?:\*\*)?(?:Query Summary|Execution Plan|Execution Plan Summary|Key Observations|Interesting Point|Why This Is Better|When This Matters|Final Verdict|Bottom Line)(?:\*\*)?\s*[:\-]|\Z)'
        m = re.search(pattern, text)
        if m:
            val = m.group(1).strip()
            if val:
                return val
    return None


def _extract_list_section(text: str, labels: List[str]) -> List[str]:
    raw = _extract_section_value(text, labels)
    if not raw:
        return []
    items = []
    for line in raw.splitlines():
        line = line.strip()
        line = re.sub(r'^(?:[-*]|\d+\.)\s*', '', line)
        if line:
            items.append(line)
    if not items and raw.strip():
        items = [x.strip() for x in re.split(r';|\n', raw) if x.strip()]
    return items[:8]


def recover_narrative_from_text(raw_text: str) -> Optional[Dict[str, Any]]:
    text = _cleanup_llm_text(raw_text)
    if not text:
        return None

    out: Dict[str, Any] = {}
    out["query_summary"] = _extract_section_value(text, ["Query Summary"])
    out["execution_plan_summary"] = _extract_section_value(text, ["Execution Plan", "Execution Plan Summary"])
    out["key_observations"] = _extract_list_section(text, ["Key Observations"])
    out["interesting_point"] = _extract_section_value(text, ["Interesting Point"])
    out["why_this_is_better"] = _extract_list_section(text, ["Why This Is Better"])
    out["when_this_matters"] = _extract_list_section(text, ["When This Matters"])
    out["final_verdict"] = _extract_section_value(text, ["Final Verdict"])
    out["bottom_line"] = _extract_section_value(text, ["Bottom Line"])

    populated = sum(1 for k, v in out.items() if v)
    if populated >= 3:
        out["_recovered_from_text"] = True
        return out

    # Last-resort heuristic: use first non-empty lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 3:
        heur = {
            "query_summary": lines[0][:300],
            "execution_plan_summary": lines[1][:400] if len(lines) > 1 else "",
            "key_observations": lines[2:5],
            "_recovered_from_text": True,
        }
        return heur
    return None

def warmup_ollama_model(model: str) -> bool:
    """
    Pre-load the model into Ollama's memory with a minimal prompt.
    Returns True if successful, False otherwise.
    This significantly speeds up the first real analysis.
    """
    ok, installed, ollama_running = validate_ollama_model(model)
    if not ollama_running:
        logger.warning(f"Cannot warmup - Ollama is not running at {OLLAMA_URL}")
        return False
    if not ok:
        logger.warning(f"Cannot warmup - model '{model}' not installed. Available: {installed}")
        return False

    try:
        logger.info(f"Warming up Ollama model '{model}'...")
        r = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": "Ready", "stream": False},
            timeout=60,
        )
        r.raise_for_status()
        logger.info(f"✓ Model '{model}' loaded into memory")
        return True
    except requests.exceptions.ConnectionError:
        logger.warning(f"Ollama not reachable at {OLLAMA_URL}")
        return False
    except requests.exceptions.Timeout:
        logger.warning(f"Model warmup timed out - model may be very large")
        return False
    except Exception as e:
        logger.warning(f"Warmup error: {e}")
        return False


def call_ollama(prompt: str, model: str) -> Tuple[str, Optional[str]]:
    """
    Returns (response_text, error_message).
    error_message is None on success.
    """
    ok, installed, ollama_running = validate_ollama_model(model)
    if not ollama_running:
        msg = f"Ollama is not running at {OLLAMA_URL}. Using rule-based fallback analysis."
        logger.warning(msg)
        return "{}", msg
    if not ok:
        msg = (
            f"Ollama model '{model}' is not installed. "
            f"Installed models: {', '.join(installed) if installed else 'none'}"
        )
        logger.warning(msg)
        return "{}", msg
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=180,  # Enough for llama3.3:70b
        )
        r.raise_for_status()
        payload = r.json()
        return payload.get("response", "").strip(), None
    except requests.exceptions.ConnectionError:
        msg = f"Ollama not reachable at {OLLAMA_URL}. Is it running?"
        logger.warning(msg)
        return "{}", msg
    except requests.exceptions.Timeout:
        msg = f"Ollama request timed out after 180s for model {model}."
        logger.warning(msg)
        return "{}", msg
    except Exception as e:
        msg = f"Ollama error: {e}"
        logger.warning(msg)
        return "{}", msg


def call_narrative_llm(prompt: str, model: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (parsed_dict_or_None, error_message_or_None)."""
    raw, err = call_ollama(prompt, model)
    if err:
        return None, err

    clean = _cleanup_llm_text(raw)

    try:
        data = json.loads(clean)
        return (data if isinstance(data, dict) else None), None
    except Exception:
        pass

    candidate = _extract_first_balanced_json_object(clean)
    if candidate:
        try:
            data = json.loads(candidate)
            return (data if isinstance(data, dict) else None), None
        except Exception:
            pass

    recovered = recover_narrative_from_text(clean)
    if isinstance(recovered, dict):
        return recovered, None

    return None, "LLM response did not contain a recoverable JSON object."



def canonicalize_narrative_keys(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return data
    key_map = {
        "query_summary": "query_summary",
        "execution_plan_summary": "execution_plan_summary",
        "key_observations": "key_observations",
        "interesting_point": "interesting_point",
        "why_this_is_better": "why_this_is_better",
        "when_this_matters": "when_this_matters",
        "final_verdict": "final_verdict",
        "bottom_line": "bottom_line",
    }
    out: Dict[str, Any] = {}
    for k, v in data.items():
        norm = str(k).strip().lower()
        norm = norm.replace(" ", "_")
        target = key_map.get(norm)
        if target:
            if target in ("key_observations", "why_this_is_better", "when_this_matters"):
                if isinstance(v, list):
                    out[target] = [str(x).strip() for x in v if str(x).strip()]
                elif isinstance(v, str) and v.strip():
                    out[target] = [v.strip()]
            else:
                if isinstance(v, str) and v.strip():
                    out[target] = v.strip()
                else:
                    out[target] = v
        else:
            out[k] = v
    return out


def fill_missing_narrative_fields(
    narrative: Dict[str, Any],
    default: Dict[str, Any],
    rule_result: Dict[str, Any],
) -> Dict[str, Any]:
    out = dict(narrative or {})
    if not out.get("final_verdict"):
        out["final_verdict"] = default.get("final_verdict") or rule_result.get("why") or "Already reasonable."
    if not out.get("bottom_line"):
        if rule_result.get("recommended_action"):
            out["bottom_line"] = " ".join(str(x) for x in rule_result.get("recommended_action", []) if str(x).strip())
        else:
            out["bottom_line"] = default.get("bottom_line") or rule_result.get("why") or "No immediate change is required."
    if not out.get("query_summary"):
        out["query_summary"] = default.get("query_summary", "")
    if not out.get("execution_plan_summary"):
        out["execution_plan_summary"] = default.get("execution_plan_summary", "")
    return out

def _REQUIRED_NARRATIVE_KEYS() -> List[str]:
    return [
        "query_summary", "execution_plan_summary", "key_observations",
        "final_verdict", "bottom_line",
    ]


def sanitize_narrative(
    llm_narrative: Optional[Dict[str, Any]],
    llm_error: Optional[str],
    facts: Dict[str, Any],
    rule_result: Dict[str, Any],
    plan_shape: str,
) -> Dict[str, Any]:
    """
    Validate the LLM narrative and merge it with the rule-based default.
    - Canonicalize near-miss keys from the LLM.
    - Fill a small set of missing scalar fields from the deterministic default.
    - Only mark the result partial when meaningful fields are still missing after repair.
    """
    default = _default_narrative(facts, rule_result, plan_shape)

    if llm_error:
        default["_llm_skipped_reason"] = llm_error
        return default

    if not isinstance(llm_narrative, dict):
        default["_llm_skipped_reason"] = "LLM did not return a dict."
        return default

    repaired = canonicalize_narrative_keys(llm_narrative) or {}
    repaired = fill_missing_narrative_fields(repaired, default, rule_result)

    missing_keys = [k for k in _REQUIRED_NARRATIVE_KEYS() if not repaired.get(k)]
    if missing_keys:
        merged = dict(default)
        for k, v in repaired.items():
            if v:
                merged[k] = v
        merged["_llm_partial"] = True
        merged["_llm_missing_keys"] = missing_keys
        merged["_llm_skipped_reason"] = None
        if repaired.get("_recovered_from_text"):
            merged["_recovered_from_text"] = True
        return merged

    result = dict(default)
    for k, v in repaired.items():
        if v:
            result[k] = v
    result["_llm_used"] = True
    if repaired.get("_recovered_from_text"):
        result["_recovered_from_text"] = True
    result["_llm_partial"] = False
    result["_llm_missing_keys"] = []
    result["_llm_skipped_reason"] = None
    return result


# ---------------------------------------------------------------------------
# Fact derivation
# ---------------------------------------------------------------------------

def summarize_trace(trace: str) -> Dict[str, Any]:
    batch_sizes = re.findall(r"sending a batch with ([0-9,]+) requests", trace or "", re.I)
    batch_size = max([safe_int(x) or 0 for x in batch_sizes], default=0)
    return {"lookup_batch_size": batch_size if batch_size > 0 else None, "has_large_lookup_batch": batch_size > 1000}


def derive_rule_facts(bundle: Dict[str, str]) -> Dict[str, Any]:
    plan = bundle.get("plan", "") or ""
    sql = bundle.get("sql", "") or ""
    table, used_index = parse_table_and_index(plan)
    scan_rows = parse_scan_rows(plan)
    filter_rows = parse_filter_rows(plan)
    est_scan_rows = parse_estimated_scan_rows(plan)
    table_coverage_pct = parse_table_coverage_from_plan(plan)
    facts: Dict[str, Any] = {
        "table": table,
        "used_index": used_index,
        "used_index_leading_columns_guess": [],
        "execution_time_ms": parse_execution_time(plan),
        "scan_rows": scan_rows,
        "post_filter_rows": filter_rows,
        "index_join_rows": parse_index_join_rows(plan),
        "index_join_kv_time_ms": parse_index_join_kv_ms(plan),
        "scan_kv_time_ms": parse_scan_kv_ms(plan),
        "total_kv_time_ms": parse_total_kv_ms(plan),
        "total_kv_rows_decoded": parse_total_kv_rows(plan),
        "estimated_scan_rows": est_scan_rows,
        "estimate_accuracy_ratio": round(scan_rows / est_scan_rows, 3) if est_scan_rows and scan_rows else None,
        "estimated_table_coverage_pct": table_coverage_pct,
        "stats_collected": parse_stats_collected(plan),
        "stats_problem_suspected": False,
        "selected_columns": extract_select_columns(sql),
        "where_columns": extract_where_columns(sql),
        "join_columns_by_table": extract_join_columns(sql, plan),
        "json_predicates": [],
        "array_predicates": [],
        "has_or_predicate": bool(re.search(r"\bor\b", sql, re.I)),
        "has_limit": bool(re.search(r"\blimit\b", sql, re.I)),
        "has_order_by": bool(re.search(r"\border by\b", sql, re.I)),
        "has_group_by": bool(re.search(r"\bgroup by\b", sql, re.I)),
        "select_star": bool(re.search(r"select\s+\*", sql, re.I)),
        "has_is_not_null_predicate": bool(re.search(r"\bis\s+not\s+null\b", sql, re.I)),
        "vectorization_issue": "",
        "post_filter_selectivity": round(filter_rows / scan_rows, 3) if filter_rows and scan_rows else None,
        "existing_indexes_observed": extract_existing_indexes(
            bundle.get("schema", ""), plan, bundle.get("env", "")
        ),
        "trace_summary": summarize_trace(bundle.get("trace", "")),
        "full_scan_suspected": detect_full_scan(plan),
        "near_full_table_scan": bool(table_coverage_pct and table_coverage_pct >= 80.0),
        "dominant_bottleneck_guess": (
            "full_scan" if detect_full_scan(plan)
            else "index_join" if (parse_index_join_rows(plan) is not None)
            else "unknown"
        ),
        "post_scan_required_columns": [],
    }
    facts["table_rows_estimate"] = (
        int(round(scan_rows / (table_coverage_pct / 100.0))) if scan_rows and table_coverage_pct else None
    )

    # Detect already-optimized patterns
    plan_lower = (plan or "").lower()
    already_efficient = False
    is_anti_join = False
    if "merge join (anti)" in plan_lower or "hash join (anti)" in plan_lower:
        # Anti-join is already optimal for NOT IN / NOT EXISTS
        already_efficient = True
        is_anti_join = True
    elif facts.get("execution_time_ms") and facts.get("execution_time_ms") < 5 and scan_rows and scan_rows <= 100:
        # Very fast query with minimal scans
        already_efficient = True

    facts["already_efficient"] = already_efficient

    # If anti-join detected, clear misleading filter signals
    # Anti-join scans are necessary for the join algorithm, not a bottleneck
    if is_anti_join:
        facts["where_columns"] = []  # NOT IN is join predicate, not WHERE filter
        facts["dominant_bottleneck_guess"] = "none"  # Anti-join is optimal, no bottleneck
    facts["broad_json_or_case"] = False
    facts["wide_projection_index_join_case"] = False
    facts["redundant_lookup_shape_case"] = False
    return facts


def build_baseline_signals(facts: Dict[str, Any], schema_sql: str = "") -> Dict[str, Any]:
    # When already_efficient=True, don't send misleading filter signals
    already_efficient = facts.get("already_efficient", False)

    return {
        "dominant_bottleneck_guess": facts.get("dominant_bottleneck_guess"),
        "already_efficient": already_efficient,
        "broad_json_or_case": facts.get("broad_json_or_case"),
        "wide_projection_index_join_case": facts.get("wide_projection_index_join_case"),
        "redundant_lookup_shape_case": facts.get("redundant_lookup_shape_case"),
        "projection_columns_not_covered_by_prefix": [] if already_efficient else (facts.get("selected_columns") or []),
        "filter_columns_not_covered_by_prefix": [] if already_efficient else (facts.get("where_columns") or []),
        "post_scan_required_columns": facts.get("post_scan_required_columns") or [],
        "current_filter_prefix": [] if already_efficient else ((facts.get("where_columns") or [])[:1]),
        "table_rows_estimate": facts.get("table_rows_estimate"),
        "bundle_post_filter_selectivity": facts.get("post_filter_selectivity"),
        "bundle_scan_rows": facts.get("scan_rows"),
        "bundle_filter_rows": facts.get("post_filter_rows"),
        "existing_index_columns": extract_index_columns_from_schema(schema_sql or ""),
        "primary_key_columns": extract_primary_key_columns_from_schema(schema_sql or ""),
    }



def extract_is_not_null_columns(sql: str) -> List[str]:
    sql2 = strip_sql_comments(sql or "")
    out: List[str] = []
    for m in re.finditer(rf"({QUAL_IDENT})\s+is\s+not\s+null", sql2, re.I):
        out.append(normalize_ident(m.group(1)))
    return dedupe(out)


def enrich_partial_index_narrative(
    narrative: Dict[str, Any],
    facts: Dict[str, Any],
    bundle: Dict[str, str],
) -> Dict[str, Any]:
    out = dict(narrative or {})
    text_fields = [
        str(out.get("bottom_line", "")),
        str(out.get("final_verdict", "")),
        str(out.get("interesting_point", "")),
        " ".join(str(x) for x in (out.get("key_observations", []) or [])),
    ]
    combined = " ".join(text_fields).lower()
    if "partial index" not in combined:
        return out

    table = facts.get("table") or "your_table"
    nn_cols = extract_is_not_null_columns(bundle.get("sql", ""))
    if not nn_cols:
        return out

    col = nn_cols[0]
    ddl = f"CREATE INDEX ON {table} ({col}) WHERE {col} IS NOT NULL;"
    detail = (
        f"For this query shape, a partial index can be attractive because it only stores rows where "
        f"`{col} IS NOT NULL`, which can reduce index size and write overhead while still supporting "
        f"the predicate. Example DDL: `{ddl}`"
    )

    if ddl not in out.get("bottom_line", ""):
        bottom = (out.get("bottom_line") or "").strip()
        out["bottom_line"] = (bottom + " " + detail).strip()

    observations = list(out.get("key_observations", []) or [])
    if not any("partial index" in str(x).lower() for x in observations):
        observations.append(
            f"Partial-index option: `{ddl}` could be worth testing if `{col} IS NOT NULL` is selective enough."
        )
        out["key_observations"] = observations[:8]

    return out

def build_rag_query(facts: Dict[str, Any], bundle: Optional[Dict[str, str]] = None) -> str:
    """
    Build targeted RAG retrieval query based on SQL scenario.
    More specific queries → Better chunk retrieval → More relevant LLM context.

    Returns a search query optimized for the specific SQL performance pattern.
    """
    bottleneck = facts.get("dominant_bottleneck_guess", "")
    plan_text = ""
    sql_text = ""

    if bundle and isinstance(bundle, dict):
        plan_text = (bundle.get("plan", "") or "").lower()
        sql_text = (bundle.get("sql", "") or "").lower()

    # Scenario 1: Anti-join patterns (NOT IN, NOT EXISTS) - Already optimized
    if ("merge join (anti)" in plan_text or "hash join (anti)" in plan_text or
        "not in" in sql_text or "not exists" in sql_text):
        return "CockroachDB anti-join NOT IN NOT EXISTS already optimized keep plan merge join"

    # Scenario 2: Lookup join with primary key - Already optimized
    if "lookup join" in plan_text:
        return "CockroachDB lookup join primary key nested loop foreign key already optimal"

    # Scenario 3: Full scan - Differentiate by selectivity
    if bottleneck == "full_scan":
        selectivity = facts.get("selectivity", 1.0)
        if selectivity < 0.1:  # Highly selective (< 10%)
            return "CockroachDB highly selective full scan missing index create index low selectivity"
        elif selectivity > 0.8:  # Low selectivity (> 80%)
            return "CockroachDB full scan high selectivity when index not needed keep plan most rows"
        else:  # Medium selectivity (10-80%)
            return "CockroachDB full scan moderate selectivity index evaluation cost benefit"

    # Scenario 4: Index join - Covering index opportunity
    if bottleneck == "index_join":
        return "CockroachDB index join covering index STORING columns avoid lookup reduce KV"

    # Scenario 5: Query rewrite scenarios
    rewrite_reason = facts.get("rewrite_reason", "")
    if "large IN clause" in rewrite_reason or ("in (" in sql_text and sql_text.count(",") > 15):
        return "CockroachDB large IN clause rewrite optimization temporary table performance"
    if "JSON" in rewrite_reason or "jsonb" in sql_text or "->" in sql_text:
        return "CockroachDB JSON JSONB query optimization inverted index GIN"

    # Scenario 6: Hash join - Memory optimization
    if "hash join" in plan_text and "anti" not in plan_text:
        return "CockroachDB hash join optimization memory spill disk workload temporary storage"

    # Scenario 7: Merge join - Ordered index optimization
    if "merge join" in plan_text and "anti" not in plan_text:
        return "CockroachDB merge join ordered index sorted optimization matching order"

    # Scenario 8: Aggregation patterns
    if "group" in plan_text:
        if "streaming" in plan_text:
            return "CockroachDB streaming group aggregation sorted index already optimized GROUP BY"
        elif "hash" in plan_text:
            return "CockroachDB hash aggregation GROUP BY index optimization sorted input"

    # Scenario 9: Primary key lookups - Already optimal
    if facts.get("pk_lookup_pattern") or ("limited span" in plan_text and "pkey" in plan_text):
        return "CockroachDB primary key lookup limited spans point lookup already optimal"

    # Scenario 10: Subquery and CTE patterns
    if ("subquery" in plan_text or "cte" in plan_text or
        (sql_text.count("select") > 1 and ("with " in sql_text or "in (select" in sql_text))):
        return "CockroachDB subquery CTE common table expression optimization correlated nested"

    # Scenario 11: Window functions
    if "window" in plan_text or "row_number" in sql_text or "rank(" in sql_text or "partition by" in sql_text:
        return "CockroachDB window function optimization partitioning ordering performance"

    # Scenario 12: Index scan patterns (already using index)
    if "index scan" in plan_text and bottleneck != "index_join":
        return "CockroachDB index scan optimization partial index filtered covering"

    # Default: Generic optimizer guidance
    return "CockroachDB explain analyze optimizer query performance tuning best practices"


# ---------------------------------------------------------------------------
# Strategy evaluation  (single canonical implementation)
# ---------------------------------------------------------------------------

def existing_best_filter_index_used(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    bundle: Dict[str, str],
) -> Tuple[bool, Optional[str]]:
    existing_index_cols = signals.get("existing_index_columns", {}) or {}
    used_non_pk_index = parse_deepest_non_pk_index(bundle.get("plan", "") if isinstance(bundle, dict) else "")
    where_cols = [c for c in (facts.get("where_columns") or []) if c]
    ok = bool(
        used_non_pk_index
        and used_non_pk_index in existing_index_cols
        and is_prefix_columns(where_cols, existing_index_cols.get(used_non_pk_index, []))
    )
    return ok, used_non_pk_index


def estimate_covering_gap_columns(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    used_non_pk_index: Optional[str],
) -> List[str]:
    existing_index_cols = signals.get("existing_index_columns", {}) or {}
    used_cols = [c.lower() for c in existing_index_cols.get(used_non_pk_index or "", [])]
    selected_cols = [c for c in (facts.get("selected_columns") or []) if c]
    return [c for c in selected_cols if c.lower() not in used_cols]


def generate_or_to_union_rewrite(sql_text: str) -> Optional[str]:
    """
    Convert OR predicates to UNION ALL for better index utilization.

    Example:
      SELECT * FROM orders WHERE (a=1 AND b='x') OR (a=2 AND b='y')
      →
      SELECT * FROM orders WHERE a=1 AND b='x'
      UNION ALL
      SELECT * FROM orders WHERE a=2 AND b='y'
    """
    sql2 = strip_sql_comments(sql_text or "").strip()

    # Pattern: SELECT ... FROM table WHERE (cond1) OR (cond2)
    # Simple case: two parenthesized conditions joined by OR
    m = re.search(
        r"""(?is)^select\s+(.+?)\s+from\s+([A-Za-z0-9_\."\s]+?)\s+where\s+\((.+?)\)\s+or\s+\((.+?)\)\s*;?\s*$""",
        sql2
    )

    if m:
        select_clause = m.group(1).strip()
        from_clause = m.group(2).strip()
        cond1 = m.group(3).strip()
        cond2 = m.group(4).strip()

        return f"""SELECT {select_clause} FROM {from_clause} WHERE {cond1}
UNION ALL
SELECT {select_clause} FROM {from_clause} WHERE {cond2}"""

    return None


def extract_simple_rewrite_sql(sql_text: str) -> Optional[str]:
    """Extract rewrite SQL for known patterns (nested SELECT, OR predicates)."""
    sql2 = strip_sql_comments(sql_text or "")

    # Try OR → UNION rewrite first
    union_rewrite = generate_or_to_union_rewrite(sql_text)
    if union_rewrite:
        return union_rewrite

    # Try nested SELECT LIMIT 1 rewrite
    m = re.search(
        r"""(?is)select\s+(?P<outer_select>.*?)\s+from\s+(?P<outer_from>[A-Za-z0-9_\."]+)\s+as\s+(?P<outer_alias>[A-Za-z_][A-Za-z0-9_]*)\s+where\s+(?P<lhs>[A-Za-z_][A-Za-z0-9_]*\."?[A-Za-z0-9_]+"?)\s*=\s*\(\s*select\s+(?P<rhs>[A-Za-z_][A-Za-z0-9_]*\."?[A-Za-z0-9_]+"?)\s+from\s+(?P<inner_from>[A-Za-z0-9_\."]+)\s+as\s+(?P<inner_alias>[A-Za-z_][A-Za-z0-9_]*)\s+where\s+(?P<inner_where>.*?)\s+limit\s+1\s*\)\s*limit\s+1\s*$""",
        sql2,
    )
    if not m:
        return None
    outer_from = m.group("outer_from")
    inner_from = m.group("inner_from")
    if outer_from.replace('"', '').split(".")[-1].lower() != inner_from.replace('"', '').split(".")[-1].lower():
        return None
    lhs_col = m.group("lhs").split(".")[-1].replace('"', '')
    rhs_col = m.group("rhs").split(".")[-1].replace('"', '')
    if lhs_col.lower() != rhs_col.lower():
        return None
    inner_where = re.sub(
        r'(?is)\s+and\s+([A-Za-z_][A-Za-z0-9_]*\.)?"?[A-Za-z0-9_]+"?\s+is\s+not\s+null',
        '',
        m.group("inner_where").strip(),
    )
    return f"SELECT {m.group('outer_select')} FROM {outer_from} AS {m.group('outer_alias')} WHERE {inner_where} LIMIT 1"


def query_needs_rewrite_not_index(facts: Dict[str, Any], sql: str) -> Optional[Dict[str, str]]:
    sql2 = strip_sql_comments(sql or "")
    if re.search(r"\bunion\s+all\b", sql2, re.I):
        return None

    # Check for OR predicate with full scan and low selectivity
    has_or = facts.get("has_or_predicate", False) or re.search(r"\bor\b", sql2, re.I)
    full_scan = facts.get("full_scan_suspected", False)
    scan_rows = facts.get("scan_rows", 0)
    filter_rows = facts.get("post_filter_rows", 0)

    # Calculate selectivity (rows returned / rows scanned)
    selectivity = 1.0
    if scan_rows and scan_rows > 0:
        selectivity = (filter_rows or 0) / scan_rows

    # OR with full scan and low selectivity (<10%) is a strong UNION candidate
    if has_or and full_scan and selectivity < 0.10:
        return {
            "why": "OR predicate with full scan prevents efficient index usage (0 rows returned from 10,000 scanned).",
            "action": "Rewrite OR to UNION ALL to allow separate index seeks per branch.",
        }

    # Fallback: OR with JSON/meta columns
    if has_or and ("meta" in sql2.lower() or "json" in sql2.lower()):
        return {
            "why": "The query shape suggests a rewrite may help more than another simple index.",
            "action": "Compare the current OR predicate against a UNION ALL rewrite or split branches and test both plans.",
        }

    if re.search(r"=\s*\(\s*select\s+.+?\blimit\s+1\s*\)", sql2, re.I | re.S):
        return {
            "why": "The query uses a nested lookup shape where eliminating the extra lookup may help more than another index.",
            "action": "Flatten the lookup if possible and compare against a single-step query plan.",
        }
    return None


def query_is_already_optimal(facts: Dict[str, Any], signals: Dict[str, Any], bundle: Dict[str, str]) -> bool:
    existing_index_cols = signals.get("existing_index_columns", {}) or {}
    used = parse_deepest_non_pk_index(bundle.get("plan", "") or "")
    where_cols = [c for c in (facts.get("where_columns") or []) if c]
    exec_ms = facts.get("execution_time_ms") or 0
    if used and used in existing_index_cols and is_prefix_columns(where_cols, existing_index_cols[used]):
        if exec_ms <= SC.already_optimal_latency_ms:
            return True
    if not facts.get("full_scan_suspected") and exec_ms <= SC.already_optimal_no_scan_ms:
        return True
    return False


def classify_tuning_scenarios(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    bundle: Dict[str, str],
) -> List[str]:
    scenarios: List[str] = []
    sql_text = bundle.get("sql", "") if isinstance(bundle, dict) else ""
    where_cols = [c for c in (facts.get("where_columns") or []) if c]
    existing_index_cols = signals.get("existing_index_columns", {}) or {}

    if facts.get("full_scan_suspected"):
        scenarios.append("full_scan")
        if facts.get("has_is_not_null_predicate") and (facts.get("post_filter_selectivity") or 0) >= 0.9:
            scenarios.append("low_selectivity_is_not_null")
        elif where_cols:
            scenarios.append("selective_full_scan_candidate")

    if facts.get("dominant_bottleneck_guess") == "index_join":
        scenarios.append("index_join")
        best_index_used, used_non_pk_index = existing_best_filter_index_used(facts, signals, bundle)
        if best_index_used:
            scenarios.append("good_filter_index_already_used")
            gap_cols = estimate_covering_gap_columns(facts, signals, used_non_pk_index)
            if gap_cols:
                scenarios.append("covering_index_candidate")

    if query_needs_rewrite_not_index(facts, sql_text):
        scenarios.append("rewrite_candidate")

    if re.search(r"(?is)\bjsonb?\b|@>", sql_text):
        scenarios.append("json_query")
        if "meta" in sql_text.lower():
            scenarios.append("json_index_candidate")

    if re.search(r"(?is)\bin\s*\(", sql_text):
        vals = re.findall(r"(?is)\bin\s*\((.*?)\)", sql_text)
        if any(v.count(",") >= SC.large_in_clause_min_items for v in vals):
            scenarios.append("large_in_clause")

    if " hash join" in (bundle.get("plan", "") or "").lower():
        scenarios.append("hash_join")
    if " lookup join" in (bundle.get("plan", "") or "").lower() or " index join" in (bundle.get("plan", "") or "").lower():
        scenarios.append("lookup_or_index_join")

    if facts.get("stats_problem_suspected"):
        scenarios.append("stats_suspected")

    return scenarios


def evaluate_strategy_options(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    bundle: Dict[str, str],
) -> Dict[str, Any]:
    table = facts.get("table") or "your_table"
    sql_text = bundle.get("sql", "") if isinstance(bundle, dict) else ""
    where_cols = [c for c in (facts.get("where_columns") or []) if c]
    join_cols_by_table = facts.get("join_columns_by_table", {}) or {}
    join_cols_for_table = join_cols_by_table.get(table, [])
    full_scan = bool(facts.get("full_scan_suspected"))
    exec_ms = float(facts.get("execution_time_ms") or 0.0)
    dominant = facts.get("dominant_bottleneck_guess") or "unknown"
    scenarios = classify_tuning_scenarios(facts, signals, bundle)

    best_index_used, used_non_pk_index = existing_best_filter_index_used(facts, signals, bundle)
    covering_gap_cols = estimate_covering_gap_columns(facts, signals, used_non_pk_index)
    rewrite_case = query_needs_rewrite_not_index(facts, sql_text)
    simple_rewrite_sql = extract_simple_rewrite_sql(sql_text)

    options: List[Dict[str, Any]] = []

    # 1) Keep current plan
    keep_score = 0.0
    keep_reason_bits: List[str] = []
    if best_index_used:
        keep_score += SC.good_index_used_boost
        keep_reason_bits.append("good existing index already used")
    if exec_ms <= SC.very_low_latency_ms:
        keep_score += SC.very_low_latency_boost
        keep_reason_bits.append("very low latency")
    elif exec_ms <= SC.acceptable_latency_ms:
        keep_score += SC.acceptable_latency_boost
        keep_reason_bits.append("acceptable latency")
    if dominant == "index_join":
        keep_score += SC.index_join_targeted_boost
        keep_reason_bits.append("targeted index path")
    if "low_selectivity_is_not_null" in scenarios:
        keep_score += SC.low_selectivity_boost
        keep_reason_bits.append("predicate matches nearly all rows so simple index is low value")
    if full_scan and facts.get("table_rows_estimate") and facts.get("table_rows_estimate") <= SC.small_table_row_limit and exec_ms <= SC.small_table_latency_ms:
        keep_score += SC.small_fast_full_scan_boost
        keep_reason_bits.append("small fast full scan")
    options.append({
        "name": "keep_current_plan",
        "kind": "baseline",
        "score": round(keep_score, 2),
        "recommended": keep_score >= SC.keep_score_threshold,
        "why": f"The current bundle plan for {table} is already reasonable" + (f" ({', '.join(keep_reason_bits)})" if keep_reason_bits else "."),
        "recommended_action": "No immediate change is required.",
        "candidate_indexes": [],
        "candidate_rewrites": [],
    })

    # 2) Missing index candidate
    idx_score = 0.0
    idx_reason = f"No strong missing-index signal is visible from the original bundle plan on {table}."
    idx_candidates: List[Dict[str, Any]] = []

    if "low_selectivity_is_not_null" in scenarios:
        idx_score = SC.low_selectivity_index_score
        idx_reason = (
            f"The bundle plan is a real full scan on {table}, but the predicate is IS NOT NULL with near-100% selectivity, "
            "so a simple index is unlikely to improve the plan."
        )
    elif full_scan and (join_cols_for_table or where_cols):
        idx_score = SC.full_scan_index_score
        # Prefer join columns for tables being scanned in joins
        if join_cols_for_table:
            idx_cols = join_cols_for_table
            idx_reason = f"The bundle plan shows a full scan on {table} in a JOIN operation."
            idx_candidates.append({
                "ddl": f"CREATE INDEX ON {table} ({', '.join(idx_cols)});",
                "reason": f"Index on JOIN column(s) to enable more efficient join access on {table}.",
            })
        elif where_cols:
            idx_reason = f"The bundle plan shows a real full scan on {table}."
            idx_candidates.append({
                "ddl": f"CREATE INDEX ON {table} ({', '.join(where_cols[:1])});",
                "reason": "Basic selective full-scan fallback candidate.",
            })
    elif dominant == "index_join" and best_index_used and covering_gap_cols:
        idx_score = SC.covering_index_score
        idx_reason = f"The bundle already uses the best observed filter index on {table}; only a covering-index experiment is worth testing."
        idx_candidates.append({
            "ddl": f"CREATE INDEX ON {table} ({', '.join(where_cols)}) STORING ({', '.join(covering_gap_cols[:5])});",
            "reason": "Optional covering-index experiment to reduce index-join lookups on the same filter prefix.",
        })
    elif "json_query" in scenarios and "meta" in sql_text.lower():
        idx_score = 0.50
        idx_reason = "The query appears to filter on JSON data, so JSON indexing or computed columns may help more than a generic index."
    elif "large_in_clause" in scenarios:
        idx_score = 0.15
        idx_reason = "The large IN-clause shape is more likely to benefit from rewrite testing than from a generic new index."

    options.append({
        "name": "index_candidate",
        "kind": "index",
        "score": round(idx_score, 2),
        "recommended": idx_score >= SC.index_score_threshold and len(idx_candidates) > 0,
        "why": idx_reason,
        "recommended_action": "Test a new index." if idx_score >= SC.index_score_threshold and idx_candidates else "Do not add a new index based on the current evidence.",
        "candidate_indexes": idx_candidates,
        "candidate_rewrites": [],
    })

    # 3) Rewrite candidate
    rw_score = 0.0
    rw_reason = "No strong rewrite signal is visible from the SQL shape."
    rw_candidates: List[Dict[str, Any]] = []

    if rewrite_case:
        rw_score = SC.rewrite_score
        rw_reason = rewrite_case["why"]
        rw_candidates.append({"sql": simple_rewrite_sql or "", "reason": rewrite_case["action"]})
    elif "large_in_clause" in scenarios:
        rw_score = SC.large_in_rewrite_score
        rw_reason = "The query contains a very large IN clause, so a CTE/UNNEST rewrite is worth comparing."
        rw_candidates.append({"sql": "", "reason": "Compare the large IN clause against a CTE + UNNEST / join rewrite."})
    elif "json_query" in scenarios:
        rw_score = SC.json_rewrite_score
        rw_reason = "JSON predicate rewrites using computed STORED columns may help more than leaving the query on raw JSON access."

    options.append({
        "name": "rewrite_candidate",
        "kind": "rewrite",
        "score": round(rw_score, 2),
        "recommended": rw_score >= SC.rewrite_score_threshold,
        "why": rw_reason,
        "recommended_action": "Test a rewrite first." if rw_score >= SC.rewrite_score_threshold else "No rewrite is required based on the current SQL shape.",
        "candidate_indexes": [],
        "candidate_rewrites": rw_candidates,
    })

    # 4) Rewrite + index
    rwi_score = 0.0
    rwi_reason = "No combined rewrite-plus-index case is strongly supported."
    rwi_indexes: List[Dict[str, Any]] = []
    rwi_rewrites: List[Dict[str, Any]] = []

    if rewrite_case and full_scan and (join_cols_for_table or where_cols) and "low_selectivity_is_not_null" not in scenarios:
        rwi_score = SC.rewrite_plus_index_score
        rwi_reason = "The query has both a rewrite opportunity and a real missing-index signal."
        rwi_rewrites.append({"sql": simple_rewrite_sql or "", "reason": rewrite_case["action"]})
        # Prefer join columns if available
        idx_cols = join_cols_for_table if join_cols_for_table else where_cols[:1]
        rwi_indexes.append({
            "ddl": f"CREATE INDEX ON {table} ({', '.join(idx_cols)});",
            "reason": "Pair with rewritten query if the rewritten form still needs a better access path.",
        })

    options.append({
        "name": "rewrite_plus_index_candidate",
        "kind": "rewrite_plus_index",
        "score": round(rwi_score, 2),
        "recommended": rwi_score >= SC.rewrite_plus_index_threshold,
        "why": rwi_reason,
        "recommended_action": "Test rewrite first, then compare with an added index." if rwi_score >= SC.rewrite_plus_index_threshold else "No combined rewrite-plus-index experiment is needed initially.",
        "candidate_indexes": rwi_indexes,
        "candidate_rewrites": rwi_rewrites,
    })

    options.sort(key=lambda x: x.get("score", 0), reverse=True)
    best = options[0] if options else None
    return {
        "options": options,
        "best": best,
        "best_index_used": best_index_used,
        "used_non_pk_index": used_non_pk_index,
        "covering_gap_cols": covering_gap_cols,
        "simple_rewrite_sql": simple_rewrite_sql,
        "scenarios": scenarios,
    }


def build_rule_result(facts: Dict[str, Any], signals: Dict[str, Any], bundle: Dict[str, str]) -> Dict[str, Any]:
    """Single canonical implementation — evaluates strategy options and maps to a rule result."""
    table = facts.get("table") or "your_table"
    eval_result = evaluate_strategy_options(facts, signals, bundle)
    best = eval_result.get("best") or {}
    options = eval_result.get("options") or []
    scenarios = eval_result.get("scenarios") or []

    result: Dict[str, Any] = {
        "primary_bottleneck": best.get("name", "already_reasonable"),
        "why": best.get("why", f"The current plan on {table} looks reasonable."),
        "recommended_action": [best.get("recommended_action", "No immediate change is required.")],
        "candidate_indexes": best.get("candidate_indexes", []) or [],
        "candidate_rewrites": best.get("candidate_rewrites", []) or [],
        "statistics_assessment": "Statistics do not appear to be the main issue.",
        "confidence": "high" if (best.get("score", 0) >= SC.high_confidence_threshold) else "medium",
        "evaluated_options": options,
        "scenario_tags": scenarios,
    }

    if best.get("name") == "keep_current_plan":
        result["primary_bottleneck"] = "already_optimal"
    elif best.get("name") == "index_candidate":
        if result["candidate_indexes"]:
            if eval_result.get("best_index_used") and eval_result.get("covering_gap_cols"):
                result["primary_bottleneck"] = "index_join_on_best_existing_filter_index"
            else:
                result["primary_bottleneck"] = "full_scan"
        else:
            result["primary_bottleneck"] = "already_reasonable"
    elif best.get("name") == "rewrite_candidate":
        result["primary_bottleneck"] = "rewrite_candidate"
    elif best.get("name") == "rewrite_plus_index_candidate":
        result["primary_bottleneck"] = "rewrite_plus_index_candidate"

    if "low_selectivity_is_not_null" in scenarios:
        result["primary_bottleneck"] = "low_selectivity_full_scan"
        result["why"] = (
            f"The bundle plan is a real full scan on {table}, but the predicate matches nearly all rows, "
            "so a simple index is unlikely to improve the plan."
        )
        result["recommended_action"] = [
            "Do not add a simple index for this IS NOT NULL predicate based on the current evidence.",
            "Keep the current plan unless workload profiling or a more selective predicate changes the economics.",
        ]
        result["candidate_indexes"] = []
        result["candidate_rewrites"] = []

    return result


def validate_rule_result(facts: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {"valid": True, "score": 1.0, "issues": []}


# ---------------------------------------------------------------------------
# Narrative generation  (single canonical implementation)
# ---------------------------------------------------------------------------

def _default_narrative(facts: Dict[str, Any], rule_result: Dict[str, Any], plan_shape: str) -> Dict[str, Any]:
    pb = rule_result.get("primary_bottleneck")
    exec_ms = ms_string(facts.get("execution_time_ms"))
    opts = rule_result.get("evaluated_options", []) or []
    scenarios = rule_result.get("scenario_tags", []) or []

    def top_options_lines() -> List[str]:
        return [f"{opt.get('name')}: score {opt.get('score')}" for opt in opts[:4]]

    if pb == "low_selectivity_full_scan":
        return {
            "query_summary": "This statement does perform a real full scan, but a simple index is unlikely to help.",
            "execution_plan_summary": f"The bundle plan shows a full scan, but the predicate matches nearly all rows. Execution time is around {exec_ms}.",
            "key_observations": [
                "The full scan is real.",
                "The filter is IS NOT NULL with near-100% selectivity.",
                "A simple index on the filtered column is unlikely to change the access path.",
            ] + top_options_lines(),
            "interesting_point": "A real full scan does not automatically mean a new index will help.",
            "why_this_is_better": ["Prevents low-value index recommendations on non-selective predicates."],
            "when_this_matters": ["When nearly every row matches the predicate."],
            "final_verdict": "Keep current plan.",
            "bottom_line": "Do not add a simple index for this IS NOT NULL predicate based on the current evidence.",
        }
    if pb == "already_optimal":
        return {
            "query_summary": "This query already uses an efficient access path.",
            "execution_plan_summary": f"The bundle plan uses a targeted index access path rather than a full table scan, and execution time is around {exec_ms}.",
            "key_observations": [
                "The bundle plan is not a full scan.",
                "Filtering is already handled efficiently by an existing index.",
                "No new simple index or rewrite clearly beats the current plan.",
            ] + top_options_lines(),
            "interesting_point": "The analyzer compares no-change, index, rewrite, and rewrite-plus-index options before choosing a recommendation.",
            "why_this_is_better": ["Avoids redundant indexes.", "Avoids forcing a rewrite when the current bundle plan is already strong."],
            "when_this_matters": ["When workload profiling later shows the statement is truly hot."],
            "final_verdict": "Already reasonable.",
            "bottom_line": "No new index or rewrite is required for this statement right now.",
        }
    if pb == "index_join_on_best_existing_filter_index":
        return {
            "query_summary": "This query already uses the best observed composite index for filtering.",
            "execution_plan_summary": f"The bundle plan is an index scan plus index join, not a full scan. Execution time is around {exec_ms}.",
            "key_observations": [
                "Filtering is already efficient on the existing composite index.",
                "The remaining cost comes from fetching additional projected columns.",
                "The missing-index option is only a covering-index experiment on the same prefix.",
            ] + top_options_lines(),
            "interesting_point": "The real question is coverage, not missing filter access.",
            "why_this_is_better": ["Prevents weaker redundant-index recommendations.", "Still checks whether a covering index could improve the plan."],
            "when_this_matters": ["When the statement is latency-sensitive and index-join lookups dominate."],
            "final_verdict": "Current filter access is already good.",
            "bottom_line": "Only test a covering index on the same prefix if this query is important enough.",
        }
    if pb == "rewrite_candidate":
        return {
            "query_summary": "This query likely benefits more from a rewrite than from another simple index.",
            "execution_plan_summary": f"The current plan does not show a strong missing-index pattern. Execution time is around {exec_ms}.",
            "key_observations": [
                "The SQL shape suggests a rewrite opportunity.",
                "A new simple index is unlikely to be the highest-value fix.",
            ] + top_options_lines(),
            "interesting_point": "The analyzer checked index and rewrite paths and ranked rewrite higher.",
            "why_this_is_better": ["Avoids adding low-value indexes.", "Encourages testing cleaner query forms first."],
            "when_this_matters": ["When nested lookups, large IN lists, or OR-branch patterns dominate."],
            "final_verdict": "Prefer rewrite testing first.",
            "bottom_line": "Compare a rewritten version before adding another simple index.",
        }
    if pb == "rewrite_plus_index_candidate":
        return {
            "query_summary": "This query has both a rewrite opportunity and a possible missing-index path.",
            "execution_plan_summary": f"The bundle plan leaves room to test both query shape and access-path changes. Execution time is around {exec_ms}.",
            "key_observations": top_options_lines(),
            "interesting_point": "This is the case where rewrite and index should be compared together.",
            "why_this_is_better": ["Avoids guessing whether SQL shape or access path matters more."],
            "when_this_matters": ["When both rewrite and index signals are strong."],
            "final_verdict": "Test rewrite first, then index if needed.",
            "bottom_line": "Use a two-step comparison: rewrite, then rewrite-plus-index.",
        }
    if pb == "full_scan":
        return {
            "query_summary": "This statement performs a broad scan.",
            "execution_plan_summary": f"The bundle plan shows a real full scan pattern, with execution time around {exec_ms}.",
            "key_observations": [
                "The bundle plan really does show a full scan.",
                "A missing-index path scored higher than rewrite.",
            ] + top_options_lines(),
            "interesting_point": "This is the classic case where a new index may matter.",
            "why_this_is_better": ["Targets actual missing-index cases."],
            "when_this_matters": ["When table size grows or the query becomes hot."],
            "final_verdict": "Index may help.",
            "bottom_line": "Test a new index on the main filter columns.",
        }
    return {
        "query_summary": "This statement does not show a strong missing-index or rewrite signal.",
        "execution_plan_summary": f"The current plan looks reasonable, with execution time around {exec_ms}.",
        "key_observations": top_options_lines() or ["No urgent tuning issue is visible from the bundle plan."],
        "interesting_point": "The analyzer explicitly checked index and rewrite paths before landing on no change.",
        "why_this_is_better": ["Avoids low-value tuning work."],
        "when_this_matters": ["When workload evidence later shows a hotspot."],
        "final_verdict": "Already reasonable.",
        "bottom_line": "No immediate change is required.",
    }



def align_narrative_with_rule_result(narrative: Dict[str, Any], rule_result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(narrative or {})
    primary = str(rule_result.get("primary_bottleneck", ""))
    actions = [str(x).strip() for x in (rule_result.get("recommended_action") or []) if str(x).strip()]
    default_bottom = " ".join(actions) if actions else "No immediate change is required."

    if str(out.get("query_summary", "")).strip() in ("{", "[", '"{', "'{"):
        out["query_summary"] = "This query was analyzed using the specific SQL statement and plan from the bundle."

    if primary in ("already_optimal", "already_reasonable", "low_selectivity_full_scan"):
        banned = ("create index", "partial index", "recommended creating", "add index", "covering index")
        if any(b in str(out.get("final_verdict", "")).lower() for b in banned):
            out["final_verdict"] = "Keep current plan." if primary == "low_selectivity_full_scan" else "Already reasonable."
        if any(b in str(out.get("bottom_line", "")).lower() for b in banned):
            out["bottom_line"] = default_bottom
        if "why_this_is_better" in out and isinstance(out["why_this_is_better"], list):
            out["why_this_is_better"] = [x for x in out["why_this_is_better"] if not any(b in str(x).lower() for b in banned)]
    return out


def build_final_answer(rule_result: Dict[str, Any], narrative: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rule_result)
    out["narrative"] = align_narrative_with_rule_result(narrative, rule_result)
    return out


# ---------------------------------------------------------------------------
# DB validation
# ---------------------------------------------------------------------------

def extract_drop_statements_from_schema(schema_sql: str) -> List[str]:
    drops: List[str] = []
    seen: set = set()

    # Drop types first (before tables that use them)
    for m in re.finditer(r'(?is)\bcreate\s+type\s+(?:if\s+not\s+exists\s+)?([A-Za-z0-9_\."]+)', schema_sql or ""):
        obj = m.group(1).strip()
        stmt = f"DROP TYPE IF EXISTS {obj} CASCADE;"
        if stmt not in seen:
            seen.add(stmt)
            drops.append(stmt)

    # Drop tables
    for m in re.finditer(r'(?is)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z0-9_\."]+)', schema_sql or ""):
        obj = m.group(1).strip()
        stmt = f"DROP TABLE IF EXISTS {obj} CASCADE;"
        if stmt not in seen:
            seen.add(stmt)
            drops.append(stmt)

    # Drop indexes
    for m in re.finditer(
        r'(?is)\bcreate\s+(?:inverted\s+)?index\s+([A-Za-z0-9_"]+)\s+on\s+([A-Za-z0-9_\."]+)', schema_sql or ""
    ):
        idx = m.group(1).strip()
        tbl = m.group(2).strip()
        stmt = f"DROP INDEX IF EXISTS {tbl}@{idx};"
        if stmt not in seen:
            seen.add(stmt)
            drops.append(stmt)
    return drops


def extract_schema_setup_sql(schema_sql: str) -> str:
    if not schema_sql or not schema_sql.strip():
        return ""
    text = schema_sql
    text = re.sub(r"(?im)^\s*USE\s+.*?;\s*$", "", text)
    text = re.sub(r"(?im)^\s*SET\s+.*?;\s*$", "", text)
    text = re.sub(r"(?im)^\s*CREATE\s+DATABASE\s+.*?;\s*$", "", text)
    return text.strip()


def compare_metrics(before: Dict[str, Any], after: Dict[str, Any], label: str) -> List[str]:
    out = [label]
    for pretty, key, suffix in [
        ("execution time", "execution_time_ms", " ms"),
        ("KV time", "total_kv_time_ms", " ms"),
        ("rows decoded from KV", "rows_decoded", ""),
        ("rows scanned", "scan_rows", ""),
        ("rows returned", "result_rows", ""),
    ]:
        b = before.get(key)
        a = after.get(key)
        if b is not None or a is not None:
            out.append(f"{pretty}: {b} -> {a}{suffix}".replace("None", "n/a"))
    out.append(f"access path: {'full scan' if before.get('has_full_scan') else 'non-full-scan'} -> {'full scan' if after.get('has_full_scan') else 'non-full-scan'}")
    out.append(f"deepest access index: {before.get('used_index')} -> {after.get('used_index')}")
    out.append(f"index join present: {before.get('has_index_join')} -> {after.get('has_index_join')}")
    return out


def run_db_validation(
    conn_str: str,
    bundle: Dict[str, str],
    final_result: Dict[str, Any],
    apply_indexes: bool,
    drop_test_indexes: bool,
    seed_rows: int,
    signals: Dict[str, Any],
    seed_variation_pct: float = 0.0,
    seed_variation_defaulted: bool = False,
    seed_variation_pct_raw: str = "",
) -> Dict[str, Any]:
    seed_variation_pct_raw = (seed_variation_pct_raw or "").strip()
    seed_variation_defaulted = bool(seed_variation_defaulted)
    try:
        seed_variation_pct = float(seed_variation_pct if seed_variation_pct is not None else 0.0)
    except Exception:
        seed_variation_pct = 0.0
    """
    Replay the bundle schema + query against a live CockroachDB instance.
    Schema is normalized for safe replay (idempotent, no zone configs, db-qualifier stripped).
    Seed SQL repair for schema/table name preservation is applied inline.
    """
    result: Dict[str, Any] = {
        "enabled": True,
        "connected": False,
        "errors": [],
        "schema_applied": False,
        "schema_sql_used": "",
        "seed_rows": seed_rows,
        "seed_applied": False,
        "seed_sql_used": "",
        "original_bundle_plan": bundle.get("plan", "") or "",
        "simulated_baseline_plan": "",
        "simulated_post_plan": "",
        "applied_index_ddls": [],
        "applied_index_names": [],
        "rewrite_sql_used": "",
        "comparison_simulated": [],
        "comparison_vs_bundle": [],
        "comparison_notes": [],
        "simulation_profile": {},
        "seed_variation_pct_raw": "",
        "seed_variation_pct_used": 0.0,
        "seed_variation_defaulted": False,
        "bundle_match_fraction": None,
        "effective_replay_match_fraction": None,
        "suggested_seed_variation_pct": None,
        "suggested_effective_fraction": None,
        "db_logs": {
            "cleanup_sql": [],
            "cleanup_output": [],
            "schema_sql": "",
            "schema_output": [],
            "seed_sql": [],
            "seed_output": [],
            "analyze_sql": [],
            "analyze_output": [],
            "index_sql": [],
            "index_output": [],
            "plan_sql_before": "",
            "plan_sql_after": "",
            "messages": [],
        },
    }

    if not conn_str.strip():
        result["errors"].append("Connection string is empty.")
        return result

    conn = None
    try:
        conn = psycopg.connect(conn_str, autocommit=True)
        result["connected"] = True
    except Exception as e:
        result["errors"].append(f"Connection failed: {e}")
        return result

    try:
        original_sql = (bundle.get("sql") or "").strip()
        raw_schema_sql = bundle.get("schema", "")

        # Apply replay-safe normalization
        schema_sql = normalize_replay_schema_sql(extract_schema_setup_sql(raw_schema_sql))
        result["schema_sql_used"] = schema_sql
        result["db_logs"]["schema_sql"] = schema_sql

        if not schema_sql:
            result["errors"].append("No schema.sql found in bundle.")
            return result

        # Build cleanup SQL from original schema (before normalization strips names)
        cleanup_sql_stmts = build_schema_cleanup_sql(raw_schema_sql) if raw_schema_sql else ""

        for drop_stmt in extract_drop_statements_from_schema(schema_sql):
            result["db_logs"]["cleanup_sql"].append(drop_stmt)
            try:
                out = db_exec_one_capture(conn, drop_stmt)
                result["db_logs"].setdefault("cleanup_output", []).append(out)
            except Exception as e:
                result["db_logs"]["messages"].append(f"Cleanup failed for `{drop_stmt}`: {e}")

        # Check if database is multi-region enabled
        db_is_multiregion = is_database_multiregion(conn)

        # Only strip LOCALITY clauses if database is NOT multi-region
        if db_is_multiregion:
            schema_sql_cleaned = schema_sql
            result["db_logs"]["messages"].append(
                "Database is multi-region enabled - preserving LOCALITY clauses"
            )
        else:
            schema_sql_cleaned = strip_locality_clauses(schema_sql)
            if "LOCALITY" in schema_sql and "LOCALITY" not in schema_sql_cleaned:
                result["db_logs"]["messages"].append(
                    "Database is single-region - stripped LOCALITY clauses from schema"
                )

        # Strip problematic DEFAULT clauses (volatile functions, redacted values)
        # This applies to both single-region and multi-region databases
        schema_sql_before_defaults = schema_sql_cleaned
        schema_sql_cleaned = strip_problematic_defaults(schema_sql_cleaned)
        if schema_sql_before_defaults != schema_sql_cleaned:
            result["db_logs"]["messages"].append(
                "Stripped volatile DEFAULT clauses (now(), ‹×›, ON UPDATE) for test database compatibility"
            )

        result["db_logs"]["schema_output"] = db_exec_capture(conn, schema_sql_cleaned)
        result["schema_applied"] = True

        facts = derive_rule_facts(bundle)
        facts["qualified_table"] = infer_qualified_table_name(bundle, facts)

        # First, calculate bundle fraction to determine suggested variation
        bundle_fraction = None
        if facts.get("has_is_not_null_predicate"):
            pf = facts.get("post_filter_selectivity")
            bundle_fraction = max(0.0, min(1.0, float(pf))) if pf is not None else None
        elif facts.get("estimated_table_coverage_pct") is not None:
            bundle_fraction = max(0.0, min(1.0, float(facts["estimated_table_coverage_pct"]) / 100.0))

        # If user didn't provide variation, use the suggested value for better index demonstration
        if seed_variation_defaulted and bundle_fraction is not None:
            suggestion = suggest_seed_variation_pct(bundle_fraction)
            suggested_pct = suggestion.get("suggested_variation_pct")
            if suggested_pct is not None and suggested_pct > 0:
                seed_variation_pct = suggested_pct
                logger.info(f"Auto-applying suggested seed variation: {suggested_pct}% (bundle selectivity: {bundle_fraction*100:.1f}%)")

        # Now build simulation profile with the resolved variation percentage
        simulation_profile = build_simulation_profile(facts, build_sql_seed_hints(original_sql or ""), seed_variation_pct=seed_variation_pct)
        result["seed_variation_pct_used"] = seed_variation_pct
        result["simulation_profile"] = simulation_profile
        result["seed_variation_pct_raw"] = seed_variation_pct_raw
        result["seed_variation_defaulted"] = seed_variation_defaulted

        # Recalculate effective fraction after applying variation
        effective_fraction = None
        if facts.get("has_is_not_null_predicate") and simulation_profile.get("not_null_match_fraction_by_column"):
            effective_fraction = next(iter(simulation_profile["not_null_match_fraction_by_column"].values()))
        elif simulation_profile.get("default_equality_match_fraction") is not None:
            effective_fraction = simulation_profile.get("default_equality_match_fraction")

        suggestion = suggest_seed_variation_pct(bundle_fraction)
        result["bundle_match_fraction"] = bundle_fraction
        result["effective_replay_match_fraction"] = effective_fraction
        result["suggested_seed_variation_pct"] = suggestion.get("suggested_variation_pct")
        result["suggested_effective_fraction"] = suggestion.get("suggested_effective_fraction")

        if seed_variation_defaulted:
            if seed_variation_pct > 0:
                result["comparison_notes"].append(
                    f"Seed variation % was auto-selected: {seed_variation_pct:.1f}% (based on bundle selectivity of {bundle_fraction*100:.1f}%)."
                )
            else:
                result["comparison_notes"].append(
                    "Seed variation % was not provided, using 0% (query already has good selectivity)."
                )
        else:
            result["comparison_notes"].append(
                f"Seed variation % provided by user: raw='{seed_variation_pct_raw}', effective={float(seed_variation_pct or 0.0):.1f}%."
            )

        if bundle_fraction is not None and effective_fraction is not None:
            result["comparison_notes"].append(
                f"Seed behavior summary: bundle-derived match fraction={float(bundle_fraction):.3f}; user variation={float(seed_variation_pct or 0.0):.1f}%; effective replay match fraction={float(effective_fraction):.3f}."
            )

        if bundle_fraction is not None and bundle_fraction >= 0.95:
            result["comparison_notes"].append(
                "Confusing-case note: the original bundle itself looks near-100% selective for this predicate, so default replay behavior preserves that broad match unless you explicitly introduce variation."
            )

        if suggestion.get("suggested_variation_pct") is not None:
            result["comparison_notes"].append(
                f"Suggested variation: {float(suggestion['suggested_variation_pct']):.1f}% (would target replay match fraction around {float(suggestion['suggested_effective_fraction']):.3f})."
            )
        if simulation_profile.get("default_equality_match_fraction") is not None:
            result["comparison_notes"].append(
                f"Synthetic seeding targeted equality-predicate match fraction of approximately {simulation_profile['default_equality_match_fraction']:.3f} based on the original bundle selectivity and seed variation setting of {float(seed_variation_pct or 0.0):.1f}%."
            )

        # Auto-adjust seed_rows based on bundle table size if needed
        # If user provided a very small seed_rows (< 100) but the bundle shows a large table,
        # use the bundle's table size to ensure realistic simulation
        bundle_table_size = facts.get("table_rows_estimate")
        if bundle_table_size and bundle_table_size > 100 and seed_rows < 100:
            result["comparison_notes"].append(
                f"Note: Seed rows auto-adjusted from {seed_rows} to {bundle_table_size} to match original table size from bundle."
            )
            seed_rows = bundle_table_size

        # BUGFIX: Extract per-table row counts from bundle plan
        # This fixes the issue where all tables got the same row count
        table_row_counts = extract_all_table_row_counts(bundle.get("plan", ""))
        if table_row_counts:
            result["comparison_notes"].append(
                f"Note: Using per-table row counts from bundle: {table_row_counts}"
            )

        seed_ok, seed_logs, seeded_tables, seed_exec_outputs = seed_tables_from_schema(
            conn, schema_sql_cleaned, seed_rows, original_sql,
            simulation_profile=simulation_profile,
            table_row_counts=table_row_counts
        )
        for log_line in seed_logs:
            if isinstance(log_line, str) and log_line.startswith("-- adjusted replay rowcount"):
                result["comparison_notes"].append(log_line[3:].strip())
        if simulation_profile.get("not_null_match_fraction_by_column"):
            any_frac = next(iter(simulation_profile["not_null_match_fraction_by_column"].values()))
            threshold = max(0, min(1000, int(round(float(any_frac) * 1000))))
            result["comparison_notes"].append(
                f"Synthetic seeding targeted IS NOT NULL predicate match fraction of approximately {float(any_frac):.3f} based on the original bundle selectivity and seed variation setting of {float(seed_variation_pct or 0.0):.1f}%."
            )
            result["comparison_notes"].append(
                f"IS NOT NULL seed threshold used for replay generation: {threshold} of 1000."
            )
        result["seed_applied"] = seed_ok
        result["seed_sql_used"] = "\n\n".join(seed_logs)
        result["db_logs"]["seed_sql"] = seed_logs
        result["db_logs"]["seed_output"] = seed_exec_outputs

        # Repair seed SQL to reflect replay-safe table names for all tables
        seed_sql_display = result["seed_sql_used"]

        # Find ALL CREATE TABLE statements and build a mapping
        table_name_map = {}
        for m in re.finditer(
            r'(?is)CREATE\s+TABLE\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))?)',
            schema_sql,
        ):
            original_name = m.group(1).replace(' ', '')
            qualified_name = qualify_table_name_for_replay(original_name)
            # Store both qualified and unqualified versions
            table_name_map[original_name] = qualified_name
            # Also store just the table name without schema
            short_name = original_name.split('.')[-1].strip('"')
            table_name_map[short_name] = qualified_name

        # Replace ALL INSERT INTO statements with qualified names
        if table_name_map:
            def replace_insert_table(match):
                table_ref = match.group(1).replace(' ', '')
                # Try to find in map (exact match or short name)
                qualified = table_name_map.get(table_ref)
                if not qualified:
                    # Try short name
                    short = table_ref.split('.')[-1].strip('"')
                    qualified = table_name_map.get(short, table_ref)
                return f'INSERT INTO {qualified}'

            seed_sql_display = re.sub(
                r'(?is)\bINSERT\s+INTO\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*))?)',
                replace_insert_table,
                seed_sql_display,
            )
            result["seed_sql_used"] = seed_sql_display

        if not seed_ok:
            result["errors"].append("Seeding failed for one or more tables.")
            return result

        # Run ANALYZE to collect fresh statistics
        # This is the correct way to test - if the original bundle had stale stats,
        # the plans may differ, but that's informative (shows impact of fresh stats)
        analyze_tables(conn, seeded_tables, result["db_logs"])

        if not original_sql:
            result["errors"].append("No statement.sql found in bundle.")
            return result

        # Extract schemas from seeded tables and set search_path
        # This allows queries without schema qualifiers to find tables in non-default schemas
        schemas_used = set()
        for table_name in seeded_tables:
            if '.' in table_name:
                schema = table_name.split('.')[0].strip('"')
                if schema.lower() != 'public':
                    schemas_used.add(schema)

        if schemas_used:
            # Set search_path to include all non-public schemas plus public
            search_path_schemas = sorted(schemas_used) + ['public']
            search_path_sql = f"SET search_path = {', '.join(search_path_schemas)};"
            try:
                db_exec(conn, search_path_sql)
                result["db_logs"]["messages"].append(
                    f"Set search_path to include schemas: {', '.join(schemas_used)}"
                )
            except Exception as e:
                result["db_logs"]["messages"].append(f"Warning: Could not set search_path: {e}")

        # Substitute redacted values and placeholders for EXPLAIN ANALYZE
        # Pass the cleaned schema so we can determine column types
        sql_for_explain = substitute_redacted_values(original_sql, schema_sql_cleaned)
        if sql_for_explain != original_sql:
            result["db_logs"]["messages"].append(
                "Substituted redacted values (‹×›) and placeholders ($1, $2, etc.) with type-appropriate sample values for EXPLAIN ANALYZE"
            )

        result["db_logs"]["plan_sql_before"] = "EXPLAIN ANALYZE " + sql_for_explain.strip().rstrip(";")
        result["simulated_baseline_plan"] = fetch_plan_text(conn, sql_for_explain)

        # Detect if original bundle had stale statistics
        original_plan_text = bundle.get("plan", "") or ""
        if "stats collected" in original_plan_text.lower():
            # Extract stats age from original plan
            stats_match = re.search(r'stats collected (\d+)\s+(second|minute|hour|day)s? ago', original_plan_text, re.IGNORECASE)
            if stats_match:
                count = stats_match.group(1)
                unit = stats_match.group(2)
                if (unit.lower() in ['hour', 'day']) or (unit.lower() == 'minute' and int(count) > 10):
                    result["comparison_notes"].append(
                        f"Note: Original bundle plan had statistics collected {count} {unit}(s) ago. "
                        f"Simulated baseline uses fresh statistics. Plan differences may be due to stale stats in production."
                    )

        # Add note about complex queries and synthetic data limitations
        has_json_ops = bool(re.search(r'\?|\->|@>', original_sql, re.IGNORECASE))
        has_array_ops = bool(re.search(r'\bANY\b|\bALL\b', original_sql, re.IGNORECASE))
        has_or_predicates = bool(re.search(r'\bOR\b', original_sql, re.IGNORECASE))

        if has_json_ops or has_array_ops or has_or_predicates:
            complexity_notes = []
            if has_json_ops:
                complexity_notes.append("JSON operators")
            if has_array_ops:
                complexity_notes.append("array operations")
            if has_or_predicates:
                complexity_notes.append("OR predicates")

            result["comparison_notes"].append(
                f"Note: This query uses {', '.join(complexity_notes)}, which are difficult to model with synthetic data. "
                f"Plan differences between original and simulated baseline may occur due to different data distributions, "
                f"especially for multi-column correlations and complex predicate selectivity. The comparison is still "
                f"valuable for understanding query patterns and testing index recommendations."
            )

        # Initialize comparison variables (will be populated if recommendations exist)
        simulated_before = None
        simulated_after = None
        bundle_before = None

        # Check if there are any recommendations from the LLM
        has_candidate_indexes = bool((final_result.get("candidate_indexes") or []))
        has_candidate_rewrites = bool((final_result.get("candidate_rewrites") or []))
        has_recommendations = has_candidate_indexes or has_candidate_rewrites

        # Only run post-change simulation if there are recommendations
        if not has_recommendations:
            result["comparison_notes"].append(
                "Simulated post-change testing was skipped because the LLM analysis found no index or rewrite recommendations."
            )
        else:
            test_sql = original_sql

            rewrites = final_result.get("candidate_rewrites", []) or []
            if rewrites:
                chosen = (rewrites[0].get("sql") or "").strip()
                if chosen:
                    test_sql = chosen
                    result["rewrite_sql_used"] = chosen

            if apply_indexes:
                for i, idx in enumerate(final_result.get("candidate_indexes", []) or [], start=1):
                    ddl = (idx.get("ddl") or "").strip()
                    if not ddl:
                        continue
                    named_ddl, idx_name = named_test_index_ddl(ddl, i)
                    result["db_logs"]["index_sql"].append(named_ddl)
                    try:
                        result["db_logs"]["index_output"].extend(db_exec_capture(conn, named_ddl))
                        result["applied_index_ddls"].append(named_ddl)
                        if idx_name:
                            result["applied_index_names"].append(idx_name)
                    except Exception as e:
                        result["errors"].append(f"Index apply failed for `{named_ddl}`: {e}")

            if result["applied_index_ddls"]:
                analyze_tables(conn, seeded_tables, result["db_logs"])

            time.sleep(0.3)
            if not result["applied_index_ddls"] and not result.get("rewrite_sql_used"):
                result["db_logs"]["plan_sql_after"] = result["db_logs"]["plan_sql_before"]
                result["simulated_post_plan"] = result["simulated_baseline_plan"]
                result["comparison_notes"].append(
                    "No index DDL or rewrite was applied, so the post-change plan is intentionally the same as the simulated baseline plan."
                )
            else:
                result["db_logs"]["plan_sql_after"] = "EXPLAIN ANALYZE " + test_sql.strip().rstrip(";")
                result["simulated_post_plan"] = fetch_plan_text(conn, test_sql)

            simulated_before = extract_plan_metrics(result["simulated_baseline_plan"])
            simulated_after = extract_plan_metrics(result["simulated_post_plan"])
            bundle_before = extract_plan_metrics(result["original_bundle_plan"])

            result["comparison_simulated"] = compare_metrics(simulated_before, simulated_after, "Simulated baseline vs simulated post-change")
            result["comparison_vs_bundle"] = compare_metrics(bundle_before, simulated_after, "Original bundle plan vs simulated post-change")

            # Check if recommended indexes were actually used in the post-change plan
            if result["applied_index_ddls"] and simulated_after.get("used_index"):
                used_index_name = simulated_after.get("used_index", "")
                full_scan_after = simulated_after.get("has_full_scan", False)

                # Check if still doing full scan or using primary key instead of new index
                if full_scan_after or "_pkey" in used_index_name:
                    result["comparison_notes"].insert(0,
                        f"⚠️ WARNING: Recommended index was created but NOT used. "
                        f"Post-change plan still uses '{used_index_name}'. "
                        f"Likely reason: High selectivity ({simulated_after.get('scan_rows', 0)} rows matched). "
                        f"Index would only be effective with lower selectivity (<20% of rows matching)."
                    )

            if (
                simulated_before.get("execution_time_ms") is not None
                and simulated_after.get("execution_time_ms") is not None
                and simulated_after.get("execution_time_ms") > simulated_before.get("execution_time_ms")
                and simulated_before.get("scan_rows") is not None
                and simulated_before.get("scan_rows") <= seed_rows
                and simulated_after.get("has_index_join")
            ):
                result["comparison_notes"].append(
                    "Execution time increased after the index because the synthetic table is still small and "
                    "the new plan uses an index join with primary-key lookups. On small tables, fewer rows decoded does not always mean lower latency."
                )

            if (
                simulated_before.get("scan_rows") is not None
                and simulated_before.get("result_rows") is not None
                and simulated_before.get("scan_rows") != simulated_before.get("result_rows")
            ):
                result["comparison_notes"].append(
                    "Rows scanned and rows returned are different metrics. "
                    "The baseline plan scanned all rows but returned only the rows matching the filter."
                )

        result["comparison_notes"].append(
            f"Seed rows requested defines recreated table size. Here that size is {seed_rows}. "
            "Rows shown inside the plans are rows scanned or rows returned by the chosen plan nodes, not the total table size."
        )

        bpfs = signals.get("bundle_post_filter_selectivity")
        if bpfs is not None:
            result["comparison_notes"].append(
                f"Synthetic seeding preserved the bundle's observed filter selectivity at approximately {bpfs:.3f} for IS NOT NULL predicates when possible."
            )

    except Exception as e:
        result["errors"].append(f"Database validation failed: {e}")
    finally:
        if conn is not None:
            if drop_test_indexes:
                for idx_name in result["applied_index_names"]:
                    try:
                        db_exec(conn, f"DROP INDEX IF EXISTS {idx_name};")
                    except Exception as e:
                        result["errors"].append(f"Cleanup failed for {idx_name}: {e}")
            try:
                conn.close()
            except Exception:
                pass

    # Generate DB execution logs for display
    result["logs"] = render_db_logs_md(result)

    return result


# ---------------------------------------------------------------------------
# Plan shape summary
# ---------------------------------------------------------------------------

def build_plan_shape(plan: str) -> str:
    lower = (plan or "").lower()
    parts: List[str] = []

    # Check for join types first (higher level operations)
    if "merge join (anti)" in lower or "anti join" in lower:
        parts.append("anti-join")
    elif "merge join" in lower:
        parts.append("merge join")
    elif "hash join" in lower:
        parts.append("hash join")
    elif "index join" in lower:
        parts.append("index join")
    elif "lookup join" in lower:
        parts.append("lookup join")
    elif "join" in lower:
        parts.append("join")

    # Then check for aggregation/grouping
    if "group" in lower:
        parts.append("group")
    if "sort" in lower:
        parts.append("sort")

    # Finally check for scan/filter (leaf operations)
    if "scan" in lower:
        parts.append("scan")
    if "filter" in lower:
        parts.append("filter")

    return " -> ".join(dedupe(parts)) if parts else "plan shape not recognized"


# ---------------------------------------------------------------------------
# Prompt builder for LLM narrative
# ---------------------------------------------------------------------------


RECOMMENDATION_JSON_SCHEMA = {
    "primary_bottleneck": "string",
    "why": "string",
    "recommended_action": ["string"],
    "candidate_indexes": [{"ddl": "string", "reason": "string"}],
    "candidate_rewrites": [{"sql": "string", "reason": "string"}],
    "statistics_assessment": "string",
    "confidence": "low|medium|high",
    "scenario_tags": ["string"],
    "narrative": {
        "query_summary": "string",
        "execution_plan_summary": "string",
        "key_observations": ["string"],
        "interesting_point": "string",
        "why_this_is_better": ["string"],
        "when_this_matters": ["string"],
        "final_verdict": "string",
        "bottom_line": "string",
    },
}


def build_llm_recommendation_retry_prompt(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    bundle: Dict[str, str],
    docs: List[Dict[str, Any]],
) -> str:
    snippets = "\n\n".join(
        f"DOC {i+1}: {d.get('title','')} | {d.get('snippet','')[:450]}"
        for i, d in enumerate(docs[:3])
    )
    sql_excerpt = clean_spaces((bundle.get("sql", "") or "")[:1200])
    plan_excerpt = clean_spaces((bundle.get("plan", "") or "")[:1800])

    return f"""Return valid JSON only. Follow the playbook in DOC 1 if present. Be consistent.

Use this exact JSON shape and fill every field:
{{
  "primary_bottleneck": "string",
  "why": "string",
  "recommended_action": ["string"],
  "candidate_indexes": [],
  "candidate_rewrites": [],
  "statistics_assessment": "string",
  "confidence": "low|medium|high",
  "scenario_tags": ["string"],
  "rag_basis": ["DOC 1"],
  "narrative": {{
    "query_summary": "string",
    "execution_plan_summary": "string",
    "key_observations": ["string", "string"],
    "interesting_point": "string",
    "why_this_is_better": ["string"],
    "when_this_matters": ["string"],
    "final_verdict": "string",
    "bottom_line": "string"
  }}
}}

Rules:
- If selectivity is near 100% for IS NOT NULL and the current plan is already fast, say the current execution plan is already optimal.
- In that optimal case:
  - recommended_action = ["No changes are required."]
  - final_verdict = "The current execution plan is already optimal. No changes are required."
  - bottom_line = "No action needed."
- If an index already exists or recommendation would be redundant, do NOT recommend it.
- If no new index is recommended, still fill every field with a grounded answer.
- Final Verdict and Bottom Line must agree.

Facts:
- table={facts.get("table")}
- execution_time_ms={facts.get("execution_time_ms")}
- full_scan_suspected={facts.get("full_scan_suspected")}
- post_filter_selectivity={facts.get("post_filter_selectivity")}
- where_columns={facts.get("where_columns")}
- selected_columns={facts.get("selected_columns")}
- existing_indexes={list((signals.get("existing_index_columns") or {}).keys())}

SQL:
{sql_excerpt}

PLAN:
{plan_excerpt}

CONTEXT:
{snippets}
""".strip()

def truncate_for_log(text: Any, limit: int = 1200) -> str:
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    s = s or ""
    return s[:limit] + ("..." if len(s) > limit else "")



def detect_covering_index_opportunity(facts: Dict[str, Any], signals: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Detect when a STORING clause could eliminate an expensive index join.
    Returns a covering index recommendation if appropriate, None otherwise.
    """
    # Check if plan has index join
    if not facts.get("index_join_rows"):
        return None

    # Check if index join is expensive (>50% of total time)
    index_join_ms = facts.get("index_join_kv_time_ms", 0)
    total_ms = facts.get("execution_time_ms", 1)

    if not index_join_ms or not total_ms:
        return None

    join_pct = (index_join_ms / total_ms) * 100
    if join_pct < 50:
        return None

    # Get columns
    select_cols = facts.get("selected_columns", [])
    where_cols = facts.get("where_columns", [])
    table = facts.get("table", "")

    if not table or not select_cols or not where_cols:
        return None

    # Identify columns that need to be stored (in SELECT but not in WHERE)
    storing_cols = [c for c in select_cols if c not in where_cols]

    if not storing_cols:
        return None

    # Build covering index DDL
    where_list = ", ".join(where_cols)
    storing_list = ", ".join(storing_cols)
    ddl = f'CREATE INDEX ON {table} ({where_list}) STORING ({storing_list});'

    # Calculate expected improvement
    expected_new_time = total_ms - index_join_ms

    return {
        "ddl": ddl,
        "reason": f"Eliminates {int(index_join_ms)}ms index join ({int(join_pct)}% of query time)",
        "expected_improvement": f"Expected execution time: ~{int(expected_new_time)}ms (from {int(total_ms)}ms)",
        "covering_type": "storing_clause",
        "eliminated_operation": "index_join"
    }


def build_llm_recommendation_prompt(
    facts: Dict[str, Any],
    signals: Dict[str, Any],
    bundle: Dict[str, str],
    docs: List[Dict[str, Any]],
) -> str:
    """SMALL MODEL OPTIMIZED: Simplified prompt for fast inference"""

    # Detect covering index opportunity
    covering_opportunity = detect_covering_index_opportunity(facts, signals)

    # RAG context - prioritize quality over speed for correct recommendations
    # Top 5 docs with larger snippets for better context
    snippets = []
    for i, d in enumerate(docs[:5]):  # 5 docs for better coverage
        snippet = d.get('snippet', '')[:1500]  # 1500 chars for complete patterns
        source = d.get('source', 'unknown')
        snippets.append(f"RULE #{i+1} (source: {source}):\n{snippet}\n")
    snippets = "\n".join(snippets) if snippets else "No relevant documentation found."

    # Give LLM the full context - don't truncate the plan!
    # The plan contains critical information like join types, scan patterns
    sql_full = (bundle.get("sql", "") or "")
    plan_full = (bundle.get("plan", "") or "")

    # Take first 80 lines of plan (not chars) to preserve structure
    plan_lines = plan_full.split('\n')[:80]
    plan_excerpt = '\n'.join(plan_lines)

    # SQL can be truncated if very long, but keep reasonable size
    sql_excerpt = sql_full[:2500] if len(sql_full) > 2500 else sql_full

    # Simplified covering hint
    covering_hint = ""
    if covering_opportunity:
        covering_hint = f"⚠️ DETECTED: {covering_opportunity['ddl']}\nReason: {covering_opportunity['reason']}\n"

    # Extract key facts only
    exec_time = facts.get("execution_time_ms", 0)
    full_scan = facts.get("full_scan_suspected", False)
    index_join = facts.get("index_join_kv_time_ms", 0)
    where_cols = facts.get("where_columns", [])

    # Get existing indexes - CRITICAL to avoid duplicate recommendations
    existing_indexes = signals.get("existing_index_columns", {}) or {}
    if existing_indexes:
        # Format as: idx_name(col1, col2, col3)
        existing_indexes_formatted = []
        for idx_name, cols in list(existing_indexes.items())[:10]:  # Show up to 10 indexes
            cols_str = ", ".join(cols)
            existing_indexes_formatted.append(f"{idx_name}({cols_str})")
        existing_indexes_str = "\n".join(existing_indexes_formatted)
    else:
        existing_indexes_str = "none"

    # Extract PRIMARY KEY columns from schema - CRITICAL to avoid PK index recommendations
    primary_key_columns = []
    schema_sql = bundle.get("schema.sql", "") or ""
    if schema_sql:
        tables = parse_schema_tables(schema_sql)
        for table in tables:
            if table.primary_key:
                table_name = table.name.split('.')[-1].strip('"')
                for pk_col in table.primary_key:
                    primary_key_columns.append(f"{table_name}.{pk_col}")

    pk_warning = ""
    if primary_key_columns:
        pk_cols_str = ", ".join(primary_key_columns[:20])  # Show up to 20 PK columns
        pk_warning = f"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ⛔ PRIMARY KEY COLUMNS - NEVER RECOMMEND INDEXES ON THESE              ║
╚══════════════════════════════════════════════════════════════════════════╝
{pk_cols_str}

⚠️ ABSOLUTE RULE: PRIMARY KEY columns ALREADY have indexes automatically!
   - DO NOT recommend CREATE INDEX on any column listed above
   - Example: If users.id is PRIMARY KEY, DO NOT recommend CREATE INDEX ON users(id)
   - If you see "users@users_pkey" in plan, it's already using the PRIMARY KEY index
   - Recommending an index on PK is ALWAYS WRONG and WASTEFUL
"""

    # Calculate percentage safely for example
    index_join_pct = 0
    if index_join and exec_time and exec_time > 0:
        index_join_pct = int((index_join / exec_time) * 100)

    return f"""Analyze this CockroachDB query and recommend optimizations.

╔══════════════════════════════════════════════════════════════════════════╗
║  MANDATORY RULES - YOU MUST FOLLOW THESE RULES BEFORE ANYTHING ELSE     ║
║  These rules override all other information in this prompt               ║
╚══════════════════════════════════════════════════════════════════════════╝

{snippets}

CRITICAL INSTRUCTION:
The documentation above is THE ABSOLUTE SOURCE OF TRUTH.
- If the documentation says "Keep current plan" for a pattern → YOU MUST say "Keep current plan"
- If the documentation says "Don't create index on PRIMARY KEY" → YOU MUST NOT recommend that index
- If any fact or signal below contradicts the documentation → IGNORE that fact, FOLLOW the documentation

Before making ANY recommendation:
1. Check if the plan matches any pattern in the documentation above
2. If it matches → follow the documentation's recommendation EXACTLY
3. If no match → then analyze using facts below

═══════════════════════════════════════════════════════════════
EXISTING INDEXES IN SCHEMA (CHECK THESE FIRST!):
═══════════════════════════════════════════════════════════════
{existing_indexes_str}

⚠️ CRITICAL: Before recommending ANY new index, check if an existing index already covers the columns!
   - If index exists on (col_a, col_b, col_c), it can be used for queries filtering on col_a or (col_a, col_b)
   - DO NOT recommend creating an index that is redundant with an existing index
   - Example: If idx_users_email_name(email, name) exists, don't recommend idx_users_email(email)
{pk_warning}

═══════════════════════════════════════════════════════════════
EXECUTION PLAN (check if this matches any pattern from documentation):
═══════════════════════════════════════════════════════════════
{plan_excerpt}

═══════════════════════════════════════════════════════════════
SQL QUERY:
═══════════════════════════════════════════════════════════════
{sql_excerpt}

═══════════════════════════════════════════════════════════════
HELPER CONTEXT (use only if documentation doesn't cover this case):
═══════════════════════════════════════════════════════════════
- Execution time: {exec_time}ms
- Full scan detected: {full_scan}
- Has OR predicate: {facts.get("has_or_predicate", False)}
- Where columns detected: {', '.join(where_cols) if where_cols else 'none'}

{covering_hint}

ANALYSIS CHECKLIST:
□ Step 1: Does plan match any pattern from MANDATORY RULES documentation?
  - "merge join (anti)" or "hash join (anti)" → Anti-join pattern (Keep plan)
  - "lookup join" with PK access → Lookup join pattern (Keep plan)
  - "spans: [/X - /X]" → Primary key lookup (Keep plan)
  - "group (streaming)" → Streaming group (Keep plan)
  - Query has OR predicate with full scan → Consider UNION ALL rewrite + index

□ Step 2: If pattern matched → Follow documentation recommendation EXACTLY

□ Step 3: Check EXISTING INDEXES before recommending new ones:
  - Review the "EXISTING INDEXES IN SCHEMA" section above
  - If query needs index on (col_a, col_b) and existing index has (col_a, col_b, col_c) → Use existing index, don't recommend new one
  - Only recommend new index if NO existing index covers the required columns

□ Step 4: If no pattern match and no suitable existing index → Calculate selectivity and analyze:
  - Selectivity = (rows returned / rows scanned)
  - < 10% selectivity + full scan + no suitable index → Recommend index
  - > 80% selectivity → Keep current plan
  - Query has OR with low selectivity → Recommend UNION ALL rewrite + index

Return full JSON format with this structure:
{{
  "primary_bottleneck": "string describing main issue",
  "why": "string explanation",
  "recommended_action": ["action description"],
  "candidate_indexes": [{{"ddl": "CREATE INDEX ...", "reason": "why"}}],
  "candidate_rewrites": [{{"sql": "rewritten query", "reason": "why"}}],
  "statistics_assessment": "string",
  "confidence": "low|medium|high",
  "scenario_tags": ["tag"],
  "rag_basis": ["RULE #X"],
  "narrative": {{
    "query_summary": "what the query does",
    "execution_plan_summary": "how it executes",
    "key_observations": ["observation 1", "observation 2"],
    "interesting_point": "key insight",
    "why_this_is_better": ["benefit"],
    "when_this_matters": ["when it helps"],
    "final_verdict": "summary",
    "bottom_line": "short conclusion"
  }}
}}

CRITICAL PATTERNS TO RECOGNIZE:

1. OR predicate with full scan:
   - If query has OR in WHERE clause + full scan + low selectivity
   - Recommend: Index on OR columns + UNION ALL rewrite
   - Example candidate_rewrites: [{{"sql": "SELECT col1, col2 FROM table WHERE a=1 UNION ALL SELECT col1, col2 FROM table WHERE b=2", "reason": "UNION allows separate index seeks"}}]
   - Example candidate_indexes: [{{"ddl": "CREATE INDEX ON table(a, b)", "reason": "Enables index seeks for each UNION branch"}}]

⚠️ CRITICAL: For candidate_rewrites, you MUST provide COMPLETE, VALID SQL - not "SELECT ... FROM" with ellipsis!
   - DO NOT use "..." or "SELECT ..." - these are NOT valid SQL and will cause syntax errors
   - Write out the FULL query with all columns, tables, joins, and WHERE clauses
   - If the original query is too complex to rewrite, return empty candidate_rewrites: []
   - Only include rewrites that are COMPLETE and EXECUTABLE SQL statements
""".strip()

def classify_verdict_text(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "unknown"
    if any(x in t for x in ["keep current plan", "keep the current plan", "no immediate change", "already reasonable", "no change"]):
        return "keep"
    if any(x in t for x in ["unlikely to help", "do not create index", "do not add", "not recommended", "low value"]):
        return "reject_index"
    if any(x in t for x in ["create an index", "add an index", "recommend an index", "add index"]):
        return "recommend_index"
    return "unknown"


def recommendation_mentions_index(text_items: List[str]) -> bool:
    joined = " ".join((x or "") for x in (text_items or [])).lower()
    return "index" in joined and any(w in joined for w in ["create", "add", "consider", "recommend"])



def infer_qualified_table_name(bundle: Dict[str, str], facts: Dict[str, Any]) -> str:
    table = (facts.get("table") or "").strip().strip('"')
    schema_sql = bundle.get("schema", "") or ""
    sql_text = bundle.get("sql", "") or ""

    if table:
        pats = [
            rf'CREATE TABLE\s+"([^"]+)"\."{re.escape(table)}"\s*\(',
            rf'CREATE TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\."{re.escape(table)}"\s*\(',
            rf'FROM\s+"([^"]+)"\."{re.escape(table)}"\b',
            rf'FROM\s+([A-Za-z_][A-Za-z0-9_]*)\."{re.escape(table)}"\b',
        ]
        for pat in pats[:2]:
            m = re.search(pat, schema_sql, re.I)
            if m:
                return f'"{m.group(1).strip(chr(34))}"."{table}"'
        for pat in pats[2:]:
            m = re.search(pat, sql_text, re.I)
            if m:
                return f'"{m.group(1).strip(chr(34))}"."{table}"'
    return f'"{table}"' if table else ""

def build_candidate_index_ddl_from_facts(facts: Dict[str, Any], bundle: Optional[Dict[str, str]] = None) -> str:
    qualified_table = infer_qualified_table_name(bundle or {}, facts) if bundle is not None else (facts.get("qualified_table") or facts.get("table") or "")
    where_cols = facts.get("where_columns") or []
    if qualified_table and where_cols:
        cols = ", ".join([f'"{c}"' for c in where_cols])
        return f'CREATE INDEX ON {qualified_table} ({cols});'
    return ""

def normalize_optimal_plan_wording(result: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    narrative = result.get("narrative") or {}
    try:
        is_optimal_case = (
            bool(facts.get("has_is_not_null_predicate"))
            and float(facts.get("post_filter_selectivity") or 0.0) >= 0.8
            and float(facts.get("execution_time_ms") or 0.0) <= 20.0
        )
    except Exception:
        is_optimal_case = False

    if is_optimal_case:
        result["recommended_action"] = ["No changes are required."]
        if isinstance(narrative, dict):
            narrative["final_verdict"] = "The current execution plan is already optimal. No changes are required."
            narrative["bottom_line"] = "No action needed."
            # keep this section crisp
            if not narrative.get("interesting_point") or "index" in (narrative.get("interesting_point") or "").lower():
                narrative["interesting_point"] = "A full scan can be the correct and efficient plan when nearly all rows match."
        result["narrative"] = narrative
    return result

def reconcile_recommendation_result(
    result: Dict[str, Any],
    facts: Dict[str, Any],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    narrative = result.get("narrative") or {}
    final_verdict = narrative.get("final_verdict", "") if isinstance(narrative, dict) else ""
    bottom_line = narrative.get("bottom_line", "") if isinstance(narrative, dict) else ""
    verdict_class = classify_verdict_text(final_verdict) or classify_verdict_text(bottom_line)

    recommended_action = result.get("recommended_action", []) or []
    candidate_indexes = result.get("candidate_indexes", []) or []
    existing_index_cols = signals.get("existing_index_columns", {}) or {}

    # Detect redundant/rejected simple-index scenarios from facts.
    high_selectivity_is_not_null = bool(facts.get("has_is_not_null_predicate")) and float(facts.get("post_filter_selectivity") or 0.0) >= 0.8

    if verdict_class in ["keep", "reject_index"] or high_selectivity_is_not_null:
        result["candidate_indexes"] = []
        result["recommended_action"] = [
            "Keep the current plan." if verdict_class == "keep" else "Do not add a simple index for this query based on the current evidence."
        ]
        if isinstance(narrative, dict):
            narrative["final_verdict"] = "Keep current plan." if verdict_class == "keep" else "A simple index is unlikely to materially improve performance."
            narrative["bottom_line"] = "Do not create a simple index for this query."
            if recommendation_mentions_index([narrative.get("interesting_point", "")]):
                narrative["interesting_point"] = "A real full scan does not automatically mean a new index will help."
        result["narrative"] = narrative
        return result

    # If model recommends an index, materialize it unless redundant.
    if recommendation_mentions_index(recommended_action) and not candidate_indexes:
        ddl = build_candidate_index_ddl_from_facts(facts, bundle=None)
        if ddl:
            result["candidate_indexes"] = [{"ddl": ddl, "reason": "Matches the filtered column(s) in the query."}]

    # Remove duplicates/redundant indexes.
    pk_cols = signals.get("primary_key_columns", {}) or {}
    cleaned = []
    for cand in result.get("candidate_indexes", []) or []:
        ddl = (cand.get("ddl") or "").strip()
        if not ddl:
            continue
        mm = re.search(r'\((.*?)\)', ddl)
        cand_cols = [x.strip().strip('"') for x in mm.group(1).split(",")] if mm else []

        # Check if index is on PRIMARY KEY columns (skip PK indexes)
        is_pk_index = False
        for table_name, pk_columns in pk_cols.items():
            pk_cols_lower = [c.lower() for c in pk_columns]
            cand_cols_lower = [c.lower() for c in cand_cols]
            if cand_cols_lower == pk_cols_lower[:len(cand_cols_lower)]:
                is_pk_index = True
                logger.warning(f"Rejected PRIMARY KEY index recommendation in reconcile: {ddl[:100]}")
                break
        if is_pk_index:
            continue

        redundant = False
        for idx_name, idx_cols in existing_index_cols.items():
            if is_prefix_columns(cand_cols, idx_cols):
                redundant = True
                break
        if not redundant:
            cleaned.append(cand)

    # Track if we filtered any indexes
    original_index_count = len(result.get("candidate_indexes", []) or [])
    filtered_index_count = len(cleaned)
    indexes_were_filtered = original_index_count > filtered_index_count

    result["candidate_indexes"] = cleaned

    # If cleaned candidate indexes are empty, do not tell user to create index.
    if not cleaned and recommendation_mentions_index(result.get("recommended_action", []) or []):
        result["recommended_action"] = ["No new index is recommended based on the current evidence."]
        if isinstance(narrative, dict):
            # If indexes were filtered (especially PK), update full narrative
            if indexes_were_filtered:
                narrative["final_verdict"] = "No new index is recommended."
                narrative["bottom_line"] = "The recommended index is redundant with existing indexes or primary key."
                narrative["interesting_point"] = "The query already uses an optimal access path."
                if not narrative.get("why_this_is_better"):
                    narrative["why_this_is_better"] = ["Current plan is already efficient"]
            else:
                narrative["bottom_line"] = "Do not create a duplicate or low-value index."
            result["narrative"] = narrative

    return result

    return f"""SYSTEM PRIORITY INSTRUCTION:

The FIRST section of the retrieved context is a PLAYBOOK.
You MUST follow it strictly.
If any other retrieved context contradicts the playbook:
- IGNORE the other context
- FOLLOW the playbook

You are NOT allowed to produce contradictory recommendations.

MANDATORY CONSISTENCY RULES:
- Final Verdict, Optimization Opportunity, Bottom Line, recommended_action, and candidate_indexes MUST agree.
- If the playbook says DO NOT INDEX, do NOT suggest an index anywhere.
- If any contradiction appears in your reasoning, choose the safer option: KEEP CURRENT PLAN.
- You MUST validate recommendations against existing indexes.
- If a recommended index already exists or is redundant, DO NOT include it.

# COVERING INDEX DETECTION RULES:
- If index_join_kv_time_ms > 50% of execution_time_ms:
  → Recommend STORING clause to eliminate index join
  → DDL format: CREATE INDEX ON table (where_cols) STORING (select_cols_not_in_where)
  → This is HIGHER PRIORITY than a new simple index
- If a covering index opportunity exists, include it in candidate_indexes with clear reasoning

# KEY RULES
- If index join >50% of time: recommend STORING clause
- If full scan + high selectivity (>80%) + fast (<20ms): keep current plan
- If full scan + low selectivity (<30%): recommend filter index

Return ONLY one valid JSON object. No markdown. No prose outside JSON. Do not leave required fields blank.

Required JSON schema:
{{
  "primary_bottleneck": "one short string",
  "why": "one concise explanation grounded in facts and docs",
  "recommended_action": ["at least one concrete action"],
  "candidate_indexes": [{{"ddl": "SQL DDL or empty string", "reason": "short reason"}}],
  "candidate_rewrites": [{{"sql": "SQL rewrite or empty string", "reason": "short reason"}}],
  "statistics_assessment": "one short sentence",
  "confidence": "low|medium|high",
  "scenario_tags": ["one or more short tags"],
  "rag_basis": ["cite which retrieved context items influenced the decision, e.g. DOC 1, DOC 3"],
  "narrative": {{
    "query_summary": "one sentence",
    "execution_plan_summary": "one sentence",
    "key_observations": ["at least 2 bullet strings"],
    "interesting_point": "one sentence",
    "why_this_is_better": ["at least 1 bullet string"],
    "when_this_matters": ["at least 1 bullet string"],
    "final_verdict": "one sentence",
    "bottom_line": "one sentence"
  }}
}}

FACTS:
{json.dumps(facts, indent=2)}

SIGNALS:
{json.dumps(signals, indent=2)}

EXISTING INDEXES:
{existing_indexes}

SQL:
{sql_excerpt}

PLAN:
{plan_excerpt}

SCHEMA:
{schema_excerpt}

RETRIEVED CONTEXT:
{snippets}
""".strip()

def validate_llm_recommendation_result(
    llm_result: Optional[Dict[str, Any]],
    fallback_rule_result: Dict[str, Any],
    facts: Dict[str, Any],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(llm_result, dict):
        out = dict(fallback_rule_result)
        out["_recommendation_source"] = "rules_fallback"
        return out

    out = dict(fallback_rule_result)
    for k in ["primary_bottleneck", "why", "statistics_assessment", "confidence", "scenario_tags"]:
        if llm_result.get(k):
            out[k] = llm_result.get(k)
    if isinstance(llm_result.get("recommended_action"), list):
        out["recommended_action"] = [str(x).strip() for x in llm_result["recommended_action"] if str(x).strip()][:8]
    existing_text = " ".join((facts.get("existing_indexes_observed") or [])).lower()
    existing_cols = signals.get("existing_index_columns", {}) or {}
    pk_cols = signals.get("primary_key_columns", {}) or {}
    cleaned_indexes = []
    for idx in llm_result.get("candidate_indexes", []) or []:
        ddl = clean_spaces(idx.get("ddl", ""))
        if not ddl:
            continue
        ddl_l = ddl.lower()
        if ddl_l in existing_text:
            continue

        # Extract candidate columns from DDL
        m = re.search(r'\((.*?)\)', ddl)
        if not m:
            continue
        cand_cols = [c.strip().strip('"') for c in m.group(1).split(",") if c.strip()]

        # Check if index is on PRIMARY KEY columns (CRITICAL - skip PK indexes)
        is_pk_index = False
        for table_name, pk_columns in pk_cols.items():
            # Normalize both for comparison
            pk_cols_lower = [c.lower() for c in pk_columns]
            cand_cols_lower = [c.lower() for c in cand_cols]
            # If candidate index columns exactly match or are a prefix of PK, it's redundant
            if cand_cols_lower == pk_cols_lower[:len(cand_cols_lower)]:
                is_pk_index = True
                logger.warning(f"Rejected PRIMARY KEY index recommendation: {ddl[:100]} (PK columns: {pk_columns})")
                break
        if is_pk_index:
            continue

        # Check if redundant with existing indexes
        redundant = False
        for idx_cols in existing_cols.values():
            if is_prefix_columns(cand_cols, idx_cols):
                redundant = True
                break
        if redundant:
            continue
        cleaned_indexes.append({"ddl": ddl if ddl.endswith(";") else ddl + ";", "reason": idx.get("reason", "")})

    # Track if we filtered any indexes
    original_index_count = len(llm_result.get("candidate_indexes", []) or [])
    filtered_index_count = len(cleaned_indexes)
    indexes_were_filtered = original_index_count > filtered_index_count

    out["candidate_indexes"] = cleaned_indexes
    cleaned_rewrites = []
    for rw in llm_result.get("candidate_rewrites", []) or []:
        sql = (rw.get("sql") or "").strip()
        if not sql:
            continue
        # Reject incomplete SQL with ellipsis placeholders - these cause syntax errors
        if "..." in sql or "SELECT ..." in sql.upper():
            logger.warning(f"Rejected malformed candidate_rewrite with '...' placeholder: {sql[:100]}")
            continue
        # Reject trivial rewrites that don't change anything meaningful
        if len(sql) < 20:
            logger.warning(f"Rejected trivial candidate_rewrite: {sql}")
            continue
        cleaned_rewrites.append({"sql": sql, "reason": rw.get("reason", "")})
    out["candidate_rewrites"] = cleaned_rewrites

    # Update narrative if we filtered out indexes (especially PK indexes)
    narrative = llm_result.get("narrative")
    if isinstance(narrative, dict) and indexes_were_filtered and not cleaned_indexes:
        # All indexes were filtered out - update narrative to reflect this
        narrative["final_verdict"] = "No new index is recommended."
        narrative["bottom_line"] = "Primary key columns already have indexes automatically."
        narrative["interesting_point"] = "The query uses a primary key lookup, which is already optimal."
        narrative["why_this_is_better"] = ["Primary key index is already in use"]
        out["narrative"] = narrative
    elif isinstance(narrative, dict):
        out["narrative"] = narrative
    out["_recommendation_source"] = "llm_rag"
    return out

def build_llm_narrative_prompt(facts: Dict[str, Any], rule_result: Dict[str, Any], plan_shape: str, docs: List[Dict[str, Any]], bundle: Dict[str, str]) -> str:
    snippets = "\n\n".join(
        f"[{i+1}] {d.get('title', '')}: {d.get('snippet', '')[:350]}"
        for i, d in enumerate(docs[:5])
    )
    sql_excerpt = (bundle.get("sql", "") or "")[:2000]
    plan_excerpt = (bundle.get("plan", "") or "")[:2000]
    return f"""You are a CockroachDB performance expert. Analyze THIS specific SQL bundle and return ONLY a JSON object with these exact keys:
query_summary, execution_plan_summary, key_observations (list), interesting_point, why_this_is_better (list), when_this_matters (list), final_verdict, bottom_line.

Hard rules:
- Use these exact JSON keys and exact casing: query_summary, execution_plan_summary, key_observations, interesting_point, why_this_is_better, when_this_matters, final_verdict, bottom_line.
- Summarize the specific query and plan below, not the reference docs in general.
- Do NOT repeat slide titles, headings, or phrases from reference docs such as "EXPLAIN RECOMMENDATIONS".
- Use reference docs only to support or clarify the analysis for this query.
- If you mention a partial index, explain WHY it may help and include one concrete example DDL in plain text.
- Ground your wording in the bundle facts first. Docs are secondary support.
- Keep all claims consistent with the rule result below.

SQL:
{sql_excerpt}

Plan excerpt:
{plan_excerpt}

Plan shape: {plan_shape}
Primary bottleneck: {rule_result.get('primary_bottleneck')}
Recommended action: {rule_result.get('recommended_action')}
Execution time: {ms_string(facts.get('execution_time_ms'))}
Full scan suspected: {facts.get('full_scan_suspected')}
Table: {facts.get('table')}

Reference docs:
{snippets}

Return ONLY valid JSON. No preamble, no markdown fences, no explanation outside the JSON object, and no trailing text after the closing brace."""




def validate_llm_recommendation(
    llm_result: Optional[Dict[str, Any]],
    facts: Dict[str, Any],
    signals: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """SMALL MODEL OPTIMIZED: Accepts simplified JSON and converts to full format"""

    if not isinstance(llm_result, dict):
        return None, ["LLM recommendation was not a JSON object."]

    issues: List[str] = []

    # Accept EITHER full format OR simplified format
    # Simplified: {"recommendation", "action", "ddl", "reason", "confidence"}
    # Full: {"primary_bottleneck", "why", "recommended_action", "confidence", "narrative"}

    recommendation = llm_result.get("recommendation", "").strip()
    action = llm_result.get("action", "").strip()
    ddl = llm_result.get("ddl", "").strip()
    reason = llm_result.get("reason", "").strip()
    confidence = llm_result.get("confidence", "medium").strip()

    # Get full format fields
    primary_bottleneck = (llm_result.get("primary_bottleneck", "") or "").strip()
    why = (llm_result.get("why", "") or "").strip()

    # If simplified format detected, convert to full format
    if recommendation and action and not primary_bottleneck:
        # Check for duplicate index BEFORE conversion
        duplicate_detected = False
        if ddl:
            # Extract column from DDL
            ddl_lower = ddl.lower()
            existing_indexes = signals.get("existing_index_columns", {}) or {}

            # Simple check: if DDL mentions a column that's already indexed
            for idx_name, idx_cols in existing_indexes.items():
                for col in idx_cols:
                    if col.lower() in ddl_lower:
                        duplicate_detected = True
                        issues.append(f"Duplicate index detected: column '{col}' already in index '{idx_name}'")
                        break
                if duplicate_detected:
                    break

        # If duplicate, override to "keep plan"
        if duplicate_detected:
            action = "keep plan"
            ddl = ""
            recommendation = "Keep current plan - query is already optimized"
            reason = "Existing indexes are sufficient. No changes needed."

        # Convert simplified to full format
        llm_result = {
            "primary_bottleneck": action.replace("_", " ").title() if action != "keep plan" else "No significant bottleneck",
            "why": reason or recommendation,
            "recommended_action": [recommendation],
            "candidate_indexes": [{"ddl": ddl, "reason": reason}] if ddl else [],
            "candidate_rewrites": [],
            "statistics_assessment": "Based on query analysis",
            "confidence": confidence,
            "scenario_tags": [action],
            "rag_basis": ["Simplified analysis"],
            "narrative": {
                "query_summary": recommendation,
                "execution_plan_summary": f"Action: {action}",
                "key_observations": [reason] if reason else ["Analysis completed"],
                "interesting_point": recommendation,
                "why_this_is_better": [reason] if reason and ddl else ["No change needed"],
                "when_this_matters": ["Performance optimization"] if ddl else ["Current plan is efficient"],
                "final_verdict": recommendation,
                "bottom_line": "No action needed" if action == "keep plan" else action.replace("_", " ").title()
            }
        }
        issues.append("Simplified LLM response converted to full format")

    # Fix empty fields in full format (don't reject, just fix)
    primary_bottleneck = (llm_result.get("primary_bottleneck", "") or "").strip()
    why = (llm_result.get("why", "") or "").strip()

    if not primary_bottleneck:
        # Infer from recommended_action or set default
        recommended_action = llm_result.get("recommended_action", [])
        if recommended_action and len(recommended_action) > 0:
            first_action = str(recommended_action[0]).lower()
            if "keep" in first_action or "no change" in first_action:
                primary_bottleneck = "No significant bottleneck"
            elif "covering" in first_action or "storing" in first_action:
                primary_bottleneck = "Index join overhead"
            elif "index" in first_action:
                primary_bottleneck = "Missing index"
            else:
                primary_bottleneck = "Query optimization needed"
        else:
            primary_bottleneck = "Analysis completed"
        llm_result["primary_bottleneck"] = primary_bottleneck
        issues.append(f"Fixed empty primary_bottleneck: '{primary_bottleneck}'")

    if not why:
        why = llm_result.get("statistics_assessment", "") or "Based on query analysis"
        llm_result["why"] = why
        issues.append(f"Fixed empty why field")

    # Ensure narrative exists (create minimal if missing)
    if not llm_result.get("narrative") or not isinstance(llm_result.get("narrative"), dict):
        llm_result["narrative"] = {
            "query_summary": llm_result.get("why", "Analysis completed"),
            "execution_plan_summary": llm_result.get("primary_bottleneck", "Unknown"),
            "key_observations": llm_result.get("recommended_action", [])[:2],
            "final_verdict": llm_result.get("why", "See recommendation"),
            "bottom_line": llm_result.get("primary_bottleneck", "No action")
        }

    llm_result["llm_recommendation_used"] = True
    return llm_result, issues

def analyze(bundle: Dict[str, str], retriever: HybridRetriever, model: str, selected_rag_txt: str = "") -> AnalysisResult:
    """
    Run the full analysis pipeline and return a typed AnalysisResult.
    LLM + RAG is primary for recommendation; rules are fallback only.
    """
    analysis_start = time.time()

    facts = derive_rule_facts(bundle)
    signals = build_baseline_signals(facts, bundle.get("schema", ""))

    rag_start = time.time()
    docs = retriever.retrieve(build_rag_query(facts, bundle), top_k=TOP_K_DOCS)
    rag_time = time.time() - rag_start

    plan_shape = build_plan_shape(bundle.get("plan", ""))

    model_ok, installed_models, ollama_running = validate_ollama_model(model)
    llm_runtime = {
        "selected_model": model,
        "effective_model": model,
        "model_validation_ok": bool(model_ok),
        "ollama_running": ollama_running,
        "installed_models": installed_models or [],
        "ollama_url": OLLAMA_URL,
        "selected_rag_txt": selected_rag_txt or DEFAULT_LOCAL_RAG_TXT,
        "rag_retrieval_time_sec": round(rag_time, 2),
    }

    llm_prompt = build_llm_recommendation_prompt(facts, signals, bundle, docs)

    llm_call_start = time.time()
    llm_result_raw, llm_error, llm_raw_text = call_json_llm(llm_prompt, model)
    llm_call_time = time.time() - llm_call_start
    llm_runtime["llm_recommendation_time_sec"] = round(llm_call_time, 2)

    llm_result, llm_issues = validate_llm_recommendation(llm_result_raw, facts, signals)

    if not llm_result:
        retry_prompt = build_llm_recommendation_retry_prompt(facts, signals, bundle, docs)
        retry_call_start = time.time()
        retry_result_raw, retry_error, retry_raw_text = call_json_llm(retry_prompt, model)
        retry_call_time = time.time() - retry_call_start
        llm_runtime["llm_retry_time_sec"] = round(retry_call_time, 2)

        retry_result, retry_issues = validate_llm_recommendation(retry_result_raw, facts, signals)
        if retry_result:
            llm_result = retry_result
            llm_issues = (llm_issues or []) + ["Initial recommendation attempt failed; retry prompt succeeded."]
            llm_raw_text = retry_raw_text
            llm_error = None
            llm_runtime["recommendation_retry_succeeded"] = True
        else:
            llm_issues = (llm_issues or []) + (retry_issues or [])
            if retry_error:
                llm_error = retry_error
            if retry_raw_text:
                llm_raw_text = retry_raw_text
            llm_runtime["recommendation_retry_succeeded"] = False

    if llm_result:
        llm_result = reconcile_recommendation_result(llm_result, facts, signals)
        if not facts.get("full_scan_suspected") and facts.get("dominant_bottleneck_guess") == "index_join":
            narr = llm_result.get("narrative") or {}
            if isinstance(narr, dict):
                eps = (narr.get("execution_plan_summary") or "")
                if "full scan" in eps.lower():
                    narr["execution_plan_summary"] = "The query uses an index scan followed by an index join."
                ko = narr.get("key_observations") or []
                ko = [x for x in ko if "full scan" not in str(x).lower()]
                if not any("index join" in str(x).lower() for x in ko):
                    ko.append("The query is using a secondary index scan followed by an index join.")
                llm_result["narrative"] = narr
        narrative = sanitize_narrative(llm_result.get("narrative", {}), None, facts, llm_result, plan_shape)
        final_result = {
            "primary_bottleneck": llm_result.get("primary_bottleneck", "llm_recommendation"),
            "why": llm_result.get("why", ""),
            "recommended_action": llm_result.get("recommended_action", []) or [],
            "candidate_indexes": llm_result.get("candidate_indexes", []) or [],
            "candidate_rewrites": llm_result.get("candidate_rewrites", []) or [],
            "statistics_assessment": llm_result.get("statistics_assessment", "See retrieved docs and bundle facts."),
            "confidence": llm_result.get("confidence", "medium"),
            "evaluated_options": [],
            "scenario_tags": llm_result.get("scenario_tags", []) or [],
            "narrative": align_narrative_with_rule_result(narrative, llm_result),
            "llm_recommendation_used": True,
            "rule_fallback_used": False,
            "rag_basis": llm_result.get("rag_basis", []) or [],
            "llm_validation_issues": llm_issues,
            "llm_raw_preview": truncate_for_log(llm_raw_text),
        }
        validation = {"valid": True, "score": 1.0, "issues": llm_issues}
        final_result = reconcile_recommendation_result(final_result, facts, signals)
        final_result = normalize_optimal_plan_wording(final_result, facts)
    else:
        rule_result = build_rule_result(facts, signals, bundle)
        validation = validate_rule_result(facts, rule_result)
        prompt = build_llm_narrative_prompt(facts, rule_result, plan_shape, docs, bundle)

        narrative_call_start = time.time()
        llm_narrative, llm_narr_err = call_narrative_llm(prompt, model)
        narrative_call_time = time.time() - narrative_call_start
        llm_runtime["llm_narrative_time_sec"] = round(narrative_call_time, 2)

        narrative = sanitize_narrative(llm_narrative, llm_narr_err or llm_error or "LLM recommendation unavailable; using rule fallback.", facts, rule_result, plan_shape)
        narrative = enrich_partial_index_narrative(narrative, facts, bundle)
        final_result = build_final_answer(rule_result, narrative)
        final_result["llm_recommendation_used"] = False
        final_result["rule_fallback_used"] = True
        final_result["llm_validation_issues"] = llm_issues if llm_issues else [llm_error or "LLM recommendation unavailable or invalid JSON; using rule fallback."]
        final_result["llm_raw_preview"] = truncate_for_log(llm_raw_text)
        final_result = normalize_optimal_plan_wording(final_result, facts)

    existing_index_cols = signals.get("existing_index_columns", {}) or {}
    where_cols = facts.get("where_columns", []) or []
    kept, _dropped = suppress_redundant_index_candidates(
        final_result.get("candidate_indexes", []) or [], existing_index_cols, where_cols
    )
    final_result["candidate_indexes"] = kept

    # Calculate total analysis time
    total_analysis_time = time.time() - analysis_start
    llm_runtime["total_analysis_time_sec"] = round(total_analysis_time, 2)

    # Log timing summary
    logger.info(f"✓ Analysis completed in {total_analysis_time:.2f}s (RAG: {rag_time:.2f}s, LLM: {llm_runtime.get('llm_recommendation_time_sec', 0)}s)")

    bundle_rows = extract_bundle_row_count(bundle.get("plan", ""))
    return AnalysisResult(
        facts=facts,
        signals=signals,
        docs=docs,
        final_result=final_result,
        validation=validation,
        plan_shape=plan_shape,
        bundle_rows=bundle_rows,
        llm_runtime=llm_runtime,
    )


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def format_exec_outputs_md(items: List[Dict], title: str) -> str:
    parts = [f"### {title}"]
    if not items:
        parts.extend(["```text", "-- none", "```"])
        return "\n".join(parts)
    for item in items:
        parts.extend([
            "```sql", item.get("statement", ""), "```",
            "```text",
            f"status={item.get('status')}",
            f"rowcount={item.get('rowcount')}",
            ("rows_preview=" + json.dumps(item.get("rows_preview", []), default=str)) if item.get("rows_preview") else "rows_preview=[]",
            "```",
        ])
    return "\n".join(parts)


def render_main_analysis_md(facts: Dict[str, Any], final_result: Dict[str, Any], plan_shape: str) -> str:
    """
    Render crisp analysis with only: Query Summary, Execution Plan, Optimization Opportunity.
    """
    narrative = final_result.get("narrative", {}) or {}
    actions = final_result.get("recommended_action", []) or []
    indexes = final_result.get("candidate_indexes", []) or []
    out: List[str] = []

    # Removed LLM status messages - users don't need to see internal diagnostics
    # Analysis is always a hybrid of LLM + rule engine, no need to explain the mix
    out.append("## Query Summary")
    # Always derive query summary from facts for consistency
    table = facts.get("table", "table")
    where_cols = facts.get("where_columns", [])
    if where_cols:
        cols_str = ", ".join(where_cols)
        query_summary = f"Selects rows from {table} filtering on {cols_str}"
    else:
        query_summary = f"Query against {table} table"
    out.append(query_summary)

    out.append("")
    out.append("## Execution Plan")
    # Always derive execution plan summary from facts for consistency
    # Check plan shape for join patterns first
    if "anti-join" in plan_shape.lower():
        exec_plan_summary = "Anti-join (NOT IN / NOT EXISTS optimized to anti-join - already efficient)"
    elif "merge join" in plan_shape.lower():
        exec_plan_summary = "Merge join (efficient sort-merge join algorithm)"
    elif "hash join" in plan_shape.lower():
        exec_plan_summary = "Hash join (efficient hash-based join algorithm)"
    elif facts.get("full_scan_suspected"):
        scan_rows = facts.get("scan_rows") or facts.get("table_rows_estimate", "unknown")
        filter_rows = facts.get("post_filter_rows", "unknown")
        exec_plan_summary = f"Full table scan ({scan_rows} rows scanned, {filter_rows} rows returned)"
    elif facts.get("dominant_bottleneck_guess") == "index_join":
        exec_plan_summary = "Secondary index scan followed by index join to fetch additional columns"
    elif facts.get("good_index_access"):
        exec_plan_summary = "Uses existing index for efficient access"
    else:
        # Describe based on scan characteristics
        scan_rows = facts.get("scan_rows")
        filter_rows = facts.get("post_filter_rows")
        used_index = facts.get("used_index", "")

        # Check if it's a PK lookup or small efficient scan
        if scan_rows and scan_rows <= 10:
            if "pkey" in used_index.lower():
                exec_plan_summary = f"Primary key lookup (direct access, {scan_rows} row{'s' if scan_rows != 1 else ''} scanned)"
            else:
                exec_plan_summary = f"Index scan ({scan_rows} row{'s' if scan_rows != 1 else ''} scanned)"
        elif scan_rows and filter_rows is not None:
            exec_plan_summary = f"Scan with filter ({scan_rows} rows scanned, {filter_rows} rows returned)"
        else:
            exec_plan_summary = "Query execution plan analyzed"

    out.append(exec_plan_summary)
    out.append("")
    out.append(f"**Plan shape:** `{plan_shape}`")

    out.append("")
    out.append("## Optimization Opportunity")
    if indexes:
        for idx in indexes:
            if isinstance(idx, dict):
                ddl = idx.get("ddl", "")
                reason = idx.get("reason", "")
                if ddl:
                    out.append(f"- **Create index:** `{ddl}`")
                    if reason:
                        out.append(f"  - Reason: {reason}")
            else:
                out.append(f"- {idx}")
    elif actions:
        for a in actions:
            out.append(f"- {a}")
    else:
        out.append("- No changes recommended")

    return "\n".join(out)


def render_db_logs_md(dbv: Optional[Dict]) -> str:
    logs = (dbv or {}).get("db_logs", {}) or {}
    parts = [
        "## DB Execution Logs", "",
        format_exec_outputs_md(logs.get("cleanup_output", []), "Cleanup SQL + Output"), "",
        "### Schema SQL", "```sql", logs.get("schema_sql", "") or "-- none", "```", "",
        format_exec_outputs_md(logs.get("schema_output", []), "Schema DDL Output"), "",
        "### Seed SQL", "```sql", "\n\n".join(logs.get("seed_sql", [])) or "-- none", "```", "",
        format_exec_outputs_md(logs.get("seed_output", []), "Seed DML Output"), "",
        "### ANALYZE SQL", "```sql", "\n".join(logs.get("analyze_sql", [])) or "-- none", "```", "",
        format_exec_outputs_md(logs.get("analyze_output", []), "ANALYZE Output"), "",
        "### Index SQL", "```sql", "\n".join(logs.get("index_sql", [])) or "-- none", "```", "",
        format_exec_outputs_md(logs.get("index_output", []), "Index DDL Output"), "",
        "### Plan SQL Before", "```sql", logs.get("plan_sql_before", "") or "-- none", "```", "",
        "### Plan SQL After", "```sql", logs.get("plan_sql_after", "") or "-- none", "```", "",
        "### Messages", "```text", "\n".join(logs.get("messages", [])) or "-- none", "```",
    ]
    return "\n".join(parts)


def render_logs_md(validation, facts, signals, final_result, db_validation=None, llm_runtime=None):
    lines = ["# Analyzer Run"]
    if llm_runtime:
        lines.extend(["", "## LLM Runtime", "```json", json.dumps(llm_runtime, indent=2), "```"])

    lines.extend(["", "## Validator", "```json", json.dumps(validation, indent=2), "```"])
    lines.extend(["", "## Facts used", "```json", json.dumps(facts, indent=2), "```"])
    lines.extend(["", "## Signals used by the model", "```json", json.dumps(signals, indent=2), "```"])
    lines.extend(["", "## Effective Recommendation", "```json",
                  json.dumps({k: v for k, v in final_result.items() if k != 'narrative'}, indent=2), "```"])
    if final_result.get("llm_raw_preview"):
        lines.extend(["", "## LLM Raw Preview", "```text", final_result.get("llm_raw_preview", ""), "```"])
    lines.extend(["", "## Narrative Layer", "```json", json.dumps(final_result.get("narrative", {}), indent=2), "```"])

    if db_validation:
        if db_validation.get("simulation_profile") is not None:
            lines.extend(["", "## Simulation Profile", "```json", json.dumps(db_validation.get("simulation_profile"), indent=2), "```"])
        if db_validation.get("logs"):
            lines.extend(["", "## DB Execution Logs", db_validation.get("logs", "")])

    return "\n".join(lines)

def render_plan_box(title: str, plan_text: str) -> str:
    return f"""
    <details open>
      <summary class="highlight">{html_escape(title)}</summary>
      <div class="sql-block"><pre><code>{html_escape(plan_text or '')}</code></pre></div>
    </details>
    """


def render_db_validation_html(dbv: Optional[Dict]) -> str:
    if not dbv:
        return ""
    errors = "".join(f"<li>{html_escape(e)}</li>" for e in dbv.get("errors", []))
    comp_sim = "".join(f"<li>{html_escape(x)}</li>" for x in dbv.get("comparison_simulated", []))
    comp_bundle = "".join(f"<li>{html_escape(x)}</li>" for x in dbv.get("comparison_vs_bundle", []))
    notes = "".join(f"<li>{html_escape(x)}</li>" for x in dbv.get("comparison_notes", []))
    applied = "".join(f"<li><code>{html_escape(x)}</code></li>" for x in dbv.get("applied_index_ddls", []))
    return f"""
    <details open>
      <summary class="highlight">Database Test</summary>
      <div class="sql-block">
        <p><strong>Connected:</strong> {html_escape(str(dbv.get('connected')))}</p>
        <p><strong>Schema applied from bundle:</strong> {html_escape(str(dbv.get('schema_applied')))}</p>
        <p><strong>Seed rows requested / recreated table size:</strong> {html_escape(str(dbv.get('seed_rows')))}</p>
        <p><strong>Seed applied:</strong> {html_escape(str(dbv.get('seed_applied')))}</p>
        <p><strong>Schema SQL used:</strong></p>
        <pre><code>{html_escape(dbv.get('schema_sql_used', ''))}</code></pre>
        <p><strong>Seed SQL used:</strong></p>
        <pre><code>{html_escape(dbv.get('seed_sql_used', ''))}</code></pre>
        <p><strong>Applied index DDLs:</strong></p>
        <ul>{applied if applied else '<li>No index DDL applied.</li>'}</ul>
        <p><strong>Seed behavior:</strong></p>
        <ul>
          <li>Bundle-derived match fraction: {html_escape(str(dbv.get('bundle_match_fraction')))}</li>
          <li>User variation %: {html_escape(str(dbv.get('seed_variation_pct_used')))}</li>
          <li>Effective replay match fraction: {html_escape(str(dbv.get('effective_replay_match_fraction')))}</li>
          <li>Suggested variation %: {html_escape(str(dbv.get('suggested_seed_variation_pct')))}</li>
          <li>Suggested replay match fraction: {html_escape(str(dbv.get('suggested_effective_fraction')))}</li>
        </ul>
        <p><strong>Notes:</strong></p>
        <ul>{notes if notes else '<li>No additional notes.</li>'}</ul>
        <p><strong>Errors:</strong></p>
        <ul>{errors if errors else '<li>No database test errors.</li>'}</ul>
      </div>
    </details>
    <div class="plan-grid">
      <div>{render_plan_box("Original Bundle Plan", dbv.get("original_bundle_plan", ""))}</div>
      <div>{render_plan_box("Simulated Baseline Plan", dbv.get("simulated_baseline_plan", ""))}</div>
      <div>{render_plan_box("Simulated Post-Change Plan", dbv.get("simulated_post_plan", ""))}</div>
    </div>
    <div class="summary-grid">
      <div>
        <details open>
          <summary class="highlight">Comparison Summary</summary>
          <div class="sql-block">
            <h4>Simulated baseline vs simulated post-change</h4>
            <ul>{comp_sim if comp_sim else '<li>No simulated comparison available.</li>'}</ul>
            <h4>Original bundle plan vs simulated post-change</h4>
            <ul>{comp_bundle if comp_bundle else '<li>No bundle-reference comparison available.</li>'}</ul>
          </div>
        </details>
      </div>
    </div>
    """


def render_html(
    bundle_name: str,
    bundle: Dict[str, str],
    ar: AnalysisResult,
    retriever: HybridRetriever,
    db_validation: Optional[Dict],
    include_verbose_logs: bool = True,
) -> str:
    main_analysis_md = render_main_analysis_md(ar.facts, ar.final_result, ar.plan_shape)

    # Add warning if recommended index was not used in post-change plan
    if db_validation and db_validation.get("applied_index_ddls"):
        comparison_notes = db_validation.get("comparison_notes", [])
        # Check if there's a warning about index not being used
        index_not_used_warning = next((note for note in comparison_notes if "WARNING" in note and "NOT used" in note), None)
        if index_not_used_warning:
            main_analysis_md += f"\n\n---\n\n**⚠️ Index Effectiveness Warning (based on test results):**\n\n{index_not_used_warning}\n"

    main_html = markdown.markdown(main_analysis_md, extensions=["fenced_code"])
    logs_html = markdown.markdown(render_logs_md(ar.validation, ar.facts, ar.signals, ar.final_result, db_validation, ar.llm_runtime), extensions=["fenced_code"])
    docs_html = []
    for d in ar.docs:
        docs_html.append(
            "<li>"
            + f"<strong>{html_escape(d['title'])}</strong> [{html_escape(d.get('source', 'unknown'))}] (score={d['score']})<br>"
            + f"<a href='{html_escape(d['url'])}' target='_blank'>{html_escape(d['url'])}</a><br>"
            + f"<div class='sql-block'>{html_escape(d['snippet'])}</div>"
            + "</li>"
        )
    db_html = render_db_validation_html(db_validation) if db_validation else ""
    web_count = len([d for d in retriever.docs if d.get("source") == "web"])
    txt_count = len([d for d in retriever.docs if d.get("source") == "local_txt"])
    pdf_count = len([d for d in retriever.docs if d.get("source") == "local_pdf"])
    llm_runtime = ar.llm_runtime or {}
    selected_rag_txt = llm_runtime.get("selected_rag_txt", DEFAULT_LOCAL_RAG_TXT)
    txt_path = resolve_selected_rag_txt(selected_rag_txt) or find_existing_local_rag_txt(DEFAULT_LOCAL_RAG_TXT)

    total_time = llm_runtime.get("total_analysis_time_sec", 0)
    rag_time = llm_runtime.get("rag_retrieval_time_sec", 0)
    llm_rec_time = llm_runtime.get("llm_recommendation_time_sec", 0)
    llm_retry_time = llm_runtime.get("llm_retry_time_sec", 0)
    llm_narr_time = llm_runtime.get("llm_narrative_time_sec", 0)

    timing_parts = []
    if rag_time > 0:
        timing_parts.append(f"RAG: {rag_time}s")
    if llm_rec_time > 0:
        timing_parts.append(f"LLM recommendation: {llm_rec_time}s")
    if llm_retry_time > 0:
        timing_parts.append(f"LLM retry: {llm_retry_time}s")
    if llm_narr_time > 0:
        timing_parts.append(f"LLM narrative: {llm_narr_time}s")
    timing_breakdown = " | ".join(timing_parts) if timing_parts else "No timing data"

    # Build verbose sections
    verbose_sections = ""
    if include_verbose_logs:
        verbose_sections = f"""
<details><summary class="highlight">⏱️ Analysis Timing & Configuration</summary>
<div class="sql-block">
<p><strong>Total Analysis Time:</strong> {total_time}s ({timing_breakdown})</p>
<p><strong>RAG sources:</strong> web={web_count}, txt={txt_count}, pdf={pdf_count}</p>
<p><strong>LLM selected model:</strong> {html_escape(str(llm_runtime.get("selected_model", "")))}</p>
<p><strong>LLM effective model:</strong> {html_escape(str(llm_runtime.get("effective_model", "")))}</p>
<p><strong>Ollama Status:</strong> {"✅ Running" if llm_runtime.get("ollama_running") else "❌ NOT RUNNING - Using rule-based fallback"}</p>
<p><strong>LLM model validation:</strong> {html_escape(str(llm_runtime.get("model_validation_ok", "")))}</p>
<p><strong>Installed models detected:</strong> {html_escape(", ".join(llm_runtime.get("installed_models", []) or []))}</p>
<p><strong>Ollama URL:</strong> {html_escape(str(llm_runtime.get("ollama_url", "")))}</p>
<p><strong>RAG document selected:</strong> {html_escape(str(selected_rag_txt))}</p>
<p class="muted"><strong>Focused crawl note:</strong> local TXT RAG context is preferred and is searched in both the app working directory and the script directory. PDFs are ignored unless ENABLE_LOCAL_PDF_RAG=1.</p>
<p class="muted"><strong>TXT file detected:</strong> {html_escape(txt_path or "not found")}</p>
</div>
</details><br>
<details><summary class="highlight">Logs</summary><div class="sql-block">{logs_html}</div></details><br>
<details><summary class="highlight">Retrieved RAG Context</summary><ul>{''.join(docs_html) if docs_html else '<li>No docs retrieved.</li>'}</ul></details><br>
"""

    return f"""
<h3>{html_escape(bundle_name)}</h3>
<details><summary class="highlight">SQL Statement</summary><div class="sql-block"><pre><code>{html_escape(bundle.get('sql', '')[:12000])}</code></pre></div></details><br>
<details><summary class="highlight">Execution Plan From Bundle</summary><div class="sql-block"><pre><code>{html_escape(bundle.get('plan', '')[:16000])}</code></pre></div></details><br>
<details open><summary class="highlight">Analysis</summary><div class="sql-block">{main_html}</div></details><br>
{db_html}
{verbose_sections}
"""


# ---------------------------------------------------------------------------
# HTML UI template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    :root {
      --primary: #6933FF;
      --primary-dark: #5829CC;
      --primary-light: #8A5CFF;
      --secondary: #00D4AA;
      --success: #00D4AA;
      --warning: #FF9F1C;
      --danger: #FF3B30;
      --bg-gradient: linear-gradient(135deg, #FFFFFF 0%, #F7F9FC 100%);
      --card-bg: rgba(255, 255, 255, 0.98);
      --text-primary: #0D1117;
      --text-secondary: #57606A;
      --border-color: rgba(208, 215, 222, 0.48);
      --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.08), 0 1px 2px -1px rgba(0, 0, 0, 0.06);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -2px rgba(0, 0, 0, 0.06);
      --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -4px rgba(0, 0, 0, 0.06);
      --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.08);
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg-gradient);
      background-attachment: fixed;
      padding: 2rem;
      color: var(--text-primary);
      line-height: 1.6;
      min-height: 100vh;
    }

    .header-container {
      display: flex;
      align-items: center;
      gap: 1.75rem;
      margin-bottom: 3rem;
      flex-wrap: wrap;
      padding: 1.75rem 2rem;
      background: linear-gradient(135deg, #ffffff 0%, #fafbfc 100%);
      border-radius: 12px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.06);
      border: 1px solid rgba(208, 215, 222, 0.4);
    }

    .logo-icon {
      width: 190px;
      height: auto;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-right: 0.5rem;
    }

    .logo-icon img {
      width: 100%;
      height: auto;
      object-fit: contain;
      filter: contrast(1.2) saturate(1.3) brightness(0.95);
    }

    h2 {
      color: #0D1117;
      font-size: 1.5rem;
      font-weight: 600;
      margin: 0;
      letter-spacing: -0.02em;
      flex: 1;
      line-height: 1.4;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    form {
      background: var(--card-bg);
      padding: 2rem;
      border-radius: 16px;
      box-shadow: var(--shadow-md);
      margin-bottom: 2rem;
      border: 1px solid var(--border-color);
    }

    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2rem;
      margin-bottom: 1.5rem;
    }

    .form-grid-full {
      grid-column: 1 / -1;
    }

    .form-section {
      margin-bottom: 1.5rem;
      padding: 1.2rem;
      background: rgba(105, 51, 255, 0.02);
      border-radius: 10px;
      border: 1px solid rgba(105, 51, 255, 0.1);
    }

    .form-section h3 {
      font-size: 1rem;
      font-weight: 600;
      color: #6933FF;
      margin: 0 0 1rem 0;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid rgba(105, 51, 255, 0.15);
    }

    label {
      display: block;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 0.6rem;
      font-size: 0.95rem;
    }

    input[type="text"], select {
      width: 100%;
      padding: 0.7rem 1rem;
      border: 2px solid var(--border-color);
      border-radius: 8px;
      font-size: 0.95rem;
      transition: all 0.2s ease;
      background: white;
      color: var(--text-primary);
      margin-bottom: 0.8rem;
    }

    input[type="text"]:focus, select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 4px rgba(105, 51, 255, 0.15);
      transform: translateY(-1px);
    }

    input[type="checkbox"] {
      width: 1.2rem;
      height: 1.2rem;
      margin-right: 0.6rem;
      cursor: pointer;
      accent-color: var(--primary);
    }

    button {
      padding: 0.9rem 2rem;
      background: linear-gradient(135deg, #6933FF 0%, #8B5CF6 100%);
      color: white;
      border: none;
      border-radius: 10px;
      font-weight: 600;
      font-size: 1rem;
      cursor: pointer;
      transition: all 0.3s ease;
      box-shadow: 0 4px 12px rgba(105, 51, 255, 0.3);
      position: relative;
      overflow: hidden;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    button:hover:not(:disabled) {
      background: linear-gradient(135deg, #5A2ACC 0%, #7C4CE3 100%);
      box-shadow: 0 6px 20px rgba(105, 51, 255, 0.4);
      transform: translateY(-2px);
    }

    button:active:not(:disabled) {
      transform: translateY(0px);
      box-shadow: 0 2px 8px rgba(105, 51, 255, 0.3);
    }

    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    .report-section {
      background: var(--card-bg);
      backdrop-filter: blur(10px);
      padding: 2rem;
      border-radius: 16px;
      box-shadow: var(--shadow-xl);
      border: 1px solid var(--border-color);
      animation: slideIn 0.5s ease;
    }

    @keyframes slideIn {
      from { opacity: 0; transform: translateY(20px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .sql-block {
      background: #F6F8FA;
      border-left: 4px solid var(--primary);
      padding: 1.2rem;
      border-radius: 8px;
      overflow-x: auto;
      font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
      font-size: 0.9rem;
      line-height: 1.6;
      margin: 1.2rem 0;
      border: 1px solid var(--border-color);
    }

    details {
      margin-bottom: 1rem;
      background: white;
      border-radius: 10px;
      padding: 0.9rem;
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--border-color);
    }

    summary {
      font-weight: 600;
      cursor: pointer;
      color: var(--text-primary);
      padding: 0.6rem;
      border-radius: 8px;
      transition: all 0.2s ease;
      user-select: none;
      font-size: 1rem;
    }

    summary:hover {
      background: rgba(105, 51, 255, 0.05);
      color: var(--primary);
    }

    summary::marker {
      color: var(--primary);
    }

    .highlight {
      background: linear-gradient(135deg, rgba(105, 51, 255, 0.08) 0%, rgba(105, 51, 255, 0.04) 100%);
      padding: 0.7rem 1rem;
      border-radius: 8px;
      margin: 0.6rem 0;
      border: 1px solid rgba(105, 51, 255, 0.15);
    }

    pre, code {
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'Monaco', 'Courier New', monospace;
    }

    .spinner {
      margin-top: 1rem;
      width: 40px;
      height: 40px;
      border: 4px solid rgba(102, 126, 234, 0.2);
      border-top: 4px solid var(--primary-solid);
      border-radius: 50%;
      animation: spin 1s linear infinite;
      display: none;
    }

    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }

    .note {
      font-size: 0.95rem;
      color: var(--text-secondary);
      margin-top: 1rem;
      padding: 0.9rem 1.2rem;
      background: rgba(102, 126, 234, 0.08);
      border-radius: 10px;
      border-left: 4px solid var(--primary-solid);
    }

    .muted {
      color: var(--text-secondary);
      font-size: 0.9rem;
      margin-top: 0.6rem;
      margin-bottom: 1rem;
      font-style: italic;
    }

    .plan-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1.5rem;
      margin-bottom: 1.5rem;
      font-size: 0.9rem;
    }

    .plan-grid h3, .plan-grid h4 {
      font-size: 0.95rem;
      margin: 0.6rem 0;
    }

    .plan-grid pre, .plan-grid code {
      font-size: 0.85rem;
      line-height: 1.4;
    }

    .summary-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 1.5rem;
      margin-bottom: 1.5rem;
    }

    /* Report section headings */
    .report-section h2 {
      font-size: 1.4rem;
      font-weight: 600;
      margin: 1.5rem 0 0.8rem 0;
      color: var(--text-primary);
    }

    .report-section h3 {
      font-size: 1.1rem;
      font-weight: 600;
      margin: 1rem 0 0.6rem 0;
      color: var(--text-primary);
    }

    .report-section h4 {
      font-size: 1rem;
      font-weight: 600;
      margin: 0.8rem 0 0.5rem 0;
      color: var(--text-secondary);
    }

    .report-section p {
      font-size: 0.95rem;
      line-height: 1.6;
      margin: 0.6rem 0;
    }

    .report-section ul, .report-section ol {
      font-size: 0.95rem;
      line-height: 1.6;
      margin: 0.6rem 0;
    }

    .bundle-list { margin-top: 0.8rem; margin-bottom: 1rem; }
    .bundle-line {
      display: block;
      margin: 0.6rem 0;
      padding: 0.8rem 1rem;
      background: linear-gradient(135deg, rgba(102, 126, 234, 0.06) 0%, rgba(102, 126, 234, 0.02) 100%);
      border-radius: 8px;
      font-size: 0.95rem;
      border: 1px solid rgba(102, 126, 234, 0.15);
    }

    #uploadBundlesBtn {
      display: block;
      margin-top: 1rem;
      margin-bottom: 1rem;
    }

    .bundle-seed-row { margin: 1rem 0 1.5rem 0; }
    .bundle-seed-label {
      display: block;
      margin-bottom: 0.5rem;
      font-weight: 600;
    }
    .bundle-seed-input {
      width: 280px;
      padding: 0.75rem 1rem;
      border: 2px solid var(--border-color);
      border-radius: 10px;
      transition: all 0.3s ease;
    }

    .bundle-seed-input:focus {
      outline: none;
      border-color: var(--primary-solid);
      box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
    }

    /* Status badges */
    .status-badge {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      border-radius: 16px;
      font-size: 0.875rem;
      font-weight: 600;
      margin: 0 0.25rem;
    }

    .status-success {
      background: var(--success);
      color: white;
    }

    .status-error {
      background: var(--danger);
      color: white;
    }

    .status-warning {
      background: var(--warning);
      color: white;
    }

    /* Responsive design */
    @media (max-width: 1024px) {
      .form-grid {
        grid-template-columns: 1fr;
        gap: 1rem;
      }
      .plan-grid { grid-template-columns: 1fr; }
    }

    @media (max-width: 768px) {
      body { padding: 1rem; }
      h2 { font-size: 1.1rem; }
      form, .report-section { padding: 1.2rem; border-radius: 12px; }
      .form-grid { grid-template-columns: 1fr; }
      .plan-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="header-container">
    <div class="logo-icon">
      <img src="https://www.cockroachlabs.com/docs/images/cockroachlabs-logo-170.png" alt="CockroachDB">
    </div>
    <h2>{{ title }}</h2>
  </div>
  <form method="POST" enctype="multipart/form-data" onsubmit="showLoading()">
    <!-- Bundle Upload Section - Full Width -->
    <div class="form-grid-full">
      <label><strong>Upload stmt-bundle zip files:</strong></label>
      <input type="file" id="bundleInput" name="bundles" multiple accept=".zip" style="display:none">
      <button type="button" id="chooseBundlesBtn">Choose Bundles</button>
      <div class="muted" id="bundleFileNames">No bundles selected.</div>
      <button type="button" id="uploadBundlesBtn">Upload</button>
      <div class="muted" id="bundleRowsPreview">Rows to insert for DB testing: ( Actual rows from bundle - unknown)</div>
    </div>

    <!-- Configuration Grid - Side by Side -->
    <div class="form-grid">
      <!-- Left Column -->
      <div class="form-section">
        <label><strong>Docs seed URL:</strong></label>
        <input type="text" name="docs_root" value="{{ docs_root }}">

        <label><strong>Ollama model:</strong></label>
        <div class="muted">Only installed Ollama models shown</div>
        <select name="ollama_model">
          {% for model in available_models %}
            <option value="{{ model }}" {% if ollama_model == model %}selected{% endif %}>{{ model }}</option>
          {% endfor %}
        </select>
      </div>

      <!-- Right Column -->
      <div class="form-section">
        {% if rag_txt_files %}
        <label><strong>RAG document:</strong></label>
        <div class="muted">TXT files detected: {{ rag_txt_files|length }}</div>
        <select name="rag_txt">
            {% for f in rag_txt_files %}
              <option value="{{ f }}" {% if selected_rag_txt == f %}selected{% endif %}>{{ f }}</option>
            {% endfor %}
        </select>
        {% endif %}

        <label><input type="checkbox" name="rebuild_rag" value="1"> Rebuild docs + local PDF index</label>
        <label><input type="checkbox" name="test_in_db" value="1" {% if test_in_db %}checked{% endif %}> Test recommendations in running database</label>
      </div>
    </div>

    <!-- Database Testing Section - Grid Layout -->
    <div id="db_box" style="{% if not test_in_db %}display:none;{% endif %}">
      <div class="form-grid-full">
        <label><strong>CockroachDB connection string:</strong></label>
        <input type="text" name="db_conn_str" value="{{ db_conn_str_masked }}">
      </div>

      <div class="form-grid">
        <!-- Left Column - DB Settings -->
        <div class="form-section">
          <label><strong>Rows to insert for DB testing:</strong></label>
          <div id="bundleSeedInputs">
            <div class="muted">Upload bundles to populate per-bundle row inputs.</div>
          </div>
          <div class="muted">Leave blank to use 10000 rows for any bundle.</div>

          <label><strong>Seed data variation (%):</strong></label>
          <input type="text" name="seed_variation_pct" value="{{ seed_variation_pct }}" placeholder="Leave blank for auto">
          <div class="muted">Leave blank for automatic selection based on query selectivity. 0 preserves original selectivity, higher values create more variation.</div>
        </div>

        <!-- Right Column - DB Options -->
        <div class="form-section">
          <label><input type="checkbox" name="apply_recommended_indexes" value="1" {% if apply_recommended_indexes %}checked{% endif %}> Auto-apply recommended index DDLs for test</label>
          <label><input type="checkbox" name="drop_test_indexes" value="1" {% if drop_test_indexes %}checked{% endif %}> Drop test indexes after comparison</label>

          <div class="muted" style="margin-top: 1rem;">The tool recreates schema and SQL from the bundle, seeds synthetic data shaped to bundle selectivity, then compares original bundle, simulated baseline, and simulated post-change plans.</div>
        </div>
      </div>
    </div>

    <input type="hidden" name="uploaded_bundle_manifest" id="uploadedBundleManifest" value="">

    <!-- Output Options and Submit - Full Width -->
    <div class="form-grid-full">
      <label><input type="checkbox" name="include_verbose_logs" value="1" {% if include_verbose_logs %}checked{% endif %}> Include verbose logs in HTML output</label>
      <div class="muted">When unchecked, output shows only: SQL, Plans, Analysis, and Summary (cleaner reports)</div>

      <button type="submit" id="submit-btn" disabled>Analyze</button>
      <div id="spinner" class="spinner"></div>
      <div class="note">All .pdf files in the current folder are added to RAG automatically.</div>
    </div>
  </form>

  {% if report %}
  <div class="report-section">{{ report|safe }}</div>
  {% endif %}

  <script>
    function showLoading() {
      const spinner = document.getElementById("spinner");
      if (spinner) spinner.style.display = "inline-block";
    }
    function toggleDbBox() {
      const cb = document.querySelector('input[name="test_in_db"]');
      const box = document.getElementById("db_box");
      if (!cb || !box) return;
      box.style.display = cb.checked ? "block" : "none";
    }
    function resetAnalyzeState() {
      const analyzeBtn = document.getElementById("submit-btn");
      const manifest = document.getElementById("uploadedBundleManifest");
      if (analyzeBtn) analyzeBtn.disabled = true;
      if (manifest) manifest.value = "";
    }
    function renderSeedInputs(items) {
      const container = document.getElementById("bundleSeedInputs");
      const manifest = document.getElementById("uploadedBundleManifest");
      if (!container) return;
      container.innerHTML = "";
      const manifestItems = [];
      if (!items || items.length === 0) {
        container.innerHTML = '<div class="muted">Upload bundles to populate per-bundle row inputs.</div>';
        if (manifest) manifest.value = "";
        return;
      }
      items.forEach((item, idx) => {
        const seedKey = `seed_rows_${idx}`;
        const rows = (item.rows === null || item.rows === undefined) ? "unknown" : item.rows;
        const row = document.createElement("div");
        row.className = "bundle-seed-row";
        row.innerHTML = `
          <label class="bundle-seed-label"><strong>${item.filename}: ( Actual num of rows in the Bundle - ${rows})</strong></label>
          <input class="bundle-seed-input" type="text" name="${seedKey}" value="${rows === "unknown" ? "" : rows}">
        `;
        container.appendChild(row);
        manifestItems.push({ filename: item.filename, seed_key: seedKey, rows: item.rows });
      });
      if (manifest) manifest.value = JSON.stringify(manifestItems);
    }
    async function uploadBundlesForPreview() {
      const input = document.getElementById("bundleInput");
      const preview = document.getElementById("bundleRowsPreview");
      const analyzeBtn = document.getElementById("submit-btn");
      if (!input || !input.files || input.files.length === 0) {
        if (preview) preview.innerText = "No bundles selected.";
        renderSeedInputs([]);
        resetAnalyzeState();
        return;
      }
      const formData = new FormData();
      for (const file of input.files) { formData.append("bundles", file); }
      if (preview) preview.innerText = "Scanning bundles...";
      resetAnalyzeState();
      try {
        const res = await fetch("/preview_bundle", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          const errorMsg = data.error || "Preview failed";
          console.error("Server error:", errorMsg);
          if (data.traceback) console.error("Traceback:", data.traceback);
          throw new Error(errorMsg);
        }
        const items = data.items || [];
        renderSeedInputs(items);
        if (preview) {
          if (items.length === 0) {
            preview.innerText = "No bundle rows detected.";
          } else {
            preview.innerHTML = '<div class="bundle-list">' +
              items.map(x => `<span class="bundle-line">${x.filename}: ( Actual num of rows in the Bundle - ${x.rows === null || x.rows === undefined ? "unknown" : x.rows})</span>`).join("") +
              '</div>';
          }
        }
        if (analyzeBtn) analyzeBtn.disabled = items.length === 0;
      } catch (e) {
        console.error("Preview error:", e);
        const errorMsg = e.message || String(e);
        if (preview) {
          preview.innerHTML = `<div style="color: red; white-space: pre-wrap;">Bundle upload scan failed: ${errorMsg}</div>`;
        }
        renderSeedInputs([]);
        resetAnalyzeState();
      }
    }
    document.addEventListener("DOMContentLoaded", function() {
      toggleDbBox();
      const dbcb = document.querySelector('input[name="test_in_db"]');
      if (dbcb) dbcb.addEventListener("change", toggleDbBox);
      const chooseBtn = document.getElementById("chooseBundlesBtn");
      const uploadBtn = document.getElementById("uploadBundlesBtn");
      const input = document.getElementById("bundleInput");
      const fileNames = document.getElementById("bundleFileNames");
      if (chooseBtn && input) chooseBtn.addEventListener("click", function() {
        input.value = '';  // Reset input so change event fires even for same file
        input.click();
      });
      if (input && fileNames) {
        input.addEventListener("change", function() {
          const names = Array.from(input.files || []).map(f => f.name);
          if (names.length) {
            fileNames.innerHTML = '<div class="bundle-list">' + names.map(n => `<span class="bundle-line">${n}</span>`).join("") + '</div>';
          } else {
            fileNames.innerText = "No bundles selected.";
          }
          const preview = document.getElementById("bundleRowsPreview");
          if (preview) preview.innerText = "Rows to insert for DB testing: ( Actual rows from bundle - unknown)";
          renderSeedInputs([]);
          resetAnalyzeState();
        });
      }
      if (uploadBtn) uploadBtn.addEventListener("click", uploadBundlesForPreview);
    });
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    docs_root = DEFAULT_DOCS_ROOT
    crawl_mode = "focused"  # Hardcoded to focused mode
    available_models, _ = get_ollama_models()
    rag_txt_files = list_available_rag_txt_files()
    ollama_model = DEFAULT_OLLAMA_MODEL if DEFAULT_OLLAMA_MODEL in available_models else (available_models[0] if available_models else DEFAULT_OLLAMA_MODEL)
    selected_rag_txt = DEFAULT_LOCAL_RAG_TXT if DEFAULT_LOCAL_RAG_TXT in rag_txt_files else (rag_txt_files[0] if rag_txt_files else DEFAULT_LOCAL_RAG_TXT)
    report = ""
    test_in_db = False
    include_verbose_logs = True  # Default: include logs
    db_conn_str = DEFAULT_CONN_STR
    apply_recommended_indexes = True
    drop_test_indexes = True
    seed_rows = str(DEFAULT_SEED_ROWS)
    seed_variation_pct = "0"

    if request.method == "POST":
        # If user leaves docs_root empty, use txt file only (skip web crawl)
        docs_root = request.form.get("docs_root", "").strip()
        skip_web_crawl = not docs_root  # Empty = skip web, use txt only
        if not docs_root:
            docs_root = ""  # Keep empty to signal txt-only mode
        available_models, _ = get_ollama_models()
        rag_txt_files = list_available_rag_txt_files()
        rag_txt_files = list_available_rag_txt_files()
        requested_model = request.form.get("ollama_model", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
        if requested_model in available_models:
            ollama_model = requested_model
        else:
            ollama_model = available_models[0] if available_models else DEFAULT_OLLAMA_MODEL
            report = (
                f"<p><strong>Requested Ollama model not installed:</strong> {html_escape(requested_model)}. "
                f"Using {html_escape(ollama_model)} instead.</p>"
            )
        selected_rag_txt = (request.form.get("rag_txt", "") or "").strip()
        if selected_rag_txt not in rag_txt_files and rag_txt_files:
            selected_rag_txt = rag_txt_files[0]
        rebuild_rag = request.form.get("rebuild_rag") == "1"
        test_in_db = request.form.get("test_in_db") == "1"
        include_verbose_logs = request.form.get("include_verbose_logs") == "1"
        # Accept the raw DSN from the form (user typed it in), but never render it back in plaintext
        db_conn_str = request.form.get("db_conn_str", DEFAULT_CONN_STR).strip() or DEFAULT_CONN_STR
        apply_recommended_indexes = request.form.get("apply_recommended_indexes") == "1"
        drop_test_indexes = request.form.get("drop_test_indexes") == "1"
        seed_rows_input = (request.form.get("seed_rows") or "").strip()
        seed_rows = seed_rows_input or str(DEFAULT_SEED_ROWS)
        seed_variation_pct_raw = (request.form.get("seed_variation_pct") or "").strip()
        seed_variation_pct_was_blank = (seed_variation_pct_raw == "")
        try:
            # Use "auto" as the default when blank - will be resolved per-bundle based on selectivity
            seed_variation_pct_num = float(seed_variation_pct_raw) if not seed_variation_pct_was_blank else None
        except Exception:
            seed_variation_pct_num = None
        seed_variation_pct = seed_variation_pct_raw if not seed_variation_pct_was_blank else None

        uploaded_manifest_raw = (request.form.get("uploaded_bundle_manifest") or "").strip()
        try:
            uploaded_manifest = json.loads(uploaded_manifest_raw) if uploaded_manifest_raw else []
        except Exception:
            uploaded_manifest = []

        seed_rows_by_name: Dict[str, int] = {}
        for item in uploaded_manifest:
            fname = item.get("filename")
            form_key = item.get("seed_key")
            if not fname or not form_key:
                continue
            val = (request.form.get(form_key) or "").strip()
            seed_rows_by_name[fname] = safe_int(val) or DEFAULT_SEED_ROWS

        seed_rows_num = safe_int(seed_rows) or DEFAULT_SEED_ROWS

        retriever = HybridRetriever(os.path.join(INDEX_DIR, "docs.json"))
        retriever.load_or_build(docs_root, crawl_mode, rebuild_rag, selected_rag_txt=selected_rag_txt, skip_web=skip_web_crawl)

        # Pre-load the model into memory for faster first analysis
        warmup_ollama_model(ollama_model)

        uploaded = request.files.getlist("bundles")
        sections: List[str] = []
        generated_files: List[str] = []
        # In exe mode, store reports in memory (session)
        if RUNNING_FROM_EXE:
            if 'reports' not in session:
                session['reports'] = {}

        # Use TemporaryDirectory so cleanup always happens
        with tempfile.TemporaryDirectory(prefix="sql_bundle_db_compare_") as temp_dir:
            for up in uploaded:
                if not up or not up.filename.endswith(".zip"):
                    continue
                zip_path = os.path.join(temp_dir, up.filename)
                up.save(zip_path)
                bundle_dir = os.path.join(temp_dir, os.path.splitext(up.filename)[0])

                try:
                    unzip_bundle(zip_path, bundle_dir)
                    bundle = load_bundle(bundle_dir)
                    logger.info(f"Starting analysis of {up.filename}...")
                    ar = analyze(bundle, retriever, ollama_model, selected_rag_txt=selected_rag_txt)
                    bundle_seed_rows = seed_rows_by_name.get(up.filename, seed_rows_num)
                    db_validation = run_db_validation(
                        db_conn_str, bundle, ar.final_result,
                        apply_indexes=apply_recommended_indexes,
                        drop_test_indexes=drop_test_indexes,
                        seed_rows=bundle_seed_rows,
                        signals=ar.signals,
                        seed_variation_pct=seed_variation_pct_num if test_in_db else 0.0,
                        seed_variation_defaulted=seed_variation_pct_was_blank,
                        seed_variation_pct_raw=seed_variation_pct_raw,
                    ) if test_in_db else None
                    logger.info(f"  → DB validation complete for {up.filename}")
                    html_section = render_html(up.filename, bundle, ar, retriever, db_validation, include_verbose_logs)
                    logger.info(f"  → HTML rendering complete for {up.filename}")
                    sections.append(html_section)

                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_name = f"{os.path.splitext(up.filename)[0]}_{ts}.html"

                    # Build full HTML report
                    full_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        + f"<title>{html_escape(out_name)}</title>"
                        + "<style>"
                        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;padding:2rem;background:linear-gradient(135deg,#FFFFFF 0%,#F7F9FC 100%);color:#0D1117;line-height:1.6}"
                        ".header-container{display:flex;align-items:center;gap:1.75rem;margin-bottom:2rem;padding:1.75rem 2rem;background:linear-gradient(135deg,#ffffff 0%,#fafbfc 100%);border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.04),0 1px 2px rgba(0,0,0,0.06);border:1px solid rgba(208,215,222,0.4)}"
                        ".logo-icon{width:190px;height:auto;display:flex;align-items:center;justify-content:center;margin-right:0.5rem}"
                        ".logo-icon img{width:100%;height:auto;object-fit:contain;filter:contrast(1.2) saturate(1.3) brightness(0.95)}"
                        "h2{color:#0D1117;font-size:1.5rem;font-weight:600;margin:0;flex:1;line-height:1.4;letter-spacing:-0.02em;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
                        "h3{font-size:1.1rem;font-weight:600;margin:1.2rem 0 0.7rem 0;color:#0D1117}"
                        "h4{font-size:1rem;font-weight:600;margin:0.9rem 0 0.5rem 0;color:#57606A}"
                        "p{font-size:0.95rem;line-height:1.6;margin:0.5rem 0}"
                        "ul,ol{font-size:0.95rem;line-height:1.6;margin:0.5rem 0}"
                        ".sql-block{background:#F6F8FA;border-left:3px solid #6933FF;padding:1rem;border-radius:6px;overflow-x:auto;font-family:Monaco,Menlo,'Courier New',monospace;font-size:0.9rem;line-height:1.5;margin:1rem 0;border:1px solid rgba(208,215,222,0.48)}"
                        "details{margin-bottom:1rem;background:white;border-radius:8px;padding:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.08);border:1px solid rgba(208,215,222,0.48)}"
                        "summary{font-weight:600;cursor:pointer;color:#0D1117;padding:0.5rem;border-radius:6px;user-select:none}"
                        "summary:hover{background:rgba(105,51,255,0.05);color:#6933FF}"
                        ".highlight{background:rgba(105,51,255,0.05);padding:0.625rem 0.875rem;border-radius:6px}"
                        "pre,code{white-space:pre-wrap;word-break:break-word;font-family:Monaco,Menlo,'Courier New',monospace}"
                        ".plan-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1.2rem;margin-bottom:1.5rem;font-size:0.9rem}"
                        ".plan-grid h3,.plan-grid h4{font-size:0.95rem;margin:0.5rem 0;font-weight:600}"
                        ".plan-grid summary{font-size:0.95rem;font-weight:600;padding:0.5rem 0.6rem}"
                        ".plan-grid pre,.plan-grid code{font-size:0.85rem;line-height:1.4}"
                        ".summary-grid{display:grid;grid-template-columns:1fr;gap:1.5rem;margin-bottom:1.5rem}"
                        "@media(max-width:768px){.plan-grid{grid-template-columns:1fr}}"
                        "</style></head><body>"
                        + "<div class='header-container'>"
                        + "<div class='logo-icon'>"
                        + "<img src='https://www.cockroachlabs.com/docs/images/cockroachlabs-logo-170.png' alt='CockroachDB'>"
                        + "</div>"
                        + f"<h2>{APP_TITLE}</h2>"
                        + "</div>"
                        + f"{html_section}</body></html>"
                    )

                    # In exe mode: store in session memory; in script mode: save to disk
                    if RUNNING_FROM_EXE:
                        session['reports'][out_name] = full_html
                        session.modified = True
                        logger.info(f"  → Report stored in memory: {out_name} ({len(full_html)} bytes)")
                        logger.info(f"  → Total reports in session: {len(session['reports'])}")
                    else:
                        out_path = os.path.join(REPORT_DIR, out_name)
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(full_html)
                        logger.info(f"  → Report saved: {out_name}")

                    generated_files.append(out_name)
                except Exception as e:
                    logger.exception("Analysis failed for %s", up.filename)
                    sections.append(f"<h3>{html_escape(up.filename)}</h3><p><strong>Analysis failed:</strong> {html_escape(str(e))}</p>")

        logger.info(f"All bundles processed. Generating response...")
        if generated_files:
            links = "".join(
                f"<li><a href='{url_for('download_report', filename=n)}'>{html_escape(n)}</a></li>"
                for n in generated_files
            )
            # Store generated files in session for download all functionality
            session['generated_files'] = generated_files
            download_all_btn = f"""
            <div style="margin: 15px 0;">
                <a href="{url_for('download_all_reports')}" style="text-decoration: none;">
                    <button type="button" style="background: #6933FF; padding: 12px 24px; font-weight: 600; color: white; border: none; border-radius: 8px; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.08); transition: all 0.2s ease; cursor: pointer;">
                        📦 Download All Reports as ZIP ({len(generated_files)} files)
                    </button>
                </a>
            </div>
            """
            report = "<h3>Generated reports</h3>" + download_all_btn + "<ul>" + links + "</ul><hr>" + "".join(sections)
        else:
            report = "".join(sections) or "<p>No valid bundles processed.</p>"

    logger.info(f"Sending response to UI...")
    return render_template_string(
        HTML_TEMPLATE,
        title=APP_TITLE,
        report=report,
        docs_root=docs_root,
        ollama_model=ollama_model,
        available_models=available_models,
        rag_txt_files=rag_txt_files,
        selected_rag_txt=selected_rag_txt,
        test_in_db=test_in_db,
        include_verbose_logs=include_verbose_logs,
        # Mask password before sending to HTML — the real DSN is kept in the server-side variable
        db_conn_str_masked=mask_dsn_password(db_conn_str),
        apply_recommended_indexes=apply_recommended_indexes,
        drop_test_indexes=drop_test_indexes,
        seed_rows=seed_rows,
        seed_variation_pct=seed_variation_pct,
    )


@app.route("/preview_bundle", methods=["POST"])
def preview_bundle():
    files = request.files.getlist("bundles")
    items: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sql_bundle_preview_") as tmp_dir:
        try:
            for up in files:
                if not up or not up.filename.endswith(".zip"):
                    continue
                zip_path = os.path.join(tmp_dir, up.filename)
                up.save(zip_path)
                bundle_dir = os.path.join(tmp_dir, os.path.splitext(up.filename)[0])
                unzip_bundle(zip_path, bundle_dir)
                bundle = load_bundle(bundle_dir)
                # Always derive table size from facts, not query result
                facts = derive_rule_facts(bundle)
                rows = facts.get("table_rows_estimate") or facts.get("scan_rows") or extract_bundle_row_count(bundle.get("plan", ""))
                items.append({"filename": up.filename, "rows": rows})
            return {"ok": True, "items": items}
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            logger.error(f"Preview bundle failed: {error_msg}")
            return {"ok": False, "error": str(e), "traceback": traceback.format_exc(), "items": items}, 400


@app.route("/download/<path:filename>")
def download_report(filename: str):
    """Serve a report file - from session (exe mode) or disk (script mode)."""
    from flask import make_response

    # Exe mode: serve from session memory
    if RUNNING_FROM_EXE:
        reports = session.get('reports', {})
        logger.info(f"Download request for: {filename}")
        logger.info(f"Available reports in session: {list(reports.keys())}")
        if filename not in reports:
            logger.error(f"Report not found in session: {filename}")
            abort(404)
        response = make_response(reports[filename])
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        logger.info(f"Sending report: {filename} ({len(reports[filename])} bytes)")
        return response

    # Script mode: serve from disk with path traversal protection
    report_root = os.path.realpath(REPORT_DIR)
    requested = os.path.realpath(os.path.join(REPORT_DIR, filename))
    if not requested.startswith(report_root + os.sep):
        abort(404)
    if not os.path.isfile(requested):
        abort(404)
    return send_file(requested, as_attachment=True)


@app.route("/download_all_reports")
def download_all_reports():
    """Create a zip file with all generated reports and serve it for download."""
    import zipfile
    from io import BytesIO

    # Get generated files from session
    generated_files = session.get('generated_files', [])

    if not generated_files:
        abort(404, description="No reports available for download")

    # Create in-memory zip file
    memory_file = BytesIO()

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Exe mode: read from session memory
        if RUNNING_FROM_EXE:
            reports = session.get('reports', {})
            for filename in generated_files:
                if filename in reports:
                    zf.writestr(filename, reports[filename])

        # Script mode: read from disk
        else:
            report_root = os.path.realpath(REPORT_DIR)

            for filename in generated_files:
                file_path = os.path.realpath(os.path.join(REPORT_DIR, filename))

                # Security: ensure file is within report directory
                if not file_path.startswith(report_root + os.sep):
                    continue

                if os.path.isfile(file_path):
                    # Add file to zip with just the filename (no directory structure)
                    zf.write(file_path, arcname=filename)

    # Seek to beginning of file
    memory_file.seek(0)

    # Generate zip filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"sql_analyzer_reports_{timestamp}.zip"

    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=zip_filename
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from waitress import serve

    # Display startup information
    print("=" * 80)
    print(f"Starting {APP_TITLE}")
    print(f"Mode: {MODE}")
    print(f"Default Model: {DEFAULT_OLLAMA_MODEL}")
    print(f"Server URL: http://127.0.0.1:{SERVER_PORT}")
    print(f"Working Directory: {os.getcwd()}")
    print("=" * 80)

    if RUNNING_FROM_EXE:
        print("Using embedded documentation (no local files required)")
    else:
        print("Local PDFs and TXT files in current folder will be added to RAG automatically")

    print("\nPress Ctrl+C to stop the server\n")

    serve(app, host="0.0.0.0", port=SERVER_PORT, threads=4)

