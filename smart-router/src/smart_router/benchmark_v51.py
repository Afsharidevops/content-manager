from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip(): rows.append(json.loads(line))
    return rows


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total=len(rows); correct=0; by_tier=defaultdict(lambda:[0,0]); false_fast=0; costs=[]; latencies=[]
    profiles=Counter(); tiers=Counter(); capability_violations=0
    for r in rows:
        expected=r.get("expected_tier"); actual=r.get("actual_tier") or r.get("tier"); profile=r.get("actual_profile") or r.get("profile") or actual
        tiers[str(actual)] += 1; profiles[str(profile)] += 1
        if expected:
            by_tier[str(expected)][1]+=1
            if actual==expected: correct+=1; by_tier[str(expected)][0]+=1
            if actual=="fast" and expected in {"standard","strong"}: false_fast+=1
        if r.get("capability_violation"): capability_violations+=1
        if isinstance(r.get("cost_usd"),(int,float)): costs.append(float(r["cost_usd"]))
        if isinstance(r.get("latency_ms"),(int,float)): latencies.append(float(r["latency_ms"]))
    return {
        "rows":total,
        "accuracy":round(correct/max(1,sum(v[1] for v in by_tier.values())),4),
        "per_tier_accuracy":{k:round(v[0]/max(1,v[1]),4) for k,v in by_tier.items()},
        "false_fast_rate":round(false_fast/max(1,total),4),
        "capability_violation_rate":round(capability_violations/max(1,total),6),
        "tier_distribution":dict(tiers),"profile_distribution":dict(profiles),
        "cost_total_usd":round(sum(costs),6),"cost_avg_usd":round(statistics.mean(costs),6) if costs else None,
        "latency_avg_ms":round(statistics.mean(latencies),2) if latencies else None,
        "latency_p95_ms":round(sorted(latencies)[max(0,int(len(latencies)*.95)-1)],2) if latencies else None,
    }


def markdown(s: dict[str, Any]) -> str:
    lines=["# Hermes Smart Router v0.5.1 Benchmark","",f"- Rows: **{s['rows']}**",f"- Routing accuracy: **{s['accuracy']*100:.2f}%**",f"- False-fast rate: **{s['false_fast_rate']*100:.2f}%**",f"- Capability violation rate: **{s['capability_violation_rate']*100:.4f}%**",f"- Total measured cost: **${s['cost_total_usd']:.6f}**",f"- Average latency: **{s['latency_avg_ms']} ms**",f"- p95 latency: **{s['latency_p95_ms']} ms**","","## Tier distribution","", "```json",json.dumps(s['tier_distribution'],indent=2),"```","","## Profile distribution","","```json",json.dumps(s['profile_distribution'],indent=2),"```","","## Per-tier accuracy","","```json",json.dumps(s['per_tier_accuracy'],indent=2),"```","","> Only publish cost/quality claims from representative held-out workloads with real measured usage and documented pricing."]
    return "\n".join(lines)+"\n"


def main() -> None:
    ap=argparse.ArgumentParser(description="Hermes Smart Router v0.5.1 routing/cost benchmark reporter")
    ap.add_argument("dataset",type=Path); ap.add_argument("--out",type=Path,default=Path("benchmark-v0.5.1")); a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True); s=score(load_rows(a.dataset))
    (a.out/"summary.json").write_text(json.dumps(s,indent=2)+"\n",encoding="utf-8")
    (a.out/"report.md").write_text(markdown(s),encoding="utf-8")
    print(json.dumps(s,indent=2))

if __name__=="__main__": main()
