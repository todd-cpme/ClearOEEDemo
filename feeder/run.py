"""Entry point for both modes.

  python feeder/run.py --backfill        # ONE TIME, FIRST: 13d metrics + 6.5d logs
  python feeder/run.py                   # incremental (cron): last 20 min, idempotent

ORDER MATTERS. Grafana Cloud accepts old timestamps only when each series is
written oldest-to-newest (out-of-order window is ~2h behind a series' newest
sample; Loki ~1h per stream, hard cap 7 days old). Once the incremental feeder
has written "now", the past is closed. Backfill first. The GitHub workflow
enforces this with the .backfilled marker file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

from push import push_events, push_metrics
from simulator import Simulator, utcnow

METRIC_BACKFILL_DAYS = 13     # free-tier metric retention is 14d; older is wasted
LOG_BACKFILL_DAYS = 6.5       # Loki rejects entries older than 7d
COARSE_STEP_S = 300           # history resolution
FINE_STEP_S = 60              # recent / live resolution
FINE_WINDOW_H = 24


def probe() -> int:
    """Diagnostic: find how far back this stack accepts writes, and whether our
    line protocol is valid at all. Writes throwaway `probe`/`probe-events`
    series only - never touches the real `line` series, so the real backfill
    window stays open regardless of what this does."""
    import os

    import requests

    from simulator import Simulator

    now = utcnow().replace(second=0, microsecond=0)
    murl, mauth = os.environ["MIMIR_INFLUX_URL"], (os.environ["MIMIR_USER"], os.environ["GC_TOKEN"])
    lurl, lauth = os.environ["LOKI_URL"], (os.environ["LOKI_USER"], os.environ["GC_TOKEN"])

    print("=== real payload sample (first line the backfill would send) ===")
    from push import _line
    s = Simulator().samples(now - timedelta(minutes=2), now, 60)
    print(repr(_line(s[0])) if s else "(no samples)")

    print("\n=== metrics: age acceptance ===")
    for label, mins in [("now", 0), ("30m", 30), ("2h", 120), ("6h", 360), ("25h", 1500),
                        ("3d", 4320), ("7d", 10080), ("13d", 18720)]:
        ts = now - timedelta(minutes=mins)
        body = f"probe,age={label} v=1 {int(ts.timestamp() * 1e9)}"
        r = requests.post(murl, auth=mauth, data=body,
                          headers={"Content-Type": "text/plain"}, timeout=30)
        print(f"  {label:>4}: {r.status_code} body={r.text[:180]!r} "
              f"xerr={r.headers.get('x-error') or r.headers.get('X-Influxdb-Error')!r}")

    print("\n=== metrics: line-protocol variants at now ===")
    ts = int(now.timestamp() * 1e9)
    for name, body in [
        ("float field", f"probe2,k=a v=1.5 {ts}"),
        ("multi field", f"probe2,k=b pass_total=12.5,status=1 {ts}"),
        ("escaped tag", f"probe2,k=c,reason=Change\\ Over\\ -\\ Line\\ clearance v=1 {ts}"),
        ("ms precision", f"probe2,k=d v=1 {int(now.timestamp() * 1000)}"),
        ("no timestamp", "probe2,k=e v=1"),
    ]:
        r = requests.post(murl, auth=mauth, data=body,
                          headers={"Content-Type": "text/plain"}, timeout=30)
        print(f"  {name:>13}: {r.status_code} {r.text[:160]!r}")

    print("\n=== logs: age acceptance ===")
    for label, mins in [("now", 0), ("2h", 120), ("25h", 1500), ("3d", 4320), ("6.5d", 9360)]:
        ts_ns = str(int((now - timedelta(minutes=mins)).timestamp() * 1e9))
        payload = {"streams": [{"stream": {"job": "probe-events", "age": label},
                                "values": [[ts_ns, "probe entry"]]}]}
        r = requests.post(lurl, auth=lauth, data=json.dumps(payload),
                          headers={"Content-Type": "application/json"}, timeout=30)
        print(f"  {label:>4}: {r.status_code} {r.text[:180]!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--probe", action="store_true", help="diagnose write limits; writes throwaway series only")
    ap.add_argument("--dry-run", action="store_true", help="simulate, print stats, push nothing")
    args = ap.parse_args()

    if args.probe:
        return probe()

    sim = Simulator()
    now = utcnow().replace(second=0, microsecond=0)

    if args.backfill:
        chunks = []
        coarse_end = now - timedelta(hours=FINE_WINDOW_H)
        chunks.append((now - timedelta(days=METRIC_BACKFILL_DAYS), coarse_end, COARSE_STEP_S))
        chunks.append((coarse_end, now, FINE_STEP_S))
        ev_start = now - timedelta(days=LOG_BACKFILL_DAYS)
    else:
        chunks = [(now - timedelta(minutes=20), now, FINE_STEP_S)]  # overlap OK: deterministic
        ev_start = now - timedelta(minutes=20)

    n_metrics = 0
    for start, end, step in chunks:
        samples = sim.samples(start, end, step)
        if args.dry_run:
            n_metrics += len(samples)
            continue
        n_metrics += push_metrics(samples)
        print(f"metrics: pushed {start:%m-%d %H:%M} -> {end:%m-%d %H:%M} @ {step}s")

    events = sim.events(ev_start, now)
    if args.dry_run:
        print(f"DRY RUN: {n_metrics} samples, {len(events)} events")
        for e in events[:15]:
            print(f"  {e.ts:%m-%d %H:%M:%S} {e.factory}/{e.line} [{e.level:5}] {e.msg}")
        return 0
    n_events = push_events(events)
    print(f"done: {n_metrics} metric rows, {n_events} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
