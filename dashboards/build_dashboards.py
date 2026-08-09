"""Generate the three demo dashboards from plant.yaml and (optionally) push
them to Grafana via the HTTP API.

  python dashboards/build_dashboards.py            # writes out/*.json
  python dashboards/build_dashboards.py --push     # also POSTs to Grafana

Push env vars:
  GRAFANA_URL       e.g. https://cleartrend-demo.grafana.net
  GRAFANA_SA_TOKEN  service-account token with dashboard write

Metric names (from the Influx line-protocol push):
  line_pass_total line_fail_total line_status line_yield_pct line_avail_pct
  line_perform_pct line_oee_pct downtime_down_seconds_total{reason}
  state_info{reason}
Datasource UIDs: set PROM_UID / LOKI_UID below after stack creation.

These are scaffolds faithful to the client's SSRS layout; expect one iteration
pass against the live stack (column widths, overrides) before sharing links.
Shared-dashboard rules honored: no template variables, no library panels.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboards" / "out"
PROM_UID = os.environ.get("PROM_UID", "grafanacloud-prom")
LOKI_UID = os.environ.get("LOKI_UID", "grafanacloud-logs")
PROM = {"type": "prometheus", "uid": PROM_UID}
LOKI = {"type": "loki", "uid": LOKI_UID}

plant = yaml.safe_load((ROOT / "plant.yaml").read_text())
FACTORIES = plant["factories"]
LEGEND = plant["legend"]

_pid = 0


def pid() -> int:
    global _pid
    _pid += 1
    return _pid


def q(expr, ref, instant=True, legend=None, interval=None):
    t = {"datasource": PROM, "expr": expr, "refId": ref, "format": "table" if instant else "time_series",
         "instant": instant, "range": not instant}
    if legend:
        t["legendFormat"] = legend
    if interval:
        t["interval"] = interval
    return t


def yield_thresholds(target):
    return {"mode": "absolute", "steps": [
        {"color": "red", "value": None},
        {"color": "yellow", "value": LEGEND["yield_yellow"]},
        {"color": "green", "value": LEGEND["yield_green"]}]}


def factory_table(fac, fcfg, x, y, w=12, h=9):
    f = fac
    return {
        "id": pid(), "type": "table", "title": f'{fcfg["display"]}   [ Yield >= {fcfg["yield_target"]}% / OEE >= {fcfg["oee_target"]}% ]',
        "datasource": PROM, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [
            q(f'sum by (line) (increase(line_pass_total{{factory="{f}"}}[$__range]))', "Pass"),
            q(f'sum by (line) (increase(line_fail_total{{factory="{f}"}}[$__range]))', "Fail"),
            q(f'100 * sum by (line) (increase(line_pass_total{{factory="{f}"}}[$__range])) / clamp_min(sum by (line) (increase(line_pass_total{{factory="{f}"}}[$__range]) + increase(line_fail_total{{factory="{f}"}}[$__range])), 1)', "Yield"),
            q(f'avg by (line) (avg_over_time(line_oee_pct{{factory="{f}"}}[$__range]))', "OEE"),
            q(f'last_over_time(line_status{{factory="{f}"}}[10m])', "Status"),
            q(f'sum by (line) (increase(downtime_down_seconds_total{{factory="{f}"}}[$__range])) / 60', "Down"),
            q(f'last_over_time(state_info{{factory="{f}"}}[10m])', "Reason"),
        ],
        "transformations": [
            {"id": "joinByField", "options": {"byField": "line", "mode": "outer"}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "factory": True, "__name__": True},
                "renameByName": {"line": "Line", "Value #Pass": "Pass", "Value #Fail": "Fail",
                                  "Value #Yield": "Yield %", "Value #OEE": "OEE %",
                                  "Value #Status": "Status", "Value #Down": "Down min (range)",
                                  "reason": "Reason"},
                "excludeByNameRegex": "Value #Reason"}},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "center", "cellOptions": {"type": "auto"}}, "decimals": 0},
            "overrides": [
                {"matcher": {"id": "byName", "options": "Yield %"},
                 "properties": [{"id": "decimals", "value": 2},
                                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                {"id": "thresholds", "value": yield_thresholds(fcfg["yield_target"])}]},
                {"matcher": {"id": "byName", "options": "OEE %"},
                 "properties": [{"id": "decimals", "value": 2},
                                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                                    {"color": "red", "value": None},
                                    {"color": "yellow", "value": fcfg["oee_target"] * 0.85},
                                    {"color": "green", "value": fcfg["oee_target"]}]}}]},
                {"matcher": {"id": "byName", "options": "Status"},
                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                {"id": "mappings", "value": [{"type": "value", "options": {
                                    "1": {"text": "Running", "color": "green"},
                                    "0": {"text": "Down", "color": "red"}}}]}]},
            ]},
    }


def overview_dashboard():
    panels = [{
        "id": pid(), "type": "table", "title": "Plant rollup",
        "datasource": PROM, "gridPos": {"x": 6, "y": 0, "w": 12, "h": 7},
        "targets": [
            q('sum by (factory) (increase(line_pass_total[$__range]))', "Pass"),
            q('sum by (factory) (increase(line_fail_total[$__range]))', "Fail"),
            q('100 * sum by (factory) (increase(line_pass_total[$__range])) / clamp_min(sum by (factory) (increase(line_pass_total[$__range]) + increase(line_fail_total[$__range])), 1)', "Yield"),
            q('avg by (factory) (avg_over_time(line_oee_pct[$__range]))', "OEE"),
        ],
        "transformations": [
            {"id": "joinByField", "options": {"byField": "factory", "mode": "outer"}},
            {"id": "organize", "options": {"excludeByName": {"Time": True},
                "renameByName": {"factory": "Factory", "Value #Pass": "Pass", "Value #Fail": "Fail",
                                  "Value #Yield": "Yield %", "Value #OEE": "OEE %"}}}],
        "fieldConfig": {"defaults": {"decimals": 2, "custom": {"align": "center"}}, "overrides": []},
    }]
    pos = [(0, 7), (12, 7), (0, 16), (12, 16), (0, 25)]
    for (fac, fcfg), (x, y) in zip(FACTORIES.items(), pos):
        panels.append(factory_table(fac, fcfg, x, y))
    panels.append(footer_panel(0, 34))
    return dashboard("cleartrend-overview", "Libre Yield and OEE Dashboard", panels,
                     time_from="now/d+6h")  # production day starts 06:00


def hourly_dashboard(fac, fcfg):
    panels = []
    y = 0
    for line in fcfg["lines"]:
        panels.append({
            "id": pid(), "type": "table", "title": f"{line} - hourly",
            "datasource": PROM, "gridPos": {"x": 0, "y": y, "w": 14, "h": 8},
            "interval": "1h",
            "targets": [
                q(f'sum(increase(line_pass_total{{line="{line}"}}[1h]))', "Pass", instant=False, legend="Pass", interval="1h"),
                q(f'sum(increase(line_fail_total{{line="{line}"}}[1h]))', "Fail", instant=False, legend="Fail", interval="1h"),
                q(f'avg(avg_over_time(line_yield_pct{{line="{line}"}}[1h]))', "Yield", instant=False, legend="Yield %", interval="1h"),
                q(f'avg(avg_over_time(line_avail_pct{{line="{line}"}}[1h]))', "Avail", instant=False, legend="Avail %", interval="1h"),
                q(f'avg(avg_over_time(line_perform_pct{{line="{line}"}}[1h]))', "Perform", instant=False, legend="Perform %", interval="1h"),
                q(f'avg(avg_over_time(line_oee_pct{{line="{line}"}}[1h]))', "OEE", instant=False, legend="OEE %", interval="1h"),
            ],
            "transformations": [
                {"id": "joinByField", "options": {"byField": "Time", "mode": "outer"}},
                {"id": "formatTime", "options": {"timeField": "Time", "outputFormat": "HH:00"}},
            ],
            "fieldConfig": {"defaults": {"decimals": 2, "custom": {"align": "center"}}, "overrides": [
                {"matcher": {"id": "byName", "options": "Pass"},
                 "properties": [{"id": "decimals", "value": 0},
                                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                                    {"color": "red", "value": None},
                                    {"color": "yellow", "value": LEGEND["pass_hr_yellow"]},
                                    {"color": "green", "value": LEGEND["pass_hr_green"]}]}}]},
                {"matcher": {"id": "byName", "options": "Yield %"},
                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                {"id": "thresholds", "value": yield_thresholds(fcfg["yield_target"])}]},
            ]},
        })
        panels.append({
            "id": pid(), "type": "state-timeline", "title": f"{line} - state",
            "datasource": PROM, "gridPos": {"x": 14, "y": y, "w": 10, "h": 8},
            "targets": [q(f'line_status{{line="{line}"}}', "S", instant=False, legend=line)],
            "fieldConfig": {"defaults": {"custom": {"fillOpacity": 80}, "mappings": [
                {"type": "value", "options": {"1": {"text": "Running", "color": "green"},
                                               "0": {"text": "Down", "color": "red"}}}]}, "overrides": []},
        })
        y += 8
    panels.append(footer_panel(0, y))
    return dashboard(f"cleartrend-hourly-{fac.lower()}", f"Daily {fcfg['display']} OEE", panels,
                     time_from="now/d+6h")


def trends_dashboard():
    panels = [
        {"id": pid(), "type": "state-timeline", "title": "All lines - running / down (the panel SSRS can't do)",
         "datasource": PROM, "gridPos": {"x": 0, "y": 0, "w": 24, "h": 10},
         "targets": [q('line_status', "S", instant=False, legend="{{line}}")],
         "fieldConfig": {"defaults": {"custom": {"fillOpacity": 80}, "mappings": [
             {"type": "value", "options": {"1": {"text": "Running", "color": "green"},
                                            "0": {"text": "Down", "color": "red"}}}]}, "overrides": []}},
        {"id": pid(), "type": "bargauge", "title": "Downtime Pareto by reason (min)",
         "datasource": PROM, "gridPos": {"x": 0, "y": 10, "w": 8, "h": 9},
         "options": {"displayMode": "gradient", "orientation": "horizontal"},
         "targets": [q('sort_desc(sum by (reason) (increase(downtime_down_seconds_total[$__range])) / 60)', "P")],
         "fieldConfig": {"defaults": {"decimals": 0, "color": {"mode": "continuous-RdYlGr"}}, "overrides": []}},
        {"id": pid(), "type": "timeseries", "title": "Yield % by factory vs target",
         "datasource": PROM, "gridPos": {"x": 8, "y": 10, "w": 16, "h": 9},
         "targets": [
             q('avg by (factory) (line_yield_pct)', "Y", instant=False, legend="{{factory}}"),
         ],
         "fieldConfig": {"defaults": {"custom": {"lineWidth": 2}, "unit": "percent", "min": 80}, "overrides": []}},
        # Machine events - timestamped table (primary view per Todd), from Loki
        {"id": pid(), "type": "table", "title": "Machine events (correlate with downtimes above)",
         "datasource": LOKI, "gridPos": {"x": 0, "y": 19, "w": 24, "h": 10},
         "targets": [{"datasource": LOKI, "refId": "E",
                      "expr": '{job="plant-events"}', "queryType": "range"}],
         "transformations": [
             {"id": "extractFields", "options": {"source": "labels", "format": "json"}},
             {"id": "organize", "options": {
                 "excludeByName": {"labels": True, "tsNs": True, "id": True, "job": True},
                 "renameByName": {"Time": "Time", "factory": "Factory", "line": "Line",
                                   "level": "Level", "Line": "Event"}}}],
         "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": [
             {"matcher": {"id": "byName", "options": "Level"},
              "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}},
                             {"id": "mappings", "value": [{"type": "value", "options": {
                                 "error": {"color": "red"}, "warn": {"color": "yellow"},
                                 "info": {"color": "transparent"}}}]}]}]}},
        # Raw log stream (bonus view)
        {"id": pid(), "type": "logs", "title": "Event stream",
         "datasource": LOKI, "gridPos": {"x": 0, "y": 29, "w": 24, "h": 9},
         "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"},
         "targets": [{"datasource": LOKI, "refId": "L",
                      "expr": '{job="plant-events", level=~"warn|error"}', "queryType": "range"}]},
        footer_panel(0, 38),
    ]
    return dashboard("cleartrend-trends", "Plant Trends and Events - ClearTrend Flex", panels,
                     time_from="now-24h")


def footer_panel(x, y):
    return {"id": pid(), "type": "text", "gridPos": {"x": x, "y": y, "w": 24, "h": 2},
            "options": {"mode": "markdown", "content":
                        "**Demo data - synthetic.** ClearTrend Flex by CPME USA / Live feed, auto-refresh 1m"}}


def dashboard(uid, title, panels, time_from="now-12h"):
    return {"dashboard": {
        "uid": uid, "title": title, "panels": panels, "schemaVersion": 39,
        "time": {"from": time_from, "to": "now"}, "refresh": "1m",
        "timezone": plant["production"]["timezone"], "editable": True, "tags": ["cleartrend-demo"],
    }, "overwrite": True, "message": "built by build_dashboards.py"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    global _pid

    dashboards = []
    _pid = 0
    dashboards.append(overview_dashboard())
    for fac, fcfg in FACTORIES.items():
        _pid = 0
        dashboards.append(hourly_dashboard(fac, fcfg))
    _pid = 0
    dashboards.append(trends_dashboard())

    OUT.mkdir(parents=True, exist_ok=True)
    for d in dashboards:
        p = OUT / f'{d["dashboard"]["uid"]}.json'
        p.write_text(json.dumps(d, indent=1))
        print("wrote", p)

    if args.push:
        import requests
        url = os.environ["GRAFANA_URL"].rstrip("/") + "/api/dashboards/db"
        headers = {"Authorization": f'Bearer {os.environ["GRAFANA_SA_TOKEN"]}'}
        for d in dashboards:
            r = requests.post(url, json=d, headers=headers, timeout=30)
            r.raise_for_status()
            print("pushed", d["dashboard"]["title"], "->", r.json().get("url"))


if __name__ == "__main__":
    main()
