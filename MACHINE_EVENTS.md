# Machine event catalog

Every downtime shown on the dashboards is accompanied by a correlated event
sequence in the events table/stream, so a viewer can click from a red block on
the state timeline into the story of what happened. Sequences follow the real
pattern: precursor warnings while still running, a fault/trigger event at the
moment the line stops, operator actions during the down, and a restart sequence
at the end. Machine sources: PLC (controller), HMI (operator panel), MES
(execution system), VIS (vision inspection).

## Downtime reasons and their event sequences

**Change Over - Line clearance** (planned, 25-90 min; matches the reason text in the client's capture)
- before: MES info "Recipe change queued: SKU-xxxx -> SKU-yyyy"
- at stop: MES "Change over started - line clearance in progress"; PLC "Guard door opened - operator station 1"
- during: clearance checklist items (infeed cleared, reject bin verified empty); PLC "Recipe downloaded - parameters verified"
- restart: PLC "Master reset - line restart"; "Ramp to rate complete (xxxx uph)"

**Scheduled Downtime** (planned, 1-4 h; matches their LIB01 reason)
- before: MES "PM work order WO-4xxxx scheduled - line will stop at end of current lot"
- at stop: MES "Scheduled downtime started"; PLC warn "LOTO applied - energy isolation verified"
- during: PM checklist completion
- restart: "LOTO removed"; "Master reset - line restart"

**Jam** (unplanned, 4-25 min)
- before: PLC warns "Infeed backlog high - accumulation table at 85%", "Torque limit warning - infeed screw drive"
- at stop: PLC error "E201 INFEED JAM - line stopped by PLC interlock"
- during: HMI "Alarm E201 acknowledged by operator"; "Jam cleared - guards closed"
- restart: "Fault reset"; "Ramp to rate complete"

**Starved - Upstream** (unplanned, 5-20 min)
- before: PLC warn "Infeed conveyor low - upstream supply degraded"
- at stop: PLC error "E310 STARVED - no product at infeed photo-eye for 60s"
- restart: "Product present at infeed - auto restart"

**Blocked - Downstream** (unplanned, 4-15 min)
- before: PLC warn "Discharge back-pressure rising - downstream accumulation at 90%"
- at stop: PLC error "E311 BLOCKED - discharge full, line paused"
- restart: "Downstream cleared - auto restart"

**Quality Hold** (unplanned, 15-45 min; correlates with visible yield dips)
- before: VIS warns "Vision reject rate high: rolling 15 min at x.x% (limit 3.0%)", "Camera 2 calibration drift detected"
- at stop: MES error "QUALITY HOLD - lot paused pending QA disposition"
- during: "QA sampling in progress - 32 pc AQL pull"
- restart: "QA disposition: RELEASE - hold lifted"

**No Operator** (unplanned, 8-30 min)
- at stop: HMI warn "Operator sign-in timeout - line paused (break / shift relief)"
- restart: "Operator J.Alvarez signed in - line restart"

**E-Stop** (unplanned, 3-12 min)
- at stop: PLC error "E001 E-STOP PRESSED - operator station 2 - safety circuit open"
- during: HMI "E-stop reset - safety circuit closed"
- restart: "Safety reset complete - master reset"

## Background chatter while running

Sparse info/warn events so the log doesn't only speak when things break:
hourly production summaries (pass/fail/yield), occasional vision reject bursts
that self-clear, a servo temperature warning, operator sign-ins. The vision
warnings are weighted toward the hours where the simulator injects yield
excursions, so metric dips and log noise line up.

## Correlation guarantees (by construction)

The simulator generates events FROM the same block schedule that drives the
metrics, so every down block in `line_status` has its matching fault event at
the same timestamp, every unplanned down has 1-2 precursor warnings in the
minutes before, and every restart lines up with the counter resuming. Nothing
is randomly sprinkled; a skeptical plant engineer can zoom into any red block
and the story holds.

## Tuning

Reason mix, durations, and message text all live in `events.yaml` (weights per
reason; AAL05 is changeover-heavy, ABW07 is prone to multi-hour downs, both per
the client captures). Edit and re-run; no code changes needed.
