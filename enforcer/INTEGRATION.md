# Wiring Keel + SequentialMonitor into `live/engine.py`

Untested against the live GRFICS container (no Docker in the authoring sandbox);
the unit tests cover the logic. Four small edits.

```python
# --- imports ---
from enforcer.keel import Keel, KeelLimits, SisFrame
from monitor.sequential_monitor import SequentialMonitor
KEEL_KEY = b"scram-keel-log-key-not-a-real-secret"

# --- _make_brain(): slider becomes a CAP, Keel's budget is the live value ---
self.brain = AIOperator(policy=policy,
                        authority_fn=lambda: min(self.authority, self.keel.authority()))

# --- _connect(): after self.monitor = BehaviorMonitor(...) ---
self.keel = Keel(KEEL_KEY, KeelLimits(trip=self.limits))   # same limits as L0's SIS
self.seq_monitor = SequentialMonitor()

# --- _cycle(): replace the "cmd = self.signer.sign(self.l1.regulate(...))" line ---
targets = self.brain.step(state)
codes = [r.code for r in (self.keel.last.reasons if self.keel.last else ())]
sus = self.seq_monitor.observe(state, targets, t, keel_codes=codes)      # advisory
raw = self.l1.regulate(targets, state, dt=DT)
dec = self.keel.step(raw, state, SisFrame.from_state(state), t, sus.score)
cmd = self.signer.sign(dec.command)                                       # sign AFTER Keel
if dec.verdict != "allow":
    self.log("keel", f"{dec.verdict} A={dec.authority:.2f} sev={dec.severity:.2f} "
                     + "; ".join(f"{r.code}:{r.detail}" for r in dec.reasons))

# --- _gate_bookkeeping(): one hook covers every rejection path ---
for e in new:
    if e.kind == "rejected_command":
        self.keel.note_gate_rejection(self.now(), e.detail)

# --- _snapshot(): expose to the control room ---
"keel": {"authority": self.keel.authority(), "verdict": getattr(self.keel.last, "verdict", None),
         "reasons": [r.code for r in (self.keel.last.reasons if self.keel.last else ())],
         "divert": self.keel.divert, "suspicion": self.seq_monitor.history[-1].as_dict()
         if self.seq_monitor.history else None},
```

## Trust-boundary caveat (say this in the viva)
Keel only constrains commands that pass through it. The `stolen_key` scenario signs
*after* Keel and so bypasses it; only the SIS trip stops that. To close it, the
signing key must live inside Keel's process/boundary so nothing else can sign.
Out-of-band writes to :55555 are still only detected by `ProvenanceGate.audit`.

## Calibration TODO before trusting the thresholds
Defaults come from numbers in the repo docstrings (3.75 kPa/s climb, 0.013 %/s
drain, noise assumed small). Replay `training/states.jsonl` (real GRFICS states)
through `Keel.step` with nominal commands to measure the false-intervention rate,
then sweep `pressure_rise_budget_kpa` / `level_drop_budget_pct` for the H2 frontier.
