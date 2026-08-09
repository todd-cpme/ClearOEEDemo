# ClearTrend Flex demo — Grafana Cloud feeder + dashboards

Synthetic factory (5 factories / 21 lines, modeled on client SSRS captures)
pushed to a free Grafana Cloud stack; shared via public dashboard links.
See `claude/demo-plan.md` in the ClearTrend Flex Demo project for the full plan.

## Layout

- `plant.yaml` — the entire plant model (factories, lines, targets, tunings). Rebuild for another prospect by editing this file only.
- `events.yaml` / `MACHINE_EVENTS.md` — machine event catalog correlated with downtimes.
- `feeder/` — deterministic simulator + Grafana Cloud push (metrics via Influx line-protocol endpoint, events via Loki push API).
- `dashboards/build_dashboards.py` — generates/pushes the 7 dashboards (overview, 5x hourly, trends+events).
- `.github/workflows/feed.yml` — backfill (manual dispatch) + 10-min cron.

## Setup order — IMPORTANT, the sequence is one-way

Backfill only works on series that have never seen a newer sample (Grafana
Cloud accepts historical data pushed oldest-to-newest; the out-of-order window
is ~2h for metrics, ~1h and max 7 days age for logs). **Run the backfill before
the cron ever fires.** The workflow guards this with the `.backfilled` marker.

1. Grafana Cloud: create/reuse the free stack. Collect:
   - Influx metrics endpoint URL + hosted-metrics instance ID
   - Loki push URL + hosted-logs instance ID
   - Cloud Access Policy token (`metrics:write`, `logs:write`)
   - Service-account token for dashboard push
2. GitHub repo secrets: `MIMIR_INFLUX_URL`, `MIMIR_USER`, `LOKI_URL`, `LOKI_USER`, `GC_TOKEN`.
3. Actions -> feed -> Run workflow with `backfill: true` (one time). Pushes 13 days
   of metrics (5-min resolution, last 24 h at 1-min) and 6.5 days of events.
4. Cron takes over automatically (guard clears once `.backfilled` is committed).
5. `PROM_UID=<uid> LOKI_UID=<uid> python dashboards/build_dashboards.py --push`
   (datasource UIDs from the stack's datasource settings; defaults usually
   `grafanacloud-<slug>-prom` / `-logs`).
6. In Grafana: Share -> Share externally on each dashboard; verify links in
   incognito + phone. No template variables anywhere — they break external sharing.

## Local smoke test (no credentials needed)

    pip install pyyaml requests
    python feeder/run.py --dry-run

## Free-tier boundaries this design respects

- 10k active series cap (we use ~600), 14-day metric retention (backfill 13d),
  Loki 7-day max sample age (backfill 6.5d), no variables on shared dashboards.
- GitHub schedules pause after 60 days repo inactivity; workflow self-commits
  a monthly heartbeat.

## Demo-day notes

- Trends dashboard is the closer: state timeline + downtime Pareto + events
  table are things paginated SSRS structurally cannot show.
- Do not add the client's logo or real SVA numbers. Footer text panel carries
  the synthetic-data disclaimer.
- Expect the validation question (they're a validated site); bring a one-slide answer.
