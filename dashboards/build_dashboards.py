"""Generate the demo dashboards and (optionally) push them to Grafana.

  python dashboards/build_dashboards.py            # writes out/*.json
  python dashboards/build_dashboards.py --push     # also POSTs to Grafana

Env:
  GRAFANA_URL       https://cpmeusa.grafana.net
  GRAFANA_SA_TOKEN  service-account token with dashboard write
  INFINITY_UID      uid of the Infinity datasource (Connections -> Data sources)
  DATA_BASE         raw base URL for the CSVs (default: this repo's main branch)

WHY INFINITY AND NOT PROMETHEUS: measured on this stack, the metrics endpoint
accepts samples ~30 min old but rejects 2 h+ (400). Weeks of history cannot be
written through it, and the demo has no time to soak. The CSVs in data/ carry
the full 14-day window plus a few days into the future, so the history is there
the moment the datasource is pointed at them.

Infinity queries use parser: "backend" throughout — externally shared
dashboards only execute backend queries, so a frontend parser would render
blank on the public link. No template variables anywhere, same reason.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboards" / "out"
INF = {"type": "yesoreyeram-infinity-datasource", "uid": os.environ.get("INFINITY_UID", "infinity")}
BASE = os.environ.get(
    "DATA_BASE", "https://raw.githubusercontent.com/todd-cpme/ClearOEEDemo/main/data")

plant = yaml.safe_load((ROOT / "plant.yaml").read_text())
FACTORIES = plant["factories"]
LEG = plant["legend"]

_pid = 0


def pid() -> int:
    global _pid
    _pid += 1
    return _pid


def col(sel, text, typ="string"):
    return {"selector": sel, "text": text, "type": typ}


def q(file, columns, ref="A", fmt="table", filt=""):
    """One Infinity backend query against a CSV in data/."""
    return {
        "refId": ref, "datasource": INF, "type": "csv", "source": "url",
        "format": fmt, "parser": "backend", "url": f"{BASE}/{file}",
        "url_options": {"method": "GET", "data": ""}, "root_selector": "",
        "columns": columns, "filterExpression": filt, "computed_columns": [],
    }


def thr(steps):
    return {"mode": "absolute", "steps": steps}


def yield_thr(target):
    return thr([{"color": "red", "value": None},
                {"color": "yellow", "value": LEG["yield_yellow"]},
                {"color": "green", "value": LEG["yield_green"]}])


def oee_thr(target):
    return thr([{"color": "red", "value": None},
                {"color": "yellow", "value": round(target * 0.85, 1)},
                {"color": "green", "value": target}])


STATUS_OVERRIDE = {
    "matcher": {"id": "byName", "options": "Status"},
    "properties": [
        {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
        {"id": "mappings", "value": [{"type": "value", "options": {
            "Running": {"text": "Running", "color": "green", "index": 0},
            "Down": {"text": "Down", "color": "red", "index": 1}}}]}]}


def footer(y):
    return {"id": pid(), "type": "text", "gridPos": {"x": 0, "y": y, "w": 24, "h": 3},
            "options": {"mode": "markdown", "content":
                        "**Demo data - synthetic.** ClearTrend Flex by CPME USA. "
                        "Live feed, page refreshes every minute; underlying data regenerates "
                        "every 10 minutes. Production calendar Mon-Sat 06:00-22:00, so lines "
                        "read Down outside those hours."}}


TAB_ORDER = [
    ("cleartrend-overview", "Plant overview"),
    ("cleartrend-daily-assembly", "Assembly"),
    ("cleartrend-daily-molding", "Molding"),
    ("cleartrend-daily-coating", "Coating"),
    ("cleartrend-daily-packaging", "Packaging"),
    ("cleartrend-daily-kitting", "Kitting"),
    ("cleartrend-trends", "Trends & events"),
]
LINKS_FILE = ROOT / "dashboards" / "public_links.json"
LINKS = json.loads(LINKS_FILE.read_text()) if LINKS_FILE.exists() else {}
PUBLIC_BASE = os.environ.get(
    "GRAFANA_URL", "https://cpmeusa.grafana.net").rstrip("/") + "/public-dashboards"
NAV_H = 3


def nav(active):
    """Tab bar across the seven dashboards.

    Native dashboard links are unreliable on externally shared dashboards, but a
    text panel is just a panel and always renders, so the tabs are markdown
    links pointing at each dashboard's public URL. Tokens live in
    public_links.json; regenerating a share link means updating that file.
    """
    parts = []
    for uid, label in TAB_ORDER:
        if uid == active:
            parts.append(f"**{label}**")
        elif uid in LINKS:
            parts.append(f"[{label}]({PUBLIC_BASE}/{LINKS[uid]})")
        else:
            parts.append(label)
    return {"id": pid(), "type": "text", "transparent": True,
            "gridPos": {"x": 0, "y": 0, "w": 24, "h": NAV_H},
            "options": {"mode": "markdown", "content":
                        "## ClearTrend Flex &nbsp;&nbsp; plant demo\n\n" +
                        " &nbsp;|&nbsp; ".join(parts)}}


def dash(uid, title, panels, time_from="now-24h"):
    for p in panels:                      # make room for the tab bar at the top
        p["gridPos"]["y"] += NAV_H
    panels = [nav(uid)] + panels
    return {"dashboard": {
        "uid": uid, "title": title, "panels": panels, "schemaVersion": 39,
        "time": {"from": time_from, "to": "now"}, "refresh": "1m",
        "timezone": plant["production"]["timezone"], "editable": True,
        "tags": ["cleartrend-demo"], "templating": {"list": []},
    }, "overwrite": True, "message": "built by build_dashboards.py"}


# ---------------------------------------------------------------- overview

def factory_table(fac, fcfg, x, y):
    return {
        "id": pid(), "type": "table",
        "title": f'{fcfg["display"]}   [ Yield target {fcfg["yield_target"]}% · OEE target {fcfg["oee_target"]}% ]',
        "datasource": INF, "gridPos": {"x": x, "y": y, "w": 12, "h": 9},
        "targets": [q("rollup_line.csv", [
            col("factory", "factory"), col("line", "Line"), col("pass", "Pass", "number"),
            col("fail", "Fail", "number"), col("yield_pct", "Yield%", "number"),
            col("oee_pct", "OEE%", "number"), col("state", "Status"),
            col("duration_min", "Duration", "number"), col("reason", "Reason"),
        ], filt=f'factory == "{fac}"')],
        # Infinity can only filter on a column it selected, and it returns fields
        # in alphabetical order, so the filter column rides along and gets hidden
        # here while indexByName restores the client's column order.
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"factory": True}, "renameByName": {},
            "indexByName": {"Line": 0, "Pass": 1, "Fail": 2, "Yield%": 3, "OEE%": 4,
                             "Status": 5, "Duration": 6, "Reason": 7}}}],
        "fieldConfig": {
            "defaults": {"custom": {"align": "center", "cellOptions": {"type": "auto"},
                                     "filterable": False}, "decimals": 0},
            "overrides": [
                {"matcher": {"id": "byName", "options": "Yield%"},
                 "properties": [{"id": "decimals", "value": 2},
                                {"id": "custom.cellOptions",
                                 "value": {"type": "color-background", "mode": "basic"}},
                                {"id": "thresholds", "value": yield_thr(fcfg["yield_target"])}]},
                {"matcher": {"id": "byName", "options": "OEE%"},
                 "properties": [{"id": "decimals", "value": 2},
                                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                {"id": "thresholds", "value": oee_thr(fcfg["oee_target"])}]},
                STATUS_OVERRIDE,
                {"matcher": {"id": "byName", "options": "Reason"},
                 "properties": [{"id": "custom.align", "value": "left"}]},
                # eight columns in a half-width panel: without explicit widths the
                # last two get pushed out of view behind a scrollbar
                {"matcher": {"id": "byName", "options": "Line"},
                 "properties": [{"id": "custom.width", "value": 78}]},
                {"matcher": {"id": "byName", "options": "Pass"},
                 "properties": [{"id": "custom.width", "value": 78}]},
                {"matcher": {"id": "byName", "options": "Fail"},
                 "properties": [{"id": "custom.width", "value": 68}]},
                {"matcher": {"id": "byName", "options": "Yield%"},
                 "properties": [{"id": "custom.width", "value": 80}]},
                {"matcher": {"id": "byName", "options": "OEE%"},
                 "properties": [{"id": "custom.width", "value": 78}]},
                {"matcher": {"id": "byName", "options": "Status"},
                 "properties": [{"id": "custom.width", "value": 86}]},
                {"matcher": {"id": "byName", "options": "Duration"},
                 "properties": [{"id": "custom.width", "value": 78}]},
            ]},
    }


def overview():
    panels = [{
        "id": pid(), "type": "table", "title": "Plant rollup - current production day",
        "datasource": INF, "gridPos": {"x": 5, "y": 0, "w": 14, "h": 8},
        "targets": [q("rollup_factory.csv", [
            col("factory_display", "Factory"), col("pass", "Pass", "number"),
            col("fail", "Fail", "number"), col("yield_pct", "Yield%", "number"),
            col("oee_pct", "OEE%", "number"), col("yield_target", "Yield tgt", "number"),
            col("oee_target", "OEE tgt", "number")])],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {}, "renameByName": {},
            "indexByName": {"Factory": 0, "Pass": 1, "Fail": 2, "Yield%": 3,
                             "OEE%": 4, "Yield tgt": 5, "OEE tgt": 6}}}],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}, "decimals": 2},
                        "overrides": [
                            {"matcher": {"id": "byName", "options": "Pass"},
                             "properties": [{"id": "decimals", "value": 0}]},
                            {"matcher": {"id": "byName", "options": "Fail"},
                             "properties": [{"id": "decimals", "value": 0}]}]},
    }]
    for (fac, fcfg), (x, y) in zip(FACTORIES.items(),
                                   [(0, 7), (12, 7), (0, 16), (12, 16), (0, 25)]):
        panels.append(factory_table(fac, fcfg, x, y))
    panels.append(footer(34))
    return dash("cleartrend-overview", "Plant Yield and OEE Dashboard", panels)


# ------------------------------------------------------------ daily detail

def hourly(fac, fcfg):
    panels, y = [], 0
    for line in fcfg["lines"]:
        panels.append({
            "id": pid(), "type": "table", "title": f"{line} - hourly",
            "datasource": INF, "gridPos": {"x": 0, "y": y, "w": 14, "h": 12},
            "targets": [q("today.csv", [
                col("line", "line"),
                col("ts", "Hour", "timestamp"), col("pass", "Pass", "number"),
                col("fail", "Fail", "number"), col("yield_pct", "Yield%", "number"),
                col("avail_pct", "Avail%", "number"), col("perform_pct", "Perform%", "number"),
                col("oee_pct", "OEE%", "number")], filt=f'line == "{line}"')],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {"line": True}, "renameByName": {},
                "indexByName": {"Hour": 0, "Pass": 1, "Fail": 2, "Yield%": 3,
                                 "Avail%": 4, "Perform%": 5, "OEE%": 6}}}],
            "fieldConfig": {
                "defaults": {"custom": {"align": "center"}, "decimals": 2},
                "overrides": [
                    {"matcher": {"id": "byName", "options": "Hour"},
                     "properties": [{"id": "unit", "value": "time:HH:mm"}]},
                    {"matcher": {"id": "byName", "options": "Pass"},
                     "properties": [{"id": "decimals", "value": 0},
                                    {"id": "custom.cellOptions",
                                     "value": {"type": "color-background", "mode": "basic"}},
                                    {"id": "thresholds", "value": thr([
                                        {"color": "red", "value": None},
                                        {"color": "yellow", "value": LEG["pass_hr_yellow"]},
                                        {"color": "green", "value": LEG["pass_hr_green"]}])}]},
                    {"matcher": {"id": "byName", "options": "Fail"},
                     "properties": [{"id": "decimals", "value": 0}]},
                    {"matcher": {"id": "byName", "options": "Yield%"},
                     "properties": [{"id": "custom.cellOptions",
                                     "value": {"type": "color-background", "mode": "basic"}},
                                    {"id": "thresholds", "value": yield_thr(fcfg["yield_target"])}]},
                ]},
        })
        panels.append({
            "id": pid(), "type": "state-timeline", "title": f"{line} - state",
            "datasource": INF, "gridPos": {"x": 14, "y": y, "w": 10, "h": 12},
            "targets": [q("state.csv", [col("ts", "Time", "timestamp"),
                                        col("line", "line"),
                                        col("state", "State")], filt=f'line == "{line}"')],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {"line": True}, "renameByName": {}, "indexByName": {}}}],
            "options": {"mergeValues": True, "showValue": "never", "rowHeight": 0.9,
                        "legend": {"showLegend": False}},
            "fieldConfig": {"defaults": {"custom": {"fillOpacity": 90, "lineWidth": 0},
                                          "mappings": [{"type": "value", "options": {
                                              "Running": {"color": "green", "index": 0},
                                              "Down": {"color": "red", "index": 1}}}]},
                            "overrides": []},
        })
        y += 12
    panels.append(footer(y))
    return dash(f"cleartrend-daily-{fac.lower()}", f"Daily {fcfg['display']} OEE", panels)


# ------------------------------------------------------------------ trends

def trends():
    panels = [
        {"id": pid(), "type": "state-timeline",
         "title": "Every line, running vs down - the view a paginated report cannot give you",
         "datasource": INF, "gridPos": {"x": 0, "y": 0, "w": 24, "h": 11},
         "targets": [q("state.csv", [col("ts", "Time", "timestamp"), col("line", "line"),
                                     col("state", "State")])],
         "transformations": [{"id": "partitionByValues",
                              "options": {"fields": ["line"], "keepFields": False}}],
         "options": {"mergeValues": True, "showValue": "never", "rowHeight": 0.9,
                     "legend": {"showLegend": False}},
         "fieldConfig": {"defaults": {"custom": {"fillOpacity": 90, "lineWidth": 0},
                                       "displayName": "${__field.labels.line}",
                                       "mappings": [{"type": "value", "options": {
                                           "Running": {"color": "green", "index": 0},
                                           "Down": {"color": "red", "index": 1}}}]},
                         "overrides": []}},

        {"id": pid(), "type": "barchart", "title": "Downtime by reason - minutes, full 21-day window",
         "datasource": INF, "gridPos": {"x": 0, "y": 11, "w": 9, "h": 10},
         "targets": [q("state.csv", [col("reason", "Reason"), col("state", "state"),
                                     col("duration_min", "Minutes", "number")],
                       filt='state == "Down" && Minutes > 0')],
         "transformations": [
             {"id": "groupBy", "options": {"fields": {
                 "Reason": {"aggregations": [], "operation": "groupby"},
                 "Minutes": {"aggregations": ["sum"], "operation": "aggregate"}}}},
             {"id": "sortBy", "options": {"sort": [{"field": "Minutes (sum)", "desc": True}]}}],
         "options": {"orientation": "horizontal", "xTickLabelRotation": 0,
                     "legend": {"showLegend": False}},
         "fieldConfig": {"defaults": {"decimals": 0, "color": {"mode": "continuous-RdYlGr"}},
                         "overrides": []}},

        {"id": pid(), "type": "timeseries", "title": "Yield % by factory",
         "datasource": INF, "gridPos": {"x": 9, "y": 11, "w": 15, "h": 10},
         "targets": [q("hourly.csv", [col("ts", "Time", "timestamp"),
                                      col("factory", "factory"),
                                      col("yield_pct", "Yield%", "number")])],
         "transformations": [{"id": "partitionByValues",
                              "options": {"fields": ["factory"], "keepFields": False}}],
         "options": {"legend": {"showLegend": True, "placement": "bottom", "displayMode": "list"}},
         "fieldConfig": {"defaults": {"custom": {"lineWidth": 2, "fillOpacity": 5,
                                                  "spanNulls": True},
                                       "displayName": "${__field.labels.factory}",
                                       "unit": "percent", "min": 80, "max": 100,
                                       "decimals": 2}, "overrides": []}},

        {"id": pid(), "type": "table",
         "title": "Machine events - last 4 days, newest first",
         "datasource": INF, "gridPos": {"x": 0, "y": 21, "w": 24, "h": 12},
         "targets": [q("events.csv", [
             col("ts", "Time", "timestamp"), col("factory", "Factory"),
             col("line", "Line"), col("level", "Level"), col("src", "Source"),
             col("message", "Event")])],
         "transformations": [{"id": "sortBy",
                              "options": {"sort": [{"field": "Time", "desc": True}]}}],
         "fieldConfig": {"defaults": {"custom": {"align": "left", "filterable": True}},
                         "overrides": [
                             {"matcher": {"id": "byName", "options": "Time"},
                              "properties": [{"id": "custom.width", "value": 170}]},
                             {"matcher": {"id": "byName", "options": "Level"},
                              "properties": [
                                  {"id": "custom.width", "value": 80},
                                  {"id": "custom.cellOptions",
                                   "value": {"type": "color-background", "mode": "basic"}},
                                  {"id": "mappings", "value": [{"type": "value", "options": {
                                      "error": {"color": "red", "index": 0},
                                      "warn": {"color": "orange", "index": 1},
                                      "info": {"color": "transparent", "index": 2}}}]}]},
                             {"matcher": {"id": "byName", "options": "Source"},
                              "properties": [{"id": "custom.width", "value": 90}]},
                         ]}},
        footer(33),
    ]
    return dash("cleartrend-trends", "Plant Trends and Machine Events - ClearTrend Flex",
                panels, time_from="now-3d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    global _pid

    boards = []
    _pid = 0
    boards.append(overview())
    for fac, fcfg in FACTORIES.items():
        _pid = 0
        boards.append(hourly(fac, fcfg))
    _pid = 0
    boards.append(trends())

    OUT.mkdir(parents=True, exist_ok=True)
    for d in boards:
        (OUT / f'{d["dashboard"]["uid"]}.json').write_text(json.dumps(d, indent=1))
        print("wrote", d["dashboard"]["uid"], "-", len(d["dashboard"]["panels"]), "panels")

    if args.push:
        import requests
        url = os.environ["GRAFANA_URL"].rstrip("/") + "/api/dashboards/db"
        h = {"Authorization": f'Bearer {os.environ["GRAFANA_SA_TOKEN"]}'}
        for d in boards:
            r = requests.post(url, json=d, headers=h, timeout=30)
            if not r.ok:
                raise SystemExit(f'{d["dashboard"]["title"]}: {r.status_code} {r.text[:300]}')
            print("pushed", d["dashboard"]["title"], "->", r.json().get("url"))


if __name__ == "__main__":
    main()
