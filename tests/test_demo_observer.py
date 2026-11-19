import pytest

from monitor.observer_runtime import LABELS
from training.train_demo_observer import assessment, balanced_rows


def cases():
    return [{"kind": kind, "target": {"label": label},
             "input_sha256": f"{kind}/{label}/{group}/{index}",
             "provenance": {"source_recordings": [f"recording-{group}"]}}
            for kind, labels in LABELS.items() for label in labels
            for group in range(4) for index in range(3)]


def test_small_validation_spans_recordings_without_duplicate_padding():
    rows = balanced_rows(cases(), 8)
    assert len(rows) == len({r["input_sha256"] for r in rows}) == 64
    for kind, labels in LABELS.items():
        for label in labels:
            subset = [r for r in rows if r["kind"] == kind and r["target"]["label"] == label]
            assert len(subset) == 8
            assert len({r["provenance"]["source_recordings"][0] for r in subset}) == 4


def test_missing_class_cannot_be_padded_or_ignored():
    rows = [r for r in cases() if r["target"]["label"] != "sensor_fault"]
    with pytest.raises(ValueError, match="Insufficient"):
        balanced_rows(rows, 8)


def predictions():
    return [{"task": r["kind"], "gold": r["target"]["label"],
             "candidate": r["target"]["label"], "latency_s": 1}
            for r in balanced_rows(cases(), 8)]


def test_high_average_cannot_hide_total_sensor_failure():
    rows = predictions()
    for row in rows:
        if row["gold"] == "sensor_fault":
            row["candidate"] = "normal"
    result = assessment(rows)
    assert not result["demo_target_met"]
    assert result["minimum_class_recall"] == 0


def test_normal_false_alarms_fail_demo_target():
    rows = predictions()
    assert assessment(rows)["demo_target_met"]
    for row in rows:
        if row["task"] == "telemetry" and row["gold"] == "normal":
            row["candidate"] = "sensor_fault"
    assert not assessment(rows)["demo_target_met"]
