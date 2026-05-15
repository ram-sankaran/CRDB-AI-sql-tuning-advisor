# CockroachDB SQL Tuning Advisor

**AI-Powered SQL Query Performance Analysis & Optimization**

[![Latest Release](https://img.shields.io/github/v/release/ram-sankaran/CRDB-AI-sql-tuning-advisor?label=Latest%20Release)](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/ram-sankaran/CRDB-AI-sql-tuning-advisor/total?label=Downloads)](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)
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

**[View All Releases](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases)** | **[Documentation](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)** | **[Quick Start Guide](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/installation.html)**

---

## AI powered SQL Tuning Advisor with DB Replay

The CockroachDB SQL Tuning Advisor is an intelligent tool that analyzes multiple SQLs from CockroachDB diagnostic bundles and provides actionable optimization recommendations.

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

### Available Models

**llama3.1:8b (Default - Recommended):**
- Speed: 3-5 seconds per query
- RAM: 8 GB required
- Best for: Quick analysis, batch processing, laptops, most use cases

**llama3.3:70b (Advanced):**
- Speed: 60-90 seconds per query
- RAM: 48 GB required (or GPU)
- Best for: Complex queries, production tuning, detailed explanations

**Other Supported Models:**
- mistral:7b - Fast alternative to llama3.1:8b
- Any Ollama-compatible model

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
   - Optimal index suggestions with DDL
   - Query rewrite suggestions
   - Schema optimization advice
   - Performance estimation (before/after)

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

## Prerequisites

### 1. Ollama (Local LLM Runtime)

**Install Ollama:**

```bash
# macOS / Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Or download from: https://ollama.ai/download
```

**Download a model:**
```bash
# Recommended: Fast and accurate (requires 8 GB RAM)
ollama pull llama3.1:8b

# Optional: Advanced model (requires 48GB RAM or GPU)
ollama pull llama3.3:70b
```

**Start Ollama:**
```bash
ollama serve
# Or start the Ollama desktop app
```

**Verify installation:**
```bash
ollama list
# Should show downloaded models
```

---

### 2. CockroachDB Query Bundle

**What is a query bundle?**

A statement bundle is a .zip file containing your SQL query, execution plan, table schemas, and performance metrics.

**How to generate a bundle:**

**Method 1: SQL Shell**
```sql
-- Connect to your database
cockroach sql --url "postgresql://user@host:26257/database"

-- Generate bundle for your query
EXPLAIN ANALYZE (DEBUG) 
SELECT * FROM orders WHERE customer_id = 100;

-- Downloads: statement.zip
```

**Method 2: CockroachDB Cloud Console**
1. Go to **SQL Activity → Statements**
2. Find your slow query
3. Click **"Diagnostics"**
4. Download statement bundle

**Method 3: DB Console (Self-Hosted)**
1. Open DB Console: `http://localhost:8080`
2. Navigate to **SQL Activity**
3. Click on slow statement
4. Click **"Download Bundle"**

---

### 3. Optional: CockroachDB Test Instance

For database replay testing (validates recommendations before production):

**Option A: Local Development Cluster**
```bash
cockroach start-single-node \
  --insecure \
  --listen-addr=localhost:26257 \
  --http-addr=localhost:8080

# Create test database
cockroach sql --insecure -e "CREATE DATABASE testdb;"
```

**Option B: CockroachDB Cloud Free Tier**
- Sign up at [https://cockroachlabs.cloud](https://cockroachlabs.cloud)
- Create free cluster
- Use connection string for testing

---

## Installation

### Install SQL Tuning Advisor

**macOS (Apple Silicon - M1/M2/M3/M4):**
```bash
# Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# Extract and run
unzip sql-tuning-advisor-v1.0.0-darwin-arm64.zip
cd sql-tuning-advisor-v1.0.0
./sql-tuning-advisor-v1.0.0
```

> **macOS Security Note:** If blocked with *"cannot be opened because the developer cannot be verified"*:
> 1. Try to run the app (it will be blocked)
> 2. Open **System Settings > Privacy & Security**
> 3. Scroll to **Security** section
> 4. Click **"Allow Anyway"** next to the blocked app message
> 5. Run the app again and click **"Open"** to confirm

**Linux (Ubuntu 22.04+, Debian 12+, Fedora 36+):**
```bash
# Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip

# Extract and run
unzip sql-tuning-advisor-v1.0.0-linux-x86_64.zip
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
# Default: Uses llama3.1:8b
./sql-tuning-advisor-v1.0.0

# Use specific model
./sql-tuning-advisor-v1.0.0 --model llama3.3:70b

# Custom port
./sql-tuning-advisor-v1.0.0 --port 8080

# Show help
./sql-tuning-advisor-v1.0.0 --help
```

**Model Selection:**
- Use `--model` flag to specify which Ollama model to use
- Or select model from the web UI after starting
- Default model: `llama3.1:8b` (can be overridden with `OLLAMA_MODEL` env var)

---

## System Requirements

### Minimum
- **OS:** macOS 10.15+, Windows 10+, Ubuntu 20.04+
- **CPU:** 2 cores
- **RAM:** 8 GB (for llama3.1:8b)
- **Disk:** 10 GB free space

### Recommended
- **CPU:** 4+ cores
- **RAM:** 16 GB (llama3.1:8b), 64 GB (llama3.3:70b)
- **GPU:** Optional (2x faster for llama3.3:70b)
- **Disk:** SSD for better performance

### Linux Compatibility

The Linux executable requires **GLIBC 2.35+** and supports the following distributions:

| Distribution | Supported Versions |
|--------------|-------------------|
| **Ubuntu** | 22.04 LTS and newer |
| **Debian** | 12 (Bookworm) and newer |
| **Fedora** | 36 and newer |

To check your GLIBC version: `ldd --version`

---

## Troubleshooting

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
Error: model "llama3.1:8b" not found
```

**Solution:**
```bash
# List available models
ollama list

# Pull missing model
ollama pull llama3.1:8b

# Verify download
ollama list
# Should show llama3.1:8b with size ~4.9 GB
```

---

### Insufficient RAM

**Symptoms:**
```
Error: model failed to load
Out of memory
```

**Solution:**

For **llama3.1:8b** (recommended):
- Close other applications
- Requires minimum 8 GB RAM
- Alternative: Use smaller model `ollama pull llama3.2:1b` (2 GB)

For **llama3.3:70b** (advanced):
- Requires 48 GB RAM or GPU with 24GB+ VRAM
- Alternative: Use llama3.1:8b instead

---

### Invalid Bundle Format

**Symptoms:**
```
Error: invalid bundle format
Missing plan.txt in bundle
```

**Solution:**
1. Re-generate bundle with `EXPLAIN ANALYZE (DEBUG)`
2. Ensure .zip file is complete (not truncated)
3. Verify bundle contains required files:
   ```bash
   unzip -l statement.zip
   # Should show: plan.txt, schema.sql, statement.sql
   ```

---

### macOS Security Block

**Symptoms:**
```
"sql-tuning-advisor cannot be opened because the developer cannot be verified"
```

**Solution:**
1. Try to run the app (it will be blocked)
2. Open **System Settings > Privacy & Security**
3. Scroll to **Security** section
4. Click **"Allow Anyway"** next to the blocked app message
5. Run the app again and click **"Open"** to confirm

---

## Privacy & Security

**100% Local** - All analysis happens on your machine  
**No Cloud Uploads** - Your queries never leave your computer  
**No Telemetry** - No tracking or analytics  
**Offline Capable** - Works without internet connection after setup

---

## Additional Documentation

Additional resources available at: [https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/](https://ram-sankaran.github.io/CRDB-AI-sql-tuning-advisor/)

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
