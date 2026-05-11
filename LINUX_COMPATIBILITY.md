# Linux Compatibility Guide

## GLIBC Version Requirement

The SQL Tuning Advisor Linux executable is built on **Ubuntu 22.04** and requires **GLIBC 2.35 or newer**.

## Compatible Linux Distributions

### ✅ Works Out of the Box

| Distribution | Version | GLIBC | Status |
|--------------|---------|-------|--------|
| **Ubuntu** | 22.04 LTS+ | 2.35 | ✅ Compatible |
| **Ubuntu** | 24.04 LTS | 2.39 | ✅ Compatible |
| **Debian** | 12 (Bookworm) | 2.36 | ✅ Compatible |
| **Fedora** | 36+ | 2.35+ | ✅ Compatible |
| **RHEL** | 9+ | 2.34+ | ⚠️ May work* |
| **Rocky Linux** | 9+ | 2.34+ | ⚠️ May work* |
| **AlmaLinux** | 9+ | 2.34+ | ⚠️ May work* |

*RHEL 9 has GLIBC 2.34, which is close but may have compatibility issues.

### ❌ NOT Compatible (GLIBC too old)

| Distribution | Version | GLIBC | Status |
|--------------|---------|-------|--------|
| **Ubuntu** | 20.04 LTS | 2.31 | ❌ Too old |
| **Debian** | 11 (Bullseye) | 2.31 | ❌ Too old |
| **RHEL** | 8 | 2.28 | ❌ Too old |
| **CentOS** | 7 | 2.17 | ❌ Too old |
| **Rocky Linux** | 8 | 2.28 | ❌ Too old |
| **AlmaLinux** | 8 | 2.28 | ❌ Too old |

## Check Your GLIBC Version

```bash
ldd --version
# Output should show: ldd (GNU libc) 2.35 or higher
```

Or:

```bash
/lib/x86_64-linux-gnu/libc.so.6
# Look for version in output
```

## Workarounds for Older Distributions

### Option 1: Run from Python Source (Recommended)

Works on **any Linux distribution** with Python 3.8+:

```bash
# Install Python 3.8+ (if not already installed)
sudo yum install python3 python3-pip  # RHEL/CentOS
# or
sudo apt install python3 python3-pip  # Ubuntu/Debian

# Clone repository
git clone https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor.git
cd CRDB-AI-sql-tuning-advisor

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llama3:8b

# Run the advisor
cd code/
python3 analyze_sql_bundle_claude_RAG.py --mode slm --port 5050
```

### Option 2: Use Docker

```bash
# Coming soon - Docker image will work on any Linux
```

### Option 3: Build on Your Distribution

Build the executable on your specific Linux distribution:

```bash
# On your target machine (e.g., RHEL 8)
git clone https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor.git
cd CRDB-AI-sql-tuning-advisor

# Install build dependencies
python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller

# Run the build script
./build_executable.sh 1.0.0

# Your executable will be in dist/
```

## Why GLIBC Matters

**GLIBC (GNU C Library)** is the core system library on Linux. When PyInstaller creates an executable:

1. It bundles Python and Python libraries ✅
2. But it **dynamically links** to system GLIBC ❌
3. The executable requires GLIBC >= version it was built with

**Our executable:**
- Built on Ubuntu 22.04 (GLIBC 2.35)
- Won't run on systems with GLIBC < 2.35

## Distribution-Specific Notes

### Red Hat Enterprise Linux (RHEL)

**RHEL 9:**
```bash
# Check version
cat /etc/redhat-release
# Should show: Red Hat Enterprise Linux release 9.x

# Check GLIBC
ldd --version
# Should show: 2.34 (may work, but not guaranteed)
```

**RHEL 8:** Use Python source method (see Option 1 above)

### CentOS / Rocky / AlmaLinux

**Version 9:** Should work (GLIBC 2.34)  
**Version 8:** Use Python source method  
**Version 7:** Use Python source method (very old)

### Amazon Linux

**Amazon Linux 2023:** GLIBC 2.34 (may work)  
**Amazon Linux 2:** GLIBC 2.26 ❌ Use Python source

## Quick Compatibility Check

Run this on your Linux system:

```bash
# Download and try to run
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip
unzip sql-tuning-advisor-v1.0.0-linux-x86_64.zip
cd sql-tuning-advisor-v1.0.0-linux-x86_64
./sql-tuning-advisor-v1.0.0 --help

# If you see the help message: ✅ Compatible!
# If you see GLIBC error: ❌ Use Python source instead
```

## Recommended Solution for Enterprise

For **enterprise deployments** on RHEL/CentOS/Rocky/AlmaLinux:

**Use Python source installation (Option 1)**
- More portable across distributions
- Easier to customize
- No GLIBC compatibility issues
- Same functionality as executable

## Support

Having compatibility issues? Open an issue with your distribution info:

```bash
# Include this info in your issue:
cat /etc/os-release
ldd --version
python3 --version
```

GitHub Issues: https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/issues
