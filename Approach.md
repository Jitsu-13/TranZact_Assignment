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

**Chosen approach:** Chunk line items, render each chunk as separate HTML-to-PDF, merge with pypdf.

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

## 5. Dead Ends, Mistakes & Lessons Learned

This section documents things that went wrong during development and how they were resolved. These are real issues, not hypothetical.

### Dead End 1: Playwright Event Loop Hell

**The problem:** Flask is synchronous. Playwright is async. The naive approach — `asyncio.run()` or `asyncio.new_event_loop()` per request — works for the first PDF. The second call hangs or crashes because Playwright's browser objects are **bound to the event loop they were created on**. Creating a new loop per request means the browser pool from the first loop is unusable on the second loop.

**What I tried (chronologically):**
1. `asyncio.new_event_loop()` per `generate_pdf()` call → Browsers from loop #1 can't be used on loop #2. Hangs on second request.
2. `asyncio.get_running_loop()` → No running loop in sync Flask context. Same fundamental problem.
3. Persistent background event loop with `threading.Thread` + `asyncio.run_coroutine_threadsafe()` → **This worked.** All Playwright objects live on one persistent loop. Sync Flask code submits coroutines to that loop and waits for results.

**Lesson:** Mixing sync frameworks (Flask/Django) with async libraries (Playwright) requires understanding that async objects are loop-bound. The persistent background loop pattern is the clean solution — it's what Django's `async_to_sync` does internally.

### Dead End 2: Browser Pool Stale Connections

**The problem:** Even with the persistent event loop, browsers would become stale between requests. `browser.is_connected()` returned `True`, but `browser.new_context()` threw `TargetClosedError: Target page, context or browser has been closed`. This happened because the Playwright driver process could die/disconnect while the browser object still existed in memory.

**What I tried:**
1. Trust `is_connected()` → Crashed on stale browsers.
2. Wrap `new_context()`/`new_page()` in try/except, fall through to next browser → **Partially worked** but if ALL browsers AND the Playwright driver were dead, launching new browsers also failed.
3. Added `_reinitialize()` — when everything is dead, tear down the entire pool (browsers + Playwright instance) and restart fresh → **This solved it.** The pool now self-heals from any level of corruption.

**Lesson:** Connection health checks (`is_connected()`) are necessary but not sufficient. Always wrap the actual operation in try/except and have a nuclear recovery path. In production, this means the service auto-recovers from Chromium crashes without needing a manual restart.

### Dead End 3: PyPDF2 vs pypdf

**The problem:** Used `PyPDF2` (the older, deprecated package) initially. It worked but threw deprecation warnings and had subtle bugs with PDF merging in newer Python versions.

**Fix:** Switched to `pypdf` (the actively maintained successor). Drop-in replacement — same API, actively maintained, better performance.

### Mistake 1: Hash Registration Crashing the Entire Request

**The problem:** When Redis was unavailable, `register_hash()` threw a `ConnectionRefusedError` that propagated up and returned a 500 error — even though the PDF was already generated successfully and sitting on disk. The hash registration is optional (tamper evidence), but it was being treated as mandatory.

**Fix:** Made hash registration best-effort:
```python
try:
    register_hash(result["file_id"], result["sha256_hash"], metadata)
except Exception as e:
    logger.warning(f"Hash registration failed (Redis may be down): {e}")
```

**Lesson:** Distinguish between core functionality (PDF generation) and ancillary features (hash storage). Ancillary features should degrade gracefully, not take down the core path. Same principle applied to the verify endpoint.

### Mistake 2: Dockerfile CMD Syntax

**The problem:** Initial Dockerfile had `CMD ["gunicorn", "-b", "0.0.0.0:8080", "src.app:create_app()"]`. Gunicorn can't call factory functions with this syntax — it expects a module:variable reference to an already-instantiated WSGI app.

**Fix:** Created a `wsgi.py` entry point (`app = create_app()`) and changed CMD to `"wsgi:app"`.

---

