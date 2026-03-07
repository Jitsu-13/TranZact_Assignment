# Cost Estimation

## Monthly Infrastructure Cost Breakdown

**Assumptions:** 30,000 PDFs/month, ~500KB average PDF size, 24-hour retention, co-located on existing EC2 r6i.large with Django monolith. AWS ap-south-1 (Mumbai) region pricing.

---

### Line-by-Line Breakdown

| Component | Specification | Monthly Cost | Math |
|---|---|---|---|
| **EC2 Compute** | r6i.large (already running Django) | **$0.00** (incremental) | Instance is already paid for. PDF service uses ~2.4GB of the ~11GB available headroom. No new instance needed. |
| **EBS Storage** | gp3, additional 20 GB for PDF storage | **$1.60** | 20 GB x $0.08/GB/month. 24-hour retention means peak ~500MB on disk, but 20GB gives headroom. |
| **Redis** | ElastiCache t3.micro (already exists for Django caching) | **$0.00** (incremental) | Already in use. Celery adds ~12MB to Redis — negligible overhead on a t3.micro (0.5GB). |
| **Data Transfer Out** | ~15 GB/month (30K PDFs x 500KB) | **$0.00** | First 100 GB/month is included in AWS free tier for data transfer. |
| **RDS PostgreSQL** | db.r6i.large (already exists for Django) | **$0.00** (incremental) | Only used for persisting hash records (optional). ~30K rows/month, trivial load. |
| **CloudWatch** | Basic monitoring (included) + 5 custom metrics | **$1.50** | 5 metrics x $0.30/metric/month for custom metrics. Basic EC2 metrics are free. |
| **EBS Snapshots** | 20 GB, weekly backups (4/month) | **$1.00** | ~20 GB x 4 snapshots x ~$0.05/GB-month (incremental). Most data is unchanged. |

---

### Total Monthly Cost

| Category | Cost |
|---|---|
| Incremental compute | $0.00 |
| Storage (EBS) | $1.60 |
| Monitoring (CloudWatch) | $1.50 |
| Backups (EBS snapshots) | $1.00 |
| Data transfer | $0.00 |
| **TOTAL** | **$4.10/month (~Rs.340)** |

---

### Why So Cheap?

The key insight is that this service is **co-located on existing infrastructure**. The r6i.large running Django already costs ~$125/month (on-demand) or ~$75/month (1-year reserved). We're using spare capacity on this existing instance — the incremental cost is just the extra disk space and monitoring.

If we had to provision dedicated infrastructure:

| Scenario | Monthly Cost |
|---|---|
| Dedicated t3.medium + ElastiCache t3.micro | ~$52/month |
| ECS Fargate (0.5 vCPU, 1GB, always-on) | ~$35/month + Redis |
| AWS Lambda (30K invocations x 3s x 1GB) | ~$15/month + Redis |
| **Our approach (co-located sidecar)** | **~$4/month** |

---

### Budget Headroom

```
Budget:                    Rs.12,500/month (~$150 USD)
Existing EC2 cost:         ~$125/month (r6i.large on-demand, ap-south-1)
Existing Redis:            ~$13/month (ElastiCache t3.micro)
Existing RDS:              ~$xxx/month (already budgeted by Django)
PDF service incremental:   ~$4/month

Total with PDF service:    ~$142/month
Budget remaining:          ~$8/month
```

The tight budget means we have essentially zero room for additional services. If we needed to add an extra t3.micro for a dedicated worker ($8.50/month), we'd be at the budget limit. This validates the co-located sidecar decision.

---

### Cost at Scale

| Scale | PDFs/month | Approach | Est. Cost |
|---|---|---|---|
| Current | 30K | Co-located sidecar | ~$4/month |
| 3x | 90K | Same, but 2 Celery workers | ~$6/month |
| 10x | 300K | Dedicated c6i.xlarge + S3 | ~$120/month |
| 30x | 1M | 3x c6i.xlarge + ALB + S3 | ~$400/month |

The co-located approach works until ~100K PDFs/month. Beyond that, dedicated compute and S3 storage become necessary, and the budget constraint would need to be revisited.
