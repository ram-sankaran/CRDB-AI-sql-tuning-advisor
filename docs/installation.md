---
layout: default
title: Installation
nav_order: 4
---

# Installation Guide

Step-by-step installation instructions for all platforms.

---

## Quick Install

### macOS / Linux

```bash
# 1. Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# 2. Extract
unzip sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# 3. Navigate to folder
cd sql-tuning-advisor-v1.0.0

# 4. Run
./sql-tuning-advisor-v1.0.0
```

**Opens at:** `http://localhost:5050`

---

### Windows

1. **Download** latest release:
   - Go to [Releases](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)
   - Download `sql-tuning-advisor-v1.0.0-windows-x64.zip`

2. **Extract** the ZIP file
   - Right-click → Extract All
   - Or use 7-Zip, WinRAR, etc.

3. **Run** the executable
   - Double-click `sql-tuning-advisor-v1.0.0.exe`
   - Or from PowerShell:
     ```powershell
     .\sql-tuning-advisor-v1.0.0.exe
     ```

**Opens at:** `http://localhost:5050`

---

## Detailed Installation

### Step 1: Install Prerequisites

**Before installing SQL Tuning Advisor**, you must have:

1. **Ollama installed** → [Prerequisites: Ollama](./prerequisites.md#1-ollama-local-llm-runtime)
2. **Language model downloaded** → [Prerequisites: Models](./prerequisites.md#2-language-model)

**Quick prerequisite check:**
```bash
# Verify Ollama is installed
ollama --version

# Verify Ollama is running
curl http://localhost:11434/api/tags

# Verify model is downloaded
ollama list | grep llama3
```

---

### Step 2: Download SQL Tuning Advisor

**Choose your platform:**

<details>
<summary><strong>macOS (Apple Silicon / M1/M2/M3/M4)</strong></summary>

```bash
# Download
wget https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# Or with curl
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip
```

**File:** `sql-tuning-advisor-v1.0.0-darwin-arm64.zip` (49 MB)

**Note:** Intel Mac users can use Rosetta 2 to run the ARM64 version.
</details>

<details>
<summary><strong>Linux (x86_64)</strong></summary>

```bash
# Download
wget https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip

# Or with curl
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip
```

**File:** `sql-tuning-advisor-v1.0.0-linux-x86_64.zip` (51 MB)
</details>

<details>
<summary><strong>Windows (x64)</strong></summary>

**Option A: Direct download**
1. Visit: [Releases Page](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)
2. Download: `sql-tuning-advisor-v1.0.0-windows-x64.zip`

**Option B: PowerShell**
```powershell
# Download
Invoke-WebRequest -Uri "https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-windows-x64.zip" -OutFile "sql-tuning-advisor-v1.0.0-windows-x64.zip"
```

**File:** `sql-tuning-advisor-v1.0.0-windows-x64.zip` (48 MB)
</details>

---

### Step 3: Verify Download (Optional)

**Verify file integrity with SHA256 checksum:**

**macOS / Linux:**
```bash
# Download checksum file
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.sha256

# Verify
shasum -a 256 -c sql-tuning-advisor-v1.0.0-darwin-arm64.sha256
# Should output: sql-tuning-advisor-v1.0.0-darwin-arm64.zip: OK
```

**Windows:**
```powershell
# Get file hash
Get-FileHash sql-tuning-advisor-v1.0.0-windows-x64.zip -Algorithm SHA256

# Compare with published checksum at:
# https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases
```

---

### Step 4: Extract Archive

**macOS / Linux:**
```bash
# Extract ZIP
unzip sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# Contents:
# sql-tuning-advisor-v1.0.0/
#   ├── sql-tuning-advisor-v1.0.0
#   ├── README.md
#   ├── QUICK_START.txt
#   └── VERSION.txt
```

**Windows:**
```powershell
# Extract with PowerShell
Expand-Archive sql-tuning-advisor-v1.0.0-windows-x64.zip

# Or right-click → Extract All
```

---

### Step 5: Run the Application

**macOS / Linux:**
```bash
# Navigate to extracted folder
cd sql-tuning-advisor-v1.0.0

# Make executable (if needed)
chmod +x sql-tuning-advisor-v1.0.0

# Run application
./sql-tuning-advisor-v1.0.0
```

**Windows:**
```powershell
# Navigate to extracted folder
cd sql-tuning-advisor-v1.0.0

# Run executable
.\sql-tuning-advisor-v1.0.0.exe
```

**Expected output:**
```
[INFO] Starting SQL Tuning Advisor v1.0.0
[INFO] Mode: SLM (Fast Analysis)
[INFO] Ollama URL: http://localhost:11434
[INFO] Server starting on http://127.0.0.1:5050
[INFO] Press Ctrl+C to stop
```

---

### Step 6: Open Web Interface

**Open your browser and navigate to:**

```
http://localhost:5050
```

**You should see:**
- SQL Tuning Advisor welcome page
- File upload area for statement bundles
- Configuration options

---

## Command-Line Options

### Basic Usage

```bash
# Default: SLM mode on port 5050
./sql-tuning-advisor-v1.0.0

# LLM mode on port 5051
./sql-tuning-advisor-v1.0.0 --mode llm

# Custom port
./sql-tuning-advisor-v1.0.0 --port 8080

# Debug mode
./sql-tuning-advisor-v1.0.0 --debug

# Show help
./sql-tuning-advisor-v1.0.0 --help
```

### All Options

| Option | Description | Default |
|--------|-------------|---------|
| `--mode slm` | Fast analysis mode | `slm` |
| `--mode llm` | Detailed analysis mode | - |
| `--port PORT` | Server port number | `5050` (SLM), `5051` (LLM) |
| `--debug` | Enable debug logging | `false` |
| `--help` | Show help message | - |
| `--version` | Show version info | - |

---

## Environment Variables

Override defaults with environment variables:

```bash
# Use custom Ollama model
export OLLAMA_MODEL=mistral:7b
./sql-tuning-advisor-v1.0.0

# Use remote Ollama server
export OLLAMA_URL=http://192.168.1.100:11434/api/generate
./sql-tuning-advisor-v1.0.0

# Custom database connection
export CRDB_CONN_STR="postgresql://user@host:26257/db"
./sql-tuning-advisor-v1.0.0
```

**Available variables:**

| Variable | Purpose | Example |
|----------|---------|---------|
| `OLLAMA_MODEL` | Override default model | `llama3:8b` |
| `OLLAMA_URL` | Ollama API endpoint | `http://localhost:11434/api/generate` |
| `CRDB_CONN_STR` | Database connection string | `postgresql://root@localhost:26257/defaultdb` |
| `HTTP_TIMEOUT` | Request timeout (seconds) | `30` |

---

## Installation Locations

### Recommended Installation Paths

**macOS / Linux:**
```bash
# System-wide (requires sudo)
sudo mv sql-tuning-advisor-v1.0.0 /usr/local/bin/

# User-local
mkdir -p ~/bin
mv sql-tuning-advisor-v1.0.0 ~/bin/

# Application folder
mkdir -p ~/Applications/SQLTuningAdvisor
mv sql-tuning-advisor-v1.0.0 ~/Applications/SQLTuningAdvisor/
```

**Windows:**
```powershell
# Program Files (requires admin)
Move-Item sql-tuning-advisor-v1.0.0 "C:\Program Files\SQLTuningAdvisor\"

# User folder
Move-Item sql-tuning-advisor-v1.0.0 "$env:USERPROFILE\Applications\SQLTuningAdvisor\"
```

---

## Uninstallation

**macOS / Linux:**
```bash
# Remove executable
rm /usr/local/bin/sql-tuning-advisor-v1.0.0
# or
rm ~/bin/sql-tuning-advisor-v1.0.0

# Remove Ollama models (optional)
ollama rm llama3:8b
ollama rm llama3.3:70b
```

**Windows:**
```powershell
# Delete folder
Remove-Item -Recurse "C:\Program Files\SQLTuningAdvisor\"

# Uninstall Ollama (optional)
# Control Panel → Programs → Uninstall Ollama
```

---

## Troubleshooting Installation

### "Permission Denied" Error

**macOS / Linux:**
```bash
# Make executable
chmod +x sql-tuning-advisor-v1.0.0

# Then run
./sql-tuning-advisor-v1.0.0
```

---

### macOS "App Cannot Be Opened" Warning

**Solution:**
```bash
# Remove quarantine attribute
xattr -d com.apple.quarantine sql-tuning-advisor-v1.0.0

# Or go to: System Preferences → Security & Privacy → Allow
```

---

### Windows SmartScreen Warning

**Solution:**
1. Click "More info"
2. Click "Run anyway"

Or disable Windows SmartScreen (not recommended).

---

### Port Already in Use

**Symptoms:**
```
Error: Address already in use: http://127.0.0.1:5050
```

**Solution:**
```bash
# Use different port
./sql-tuning-advisor-v1.0.0 --port 8080

# Or kill process using port 5050
# macOS/Linux:
lsof -ti:5050 | xargs kill

# Windows:
netstat -ano | findstr :5050
taskkill /PID <PID> /F
```

---

### Cannot Connect to Ollama

**Symptoms:**
```
Error: Could not connect to Ollama at http://localhost:11434
```

**Solution:**
```bash
# Start Ollama
ollama serve

# Or check if it's running
curl http://localhost:11434/api/tags
```

See [Prerequisites](./prerequisites.md) for more Ollama troubleshooting.

---

## Next Steps

After successful installation:

1. **Generate a query bundle** → [Usage Guide](./usage.md)
2. **Upload and analyze** → [Web Interface](./usage.html#web-interface)
3. **Review recommendations** → [Understanding Reports](./reports.md)

---

[← Back to Prerequisites](./prerequisites.md) | [Next: Usage Guide →](./usage.md)
