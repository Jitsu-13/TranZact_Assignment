# PDF Generation Microservice

A PDF generation microservice for an ERP application that handles both individual and bulk document generation. Built as a co-located sidecar service alongside a Django monolith.

## Architecture

See [`architecture-diagram.svg`](./architecture-diagram.svg) for the full system diagram.

**Tech Stack:** Python 3.12+ / Flask / Celery / Playwright / Redis / Jinja2

**Key Design Decisions:**
- Co-located on existing EC2 r6i.large (~$4/mo incremental cost)
- Data snapshot pattern prevents stale-data in bulk operations
- Chunked rendering for 500+ line item documents (OOM prevention)
- SHA-256 hash registry for tamper evidence
- HTTP Range headers + SSE for unreliable client connectivity

## Project Structure

```
├── APPROACH.md                 # Design rationale and decisions (most important)
├── cost-estimation.md          # Line-by-line infrastructure cost breakdown
├── architecture-diagram.svg    # High-level system architecture
├── wsgi.py                     # WSGI entry point for Gunicorn
├── src/
│   ├── app.py                  # Flask application factory
│   ├── config.py               # Configuration from environment
│   ├── celery_app.py           # Celery configuration + beat schedule
│   ├── schemas.py              # Request validation (Marshmallow)
│   ├── routes/
│   │   ├── pdf_routes.py       # PDF generation & download endpoints
│   │   └── health_routes.py    # Health check endpoint
│   ├── services/
│   │   ├── browser_pool.py     # Playwright browser pool manager
│   │   ├── pdf_generator.py    # Core PDF generation + chunking
│   │   ├── template_engine.py  # Jinja2 template rendering
│   │   ├── storage.py          # File storage, ZIP, cleanup
│   │   └── hash_registry.py    # SHA-256 tamper evidence
│   ├── tasks/
│   │   ├── pdf_tasks.py        # Celery async tasks for bulk
│   │   └── maintenance_tasks.py # Periodic cleanup tasks
│   ├── templates/
│   │   ├── base.html           # Base template with styles
│   │   ├── invoice.html        # GST invoice template
│   │   └── purchase_order.html # Purchase order template
│   └── utils/
│       └── verify_pdf.py       # CLI verification utility
├── tests/
│   ├── conftest.py             # Shared fixtures with sample data
│   ├── unit/                   # Unit tests (schemas, templates, storage)
│   ├── integration/            # API integration tests
│   └── load/                   # Load testing script
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## How to Run

### Prerequisites

- **Python 3.12+** (3.13 works too)
- **Redis** (for Celery job queue and hash registry)
- **pip** (Python package manager)

### Option 1: Local Development (Step by Step)

#### Step 1: Clone and set up virtual environment

```bash
git clone git@github.com:Jitsu-13/TranZact_Assignment.git
cd TranZact_Assignment

# Create virtual environment
python -m venv .venv

# Activate it
# Linux/Mac:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
```

#### Step 2: Install Python dependencies

```bash
pip install -r requirements.txt
```

#### Step 3: Install Playwright browser (Chromium)

```bash
playwright install chromium
```

This downloads the headless Chromium binary (~150MB). Required for PDF rendering.

#### Step 4: Start Redis

You need Redis running locally. Easiest way:

```bash
# Using Docker (recommended):
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Or if Redis is installed natively:
redis-server
```

Verify Redis is running:
```bash
redis-cli ping
# Should print: PONG
```

#### Step 5: Set up environment config

```bash
# Linux/Mac:
cp .env.example .env

# Windows:
copy .env.example .env
```

The defaults work for local development. Edit `.env` if your Redis is on a different host/port.

#### Step 6: Start the Flask API server

```bash
python -m src.app
```

You should see:
```
[2024-03-15 10:00:00] INFO pdf_service: PDF Generation Microservice initialized
 * Running on http://0.0.0.0:5000
```

#### Step 7: Start the Celery worker (new terminal)

Open a **new terminal**, activate the venv again, then:

```bash
# Linux/Mac:
source .venv/bin/activate
# Windows:
.venv\Scripts\Activate.ps1

