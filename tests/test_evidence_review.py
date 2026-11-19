import copy
import json
from dataclasses import replace

import pytest

from monitor import observer_v2_format as V2
from monitor.evidence_review import proposal_review, telemetry_review
from monitor.governance import Governor
from monitor.observer import Verdict
from monitor.observer_runtime import (
    CPUObserver,
    ObserverRuntime,
    Request,
    UnifiedObserver,
)


def proposals():
    return {"input": "proposals", "cycles": 6, "proposals": [
        {"pressure_sp": 2700.6, "level_sp": 44.2, "production_rate_sp": 100.,
         "a_comp_sp": 47., "provenance": V2.OPERATOR_CHAIN, "rationale": "Regulate near design."}
        for _ in range(6)]}


def window():
    result = {"input": "telemetry", "window_s": 15., "pressure": [2700+i*.2 for i in range(16)],
              "level": [44.2]*16, "f1_flow": [200.]*16, "f2_flow": [5.]*16,
              "purge_flow": [0.]*16, "product_flow": [100.]*16, "a_in_purge": [.47]*16,
              "scrammed": [False]*16}
    for valve, position in (("f1", 31.45), ("f2", 13.24), ("purge", .39), ("product", 47.)):
        for suffix in ("pos", "cmd"):
            result[f"{valve}_valve_{suffix}"] = [position]*16
    return result


NORMAL_CPU = {"label": "normal", "confidence": .99}


def test_repeated_model_alarm_cannot_invent_a_trend():
    review = proposal_review(proposals(), "setpoint_drift")
    assert review["label"] is None and review["status"] == "unconfirmed"
    assert review["raw_label"] == "setpoint_drift"


def test_holding_a_different_operating_point_is_not_a_rationale_contradiction():
    data = proposals()
    for row in data["proposals"]:
        row["pressure_sp"] = 2800
        row["rationale"] = "Keep the current targets unchanged."
    assert proposal_review(data, "normal")["label"] == "normal"


def test_explicit_pressure_production_design_claim_matches_named_targets():
    data = proposals()
    data["proposals"][-1]["production_rate_sp"] = 130
    data["proposals"][-1]["rationale"] = "Keeping pressure and production at the design targets without increases."
    assert proposal_review(data, "rationale_mismatch")["label"] == "rationale_mismatch"
    data["proposals"][-1]["rationale"] = "Not keeping pressure and production at the design targets."
    assert proposal_review(data, "rationale_mismatch")["label"] is None
    data["proposals"][-1]["rationale"] = "Keeping pressure and production at the design targets."
    data["proposals"][-1]["production_rate_sp"] = 100
    assert proposal_review(data, "normal")["label"] == "normal"


def test_return_to_baseline_is_not_confirmed_as_a_continuing_one_way_creep():
    data = proposals()
    for row, value in zip(data["proposals"], (2700, 2710, 2720, 2730, 2740, 2690)):
        row["pressure_sp"] = value
    assert proposal_review(data, "setpoint_drift")["label"] is None


@pytest.mark.parametrize("missed_label", ["setpoint_drift", "provenance_violation", "rationale_mismatch"])
def test_explicit_evidence_can_catch_missed_proposal_fault(missed_label):
    data = proposals()
    for i, row in enumerate(data["proposals"]):
        if missed_label == "setpoint_drift":
            row["pressure_sp"] += 10*i
        elif missed_label == "provenance_violation":
            row["provenance"] = V2.INGESTED_TEXT
        else:
            row["pressure_sp"] += 100
            row["rationale"] = "Targets remain unchanged at design."
    review = proposal_review(data, "normal")
    assert review["label"] == missed_label and review["status"] == "supported"


def test_small_setpoint_noise_is_not_a_drift_finding():
    data = proposals()
    for i, row in enumerate(data["proposals"]):
        row["pressure_sp"] += .1*(i % 2)
    assert proposal_review(data, "setpoint_drift")["status"] == "unconfirmed"


def test_unknown_provenance_does_not_claim_authentication_or_normality():
    data = proposals()
    data["proposals"][-1]["provenance"] = "I promise this is authenticated"
    assert proposal_review(data, "normal")["label"] is None


def test_cpu_model_disagreement_is_not_hidden_as_normal():
    checked = telemetry_review(window(), "sensor_fault", NORMAL_CPU)
    assert checked["label"] is None and checked["status"] == "unconfirmed"
    assert checked["raw_label"] == "sensor_fault"
    assert telemetry_review(window(), "normal", NORMAL_CPU)["label"] == "normal"


@pytest.mark.parametrize("zero_count", [2, 5, 16])
def test_clear_pressure_signal_loss_is_caught_even_if_both_models_say_normal(zero_count):
    data = window()
    data["pressure"][-zero_count:] = [0]*zero_count
    checked = telemetry_review(data, "normal", NORMAL_CPU)
    assert checked["label"] == "sensor_fault" and checked["status"] == "supported"


def test_single_zero_is_uncertain_instead_of_being_passed_as_healthy():
    data = window()
    data["pressure"][-1] = 0
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] is None


def test_repeated_level_dropout_preserves_excursion_evidence():
    data = window()
    data["level"][2:5] = [0]*3
    data["level"][-3:] = [0]*3
    review = telemetry_review(data, "normal", NORMAL_CPU)
    assert review["label"] == "sensor_fault"
    assert {finding["label"] for finding in review["findings"]} == {"sensor_fault", "excursion"}


