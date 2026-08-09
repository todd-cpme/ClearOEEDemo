"""Deterministic plant simulator for the ClearTrend demo.

Everything derives from (plant.yaml, events.yaml, requested time interval).
Same inputs -> identical samples and events, so overlapping pushes are
idempotent and backfill + incremental cron runs agree exactly.
No wall-clock or random state is kept between runs.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _rng(*key) -> random.Random:
    h = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def load_cfg():
    plant = yaml.safe_load((ROOT / "plant.yaml").read_text())
    events = yaml.safe_load((ROOT / "events.yaml").read_text())
    return plant, events


@dataclass
class Block:
    start: datetime
    end: datetime
    state: str            # "running" | "down"
    reason: str = ""      # reason key when down


@dataclass
class Sample:
    ts: datetime
    line: str
    factory: str
    fields: dict          # metric name -> value
    tags: dict = field(default_factory=dict)


@dataclass
class Event:
    ts: datetime
    line: str
    factory: str
    level: str
    src: str
    msg: str


class Simulator:
    def __init__(self):
        self.plant, self.events_cfg = load_cfg()
        self.epoch = datetime.fromisoformat(self.plant["epoch"].replace("Z", "+00:00"))
        self.tz = ZoneInfo(self.plant["production"]["timezone"])
        self.prod_days = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        self.prod_dow = {self.prod_days[d] for d in self.plant["production"]["days"]}
        self._schedules: dict[str, list[Block]] = {}

    # ---------------- production calendar ----------------

    def in_production(self, ts: datetime) -> bool:
        lt = ts.astimezone(self.tz)
        p = self.plant["production"]
        return lt.weekday() in self.prod_dow and p["start_hour"] <= lt.hour < p["end_hour"]

    # ---------------- block schedule (deterministic per line) ----------------

    def schedule(self, factory: str, line: str, until: datetime) -> list[Block]:
        """Alternating run/down blocks from epoch to `until`, seeded by line name."""
        cached = self._schedules.get(line)
        if cached and cached[-1].end >= until:
            return cached
        rng = _rng("blocks", line)
        cfg = self.plant["factories"][factory]["lines"][line]
        reasons = self.events_cfg["reasons"]
        keys = list(reasons)
        weights = [reasons[k]["weight"] for k in keys]
        if cfg.get("changeover_heavy"):
            weights[keys.index("changeover")] *= 3
        blocks: list[Block] = []
        t = self.epoch
        while t < until + timedelta(hours=4):
            run_min = rng.uniform(25, 95)
            t_run_end = t + timedelta(minutes=run_min)
            blocks.append(Block(t, t_run_end, "running"))
            rk = rng.choices(keys, weights)[0]
            lo, hi = reasons[rk]["duration_min"]
            dur = rng.uniform(lo, hi)
            if cfg.get("long_down_prone") and rng.random() < 0.06:
                dur = rng.uniform(180, 300)   # the MLD07 pattern
            t_down_end = t_run_end + timedelta(minutes=dur)
            blocks.append(Block(t_run_end, t_down_end, "down", rk))
            t = t_down_end
        self._schedules[line] = blocks
        return blocks

    def state_at(self, factory: str, line: str, ts: datetime) -> Block:
        if not self.in_production(ts):
            return Block(ts, ts, "down", "scheduled")
        for b in self.schedule(factory, line, ts):
            if b.start <= ts < b.end:
                return b
        return Block(ts, ts, "running")

    # ---------------- rates ----------------

    def perf_factor(self, line: str, cfg: dict, ts: datetime) -> float:
        lt = ts.astimezone(self.tz)
        base = cfg["perf_base"]
        wobble = 0.08 * _rng("perf", line, lt.strftime("%Y%m%d%H")).uniform(-1, 1)
        dip = -0.15 if lt.hour == self.plant["production"]["start_hour"] and lt.minute < 20 else 0.0
        monday = -0.06 if lt.weekday() == 0 and lt.hour < 9 else 0.0
        return max(0.3, min(1.0, base + wobble + dip + monday))

    def fail_frac(self, factory: str, line: str, cfg: dict, ts: datetime) -> float:
        lt = ts.astimezone(self.tz)
        y = self.plant["factories"][factory]["yield_target"] + cfg["yield_offset"]
        y += _rng("yield", line, lt.strftime("%Y%m%d%H")).uniform(-1.2, 1.2)
        # occasional quality excursion (correlates with VIS warn chatter)
        if _rng("exc", line, lt.strftime("%Y%m%d")).random() < 0.15 and 13 <= lt.hour < 15:
            y -= 4.0
        return max(0.001, min(0.25, (100.0 - y) / 100.0))

    # ---------------- sample generation ----------------

    INTEGRATE_S = 60  # counters ALWAYS integrate at 60s so values are identical
                      # regardless of emit interval — a coarser integration would
                      # let backfill chunks disagree at seams and fake counter resets

    def samples(self, start: datetime, end: datetime, emit_s: int) -> list[Sample]:
        """Integrate from epoch at 60s; emit one row per `emit_s`.
        Deterministic, so any overlapping re-push writes identical values."""
        step_s = self.INTEGRATE_S
        out: list[Sample] = []
        for fac, fcfg in self.plant["factories"].items():
            for line, cfg in fcfg["lines"].items():
                pass_c = fail_c = 0.0
                down_c: dict[str, float] = {}
                run_60 = []   # rolling window for gauges: (ts, running, pass_inc, fail_inc, perf)
                t = self.epoch
                step = timedelta(seconds=step_s)
                while t <= end:
                    blk = self.state_at(fac, line, t)
                    running = blk.state == "running"
                    perf = self.perf_factor(line, cfg, t) if running else 0.0
                    inc = cfg["uph"] / 3600.0 * step_s * perf if running else 0.0
                    ff = self.fail_frac(fac, line, cfg, t)
                    p_inc, f_inc = inc * (1 - ff), inc * ff
                    pass_c += p_inc
                    fail_c += f_inc
                    if not running:
                        key = blk.reason or "unknown"
                        down_c[key] = down_c.get(key, 0.0) + step_s
                    run_60.append((t, running, p_inc, f_inc, perf))
                    cut = t - timedelta(minutes=60)
                    while run_60 and run_60[0][0] < cut:
                        run_60.pop(0)
                    emit = t >= start and int((t - self.epoch).total_seconds()) % emit_s == 0
                    if emit:
                        n = len(run_60)
                        avail = 100.0 * sum(1 for r in run_60 if r[1]) / n
                        p60 = sum(r[2] for r in run_60)
                        f60 = sum(r[3] for r in run_60)
                        quality = 100.0 * p60 / (p60 + f60) if (p60 + f60) else 100.0
                        perf60 = 100.0 * sum(r[4] for r in run_60 if r[1]) / max(1, sum(1 for r in run_60 if r[1]))
                        oee = avail / 100 * perf60 / 100 * quality
                        disp = self.events_cfg["reasons"].get(blk.reason, {}).get("display", "") if not running else ""
                        fields = {
                            "pass_total": round(pass_c, 2),
                            "fail_total": round(fail_c, 2),
                            "status": 1 if running else 0,
                            "yield_pct": round(quality, 2),
                            "avail_pct": round(avail, 2),
                            "perform_pct": round(perf60, 2),
                            "oee_pct": round(oee, 2),
                        }
                        # NOTE: no reason tag on the main measurement — a changing
                        # tag would split the counters into separate series.
                        out.append(Sample(t, line, fac, fields))
                        if disp:
                            out.append(Sample(t, line, fac, {"info": 1},
                                              tags={"reason": disp, "_measurement": "state"}))
                        for rk, secs in down_c.items():
                            rd = self.events_cfg["reasons"].get(rk, {}).get("display", rk)
                            out.append(Sample(t, line, fac, {"down_seconds_total": round(secs, 1)},
                                              tags={"reason": rd, "_measurement": "downtime"}))
                    t += step
        return out

    # ---------------- event generation ----------------

    def _fill(self, msg: str, line: str, cfg: dict, rng: random.Random) -> str:
        subs = {
            "{line}": line, "{rate}": str(cfg["uph"]),
            "{sku_from}": rng.choice(self.plant["skus"]), "{sku_to}": rng.choice(self.plant["skus"]),
            "{wo}": f"WO-{rng.randint(40000, 49999)}", "{op}": rng.choice(self.plant["operators"]),
            "{rej}": f"{rng.uniform(3.1, 6.5):.1f}", "{rej_n}": str(rng.randint(4, 12)),
            "{pass_hr}": str(rng.randint(1500, 3400)), "{fail_hr}": str(rng.randint(20, 300)),
            "{yield_hr}": f"{rng.uniform(88, 99):.1f}",
        }
        for k, v in subs.items():
            msg = msg.replace(k, v)
        return msg

    def events(self, start: datetime, end: datetime) -> list[Event]:
        out: list[Event] = []
        reasons = self.events_cfg["reasons"]
        for fac, fcfg in self.plant["factories"].items():
            for line, cfg in fcfg["lines"].items():
                for b in self.schedule(fac, line, end):
                    if b.state != "down" or b.end < start or b.start > end:
                        continue
                    if not self.in_production(b.start):
                        continue
                    spec = reasons[b.reason]
                    rng = _rng("evt", line, b.start.isoformat())
                    dur = (b.end - b.start).total_seconds()

                    def emit(ts, level, src, msg):
                        if start <= ts <= end:
                            out.append(Event(ts, line, fac, level, src, self._fill(msg, line, cfg, rng)))

                    for p in spec.get("precursors", []):
                        off = rng.uniform(*p["offset"]) if isinstance(p["offset"], list) else p["offset"]
                        emit(b.start + timedelta(seconds=off), p["level"], p["src"], p["msg"])
                    for s in spec.get("start", []):
                        emit(b.start, s["level"], s["src"], s["msg"])
                    for d in spec.get("during", []):
                        emit(b.start + timedelta(seconds=dur * d["at"]), d["level"], d["src"], d["msg"])
                    for e in spec.get("end", []):
                        emit(b.end + timedelta(seconds=e.get("offset", 0)), e["level"], e["src"], e["msg"])
                # running chatter
                for ch in self.events_cfg.get("running_chatter", []):
                    t = start.replace(minute=0, second=0, microsecond=0)
                    while t <= end:
                        rng = _rng("chat", line, ch["msg"][:12], t.isoformat())
                        if rng.random() < ch["per_hour"] and self.in_production(t):
                            ts = t + timedelta(seconds=rng.uniform(0, 3500))
                            if start <= ts <= end and self.state_at(fac, line, ts).state == "running":
                                out.append(Event(ts, line, fac, ch["level"], ch["src"],
                                                 self._fill(ch["msg"], line, cfg, rng)))
                        t += timedelta(hours=1)
        out.sort(key=lambda e: e.ts)
        return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
