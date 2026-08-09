"""Generate the static demo dataset that the dashboards read via the Infinity
datasource.

WHY THIS EXISTS: Grafana Cloud's metrics endpoint only accepts samples about an
hour old (measured: 30m accepted, 2h+ rejected with 400), so weeks of history
cannot be written through the push API. Instead we generate the whole window as
files, serve them straight from the repo, and let the dashboards read them. No
ingestion, no rate limits, no backfill window, and the full 14 days of history
is visible the moment the files land.

"Stays live" comes from generating PAST the current time: the dataset covers
now-14d .. now+FUTURE_DAYS, so `now` always sits inside the data and panels keep
moving as the clock advances. A daily workflow regenerates to slide the window.

Everything is deterministic from the simulator, so regenerating produces a
consistent world; only the window moves.

  python feeder/build_dataset.py            # writes data/*.csv + data/meta.json
"""
from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

from simulator import Simulator, utcnow

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HISTORY_DAYS = 14      # what the client sees as "trend depth"
FUTURE_DAYS = 7        # keeps `now` inside the data between regenerations
EVENT_PAST_DAYS = 4    # events are dense; bound the file so panels stay fast
EVENT_FUTURE_DAYS = 2


def iso(ts) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_csv(path: Path, header: list[str], rows: list[list]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    kb = path.stat().st_size / 1024
    return f"{path.name}: {len(rows)} rows, {kb:.0f} KB"


def build_hourly(sim: Simulator, start, end) -> list[list]:
    """One row per line per hour: the exact grain the client's SSRS report uses."""
    rows = []
    step = timedelta(hours=1)
    for fac, fcfg in sim.plant["factories"].items():
        for line, cfg in fcfg["lines"].items():
            t = start
            while t < end:
                nxt = t + step
                # integrate the hour at 60s, same maths as the metric path
                p = f = 0.0
                run = tot = 0
                perf_sum = 0.0
                m = t
                while m < nxt:
                    blk = sim.state_at(fac, line, m)
                    running = blk.state == "running"
                    tot += 1
                    if running:
                        run += 1
                        pf = sim.perf_factor(line, cfg, m)
                        perf_sum += pf
                        inc = cfg["uph"] / 3600.0 * 60 * pf
                        ff = sim.fail_frac(fac, line, cfg, m)
                        p += inc * (1 - ff)
                        f += inc * ff
                    m += timedelta(seconds=60)
                if run:
                    avail = 100.0 * run / tot
                    perform = 100.0 * perf_sum / run
                    yield_pct = 100.0 * p / (p + f) if (p + f) else 100.0
                    oee = avail / 100 * perform / 100 * yield_pct
                    rows.append([iso(t), fac, line, round(p), round(f),
                                 round(yield_pct, 2), round(avail, 2),
                                 round(perform, 2), round(oee, 2)])
                t = nxt
    rows.sort(key=lambda r: r[0])
    return rows


def build_state(sim: Simulator, start, end) -> list[list]:
    """One row per state transition. The state-timeline panel holds each value
    until the next row, so transitions alone draw the full timeline."""
    rows, order = [], {}
    for fi, (fac, fcfg) in enumerate(sim.plant["factories"].items()):
        for li, line in enumerate(fcfg["lines"]):
            order[line] = (fi, li)
            for b in sim.schedule(fac, line, end):
                if b.end < start or b.start > end:
                    continue
                if not sim.in_production(b.start):
                    continue
                reason = sim.events_cfg["reasons"].get(b.reason, {}).get("display", "") \
                    if b.state == "down" else ""
                dur = round((b.end - b.start).total_seconds() / 60, 1)
                rows.append([iso(b.start), fac, line,
                             "Running" if b.state == "running" else "Down",
                             reason, dur if b.state == "down" else 0])
            # Close every production day explicitly. A state timeline holds the
            # last value until the next row, so without this the final in-shift
            # state runs green straight through the night and the weekend.
            t = start.replace(minute=0, second=0, microsecond=0)
            while t <= end:
                if sim.in_production(t) and not sim.in_production(t + timedelta(hours=1)):
                    rows.append([iso(t + timedelta(hours=1)), fac, line, "Down",
                                 "Outside production hours", 0])
                t += timedelta(hours=1)
    # grouped by factory then line, time ascending within a line: partitionByValues
    # keeps this encounter order, which is what orders the timeline rows
    rows.sort(key=lambda r: (order[r[2]], r[0]))
    return rows


def build_rollups(sim: Simulator, today_rows, status_rows):
    """Per-line and per-factory totals for the current production day.

    Computed here rather than with Grafana transformations: yield is
    sum(pass)/sum(pass+fail), which takes three chained 'add field from
    calculation' steps in a panel and breaks quietly if a column is renamed.
    Precomputing keeps every table panel a straight render of a CSV."""
    per_line = {}
    for ts, fac, line, p, f, y, a, pf, oee in today_rows:
        d = per_line.setdefault((fac, line), {"p": 0.0, "f": 0.0, "oee": []})
        d["p"] += p
        d["f"] += f
        d["oee"].append(oee)
    status = {(r[1], r[2]): r for r in status_rows}

    line_rows, fac_rows = [], []
    for fac, fcfg in sim.plant["factories"].items():
        fp = ff = 0.0
        foee = []
        for line in fcfg["lines"]:                      # plant.yaml order, not alphabetical
            d = per_line.get((fac, line))
            st = status.get((fac, line), [None, fac, line, "Down", "", 0])
            if not d:
                line_rows.append([fac, line, 0, 0, 0.0, 0.0, st[3], st[4], st[5]])
                continue
            tot = d["p"] + d["f"]
            y = 100.0 * d["p"] / tot if tot else 0.0
            oee = sum(d["oee"]) / len(d["oee"]) if d["oee"] else 0.0
            fp += d["p"]
            ff += d["f"]
            foee.extend(d["oee"])
            line_rows.append([fac, line, round(d["p"]), round(d["f"]), round(y, 2),
                              round(oee, 2), st[3], st[4], st[5]])
        ftot = fp + ff
        fac_rows.append([fcfg["display"], fac, round(fp), round(ff),
                         round(100.0 * fp / ftot, 2) if ftot else 0.0,
                         round(sum(foee) / len(foee), 2) if foee else 0.0,
                         fcfg["yield_target"], fcfg["oee_target"]])
    return line_rows, fac_rows


def build_events(sim: Simulator, start, end) -> list[list]:
    return [[iso(e.ts), e.factory, e.line, e.level, e.src, e.msg]
            for e in sim.events(start, end)]


def production_day_start(sim: Simulator, now):
    """06:00 plant-local of the current production day (their reports run 06:00+)."""
    lt = now.astimezone(sim.tz)
    day = lt.replace(hour=sim.plant["production"]["start_hour"], minute=0,
                     second=0, microsecond=0)
    if lt < day:
        day -= timedelta(days=1)
    return day.astimezone(now.tzinfo)


def build_status(sim: Simulator, now) -> list[list]:
    """Current state per line: what the overview's Status/Duration/Reason show."""
    rows = []
    off_shift = not sim.in_production(now)
    for fac, fcfg in sim.plant["factories"].items():
        for line in fcfg["lines"]:
            if off_shift:
                # Outside the production calendar the simulator reports a
                # zero-length block, which would print "Scheduled Downtime, 0 min".
                # Label it for what it is instead of inventing a reason.
                rows.append([iso(now), fac, line, "Down", "Outside production hours",
                             round((now - last_production_end(sim, now)).total_seconds() / 60)])
                continue
            blk = sim.state_at(fac, line, now)
            down = blk.state != "running"
            reason = sim.events_cfg["reasons"].get(blk.reason, {}).get("display", "") if down else ""
            elapsed = round((now - blk.start).total_seconds() / 60)
            rows.append([iso(now), fac, line, "Down" if down else "Running",
                         reason, elapsed if down else 0])
    return rows


def last_production_end(sim: Simulator, now):
    """Most recent moment production was running, walking back hour by hour."""
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        if sim.in_production(t):
            return t + timedelta(hours=1)
        t -= timedelta(hours=1)
    return now


def last_production_day(sim: Simulator, now):
    """Start/end of the newest production day at or before `now`. Keeps the
    'Daily' report from rendering blank on a Sunday or before the 06:00 start,
    exactly as the shop would look at the last shift they actually ran."""
    end = min(now, last_production_end(sim, now))
    probe = end - timedelta(minutes=1)
    for _ in range(24 * 8):
        if sim.in_production(probe):
            break
        probe -= timedelta(hours=1)
    start = production_day_start(sim, probe)
    return start, end


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="fast files only (today/events/status); run every 10 min")
    args = ap.parse_args()

    sim = Simulator()
    real_now = utcnow()
    now = real_now.replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=HISTORY_DAYS)
    end = now + timedelta(days=FUTURE_DAYS)
    report = []

    if not args.live:
        # Slow-moving history. Extends past `now` so the clock keeps moving
        # through real data between daily regenerations.
        report.append(write_csv(
            DATA / "hourly.csv",
            ["ts", "factory", "line", "pass", "fail", "yield_pct", "avail_pct",
             "perform_pct", "oee_pct"],
            build_hourly(sim, start, end)))
        report.append(write_csv(
            DATA / "state.csv",
            ["ts", "factory", "line", "state", "reason", "duration_min"],
            build_state(sim, start, end)))

    # Live files. These must NOT contain future rows: the events table sorts
    # newest-first, and a future timestamp at the top gives the game away.
    day_start, day_end = last_production_day(sim, real_now)
    today_rows = build_hourly(sim, day_start, day_end)
    status_rows = build_status(sim, real_now)
    line_rows, fac_rows = build_rollups(sim, today_rows, status_rows)

    report.append(write_csv(
        DATA / "today.csv",
        ["ts", "factory", "line", "pass", "fail", "yield_pct", "avail_pct",
         "perform_pct", "oee_pct"], today_rows))
    report.append(write_csv(
        DATA / "events.csv",
        ["ts", "factory", "line", "level", "src", "message"],
        build_events(sim, real_now - timedelta(days=EVENT_PAST_DAYS), real_now)))
    report.append(write_csv(
        DATA / "status.csv",
        ["ts", "factory", "line", "state", "reason", "duration_min"], status_rows))
    report.append(write_csv(
        DATA / "rollup_line.csv",
        ["factory", "line", "pass", "fail", "yield_pct", "oee_pct", "state",
         "reason", "duration_min"], line_rows))
    report.append(write_csv(
        DATA / "rollup_factory.csv",
        ["factory_display", "factory", "pass", "fail", "yield_pct", "oee_pct",
         "yield_target", "oee_target"], fac_rows))

    meta = {}
    meta_path = DATA / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    meta["live_generated_at"] = iso(real_now)
    meta["today_covers"] = f"{iso(day_start)} .. {iso(day_end)}"
    meta["in_production_now"] = sim.in_production(real_now)
    if not args.live:
        meta.update({"window_generated_at": iso(real_now), "covers_from": iso(start),
                     "covers_to": iso(end), "regenerate_before": iso(end - timedelta(days=2))})
    meta["note"] = "Synthetic demo data. Regenerated by .github/workflows/feed.yml"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print("\n".join(report))
    print(f"live at {iso(real_now)}" + ("" if args.live else f" | window {iso(start)} .. {iso(end)}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
