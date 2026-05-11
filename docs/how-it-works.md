---
layout: default
title: How It Works
nav_order: 2
---

# How SQL Tuning Advisor Works

Deep dive into the architecture and analysis process.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Interface (Flask)                     │
│                   http://localhost:5050                      │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                   Bundle Upload & Parsing                    │
│  • Extract .zip file                                        │
│  • Parse SQL, execution plan, schema                        │
│  • Extract performance metrics                              │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                  Dual-Path Analysis Engine                   │
│                                                              │
│  ┌──────────────────┐            ┌──────────────────┐      │
│  │  Rule-Based      │            │   LLM-Based      │      │
│  │  Analysis        │            │   Analysis       │      │
│  │                  │            │                  │      │
│  │ • Pattern Match  │            │ • RAG Retrieval  │      │
│  │ • Score Issues   │            │ • Prompt Build   │      │
│  │ • Fast (10ms)    │            │ • Ollama Call    │      │
│  └──────────────────┘            └──────────────────┘      │
│           │                               │                 │
│           └───────────┬───────────────────┘                 │
│                       ▼                                     │
│            ┌──────────────────────┐                        │
│            │   Merge & Validate   │                        │
│            └──────────────────────┘                        │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              Database Validation (Optional)                  │
│  • Connect to test CockroachDB instance                     │
│  • Apply recommended indexes                                │
│  • Run EXPLAIN ANALYZE with changes                         │
│  • Compare before/after metrics                             │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    HTML Report Generation                    │
│  • Side-by-side plan comparison                             │
│  • Performance improvement metrics                           │
│  • Detailed recommendations with DDL                         │
│  • AI narrative explanation                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Process

### 1. Bundle Parsing

**Input:** CockroachDB statement bundle (.zip file)

**What happens:**
```python
# Extract files from bundle
statement.sql      → Original SQL query
plan.txt          → EXPLAIN ANALYZE output
schema.sql        → Table definitions
env.sql           → Session settings
trace.json        → Execution trace (optional)
stats-*.sql       → Table statistics
```

**Extracted data:**
- SQL text and structure
- Execution metrics (rows, latency)
- Table schemas and existing indexes
- Query plan operators (scan, filter, join)

**Example:**
```
Parsed:
  SQL: SELECT * FROM orders WHERE customer_id = 100
  Scan Rows: 10,000
  Filter Rows: 1
  Latency: 245ms
  Table: orders
  Existing Indexes: orders_pkey
```

---

### 2. Rule-Based Analysis

**Fast deterministic pattern matching (completes in ~10ms)**

#### Pattern Detection

**Full Table Scans:**
```python
if "FULL SCAN" in plan and scan_rows > 1000:
    score = 80  # Critical
    recommend: CREATE INDEX ON table(filter_columns)
```

**Missing JOIN Indexes:**
```python
if "hash join" in plan and join_column not in indexes:
    score = 70  # High
    recommend: CREATE INDEX ON table(join_column)
```

**OR → UNION Opportunities:**
```python
if "OR" in sql and full_scan and selectivity < 10%:
    score = 75  # High
    recommend: Rewrite OR as UNION ALL
```

**SELECT * Warnings:**
```python
if "SELECT *" in sql:
    score = 40  # Medium
    recommend: Specify explicit columns
```

#### Scoring System

| Score | Severity | Typical Issue |
|-------|----------|---------------|
| 80-100 | Critical | Full scans on large tables |
| 60-79 | High | Missing indexes, inefficient joins |
| 40-59 | Medium | Suboptimal patterns |
| 0-39 | Low | Minor optimizations |

---

### 3. RAG (Retrieval Augmented Generation)

**Provides context to the LLM from CockroachDB documentation**

#### How RAG Works

**1. Index Building (one-time):**
```python
# Load CockroachDB query tuning playbook
docs = load_playbook("Query_tuning_playbook_v1.txt")

# Chunk into smaller pieces
chunks = split_into_chunks(docs, max_size=1000)

# Create TF-IDF vectors
index = build_tfidf_index(chunks)
```

**2. Query-Time Retrieval:**
```python
# Build search query from SQL analysis
query = "full table scan OR predicate index optimization"

# Retrieve top 10 most relevant chunks
context = index.retrieve(query, top_k=10)

# Returns:
[
  "FULL SCAN + SELECTIVE FILTER → recommend index...",
  "Rewrite OR to UNION ALL (for indexed columns)...",
  "Example: CREATE INDEX ON users(email) ...",
  ...
]
```

