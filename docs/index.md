---
layout: default
title: Home
nav_order: 1
---

# CockroachDB SQL Tuning Advisor

**AI-Powered Query Analysis and Optimization for CockroachDB**

Transform slow queries into fast ones with intelligent recommendations powered by local LLMs.

[Download Latest Release](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View on GitHub](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor){: .btn .fs-5 .mb-4 .mb-md-0 }

---

## What is SQL Tuning Advisor?

SQL Tuning Advisor is a standalone desktop application that analyzes CockroachDB query bundles and provides actionable optimization recommendations. It combines rule-based analysis with AI-powered insights from local language models.

### Key Features

**AI-Powered Analysis** - Uses local LLMs (via Ollama) for intelligent recommendations  
**Database Validation** - Tests recommendations against real CockroachDB instances  
**Before/After Comparison** - Shows exact performance improvements  
**Detailed Reports** - Generates comprehensive HTML reports  
**Two Analysis Modes** - Fast (SLM) and detailed (LLM) analysis  
**Offline Capable** - Works without internet once installed  
**No Data Upload** - All analysis happens locally on your machine  

---

## How It Works

```
1. Upload Bundle → 2. AI Analysis → 3. Validation → 4. Report
   (.zip file)       (3-90 seconds)    (Optional)    (Download)
```

### Step-by-Step Process

**1. Generate Query Bundle**
```sql
-- In CockroachDB SQL shell
EXPLAIN ANALYZE (DEBUG) SELECT * FROM users WHERE email = 'test@example.com';
-- Downloads: statement.zip
```

**2. Upload to Advisor**
- Start the advisor: `./sql-tuning-advisor-v1.0.0`
- Open web interface: `http://localhost:5050`
- Upload your `statement.zip` bundle

**3. Get Recommendations**
- Index suggestions with exact DDL
- Query rewrites (e.g., OR → UNION)
- Schema change recommendations
- Covering index opportunities

**4. Validate & Apply**
- Automatically tests recommendations
- Shows before/after execution plans
- Displays performance improvements
- Download detailed HTML report

---

## What Problems Does It Solve?

### Problem: Slow Queries

**Before:**
```
• Full table scan: 1,000,000 rows scanned
• Execution time: 2,450ms
• No indexes used
```

**After:**
```
• Index scan: 1 row scanned
• Execution time: 12ms
• 99.5% faster
```

### Common Issues Detected

| Issue | Detection | Recommendation |
|-------|-----------|----------------|
| Full table scans | Analyzes execution plans | Create indexes on filter columns |
| Missing JOIN indexes | Detects hash/merge joins | Index foreign key columns |
| Inefficient OR queries | Pattern matching | Rewrite as UNION ALL |
| SELECT * overhead | SQL parsing | Specify explicit columns |
| Outdated statistics | Plan discrepancies | Run ANALYZE |
| Poor index choices | Index usage analysis | Create covering indexes |

---

## Quick Start

### Prerequisites

1. **Ollama** - Local LLM runtime
   ```bash
   # Install Ollama
   curl -fsSL https://ollama.ai/install.sh | sh
   
   # Download model (choose one)
   ollama pull llama3:8b          # Fast (SLM mode)
   ollama pull llama3.3:70b       # Detailed (LLM mode)
   ```

2. **CockroachDB Bundle** - Query diagnostic file
   - Generated from `EXPLAIN ANALYZE (DEBUG)`
   - Or exported from CockroachDB Cloud Console

3. **Optional: CockroachDB Instance** - For validation testing
   - Local development cluster
   - Or remote test database

### Installation

**macOS (Apple Silicon)**
```bash
# Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-darwin-arm64.zip

# Extract and run
unzip sql-tuning-advisor-v1.0.0-darwin-arm64.zip
cd sql-tuning-advisor-v1.0.0-darwin-arm64
./sql-tuning-advisor-v1.0.0
```

**Linux (Ubuntu 22.04+)**
```bash
# Download latest release
curl -LO https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases/download/v1.0.0/sql-tuning-advisor-v1.0.0-linux-x86_64.zip

# Extract and run
unzip sql-tuning-advisor-v1.0.0-linux-x86_64.zip
cd sql-tuning-advisor-v1.0.0-linux-x86_64
./sql-tuning-advisor-v1.0.0
```

