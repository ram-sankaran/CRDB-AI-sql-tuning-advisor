
# Prerequisites

Everything you need before using SQL Tuning Advisor.

---

## Required Components

### 1. Ollama (Local LLM Runtime)

**What is Ollama?**

Ollama is a free, open-source tool that runs large language models locally on your computer. SQL Tuning Advisor uses Ollama to provide AI-powered query analysis without sending your data to the cloud.

**Installation:**

<details>
<summary><strong>macOS</strong></summary>

```bash
# Using Homebrew
brew install ollama

# Or download from website
curl -fsSL https://ollama.ai/install.sh | sh
```

Verify installation:
```bash
ollama --version
# Should output: ollama version 0.x.x
```
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
# One-line install
curl -fsSL https://ollama.ai/install.sh | sh
```

Verify installation:
```bash
ollama --version
```
</details>

<details>
<summary><strong>Windows</strong></summary>

1. Download installer from: [https://ollama.ai/download](https://ollama.ai/download)
2. Run the installer
3. Verify from PowerShell:

```powershell
ollama --version
```
</details>

**Start Ollama Service:**

```bash
# Ollama needs to run in the background
ollama serve

# Or use system service (macOS)
brew services start ollama
```

---

### 2. Language Model

**Download a model for analysis:**

**For Fast Analysis (SLM Mode):**
```bash
# llama3.1:8b - 4.9 GB download
ollama pull llama3.1:8b
```

**Requirements:**
- Download size: 4.9 GB
- RAM needed: 8 GB
- Analysis speed: 3-5 seconds
- Quality: Good

**For Detailed Analysis (LLM Mode):**
```bash
# llama3.3:70b - 40 GB download
ollama pull llama3.3:70b
```

**Requirements:**
- Download size: 40 GB
- RAM needed: 48 GB (or 24GB GPU VRAM)
- Analysis speed: 60-90 seconds
- Quality: Excellent

**Verify model downloaded:**
```bash
ollama list
# Should show:
# NAME              ID            SIZE
# llama3:8b         a6990...      4.7 GB
```

---

### 3. CockroachDB Query Bundle

**What is a query bundle?**

A statement bundle is a .zip file containing:
- Your SQL query
- Execution plan from EXPLAIN ANALYZE
- Table schemas
- Performance metrics

**How to generate:**

**Method 1: SQL Shell**
```sql
-- Connect to your database
cockroach sql --url "postgresql://user@host:26257/database"

-- Generate bundle for your query
EXPLAIN ANALYZE (DEBUG) 
SELECT * FROM orders WHERE customer_id = 100;

-- Downloads: statement.zip (or statement-<timestamp>.zip)
```

**Method 2: CockroachDB Cloud Console**
1. Go to SQL Activity → Statements
2. Find your slow query
3. Click "Diagnostics"
4. Download statement bundle

**Method 3: DB Console**
1. Open DB Console: `http://localhost:8080`
2. Navigate to SQL Activity
3. Click on slow statement
4. Click "Download Bundle"

**Bundle contents example:**
```
statement.zip
├── statement.sql         # Your query
├── statement.txt         # Execution info
├── plan.txt             # EXPLAIN output
├── schema.sql           # Table DDL
├── env.sql              # Session settings
└── stats-*.sql          # Table statistics
```

---

## Optional Components

### 4. CockroachDB Test Instance (For Validation)

**Why needed?**

To test recommendations before applying to production:
- Creates temporary schema
- Applies recommended indexes
- Runs actual EXPLAIN ANALYZE
- Shows before/after comparison

**Options:**

**Option A: Local Development Cluster**
```bash
# Single-node cluster (for testing)
cockroach start-single-node \
  --insecure \
  --listen-addr=localhost:26257 \
  --http-addr=localhost:8080 \
  --store=/tmp/cockroach-test

# Create database
cockroach sql --insecure -e "CREATE DATABASE testdb;"
```

