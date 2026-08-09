# ClearTrend Flex demo - Grafana vs SSRS

Synthetic factory (5 factories / 21 lines, modeled on the client's SSRS captures)
rendered as live Grafana dashboards and shared by public link.

## How it works, and why

The obvious design was to push metrics into Grafana Cloud and backfill two weeks
of history. **That does not work on this stack.** Measured with `feeder/run.py
--probe`: the metrics endpoint accepts a sample 30 minutes old and rejects
anything 2 hours or older with a 400. There is no soak time before the demo, so
history has to come from somewhere else.

So the history is a **file**, not a time series database:

```
feeder/build_dataset.py  ->  data/*.csv  ->  Infinity datasource  ->  dashboards
        (GitHub Actions)      (this repo)     (reads raw.githubusercontent)
```

The dataset covers now-14d .. now+7d. Because it extends past the present, the
clock keeps moving through real data and the dashboards look live without any
ingestion. A 10-minute job refreshes the live files; a daily job slides the
window.

| file | grain | refresh |
|---|---|---|
| `hourly.csv` | per line per hour, 21 days | daily |
| `state.csv` | one row per run/down transition, 21 days | daily |
| `today.csv` | per line per hour, current production day | 10 min |
| `rollup_line.csv` / `rollup_factory.csv` | day totals + current status | 10 min |
| `events.csv` | machine events, last 4 days, never future | 10 min |
| `status.csv` | current state per line | 10 min |

Yield and OEE are computed in the generator, not with Grafana transformations,
so every table panel is a straight render of a CSV.

## Setup

1. Repo must be **public** (the dashboards fetch raw.githubusercontent). To keep
   it private instead, set the `DATA_BASE` repo variable to the contents API URL
   and add an auth header to the Infinity datasource.
2. Grafana: Administration -> Plugins -> install **Infinity**. Connections ->
   Data sources -> add Infinity, save, copy its UID from the URL.
3. Grafana: Administration -> Users and access -> Service accounts -> new account
   with Admin, then Add token.
4. Repo secrets: `GRAFANA_URL` (https://cpmeusa.grafana.net), `GRAFANA_SA_TOKEN`,
   `INFINITY_UID`.
5. Actions -> feed -> Run workflow with **push_dashboards: true**. Builds and
   pushes all 7 dashboards.
6. Each dashboard: Share -> Share externally. Verify in an incognito window.

## Constraints the design respects

- Externally shared dashboards run **backend** queries only, so every Infinity
  query sets `parser: "backend"`, and there are **no template variables**
  anywhere (they are unsupported on shared dashboards).
- Production calendar is Mon-Sat 06:00-22:00 America/Chicago, so outside those
  hours lines correctly read Down / "Outside production hours", and the daily
  report falls back to the last shift actually run rather than rendering blank.
- `raw.githubusercontent` caches for a few minutes, so live files can lag the
  10-minute job slightly. Fine at this cadence.

## Tuning

`plant.yaml` holds the entire plant: factories, lines, targets, rates, and the
per-line personalities (AAL05 is changeover-heavy, ABW07 takes multi-hour downs),
all lifted from the client's screens. `events.yaml` holds the machine event
catalog; see `MACHINE_EVENTS.md`. Rebuilding this demo for another prospect is
an edit to those two files plus a workflow run.

## Dead code, kept on purpose

`feeder/push.py` and `feeder/run.py` still implement the Prometheus/Loki push
path. They are not in the critical path, but `--probe` is how the write limits
above were measured, and the push path is the starting point for round two,
where this data comes from the real historian through the ClearTrend stack.

## Demo notes

- Lead with the trends dashboard: the all-lines state timeline, downtime Pareto,
  and correlated event table are things a paginated SSRS report structurally
  cannot show.
- Do not add the client's logo or their real SVA/validation numbers.
- They are a validated site; expect a question about validation and have an
  answer ready rather than improvising.