## 6. AI Chat Conversation Links

> **Tool Used:** Claude Code (via VS Code extension — `claude-sonnet-4-6` / `claude-opus-4-6` models)
>
> Claude Code is a CLI/IDE-integrated tool that does **not** produce shareable public conversation URLs. All prompts, AI responses, what I accepted, what I rejected, and why — are documented in full detail in the AI Usage Log section below.
>
> If a shareable link is required, this is a limitation of the tool, not an omission. The interaction log below is a faithful reconstruction of every significant exchange.

---

## 6. AI Usage Log

I used Claude Code (Claude Opus 4.6) throughout this project — not as an auto-pilot, but as a thinking partner. Below is an honest log of where it helped, where it was wrong, and where I had to override it.

### How I Used AI: Exploration vs. Generation

I deliberately used AI differently at different stages:

- **Exploration phase (architecture, design):** Asked open-ended questions ("What are the options for browser-based PDF rendering in Python?", "How does Celery handle task retries?"). Used responses as a starting point for my own research, not as final answers.
- **Generation phase (boilerplate, templates):** Let AI generate repetitive code (HTML templates, test fixtures, Docker config). These are low-risk — if it's wrong, tests catch it.
- **Review phase (bug hunting):** This was the highest-value use. AI as a code reviewer caught 6 bugs I missed (see Interaction 5 below).

### Interaction 1: Tech Stack Decision — Overriding AI's Default

**What I asked:** "Build a PDF generation microservice based on this assignment."
**What AI suggested:** Node.js + Express + Puppeteer + BullMQ. This is the "default internet answer" for headless PDF generation since Puppeteer is a Node.js library.
**Why I rejected it:** The assignment says the existing system is a Django monolith. Introducing Node.js alongside Python means:
  - Two package ecosystems to maintain (npm + pip)
  - Two sets of debugging tools and deployment pipelines
  - Team context-switching between languages
  - Playwright (Python) is Puppeteer's equivalent with identical Chromium rendering

**Takeaway:** AI defaults to the most common Stack Overflow answer. It doesn't consider organizational context like "what does the team already know?" That's a human judgment call.

### Interaction 2: Capacity Planning — Catching Optimistic Numbers

**What I asked:** "Calculate capacity planning for 1000 req/min on r6i.large."
**What AI suggested:** Initial framing implied the system could handle 1000 req/min. The tone was optimistic — "the queue absorbs the load."
**What was wrong:** I ran the math myself:
```
2 vCPUs × ~2.5 sec/render = max ~0.8 renders/sec per CPU
With 3 concurrent renders: ~1.2 PDF/sec = 72 PDFs/min
1000 req/min ÷ 72/min = ~14 minute drain time
```
72/min is not 1000/min. Not even close. AI was hand-waving the gap between "queued" and "processed."

**What I did:** Made the capacity planning section brutally transparent about this limitation. The system handles 1000/min as a *burst* (queue absorbs it), not as sustained throughput. Honest math > optimistic framing.

### Interaction 3: Playwright + Flask Async — AI Gave Wrong Solution

**What I asked:** "Second PDF render hangs. First one works fine. How to fix?"
**What AI suggested:** `asyncio.get_running_loop()` to reuse the current event loop.
**Why it was wrong:** There IS no running event loop in a sync Flask request handler. Flask is WSGI — it's synchronous. `get_running_loop()` throws `RuntimeError: no running event loop`. This is a generic Python async answer that ignores the sync-async boundary.

**What I did instead:** Researched how Django's `async_to_sync()` works internally. It uses a persistent background thread running `loop.run_forever()`, then submits coroutines via `run_coroutine_threadsafe()`. Implemented this pattern:
```python
_loop = asyncio.new_event_loop()
_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_thread.start()

def _run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=60)
```
This keeps all Playwright objects on one stable loop. The sync Flask code just submits work to it.

**Takeaway:** AI is good at pattern-matching common problems. But sync-framework + async-library integration is a niche problem. The solution required understanding *why* Playwright objects are loop-bound, which the AI's generic answer missed.

