"""
Simple load test script for the PDF generation service.

Usage:
    python tests/load/loadTest.py [--url http://localhost:5000] [--concurrent 10] [--total 50]

Simulates concurrent single PDF requests to measure throughput and latency.
"""

import argparse
import json
import time
import threading
import statistics
from urllib.request import Request, urlopen
from urllib.error import URLError


SAMPLE_PAYLOAD = json.dumps({
    "doc_type": "invoice",
    "data": {
        "document_number": "LOAD-TEST-001",
        "date": "2024-03-15",
        "company": {"name": "Load Test Corp", "address": "123 Test St"},
        "bill_to": {"name": "Client", "address": "456 Test Ave"},
        "ship_to": {"name": "Client", "address": "456 Test Ave"},
        "line_items": [
            {"description": f"Test Item {i}", "hsn_code": "1234", "quantity": 10,
             "unit": "pcs", "rate": 100.0, "amount": 1000.0,
             "gst_rate": 18, "gst_amount": 180.0, "total": 1180.0}
            for i in range(5)
        ],
        "subtotal": 5000.0,
        "cgst": 450.0,
        "sgst": 450.0,
        "grand_total": 5900.0,
    },
}).encode("utf-8")


def send_request(url: str, results: list, idx: int):
    start = time.time()
    try:
        req = Request(
            f"{url}/api/v1/pdf/generate",
            data=SAMPLE_PAYLOAD,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req, timeout=30)
        elapsed = time.time() - start
        status = resp.status
        results.append({"idx": idx, "status": status, "time_ms": int(elapsed * 1000), "success": True})
    except Exception as e:
        elapsed = time.time() - start
        results.append({"idx": idx, "status": 0, "time_ms": int(elapsed * 1000), "success": False, "error": str(e)})


def main():
    parser = argparse.ArgumentParser(description="Load test for PDF service")
    parser.add_argument("--url", default="http://localhost:5000", help="Base URL")
    parser.add_argument("--concurrent", type=int, default=10, help="Concurrent requests")
    parser.add_argument("--total", type=int, default=50, help="Total requests")
    args = parser.parse_args()

    print(f"Load Test: {args.total} requests, {args.concurrent} concurrent")
    print(f"Target: {args.url}")
    print("-" * 50)

    all_results = []
    start_time = time.time()

    for batch_start in range(0, args.total, args.concurrent):
        batch_size = min(args.concurrent, args.total - batch_start)
        threads = []
        batch_results = []

        for i in range(batch_size):
            t = threading.Thread(target=send_request, args=(args.url, batch_results, batch_start + i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=60)

        all_results.extend(batch_results)
        print(f"  Batch {batch_start // args.concurrent + 1}: {len(batch_results)} requests completed")

    total_time = time.time() - start_time
    successes = [r for r in all_results if r["success"]]
    failures = [r for r in all_results if not r["success"]]
    times = [r["time_ms"] for r in successes]

    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Total requests:    {len(all_results)}")
    print(f"Successful:        {len(successes)}")
    print(f"Failed:            {len(failures)}")
    print(f"Total time:        {total_time:.1f}s")
    print(f"Throughput:        {len(successes) / total_time:.1f} req/s")

    if times:
        print(f"Avg latency:       {statistics.mean(times):.0f}ms")
        print(f"P50 latency:       {statistics.median(times):.0f}ms")
        print(f"P95 latency:       {sorted(times)[int(len(times) * 0.95)]:.0f}ms")
        print(f"P99 latency:       {sorted(times)[int(len(times) * 0.99)]:.0f}ms")
        print(f"Min latency:       {min(times)}ms")
        print(f"Max latency:       {max(times)}ms")

    if failures:
        print(f"\nFailure samples:")
        for f in failures[:5]:
            print(f"  Request {f['idx']}: {f.get('error', 'unknown')}")


if __name__ == "__main__":
    main()