def test_level_zero_alone_or_physical_pressure_collapse_is_not_called_sensor_fault():
    data = window()
    data["level"][-2:] = [0, 0]
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] == "excursion"
    data["level"][2:4] = [0, 0]
    data["pressure"][-4:] = [1900, 900, 0, 0]
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] == "excursion"


def test_dropouts_still_surface_when_the_controller_changes_feed_flows():
    data = window()
    data["pressure"][4:9] = [0]*5
    data["f1_flow"] = [200.]*5+[130.]*6+[200.]*5
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] == "sensor_fault"


def test_weak_cpu_prediction_cannot_borrow_support_from_an_old_model_window():
    data = window()
    weak = {"label": "sensor_fault", "confidence": .75}
    assert telemetry_review(data, "sensor_fault", weak, aligned=False)["label"] is None
    assert telemetry_review(data, "sensor_fault", weak, aligned=True)["label"] == "sensor_fault"


def test_genuine_process_change_is_not_forced_to_be_a_dropout():
    data = window()
    data["pressure"][-4:] = [1900, 900, 0, 0]
    data["f1_flow"][-4:] = [150, 90, 10, 0]
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] is None


def test_stable_measurements_and_zero_purge_flow_do_not_prove_sensor_failure():
    data = window()
    data["pressure"] = [2700.]*16
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] == "normal"
    assert telemetry_review(data, "sensor_fault", NORMAL_CPU)["label"] is None


def test_sustained_command_mismatch_catches_model_false_negative():
    data = window()
    data["f1_valve_cmd"][-6:] = [45.]*6
    assert telemetry_review(data, "normal", NORMAL_CPU)["label"] == "actuator_fault"


def test_a_valve_converging_toward_its_command_is_not_called_stuck():
    data = window()
    data["f1_valve_cmd"] = [45.]*16
    data["f1_valve_pos"] = [30+i*.8 for i in range(16)]
    assert telemetry_review(data, "actuator_fault", NORMAL_CPU)["label"] is None


def test_malformed_or_partial_telemetry_cannot_be_normal():
    data = window()
    data["pressure"][-1] = float("nan")
    assert telemetry_review(data, "normal", NORMAL_CPU)["status"] == "insufficient_evidence"
    data = window()
    data["f1_valve_cmd"].pop()
    assert telemetry_review(data, "normal", NORMAL_CPU)["status"] == "insufficient_evidence"


def test_independent_physics_fault_is_preserved_without_model_agreement():
    assert telemetry_review(window(), "normal", {"label": "sensor_fault", "confidence": .99})["label"] == "sensor_fault"


def test_unconfirmed_review_clears_only_observer_governance_evidence():
    governor = Governor()
    for t in (0, 5):
        governor.update(t, 0., "none", Verdict(t, "proposals", "setpoint_drift", .8, "raw"), .8)
    assert governor._obs_corr > 0
    unknown = Verdict(10, "proposals", None, 0, "No supporting trend", source="evidence-review")
    governor.update(10, .8, "keel", unknown, 0)
    assert governor._obs_corr == 0
    result = governor.update(11, .8, "keel", unknown, 0)
    assert result.corroborated >= .8 and result.label.startswith("rule:")


def test_real_worker_keeps_raw_output_but_does_not_route_unsupported_alarm():
    raw_answer = json.dumps({"label": "setpoint_drift", "score": .8, "reason": "invented trend"})
    observer = UnifiedObserver(include_identity=False, measured_evidence=True, evidence_review=True,
                               http_post=lambda *args: {"message": {"content": raw_answer}})
    runtime = ObserverRuntime("unified", observer, None, evidence_review=True)
    try:
        observer.paths["proposals"].sequence = 1
        now = observer.clock()
        observer._execute(Request("proposals", json.dumps(proposals()), 0, 1, 0, now, now+20))
        assert observer.last["proposals"].label == "setpoint_drift"
        assert runtime.last["proposals"].label is None and runtime.suspicion() == 0
        snapshot = runtime.snapshot()
        assert snapshot["raw_proposals"]["label"] == "setpoint_drift"
        assert snapshot["evidence_review"]["proposals"]["status"] == "unconfirmed"
        observer.reset()
        assert not observer.reviewed and runtime.suspicion() == 0
    finally:
        runtime.close()


def test_review_does_not_mutate_supplied_history():
    data = window()
    before = copy.deepcopy(data)
    telemetry_review(data, "sensor_fault", NORMAL_CPU)
    assert data == before


def test_direct_rules_survive_classifier_failure_but_not_invalid_measurements():
    from test_observer_wrapper import CMD, STATE

    class FailedClassifier:
        def predict(self, window):
            raise ValueError("classifier unavailable")

    cpu = CPUObserver(FailedClassifier(), collect_without_detector=True)
    for t in range(16):
        cpu.feed(t, replace(STATE, pressure=0 if t >= 12 else 2700), CMD)
    assert cpu.status.startswith("detector_unavailable")
    assert cpu.prediction is None
    assert telemetry_review(cpu.window, "normal")["label"] == "sensor_fault"
    cpu.feed(16, replace(STATE, pressure=float("nan")), CMD)
    assert cpu.window is None
    assert telemetry_review(cpu.window, "normal")["status"] == "insufficient_evidence"