**Windows (x64)**
```cmd
REM Download from GitHub releases
REM Extract ZIP file
REM Run executable
sql-tuning-advisor-v1.0.0.exe
```

### Usage

**SLM Mode (Fast, 3-5 seconds per query)**
```bash
./sql-tuning-advisor-v1.0.0
# Opens at http://localhost:5050
```

**LLM Mode (Detailed, 60-90 seconds per query)**
```bash
./sql-tuning-advisor-v1.0.0 --mode llm
# Opens at http://localhost:5051
```

**Custom Port**
```bash
./sql-tuning-advisor-v1.0.0 --port 8080
```

---

## Analysis Modes

### SLM Mode (Small Language Model)

**Best for:**
- Quick analysis and iterative tuning
- Laptops and systems without GPU
- Batch processing multiple queries
- CI/CD pipeline integration

**Characteristics:**
- Model: llama3:8b (8B parameters)
- Speed: 3-5 seconds per query
- RAM: 8 GB required
- Quality: Good, practical recommendations

### LLM Mode (Large Language Model)

**Best for:**
- Complex multi-table queries
- Production performance tuning
- Detailed explanations and documentation
- Root cause analysis

**Characteristics:**
- Model: llama3.3:70b (70B parameters)
- Speed: 60-90 seconds per query
- RAM: 48 GB required (or GPU)
- Quality: Excellent, comprehensive analysis

---

## Example Output

### Index Recommendations
```sql
-- Recommendation 1 (Priority: High)
CREATE INDEX ON orders (customer_id, status);

-- Reason:
-- WHERE clause filter causing full table scan
-- Estimated impact: 99.9% reduction in rows scanned

-- Before: 10,000 rows scanned, 245ms
-- After:  1 row scanned, 12ms (95% faster)
```

### Query Rewrites
```sql
-- Original (slow)
SELECT * FROM products 
WHERE status = 'active' OR priority = 'high';

-- Optimized (fast)
SELECT product_id, name, price FROM products WHERE status = 'active'
UNION ALL
SELECT product_id, name, price FROM products WHERE priority = 'high' AND status != 'active';

-- Benefit: Each UNION branch uses separate index
```

---

## System Requirements

### Minimum
- **CPU:** 2 cores
- **RAM:** 8 GB (SLM mode)
- **Disk:** 500 MB for application + 5 GB for LLM models
- **OS:** macOS 10.15+, Windows 10+, Linux (Ubuntu 20.04+)

### Recommended
- **CPU:** 4+ cores
- **RAM:** 16 GB (SLM), 64 GB (LLM)
- **GPU:** Optional (2x faster for LLM mode)
- **Disk:** SSD for better performance

---

## Privacy & Security

**100% Local** - All analysis happens on your machine  
**No Cloud Uploads** - Your queries never leave your computer  
**No Telemetry** - No tracking or analytics  
**Open Source** - Inspect the code yourself  
**Offline Capable** - Works without internet connection  

---

## Support

- **Installation:** [Installation Guide](installation.md)
- **Prerequisites:** [Prerequisites](prerequisites.md)
- **How It Works:** [Architecture Details](how-it-works.md)
- **Issues:** [GitHub Issues](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/issues)
- **Discussions:** [GitHub Discussions](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/discussions)

---

## License

[MIT License](https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/blob/main/LICENSE)

---

## Credits

Built with:
- [Ollama](https://ollama.ai/) - Local LLM runtime
- [Flask](https://flask.palletsprojects.com/) - Web framework
- [scikit-learn](https://scikit-learn.org/) - RAG document retrieval
- [CockroachDB](https://www.cockroachlabs.com/) - The best distributed SQL database

---

<div style="text-align: center; margin-top: 40px; padding: 20px; background-color: #f0f0f0; border-radius: 8px;">
  <h3>Ready to optimize your queries?</h3>
  <a href="https://github.com/ram-sankaran/CRDB-AI-sql-tuning-advisor/releases" class="btn btn-primary" style="margin: 10px;">Download Now</a>
  <a href="installation.md" class="btn" style="margin: 10px;">Installation Guide</a>
  <a href="prerequisites.md" class="btn" style="margin: 10px;">Prerequisites</a>
</div>
