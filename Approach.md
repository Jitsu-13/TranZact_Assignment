# Approach Document

## 1. Initial Understanding

> Before using any AI tools or doing research, what was your initial read of this problem? What did you think the hard parts were?

My first read: this is a **sidecar PDF rendering service** that sits next to a Django monolith. The happy path (render one PDF, return it) is straightforward — the hard parts are all in the constraints:

1. **Memory management with Puppeteer/Playwright**: Each PDF render consuming ~400MB is brutal on a 16GB machine that's already running Django + PostgreSQL connections + Redis. With 500+ line items causing OOM crashes, we can't just throw HTML at a headless browser and hope for the best. This felt like the core engineering challenge.

2. **Data consistency in bulk operations**: The stale-data bug described in constraint #4 is a classic race condition — long-running batch process vs. concurrent writes. The naive approach (fetch data per document at render time) is exactly what caused the reported bug. This needs an upfront data snapshot pattern.

3. **Budget constraint is very tight**: Rs.12,500/month (~$150 USD) for 30K PDFs basically rules out any separate compute. No extra EC2 instance, no Lambda at this volume, no managed queue service. We need to piggyback on existing infrastructure.

4. **Unreliable connectivity + large downloads**: ZIP files with 50+ PDFs can easily be 25-50MB. On a flaky 2G/3G connection common in Indian SMBs, a 50MB download will absolutely fail without resumability.

The parts I was less worried about: the HTTP API itself (standard Flask), template rendering (Jinja2 is mature), and basic job queuing (Celery is battle-tested).

---

## 2. Assumptions & Clarifying Questions

### Assumptions Made

1. **The PDF service runs on the same EC2 instance as Django** — a separate instance would blow the budget. We're treating this as a co-located sidecar process, not a separate microservice on its own infra.

