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
import sys
from datetime import timedelta

from push import push_events, push_metrics
from simulator import Simulator, utcnow

METRIC_BACKFILL_DAYS = 13     # free-tier metric retention is 14d; older is wasted
LOG_BACKFILL_DAYS = 6.5       # Loki rejects entries older than 7d
COARSE_STEP_S = 300           # history resolution
FINE_STEP_S = 60              # recent / live resolution
FINE_WINDOW_H = 24


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="simulate, print stats, push nothing")
    args = ap.parse_args()

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
