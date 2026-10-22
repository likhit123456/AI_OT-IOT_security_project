"""Fast tests for monitor/governance.py: ceiling shape, debounce, asymmetric rates, recovery gate."""
from __future__ import annotations

from contracts import SupervisoryTargets
from monitor import governance as G
from monitor.observer import Verdict

DESIGN = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
OFF = SupervisoryTargets(pressure_sp=2850.0, level_sp=44.18, production_rate_sp=112.0, a_comp_sp=47.0)


def test_ceiling_is_monotonic_bounded_and_floored():
    xs = [i / 1000 for i in range(1001)]
    for label in (None, "setpoint_drift", "provenance_violation"):
        ys = [G.ceiling(x, label) for x in xs]
        assert all(b <= a + 1e-12 for a, b in zip(ys, ys[1:]))
        assert all(G.FLOOR_PROVENANCE <= y <= 1.0 for y in ys)
    assert G.ceiling(0.29) == 1.0 and abs(G.ceiling(0.60) - G.CEIL_TIER2) < 1e-9
    assert G.ceiling(0.95) == G.FLOOR and G.ceiling(0.95, "provenance_violation") == G.FLOOR_PROVENANCE
    assert G.ceiling(5.0) > 0 and G.ceiling(-1.0) == 1.0          # out-of-range inputs stay bounded


def verdict(t, label, score=0.9):
    return Verdict(t, "proposals", label, score, "r")


def test_one_observer_verdict_moves_nothing_two_consecutive_do():
    g = G.Governor()
    s = g.update(0.0, 0.0, "none", verdict(0.0, "provenance_violation"), 0.9, OFF)
    for t in (0.1, 2.0, 4.0):                                     # same verdict re-read: still one call
        s = g.update(t, 0.0, "none", verdict(0.0, "provenance_violation"), 0.9, OFF)
    assert s.corroborated == 0.0 and s.applied == 1.0
    s = g.update(5.0, 0.0, "none", verdict(5.0, "setpoint_drift", 0.7), 0.7, OFF)
    assert s.corroborated == 0.7 and s.label == "setpoint_drift" and s.ceiling < 1.0


def test_a_normal_verdict_between_spikes_breaks_the_streak():
    g = G.Governor()
    for i, lab in enumerate(("rationale_mismatch", "normal", "rationale_mismatch", "normal")):
        s = g.update(i * 5.0, 0.0, "none", verdict(i * 5.0, lab), 0.9 if lab != "normal" else 0.0, DESIGN)
    assert s.corroborated == 0.0 and s.applied == 1.0


def test_rule_monitor_needs_two_samples_a_second_apart():
    g = G.Governor()
    s = g.update(0.0, 1.0, "cusum", None, 0.0, OFF)
    s = g.update(0.5, 1.0, "cusum", None, 0.0, OFF)              # same sampling slot
    assert s.corroborated == 0.0
    s = g.update(1.0, 1.0, "cusum", None, 0.0, OFF)
    assert s.corroborated == 1.0 and s.label == "rule:cusum" and s.ceiling == G.FLOOR


def drive(g, t0, t1, rule, intent, dt=0.1):
    t, s = t0, None
    while t < t1:
        s = g.update(t, rule, "cusum", None, 0.0, intent)
        t += dt
    return s


def test_drop_is_fast_recovery_slow_and_gated():
    assert G.DROP_PER_S > 5 * G.RECOVER_PER_S
    g = G.Governor()
    s = drive(g, 0.0, 5.0, 1.0, OFF)                              # corroborated at t=1, ~3.4 s to floor
    assert s.applied == G.FLOOR
    s = drive(g, 5.0, 40.0, 0.0, OFF)                             # suspicion gone, proposals off-design
    assert s.applied == G.FLOOR and not s.envelope_ok and not s.recovering
    s = drive(g, 40.0, 54.0, 0.0, DESIGN)                         # back in envelope; clean since t~5
    assert s.recovering and abs(s.applied - (G.FLOOR + G.RECOVER_PER_S * 14.0)) < 0.01
    g2 = G.Governor()
    drive(g2, 0.0, 5.0, 1.0, DESIGN)
    s = drive(g2, 5.0, 19.0, 0.0, DESIGN)                         # < CLEAN_WINDOW_S of clean
    assert s.applied == G.FLOOR
    s = drive(g2, 19.0, 30.0, 0.0, DESIGN)
    assert s.recovering and G.FLOOR < s.applied <= G.FLOOR + G.RECOVER_PER_S * 11.0 + 1e-9


def test_floor_holds_under_sustained_maximal_suspicion():
    g = G.Governor()
    s = None
    for i in range(200):
        s = g.update(i * 5.0, 1.0, "rationale", verdict(i * 5.0, "provenance_violation", 1.0), 1.0, OFF)
    assert s.applied == G.FLOOR_PROVENANCE > 0