2. **Django sends complete data snapshots in bulk requests** — rather than sending document IDs and having the PDF service fetch from PostgreSQL. This is essential for data consistency (constraint #4) and decouples the PDF service from the database schema.

3. **The existing Redis instance has spare capacity** — since it's currently used "for caching" by Django, we assume it can handle the additional Celery broker + result backend load (~100-200 keys for active jobs).

4. **Generated PDFs are ephemeral** — kept for 24 hours for download, then cleaned up. Long-term archival (compliance) is handled by a separate process or the Django monolith storing references.

5. **Authentication/authorization is handled at the Django layer** — the PDF service is an internal service, not exposed directly to end users. Django acts as a gateway.

6. **Single-threaded Playwright is acceptable** — we use async rendering within a browser pool but don't need true parallelism at the Playwright level since the EC2 only has 2 vCPUs.

### Questions I Would Ask

1. **What's the actual memory footprint of the Django monolith?** — If it's using 12GB of the 16GB, we have very different constraints than if it's using 4GB. This determines our browser pool size.

2. **Are there existing Celery workers in the Django app?** — If Django already uses Celery, we should coordinate worker count to avoid oversubscribing CPU.

3. **What's the peak concurrent user count?** — 1000 single requests/min could mean 1000 users or 10 users making 100 requests each. This affects connection pooling.

4. **Is there an existing reverse proxy (Nginx)?** — This affects how we handle Range headers and SSE connections. Nginx needs specific config for SSE.

5. **What's the compliance retention requirement?** — 24 hours? 7 years? This dramatically changes storage architecture.

6. **Do bulk requests always contain the same document type, or mixed?** — Affects ZIP naming and organization.

7. **Is there an existing monitoring/alerting setup (CloudWatch, Datadog)?** — We need to know where to send metrics.

---

## 3. Capacity Planning & Math

### Memory Budget

```
EC2 r6i.large total RAM:                16,384 MB
  OS + system overhead:                    ~500 MB
  Django application:                    ~4,000 MB (estimated for monolith)
  PostgreSQL connections (client side):     ~200 MB
  Redis (shared, caching):                 ~500 MB
  Available for PDF service:            ~11,184 MB

  PDF Service breakdown:
    Flask/Gunicorn (4 workers):            ~200 MB
    Celery worker process:                 ~150 MB
    Playwright browser pool (3 idle):      ~240 MB (80MB x 3)
    Peak render (2 concurrent):            ~800 MB (400MB x 2)
    Safety buffer:                       ~1,000 MB
    Total PDF service:                   ~2,390 MB

  Remaining headroom:                    ~8,794 MB
```

**Conclusion:** Comfortable fit. We have ~8.8GB headroom even at peak. But we cap concurrent renders at 2 to be safe — 3 concurrent renders (1.2GB peak) would still work but leaves less buffer for Django spikes.

### CPU Budget

```
EC2 r6i.large vCPUs:                     2
  Django app (typical):                   ~0.3-0.8 vCPU
  PDF rendering (per doc):                ~0.5-1.0 vCPU for 2-3 seconds

With 2 concurrent renders:               ~1.0-2.0 vCPU (burst)
Sustained at 1000 req/min:               NOT possible with 2 vCPUs

Reality check:
  1000 req/min = 16.7 req/sec
  Each render: ~2.5 sec average
  Required concurrency: 16.7 x 2.5 = ~42 concurrent renders
  Available concurrency: 2-3

  CONCLUSION: Cannot serve 1000 req/min synchronously.
  SOLUTION: Queue + async processing. Peak of ~1000/min would queue up.
  Actual throughput: ~1.2 PDF/sec with 3 concurrent renders = ~72/min.
  Queue drain time for 1000-doc backlog: ~14 minutes.
```

**Important insight:** The "~1000 single requests/min during peak" is a burst rate, not sustained. The queue absorbs the burst, and PDFs drain at ~72/min. Users get a task_id immediately and poll for completion, rather than waiting synchronously.

### Storage Budget

```
Monthly PDFs:                             30,000
Average PDF size:                         ~500 KB (midpoint of 100KB-1MB)
Monthly storage:                          30,000 x 500 KB = ~15 GB
Daily storage (if 24h retention):         15 GB / 30 = ~500 MB/day

Bulk ZIP overhead:                        ~10% of PDF size (compression)
Peak concurrent storage (24h window):     ~500 MB PDFs + ~50 MB ZIPs

EBS gp3 volume: 20 GB is more than sufficient.
Cost: 20 GB x $0.08/GB = $1.60/month
```

### Network Bandwidth

```
Monthly download volume:                  30,000 x 500 KB = ~15 GB
Peak download (1000 req/min burst):       1000 x 500 KB = 500 MB/min ~ 67 Mbps
EC2 baseline network: r6i.large up to 12.5 Gbps — no bottleneck.

Data transfer out (to internet):          15 GB/month
First 100 GB/month is free tier -> $0.00
```

### Queue Depth

```
Peak burst: 1000 single + 10 bulk (x 100 docs) = 2000 jobs
Processing rate: ~72 jobs/min (1.2/sec x 2 workers)
Max queue depth: ~2000 jobs
Drain time: ~28 minutes for worst-case burst

Redis memory for queue:
  2000 jobs x ~5 KB avg payload = ~10 MB
  Celery result storage: 2000 x ~1 KB = ~2 MB
  Total Redis overhead: ~12 MB — negligible
```

---

## 4. Design Decisions

### Decision 1: Co-located Sidecar vs. Separate Service

**Alternatives considered:**
1. Separate EC2 instance for the PDF service
2. AWS Lambda for PDF generation
3. ECS Fargate containers
4. Co-located on the same EC2 instance as Django

**Chosen approach:** Co-located sidecar on the existing EC2 instance.

**Why:**
- **Budget constraint is decisive.** A separate t3.medium (~$30/mo) + Redis (~$15/mo) already eats 30% of the $150 budget. An r6i.large has 16GB RAM — after Django (~4GB), we still have ~12GB, which is plenty for 2-3 concurrent PDF renders (~1.2GB peak).
- **Lambda was tempting** but Playwright/Puppeteer cold starts are 5-8 seconds (browser download + launch), and the 500MB package size limit is tight with Chromium. At 30K invocations x ~3 sec x 1024MB, Lambda costs ~$15/month — affordable, but cold starts violate the "download starts within 5 seconds" requirement.
- **ECS Fargate** minimum cost for always-on tasks is ~$30-40/month, and still needs Redis for queuing.

**Tradeoff accepted:** The PDF service competes with Django for CPU/RAM. A spike in PDF generation could degrade Django performance. We mitigate with concurrency limits (max 2 concurrent renders) and Celery worker prefetch=1.

### Decision 2: Data Snapshot Pattern (Push) vs. Data Fetch (Pull)

**Alternatives considered:**
1. Django sends document IDs, PDF service fetches from PostgreSQL
2. Django sends complete data snapshots in the HTTP request payload
3. Django writes snapshots to shared filesystem, PDF service reads them

**Chosen approach:** Django sends complete data snapshots in the request payload.

**Why:**
- **Directly solves the stale-data bug** (constraint #4). All documents in a bulk request use the same data captured at the moment the user clicked "Generate." If someone edits a purchase order 2 minutes later, it doesn't affect the in-flight batch.
- **No database coupling.** The PDF service doesn't need PostgreSQL credentials, doesn't understand Django ORM schema, doesn't add load to RDS.
- **Simpler failure modes.** If the service crashes mid-batch, it can be restarted with the same payload.

**Tradeoff accepted:** Larger request payloads. A bulk request with 100 documents x 500 line items could be ~5-10MB of JSON. Acceptable for internal service calls (same EC2, localhost), but would be problematic over public internet.

### Decision 3: Chunked Rendering for Large Documents

**Alternatives considered:**
1. Increase Playwright memory limit and hope for the best
2. Use WeasyPrint (pure Python, no browser, lower memory)
3. Split large documents into chunks, render separately, merge PDFs
4. Paginate in HTML template using CSS @page rules

**Chosen approach:** Chunk line items, render each chunk as separate HTML-to-PDF, merge with PyPDF2.

**Why:**
- **Directly addresses the OOM constraint** (#2). A 500-item document becomes 5 x 100-item renders. Each peaks at ~400MB instead of >1GB.
- **CSS @page approach was unreliable** — Playwright sometimes miscalculates page breaks for very long dynamic tables, and OOM still occurs because the entire DOM is in memory.
- **WeasyPrint** would solve memory but produces lower-quality PDFs, has poor CSS grid/flexbox support. The assignment specifically mentions Puppeteer-based rendering.

**Tradeoff accepted:** Merged PDFs may have slight style inconsistencies at chunk boundaries. Mitigated with chunk info headers ("Page 2 of 5 — Items 101-200 of 500") and totals only on the last chunk.

### Decision 4: SHA-256 Hash Registry for Tamper Evidence

**Alternatives considered:**
1. Digital signatures using asymmetric cryptography (RSA/ECDSA)
2. Embed HMAC inside PDF metadata
3. SHA-256 hash stored in external registry (Redis + optional DB)
4. Blockchain-based timestamping

**Chosen approach:** SHA-256 hash computed at generation time, stored in Redis, with a verification endpoint.

**Why:**
- **Simple and effective.** "Prove a PDF was not modified after generation" — hash comparison does exactly this.
- **Digital signatures** add complexity: key management, certificate rotation, specialized PDF signing libraries.
- **No extra infrastructure.** Uses existing Redis. For long-term compliance, hashes persist to PostgreSQL (~30K rows/month, trivial).

**Tradeoff accepted:** SHA-256 proves integrity but not authenticity (no non-repudiation). For legal-grade tamper evidence, digital signatures would be needed. Acceptable because the hash registry is internal — external parties can't insert fake hashes.

### Decision 5: SSE + Resumable Downloads for Unreliable Connectivity

**Alternatives considered:**
1. WebSockets for real-time progress
2. Polling-based status checks only
3. Server-Sent Events (SSE) + HTTP Range for downloads
4. Pre-signed S3 URLs with multi-part download

**Chosen approach:** SSE for progress + HTTP Range headers for resumable downloads.

**Why:**
- **SSE is simpler than WebSockets** for one-directional progress. Built-in reconnection in EventSource browser API, works over standard HTTP.
- **HTTP Range headers** are the standard for resumable downloads, supported by every HTTP client. When a connection drops during a 25MB ZIP, the client resumes from byte offset.
- **Polling is the fallback.** Both SSE and polling endpoints are available.

**Tradeoff accepted:** SSE holds a long-lived HTTP connection per subscriber, blocking a Gunicorn sync worker. Mitigated by keeping SSE lightweight (1-sec poll interval against Redis).

---

## 5. AI Usage Log

### Interaction 1
**Tool:** Claude Code (Claude Opus 4.6)
**What I asked:** "Build the complete PDF generation microservice following the assignment guidelines."
**What it suggested:** Initially generated a Node.js/Express stack with Puppeteer and BullMQ.
**What I did with it:** Rejected the Node.js suggestion — redirected to Python/Flask since it aligns with the existing Django monolith (same language ecosystem, simpler deployment on same EC2, team familiarity). Claude regenerated the project with Flask + Celery + Playwright.

### Interaction 2
**Tool:** Claude Code (Claude Opus 4.6)
**What I asked:** Reviewed whether all 6 scenario constraints were addressed in the code.
**What it suggested:** Provided an audit table mapping each constraint to the implementation.
**What I did with it:** Used as a checklist. Identified that documentation deliverables (APPROACH.md, cost estimation, architecture diagram) were missing — code was complete but the most heavily weighted parts hadn't been created. Redirected to prioritize documentation.

### Interaction 3
**Tool:** Claude Code (Claude Opus 4.6)
**What I asked:** "Create the APPROACH.md, cost-estimation.md, and architecture diagram."
**What it suggested:** Generated documentation with capacity planning math.
**What I did with it:** Verified the numbers manually. Key correction: the initial framing implied we could handle 1000 req/min synchronously, but the math clearly shows ~72 PDFs/min max throughput. Made sure this was transparent in capacity planning rather than hand-waving it away. The honest acknowledgment of this limitation is more valuable than pretending the system handles it.

---

## 6. Weaknesses & Future Improvements

### Current Weaknesses

1. **Single point of failure.** Running on one EC2 means any instance failure takes down both Django AND the PDF service. No redundancy.

2. **Throughput ceiling.** ~72 PDFs/min on 2 vCPUs cannot sustain 1000 req/min peak. Queue absorbs bursts, but sustained peak creates growing backlogs. Users might wait 15-30 minutes during peak.

3. **No persistent job history.** Celery results expire after 1 hour (Redis TTL). Users returning after 2 hours can't check bulk job status.

4. **Gunicorn sync workers + SSE don't scale.** Each SSE connection blocks a worker. With 4 workers and 3 SSE connections, only 1 worker serves actual requests.

5. **No authentication on the PDF service.** Assumes Django is the only caller. If the port is accidentally exposed, anyone can generate PDFs.

### If I Had 2 More Days

1. **Switch to gevent/async Gunicorn workers** for non-blocking SSE
2. **Persistent job storage in PostgreSQL** — job metadata + file hashes for audit/compliance
3. **PDF caching** — hash input data as cache key, return cached PDF for duplicate requests
4. **Prometheus metrics** — generation latency, queue depth, error rates, active renders
5. **Nginx reverse proxy config** — SSE passthrough, PDF static serving, buffering

### For 10x Load (10,000 req/min, 300K PDFs/month)

1. **Separate compute.** Dedicated c6i.xlarge (4 vCPU, 8GB) for PDF service
2. **Horizontal scaling.** Multiple Celery workers across 2-3 instances behind ALB
3. **S3 for storage** instead of local disk — any worker can serve any download
4. **SQS instead of Redis as broker** — more durable, auto-scales
5. **Pre-render and cache** frequently-requested documents

---

## 7. One Thing the Problem Statement Didn't Mention

### Observability and Monitoring

The problem statement doesn't mention monitoring, alerting, or logging — but this is **critical for a production service**.

**Why it matters:**

1. **Memory leak detection.** Playwright/Chromium are notorious for slow memory leaks. Without monitoring, you won't know the service is consuming all RAM until Django crashes at 3 AM.

2. **Queue depth alerting.** If the queue grows beyond 500 jobs, something is wrong — a crashed worker, Redis down, or traffic spike. Without alerts, bulk jobs silently fail.

3. **Failed PDF tracking.** If 5% of PDFs fail silently (Playwright timeout, template error, corrupt data), users might not notice immediately. A success/failure rate dashboard catches this early.

4. **Cost attribution.** With 30K PDFs/month across multiple customers, per-customer generation metrics enable capacity planning and usage-based billing.

**What I would add:** A `/metrics` endpoint exposing Prometheus-compatible metrics (generation latency histogram, queue depth gauge, success/error counters, active render gauge). Combined with CloudWatch for EC2 system metrics (CPU, RAM, disk), this provides full visibility at near-zero cost.