# Start the worker
celery -A src.celery_app:celery_app worker --loglevel=info --concurrency=2 --pool=solo
```

The `--pool=solo` flag is important on Windows. On Linux/Mac you can omit it.

You should see:
```
[2024-03-15 10:00:05] [INFO] Connected to redis://localhost:6379/1
[2024-03-15 10:00:05] [INFO] celery@hostname ready.
```

#### Step 8: Test it!

**Health check:**
```bash
curl http://localhost:5000/api/v1/health
```

**Generate a single invoice PDF:**
```bash
curl -X POST http://localhost:5000/api/v1/pdf/generate \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "invoice",
    "data": {
      "document_number": "INV-2024-001",
      "date": "2024-03-15",
      "company": {
        "name": "TranZact Technologies Pvt. Ltd.",
        "address": "123 Tech Park, Bangalore, Karnataka 560001",
        "gstin": "29ABCDE1234F1Z5"
      },
      "bill_to": {
        "name": "Acme Manufacturing Ltd.",
        "address": "456 Industrial Area, Mumbai, Maharashtra 400001",
        "gstin": "27FGHIJ5678K2L6",
        "state": "Maharashtra",
        "state_code": "27"
      },
      "ship_to": {
        "name": "Acme Manufacturing Ltd.",
        "address": "789 Warehouse Rd, Pune"
      },
      "line_items": [
        {
          "description": "Steel Rod 12mm TMT",
          "hsn_code": "7214",
          "quantity": 500,
          "unit": "kg",
          "rate": 65.00,
          "amount": 32500.00,
          "gst_rate": 18,
          "gst_amount": 5850.00,
          "total": 38350.00
        },
        {
          "description": "Cement OPC 53 Grade",
          "hsn_code": "2523",
          "quantity": 100,
          "unit": "bags",
          "rate": 380.00,
          "amount": 38000.00,
          "gst_rate": 28,
          "gst_amount": 10640.00,
          "total": 48640.00
        }
      ],
      "subtotal": 70500.00,
      "cgst": 8245.00,
      "cgst_rate": "9",
      "sgst": 8245.00,
      "sgst_rate": "9",
      "grand_total": 86990.00,
      "amount_in_words": "Rupees Eighty Six Thousand Nine Hundred and Ninety Only",
      "bank_details": {
        "bank_name": "HDFC Bank",
        "account_number": "12345678901234",
        "ifsc": "HDFC0001234",
        "branch": "Bangalore Main"
      },
      "terms": "Payment due within 30 days."
    }
  }'
```

**Response:**
```json
{
  "status": "completed",
  "file_id": "a1b2c3d4-...",
  "file_size": 45231,
  "sha256_hash": "e3b0c44298fc...",
  "generation_time_ms": 2340,
  "download_url": "/api/v1/pdf/download/a1b2c3d4-..."
}
```

**Download the PDF:**
```bash
curl -O http://localhost:5000/api/v1/pdf/download/<file_id>
```

**Generate a purchase order:**
```bash
curl -X POST http://localhost:5000/api/v1/pdf/generate \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "purchase_order",
    "data": {
      "document_number": "PO-2024-042",
      "date": "2024-03-10",
      "delivery_date": "2024-03-25",
      "company": {"name": "TranZact Technologies", "gstin": "29ABCDE1234F1Z5"},
      "vendor": {"name": "Global Steel Suppliers", "address": "Jamshedpur", "gstin": "20MNOPQ3456R7S8"},
      "ship_to": {"name": "TranZact Warehouse", "address": "Bangalore"},
      "line_items": [
        {"description": "MS Flat Bar 50x6mm", "hsn_code": "7216", "quantity": 200, "unit": "kg", "rate": 55.00, "amount": 11000.00}
      ],
      "subtotal": 11000.00,
      "igst": 1980.00,
      "igst_rate": "18",
      "grand_total": 12980.00
    }
  }'
```

**Submit a bulk job (requires Celery worker running):**
```bash
curl -X POST http://localhost:5000/api/v1/pdf/bulk \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "doc_type": "invoice",
        "data": {
          "document_number": "INV-001",
          "date": "2024-03-15",
          "company": {"name": "Test Corp"},
          "bill_to": {"name": "Client A"},
          "ship_to": {"name": "Client A"},
          "line_items": [{"description": "Item 1", "quantity": 10, "rate": 100, "amount": 1000, "gst_rate": 18, "gst_amount": 180, "total": 1180}],
          "subtotal": 1000, "cgst": 90, "sgst": 90, "grand_total": 1180
        }
      },
      {
        "doc_type": "invoice",
        "data": {
          "document_number": "INV-002",
          "date": "2024-03-15",
          "company": {"name": "Test Corp"},
          "bill_to": {"name": "Client B"},
          "ship_to": {"name": "Client B"},
          "line_items": [{"description": "Item 2", "quantity": 5, "rate": 200, "amount": 1000, "gst_rate": 18, "gst_amount": 180, "total": 1180}],
          "subtotal": 1000, "cgst": 90, "sgst": 90, "grand_total": 1180
        }
      }
    ]
  }'
