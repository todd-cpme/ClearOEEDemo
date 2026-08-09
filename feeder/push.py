"""Push samples to Grafana Cloud Metrics (Influx line-protocol endpoint, no
protobuf needed) and events to Grafana Cloud Logs (Loki JSON push API).

Env vars (GitHub Actions secrets):
  MIMIR_INFLUX_URL  e.g. https://influx-prod-XX-prod-us-east-0.grafana.net/api/v1/push/influx/write
  MIMIR_USER        hosted-metrics instance ID (numeric)
  LOKI_URL          e.g. https://logs-prod-XXX.grafana.net/loki/api/v1/push
  LOKI_USER         hosted-logs instance ID (numeric)
  GC_TOKEN          Cloud Access Policy token (metrics:write + logs:write)
"""
from __future__ import annotations

import json
import os
import time

import requests

BATCH = 4000


def _esc(v: str) -> str:
    return v.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _line(sample) -> str:
    meas = sample.tags.pop("_measurement", "line")
    tags = {"factory": sample.factory, "line": sample.line, **sample.tags}
    tag_str = ",".join(f"{k}={_esc(str(v))}" for k, v in sorted(tags.items()))
    field_str = ",".join(f"{k}={v}" for k, v in sample.fields.items())
    ns = int(sample.ts.timestamp() * 1e9)
    return f"{meas},{tag_str} {field_str} {ns}"


def push_metrics(samples) -> int:
    url = os.environ["MIMIR_INFLUX_URL"]
    auth = (os.environ["MIMIR_USER"], os.environ["GC_TOKEN"])
    lines = [_line(s) for s in sorted(samples, key=lambda s: s.ts)]  # chronological: required for backfill
    for i in range(0, len(lines), BATCH):
        body = "\n".join(lines[i:i + BATCH])
        _post(url, auth, body, headers={"Content-Type": "text/plain"})
    return len(lines)


def push_events(events) -> int:
    url = os.environ["LOKI_URL"]
    auth = (os.environ["LOKI_USER"], os.environ["GC_TOKEN"])
    streams: dict[tuple, list] = {}
    for e in sorted(events, key=lambda e: e.ts):  # chronological per stream: required for backfill
        key = (e.factory, e.line, e.level)
        streams.setdefault(key, []).append(
            [str(int(e.ts.timestamp() * 1e9)), f"src={e.src} {e.msg}"])
    payload = {"streams": [
        {"stream": {"job": "plant-events", "factory": f, "line": l, "level": lv}, "values": vals}
        for (f, l, lv), vals in streams.items()]}
    _post(url, auth, json.dumps(payload), headers={"Content-Type": "application/json"})
    return sum(len(v) for v in streams.values())


def _post(url, auth, body, headers, retries=4):
    for attempt in range(retries):
        r = requests.post(url, auth=auth, data=body, headers=headers, timeout=30)
        if r.status_code in (200, 204):
            return
        if r.status_code == 429:  # rate limited: back off and retry
            time.sleep(2 ** attempt * 2)
            continue
        raise RuntimeError(f"push failed {r.status_code}: {r.text[:300]}")
    raise RuntimeError("push failed: rate-limited after retries")