**3. Context Injection:**
```python
prompt = f"""
MANDATORY RULES:
{rag_context}

SQL Query: {sql}
Execution Plan: {plan}

Provide recommendations...
"""
```

#### TF-IDF Similarity

**Term Frequency-Inverse Document Frequency:**
```
score = (term frequency) × log(total docs / docs with term)
```

Higher scores mean:
- Term appears frequently in this document
- Term is rare across all documents
- Therefore, it's an important distinctive term

---

### 4. LLM Analysis

**Intelligent reasoning powered by local language models**

#### Prompt Engineering

**Structured prompt with:**
1. **Mandatory rules** from RAG (CockroachDB best practices)
2. **Execution plan** with highlighted issues
3. **SQL query** with context
4. **Schema** with existing indexes
5. **Helper metrics** (selectivity, row counts)
6. **Output format** (strict JSON schema)

**Example prompt structure:**
```
╔══════════════════════════════════════════════════════════════╗
║  MANDATORY RULES - YOU MUST FOLLOW THESE RULES              ║
╚══════════════════════════════════════════════════════════════╝

RULE #1: FULL SCAN + SELECTIVE FILTER
If execution plan shows:
- FULL SCAN
- selective equality predicate (< 10% rows returned)
→ Recommend: CREATE INDEX ON table(filter_columns)

RULE #2: OR → UNION REWRITE
If query has:
- OR predicate in WHERE clause
- Full table scan
- Low selectivity
→ Recommend: Rewrite as UNION ALL

[... more rules from RAG ...]

═══════════════════════════════════════════════════════════════
EXECUTION PLAN:
═══════════════════════════════════════════════════════════════
execution time: 245ms
rows decoded: 10,000

• filter
│ actual row count: 1           ← Only 1 row matched!
│ filter: customer_id = 100
│
└── • scan
      actual row count: 10,000  ← Scanned all rows!
      table: orders@orders_pkey
      spans: FULL SCAN           ← Problem detected!

═══════════════════════════════════════════════════════════════
SQL QUERY:
═══════════════════════════════════════════════════════════════
SELECT * FROM orders WHERE customer_id = 100

Return recommendations in JSON format...
```

#### LLM Response Processing

**Expected JSON output:**
```json
{
  "primary_bottleneck": "Full table scan on orders table",
  "candidate_indexes": [{
    "ddl": "CREATE INDEX ON orders (customer_id);",
    "reason": "WHERE clause filter causing full scan",
    "estimated_impact": "99.99% reduction in rows scanned"
  }],
  "candidate_rewrites": [...],
  "narrative": {
    "query_summary": "Retrieves single order by customer ID",
    "execution_plan_summary": "Full table scan with post-filter",
    "final_verdict": "Create index on customer_id column",
    "bottom_line": "Index will eliminate 99.99% of unnecessary scans"
  }
}
```

**Validation:**
- Check JSON is valid
- Verify required fields present
- Ensure DDL is executable SQL
- Fallback to rule-based if LLM fails

---

### 5. Database Validation

**Test recommendations against real CockroachDB**

#### Seed Data Generation

**Mimics bundle selectivity exactly:**

```python
# From bundle
scan_rows = 10,000
filter_rows = 1
selectivity = 1 / 10,000 = 0.01%

# Generate seed data with same selectivity
INSERT INTO orders (customer_id, ...)
SELECT 
  CASE 
    WHEN (i % 10000) < 1 THEN 100      -- 0.01% match
    ELSE 99999                          -- 99.99% don't match
  END,
  ...
FROM generate_series(1, 10000) AS i;
```

**Result:** Exact same row distribution as production!

#### Baseline Plan

```sql
-- Run original query without new indexes
EXPLAIN ANALYZE 
SELECT * FROM orders WHERE customer_id = 100;
```

**Captures:**
```
Baseline:
  Execution time: 5ms
  Rows scanned: 10,000
  Rows returned: 1
  Access method: Full scan
```

#### Apply Recommendations

```sql
-- Create recommended index
CREATE INDEX orders_customer_id_idx ON orders (customer_id);

-- Analyze for fresh stats
ANALYZE orders;
```

#### Post-Change Plan