```

**Response:**
```json
{
  "status": "queued",
  "job_id": "abc123-...",
  "total_documents": 2,
  "status_url": "/api/v1/pdf/bulk/abc123-.../status",
  "progress_url": "/api/v1/pdf/bulk/abc123-.../progress",
  "download_url": "/api/v1/pdf/bulk/abc123-.../download"
}
```

**Check bulk job status:**
```bash
curl http://localhost:5000/api/v1/pdf/bulk/<job_id>/status
```

**Verify a PDF hasn't been tampered with:**
```bash
curl -X POST http://localhost:5000/api/v1/pdf/verify/<file_id>
```

---

### Option 2: Docker Compose (Everything Included)

```bash
# Build and start all services (API + Worker + Redis)
docker-compose up -d

# Check logs
docker-compose logs -f api
docker-compose logs -f worker

# Health check
curl http://localhost:5000/api/v1/health

# Stop everything
docker-compose down
```

This starts 3 containers:
- `redis` — Redis 7 on port 6379
- `api` — Flask API on port 5000 (4 Gunicorn workers)
- `worker` — Celery worker (2 concurrent tasks)

---

## Running Tests

```bash
# Activate venv first
source .venv/bin/activate  # or .venv\Scripts\Activate.ps1 on Windows

# Install test dependencies (pytest is in requirements.txt)
pip install -r requirements.txt

# Run all unit tests (no Redis/Playwright needed)
pytest tests/unit/ -v

# Run integration tests (requires Redis + Playwright)
pytest tests/integration/ -v

# Run all tests with coverage
pytest --coverage

# Load test (requires running API server)
python tests/load/loadTest.py --url http://localhost:5000 --concurrent 10 --total 50
```

---

## API Endpoints Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/pdf/generate` | Generate single PDF synchronously (~2-5s) |
| `POST` | `/api/v1/pdf/generate/async` | Queue single PDF, returns task_id |
| `GET` | `/api/v1/pdf/task/<task_id>/status` | Check async single PDF status |
| `POST` | `/api/v1/pdf/bulk` | Submit bulk generation (up to 100 docs) |
| `GET` | `/api/v1/pdf/bulk/<job_id>/status` | Poll bulk job status |
| `GET` | `/api/v1/pdf/bulk/<job_id>/progress` | SSE stream for real-time progress |
| `GET` | `/api/v1/pdf/download/<file_id>` | Download single PDF (supports HTTP Range) |
| `GET` | `/api/v1/pdf/bulk/<job_id>/download` | Download bulk ZIP (supports HTTP Range) |
| `POST` | `/api/v1/pdf/verify/<file_id>` | Verify PDF tamper evidence (SHA-256) |
| `GET` | `/api/v1/health` | Health check + Redis status + storage stats |

---

## Constraint Solutions Summary

| Constraint | Solution |
|---|---|
| **Existing Infrastructure** | Co-located sidecar on same EC2, reuses existing Redis |
| **Variable Document Complexity (OOM)** | Chunked rendering: splits 500+ items into 100-item batches, renders separately, merges with PyPDF2 |
| **Unreliable Client Connectivity** | HTTP Range headers for resumable downloads + SSE with auto-reconnect for progress |
| **Data Consistency** | Django sends complete data snapshots in request payload — no DB fetches during rendering |
| **Tamper Evidence** | SHA-256 hash computed at generation, stored in Redis, verifiable via API |
| **Budget (< Rs.12,500/mo)** | ~$4/month incremental cost — uses existing EC2 + Redis, local disk storage |

---

## Deliverables

1. **[APPROACH.md](./APPROACH.md)** — Design rationale, capacity math, tradeoffs, AI usage log
2. **[architecture-diagram.svg](./architecture-diagram.svg)** — High-level system architecture
3. **[cost-estimation.md](./cost-estimation.md)** — Line-by-line infrastructure cost breakdown
4. **AI Chat Conversations** — This project was built using Claude Code (Claude Opus 4.6)
