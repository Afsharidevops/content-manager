#!/usr/bin/env python3
"""Small dependency-free Hermes HTTP/load benchmark.

Default target is /health and is non-generative. Use --chat to benchmark
/v1/chat/completions. For authenticated targets, put the token in
HERMES_BENCHMARK_TOKEN; it is never printed.
"""
from __future__ import annotations
import argparse, concurrent.futures, json, os, statistics, time, urllib.error, urllib.request


def one(url: str, timeout: float, chat: bool, model: str) -> tuple[bool, float, int]:
    headers = {"User-Agent": "hermes-v056-benchmark"}
    token = os.getenv("HERMES_BENCHMARK_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    method = "GET"
    if chat:
        method = "POST"
        headers["Content-Type"] = "application/json"
        data = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with OK only."}], "max_tokens": 8}).encode()
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = int(r.status)
            return 200 <= code < 400, (time.perf_counter() - start) * 1000, code
    except urllib.error.HTTPError as e:
        e.read()
        return False, (time.perf_counter() - start) * 1000, int(e.code)
    except Exception:
        return False, (time.perf_counter() - start) * 1000, 0


def pct(values: list[float], q: float) -> float:
    if not values: return 0.0
    values = sorted(values)
    return values[min(len(values)-1, max(0, int(round((len(values)-1)*q))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8787")
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--chat", action="store_true", help="benchmark chat completions instead of /health; may incur provider cost")
    ap.add_argument("--model", default="auto")
    args = ap.parse_args()
    count=max(1,args.requests); workers=max(1,min(args.concurrency,count))
    url=args.base_url.rstrip('/') + ('/v1/chat/completions' if args.chat else '/health')
    started=time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        rows=list(pool.map(lambda _: one(url,args.timeout,args.chat,args.model), range(count)))
    elapsed=time.perf_counter()-started
    lat=[x[1] for x in rows]; ok=sum(1 for x in rows if x[0]); codes={}
    for _,_,code in rows: codes[code]=codes.get(code,0)+1
    result={
        "target": "/v1/chat/completions" if args.chat else "/health",
        "requests": count, "concurrency": workers, "success": ok, "failed": count-ok,
        "success_rate": round(ok/count,4), "elapsed_seconds": round(elapsed,3),
        "requests_per_second": round(count/elapsed,2) if elapsed else 0,
        "latency_ms": {"mean": round(statistics.fmean(lat),2), "p50": round(pct(lat,.50),2), "p95": round(pct(lat,.95),2), "p99": round(pct(lat,.99),2), "max": round(max(lat),2)},
        "status_codes": codes,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok == count else 2

if __name__ == "__main__":
    raise SystemExit(main())