### Interaction 4: Data Consistency — Validating AI's Approach

**What I asked:** "How should we handle data consistency for bulk PDF generation?"
**What AI suggested:** Two options — (A) pass document IDs and fetch from DB at render time, or (B) pass complete data snapshots in the request.
**What I evaluated:**
  - Option A (fetch from DB) is the stale-data bug described in Constraint #4. If someone edits a PO while we're rendering doc #50 of 100, doc #50 gets different data than doc #1. This is exactly what the assignment warns about.
  - Option B (data snapshots) means larger payloads (~5-10MB for 100 docs) but guarantees consistency. Since it's localhost (same EC2), payload size is irrelevant.

**What I did:** Went with Option B. AI presented both options neutrally — it didn't flag that Option A literally recreates the bug the assignment describes. I had to connect that dot myself.

### Interaction 5: Code Review — Highest-Value AI Use

**What I asked:** "Do a thorough audit. Find real bugs, not style issues."
**What AI found — all legitimate:**

| Bug | Impact | My Reaction |
|---|---|---|
| Dockerfile CMD: `src.app:create_app()` | Service won't start at all | Missed this completely — Gunicorn factory syntax is subtle |
| Progress: `idx/total * 100` off-by-one | Progress never reaches 100% | Would have caught in QA but good to find early |
| Verify endpoint: both branches return 200 | Dead code, no functional impact | Copy-paste artifact I overlooked |
| Browser pool grows unbounded | Memory leak in production | Important — added `_max_browsers` cap |
| Cleanup function never invoked | Disk fills up over days | Added Celery beat schedule |
| Missing `wsgi.py` entry point | Deploy fails | Direct consequence of the Dockerfile bug |

**Takeaway:** AI as a reviewer > AI as a generator. I would use it for code review on every project. It caught the Gunicorn factory syntax issue that I — and most developers — would have only discovered during deployment.

### Interaction 6: Where I Didn't Use AI

Some decisions were purely human judgment:

- **Budget architecture (co-located sidecar):** AI suggested Lambda and ECS Fargate as alternatives. Both are technically elegant but blow the Rs.12,500/month budget. The boring answer (same EC2) was the right answer. AI doesn't do budget math well.
- **Chunk size (100 items):** AI suggested 50 and 200 as alternatives. I picked 100 based on: 100 items × ~400MB peak < 500MB safe limit on r6i.large, and 100 rows fit on ~3 A4 pages which is readable. This is domain judgment, not a technical decision.
- **Making hash registration best-effort:** When Redis crashes shouldn't crash PDF generation. AI initially wrote it as mandatory (fail the whole request). I made it best-effort with a `try/except`. Core functionality should never fail because of ancillary features.

---

## 7. Weaknesses & Future Improvements

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

## 8. One Thing the Problem Statement Didn't Mention

### Observability and Monitoring

The problem statement doesn't mention monitoring, alerting, or logging — but this is **critical for a production service**.

**Why it matters:**

1. **Memory leak detection.** Playwright/Chromium are notorious for slow memory leaks. Without monitoring, you won't know the service is consuming all RAM until Django crashes at 3 AM.

2. **Queue depth alerting.** If the queue grows beyond 500 jobs, something is wrong — a crashed worker, Redis down, or traffic spike. Without alerts, bulk jobs silently fail.

3. **Failed PDF tracking.** If 5% of PDFs fail silently (Playwright timeout, template error, corrupt data), users might not notice immediately. A success/failure rate dashboard catches this early.

4. **Cost attribution.** With 30K PDFs/month across multiple customers, per-customer generation metrics enable capacity planning and usage-based billing.

**What I would add:** A `/metrics` endpoint exposing Prometheus-compatible metrics (generation latency histogram, queue depth gauge, success/error counters, active render gauge). Combined with CloudWatch for EC2 system metrics (CPU, RAM, disk), this provides full visibility at near-zero cost.
