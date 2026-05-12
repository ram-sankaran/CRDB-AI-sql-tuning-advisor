# CockroachDB SQL Tuning Advisor

**AI-Powered SQL Query Performance Analysis & Optimization**

[![Latest Release](https://img.shields.io/github/v/release/ram-sankaran/CRDB-AI-sql-tuning-advisor?label=Latest%20Release)](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/ram-sankaran/CRDB-AI-sql-tuning-advisor/total)](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-live-blue)](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)

---

## Download Latest Release (v1.0.0)

Choose your platform:

| Platform | Download | Size | SHA256 |
|----------|----------|------|--------|
| **macOS (Apple Silicon)** | [Download](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip) | 50 MB | [Checksum](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.sha256) |
| **Linux (x86_64)** | [Download](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip) | 90 MB | [Checksum](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.sha256) |
| **Windows (x64)** | [Download](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-windows-x64.zip) | 72 MB | [Checksum](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-windows-x64.sha256) |

**[View All Releases](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)** | **[Documentation](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)** | **[Quick Start Guide](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/installation.html)** | **[Linux Compatibility](LINUX_COMPATIBILITY.md)**

---

## What Is This?

The CockroachDB SQL Tuning Advisor is an intelligent tool that analyzes slow SQL queries from CockroachDB diagnostic bundles and provides actionable optimization recommendations.

**Key capabilities:**

- Automated performance analysis - Detects query bottlenecks, missing indexes, and inefficient patterns
- AI-powered recommendations - Uses local LLM (via Ollama) with RAG to suggest optimizations
- Database replay testing - Validates recommendations in a test database before production deployment
- Comprehensive reports - Generates detailed HTML reports with before/after comparisons

---

## Key Features

### Automated Detection

- Full table scans
- Missing indexes
- Inefficient joins
- Suboptimal index usage
- Cross-region queries

### AI Recommendations

- Optimal index suggestions with DDL
- Query rewrite suggestions
- Schema optimization advice
- Performance estimation (before/after)

### Database Validation

- Tests recommendations against live CockroachDB
- Compares execution plans before and after
- Validates performance improvements
- Generates detailed comparison reports

### Analysis Modes

**SLM Mode (Fast):**
- Model: llama3:8b (8B parameters)
- Speed: 3-5 seconds per query
- RAM: 8 GB required
- Best for: Quick analysis, batch processing, laptops

**LLM Mode (Detailed):**
- Model: llama3.3:70b (70B parameters)
- Speed: 60-90 seconds per query
- RAM: 48 GB required (or GPU)
- Best for: Complex queries, production tuning, detailed explanations

---

## How It Works

```
1. Generate Bundle       2. Upload to UI        3. AI Analysis         4. Get Report
   (EXPLAIN ANALYZE)        (Web interface)        (3-90 seconds)         (Download HTML)
```

**Step-by-step:**

1. **Generate CockroachDB Statement Bundle**
   ```sql
   EXPLAIN ANALYZE (DEBUG) 
   SELECT * FROM orders WHERE customer_id = 100;
   -- Downloads: statement.zip
   ```

2. **Upload to Advisor**
   - Start the advisor
   - Open browser at http://localhost:5050
   - Upload your statement.zip

3. **Get AI-Powered Recommendations**
   - Index suggestions with exact DDL
   - Query rewrites (e.g., OR to UNION ALL)
   - Schema optimization opportunities
   - Performance improvement estimates

4. **Validate & Apply**
   - Optionally test against real database
   - See before/after execution plans
   - Download detailed HTML report

---

## Example Output

**Before Optimization:**
- Full table scan: 10,000 rows scanned
- Execution time: 245ms
- No indexes used

**After Applying Recommendations:**
- Index scan: 1 row scanned
- Execution time: 12ms
- 95% faster

**Recommendation:**
```sql
-- AI Recommendation (Priority: High)
CREATE INDEX ON orders (customer_id, status);

-- Reason: WHERE clause filter causing full table scan
-- Estimated Impact: 99.9% reduction in rows scanned
```

---

## Installation

### Prerequisites

1. **Install Ollama** (local LLM runtime)
   ```bash
   curl -fsSL https://ollama.ai/install.sh | sh
   ```

2. **Download a model**
   ```bash
   # Fast analysis (recommended to start)
   ollama pull llama3:8b
   
   # Or detailed analysis (requires 48GB RAM)
   ollama pull llama3.3:70b
   ```

### Install SQL Tuning Advisor

**macOS / Linux:**
```bash
# Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# Extract and run
unzip sql-tuning-advisor-v1.0.0-darwin-arm64.zip
cd sql-tuning-advisor-v1.0.0
./sql-tuning-advisor-v1.0.0
```

**Windows:**
1. Download latest release from [Releases](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)
2. Extract the ZIP file
3. Run `sql-tuning-advisor-v1.0.0.exe`

**Opens at:** `http://localhost:5050`

---

## Command-Line Usage

```bash
# Default: SLM mode (fast)
./sql-tuning-advisor-v1.0.0

# LLM mode (detailed)
./sql-tuning-advisor-v1.0.0 --mode llm

# Custom port
./sql-tuning-advisor-v1.0.0 --port 8080

# Show help
./sql-tuning-advisor-v1.0.0 --help
```

---

## System Requirements

### Minimum
- **OS:** macOS 10.15+, Windows 10+, Ubuntu 20.04+
- **CPU:** 2 cores
- **RAM:** 8 GB (SLM mode)
- **Disk:** 10 GB free space

### Recommended
- **CPU:** 4+ cores
- **RAM:** 16 GB (SLM), 64 GB (LLM)
- **GPU:** Optional (2x faster for LLM mode)
- **Disk:** SSD for better performance

### Linux Compatibility Matrix

The Linux executable requires **GLIBC 2.31+** (built on Ubuntu 20.04).

| Distribution | Compatible Versions | GLIBC | Status |
|--------------|---------------------|-------|--------|
| **Ubuntu** | 20.04 LTS and newer | 2.31+ | ✅ Works |
| **Debian** | 11 (Bullseye) and newer | 2.31+ | ✅ Works |
| **Fedora** | 34 and newer | 2.33+ | ✅ Works |
| **RHEL** | 9 and newer | 2.34+ | ✅ Works |
| **Rocky Linux** | 9 and newer | 2.34+ | ✅ Works |
| **AlmaLinux** | 9 and newer | 2.34+ | ✅ Works |
| **Amazon Linux** | 2023 | 2.34 | ✅ Works |
| **RHEL / CentOS** | 7, 8 | 2.17-2.28 | ❌ Too old - use [Python source](LINUX_COMPATIBILITY.md) |
| **Amazon Linux** | 2 | 2.26 | ❌ Too old - use [Python source](LINUX_COMPATIBILITY.md) |

Check your GLIBC version: `ldd --version`

---

## Privacy & Security

**100% Local** - All analysis happens on your machine  
**No Cloud Uploads** - Your queries never leave your computer  
**No Telemetry** - No tracking or analytics  
**Offline Capable** - Works without internet connection after setup

---

## Documentation

Full documentation available at: [https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)

- [Prerequisites](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/prerequisites.html) - System requirements, Ollama setup
- [Installation Guide](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/installation.html) - Step-by-step installation

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## License

[MIT License](LICENSE) - Feel free to use this tool in your projects.

---

## Credits

Built with:
- [Ollama](https://ollama.ai/) - Local LLM runtime
- [Flask](https://flask.palletsprojects.com/) - Web framework
- [scikit-learn](https://scikit-learn.org/) - RAG document retrieval
- [CockroachDB](https://www.cockroachlabs.com/) - The best distributed SQL database

---

## Support

- **Documentation:** [https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)
- **Issues:** [GitHub Issues](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/issues)
- **Discussions:** [GitHub Discussions](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/discussions)

---

**If this tool helped optimize your queries, please star the repository!**

[Download Latest Release](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases) • [View Documentation](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/) • [Report Bug](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/issues)