```sql
-- Run query with new index
EXPLAIN ANALYZE 
SELECT * FROM orders WHERE customer_id = 100;
```

**Captures:**
```
Post-Change:
  Execution time: 0.8ms
  Rows scanned: 1
  Rows returned: 1
  Access method: Index scan
```

#### Comparison

```
Improvements:
  Execution time: 5ms → 0.8ms (84% faster)
  Rows scanned: 10,000 → 1 (99.99% reduction)
  Access method: Full scan → Index scan ✓
```

---

### 6. Report Generation

**Create comprehensive HTML report**

#### Report Sections

**1. Summary**
- Original query
- Key performance metrics
- Overall improvement percentage

**2. Recommendations**
```html
<div class="recommendation high-priority">
  <h3>Create Index on customer_id</h3>
  <pre>CREATE INDEX ON orders (customer_id);</pre>
  <p><strong>Reason:</strong> WHERE clause filter causing full table scan</p>
  <p><strong>Impact:</strong> 99.99% reduction in rows scanned</p>
  <p><strong>Estimated speedup:</strong> 6x faster</p>
</div>
```

**3. Before/After Plans**
```
┌─────────────────────┬─────────────────────┐
│ Original Plan       │ Optimized Plan      │
├─────────────────────┼─────────────────────┤
│ • scan (FULL)       │ • scan (INDEX)      │
│   10,000 rows       │   1 row             │
│   245ms             │   12ms              │
└─────────────────────┴─────────────────────┘
```

**4. AI Narrative**
```
The query exhibits a severe performance bottleneck due to a full 
table scan on the orders table. Despite filtering for a single 
customer (customer_id = 100), the database must scan all 10,000 
rows before applying the filter.

Creating an index on the customer_id column will allow the 
optimizer to perform an index seek, directly accessing only the 
matching rows. This eliminates 99.99% of unnecessary I/O and 
reduces execution time by 84%.

Priority: High - This change will have immediate, significant impact.
```

**5. Validation Results**
- Test database connection status
- Seed data generation success
- Index creation confirmation
- Measured performance improvements

---

## Key Technologies

### Python Libraries

- **Flask** - Web interface and API
- **scikit-learn** - TF-IDF for RAG retrieval
- **psycopg** - CockroachDB connection
- **beautifulsoup4** - HTML/XML parsing
- **waitress** - Production WSGI server

### AI/ML Stack

- **Ollama** - Local LLM runtime
- **llama3:8b** - Fast 8B parameter model (SLM)
- **llama3.3:70b** - Advanced 70B parameter model (LLM)
- **Custom prompt engineering** - Optimized for SQL analysis

### Database

- **CockroachDB** - Validation testing
- **PostgreSQL protocol** - Wire compatibility

---

## Performance Characteristics

### Analysis Speed

| Mode | Time | Model Size | RAM |
|------|------|------------|-----|
| Rule-based only | 10ms | N/A | Minimal |
| SLM (llama3:8b) | 3-5s | 4.7 GB | 8 GB |
| LLM (llama3.3:70b) | 60-90s | 40 GB | 48 GB |

### Accuracy

| Method | Accuracy | Coverage | False Positives |
|--------|----------|----------|-----------------|
| Rule-based | High | Limited | Very low |
| SLM | Good | Good | Low |
| LLM | Excellent | Excellent | Very low |
| Combined | Excellent | Excellent | Very low |

---

## Failure Handling

### LLM Unavailable
```
If Ollama not running:
  → Fall back to rule-based analysis
  → Show warning to user
  → Still provide valid recommendations
```

### Invalid JSON Response
```
If LLM returns malformed JSON:
  → Retry with stricter prompt
  → If still fails, use rule-based
  → Log error for debugging
```

### Database Connection Failed
```
If test database unreachable:
  → Skip validation step
  → Show recommendations without testing
  → Mark as "unvalidated" in report
```

---

## Security & Privacy

### Data Handling

**Local processing only** - No external API calls  
**No data retention** - Bundles deleted after analysis  
**No telemetry** - No analytics or tracking  
**Sandboxed execution** - Limited file system access  

### Network

**Localhost only** - Web interface binds to 127.0.0.1  
**No outbound calls** - Except optional docs download (one-time)  
**No authentication required** - Desktop app, single user  

---

[← Back to Home](./index.html) | [Next: Installation Guide →](./installation.html)