**Option B: CockroachDB Cloud Free Tier**
1. Sign up at: [https://cockroachlabs.cloud](https://cockroachlabs.cloud)
2. Create free cluster
3. Download connection string

**Option C: Existing Test Database**
- Use existing development/staging environment
- SQL Tuning Advisor creates temporary schemas
- No impact on existing data

**Connection requirements:**
- Read/write access
- Ability to create indexes
- Recommended: Dedicated test database

---

## System Requirements

### Minimum Configuration

| Component | Requirement |
|-----------|-------------|
| **OS** | macOS 10.15+, Windows 10+, Ubuntu 20.04+ |
| **CPU** | 2 cores, x86_64 or ARM64 |
| **RAM** | 8 GB (SLM mode only) |
| **Disk** | 10 GB free space |
| **Network** | Internet for initial setup only |

### Recommended Configuration

| Component | Requirement |
|-----------|-------------|
| **OS** | Latest stable OS version |
| **CPU** | 4+ cores, modern processor |
| **RAM** | 16 GB (SLM), 64 GB (LLM) |
| **Disk** | 50 GB free space (SSD preferred) |
| **GPU** | Optional: NVIDIA GPU with 24GB+ VRAM (2x faster LLM) |

---

## Disk Space Breakdown

| Item | Size | Purpose |
|------|------|---------|
| SQL Tuning Advisor | 50 MB | Application executable |
| Ollama Runtime | 500 MB | LLM inference engine |
| llama3:8b model | 4.7 GB | Fast analysis (SLM) |
| llama3.3:70b model | 40 GB | Detailed analysis (LLM) |
| Working space | 1-5 GB | Temporary files, reports |
| **Total (SLM)** | ~6 GB | For fast mode only |
| **Total (LLM)** | ~46 GB | For detailed mode |

---

## Network Requirements

### Initial Setup
- Internet connection required to:
  - Download Ollama
  - Download language models
  - Download SQL Tuning Advisor

### Runtime
- **No internet required!**
- All analysis happens locally
- Optional: RAG documentation update (one-time)

### Firewall
- **Inbound:** None required (localhost only)
- **Outbound:** None required (after setup)

---

## Quick Checklist

Before running SQL Tuning Advisor, ensure:

```
Ollama installed
  ✓ ollama --version works
  ✓ ollama serve is running

Model downloaded
  ✓ ollama list shows llama3:8b or llama3.3:70b
  ✓ Model fully downloaded (check size)

Query bundle ready
  ✓ statement.zip file from EXPLAIN ANALYZE (DEBUG)
  ✓ Bundle contains plan.txt and schema.sql

Sufficient disk space
  ✓ At least 10 GB free (SLM)
  ✓ At least 50 GB free (LLM)

Sufficient RAM
  ✓ 8 GB for SLM mode
  ✓ 48 GB for LLM mode (or GPU)

Optional: Test database
  ✓ CockroachDB instance accessible
  ✓ Can create/drop indexes
  ✓ Connection string available
```

---

## Troubleshooting Prerequisites

### Ollama Not Running

**Symptoms:**
```
Error: could not connect to Ollama
Connection refused: http://localhost:11434
```

**Solution:**
```bash
# Start Ollama manually
ollama serve

# Or enable as service (macOS)
brew services start ollama

# Verify
curl http://localhost:11434/api/tags
# Should return JSON with model list
```

---

### Model Not Found

**Symptoms:**
```
Error: model "llama3:8b" not found
```

**Solution:**
```bash
# List available models
ollama list

# Pull missing model
ollama pull llama3:8b

# Verify download
ollama list
# Should show llama3:8b with size 4.7 GB
```

---

### Insufficient RAM

**Symptoms:**
```
Error: model failed to load
Out of memory
```

**Solution:**

For SLM mode (llama3:8b):
- Close other applications
- Requires minimum 8 GB RAM
- Swap to smaller model: `ollama pull llama3.2:1b` (2 GB)

For LLM mode (llama3.3:70b):
- Requires 48 GB RAM or GPU
- Alternative: Use smaller model
- Or use SLM mode instead

---

### Bundle File Issues

**Symptoms:**
```
Error: invalid bundle format
Missing plan.txt in bundle
```

**Solution:**
1. Re-generate bundle with `EXPLAIN ANALYZE (DEBUG)`
2. Ensure .zip file is complete (not truncated)
3. Check bundle contains required files:
   ```bash
   unzip -l statement.zip
   # Should show: plan.txt, schema.sql, statement.sql
   ```

---

### Database Connection Failed

**Symptoms:**
```
Error: connection timeout to localhost:26257
Could not connect to test database
```

**Solution:**
```bash
# Check CockroachDB is running
cockroach version

# Start if needed
cockroach start-single-node --insecure

# Test connection
psql "postgresql://root@localhost:26257/defaultdb?sslmode=disable"
```

---

## Alternative Models

If the recommended models don't work for your system:

### Smaller Models (Lower RAM)

```bash
# 1B parameters - 2 GB RAM
ollama pull llama3.2:1b

# 3B parameters - 4 GB RAM  
ollama pull llama3.2:3b

# 7B parameters - 6 GB RAM
ollama pull mistral:7b
```

**Trade-off:** Faster but less accurate recommendations

### Different Providers

```bash
# Mistral (alternative to Llama)
ollama pull mistral:7b        # SLM alternative
ollama pull mixtral:8x7b      # LLM alternative

# Gemma (Google)
ollama pull gemma2:9b         # Good mid-range option
```

**Configure model:**
```bash
# Set environment variable
export OLLAMA_MODEL=mistral:7b

# Or use UI dropdown in application
```

---

## Next Steps

Once prerequisites are installed:

1. [Install SQL Tuning Advisor](installation.md)
2. [Learn How It Works](how-it-works.md)
3. [View Full Documentation](index.md)

---

[← Back to Home](index.md) | [Next: Installation →](installation.md)
