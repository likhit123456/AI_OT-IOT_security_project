"""Stage failures, artifact identity and independent stress accounting."""
import copy
import hashlib
import json
import sys

import pytest

from monitor.telemetry_evidence import NUMERIC_FIELDS
from training.freeze_evidence_candidate import stress_rows
from training.run_evidence_experiment import Pipeline
from training.shared_slm_data import ROOT, canonical, sha


def test_completed_stage_is_skipped_but_modified_output_is_never_accepted(tmp_path):
    pipeline = Pipeline(tmp_path)
    output = tmp_path / "evidence.json"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(output)!r}).write_text('original')"]
    pipeline.execute("actual_subprocess", command, outputs=[output])
    assert json.loads(pipeline.path.read_text())["stages"]["actual_subprocess"]["state"] == "completed"
    pipeline.execute("actual_subprocess", command, outputs=[output])
    assert pipeline.status["stages"]["actual_subprocess"]["attempt"] == 1
    output.write_text("changed")
    with pytest.raises(ValueError, match="identity changed"):
        pipeline.execute("actual_subprocess", command, outputs=[output])
    assert output.read_text() == "changed"


def test_failure_preserves_log_and_cannot_be_reported_as_completed(tmp_path):
    pipeline = Pipeline(tmp_path)
    with pytest.raises(RuntimeError, match="exit 7"):
        pipeline.execute("blocked", [sys.executable, "-c", "print('exact failure evidence'); raise SystemExit(7)"])
    stage = pipeline.status["stages"]["blocked"]
    assert stage["state"] == "failed"
    assert "exact failure evidence" in (pipeline.folder / "blocked-attempt-01.log").read_text()
    assert pipeline.status["state"] == "blocked_by_stage_failure"


def test_success_exit_without_required_artifact_is_a_failed_stage(tmp_path):
    pipeline = Pipeline(tmp_path)
    with pytest.raises(RuntimeError, match="required artifacts"):
        pipeline.execute("empty_success", [sys.executable, "-c", "pass"], outputs=[tmp_path / "missing.json"])
    assert pipeline.status["stages"]["empty_success"]["state"] == "failed"


def test_stress_variants_are_separate_children_and_never_pad_shortfalls():
    data = {key: [10.0]*16 for key in NUMERIC_FIELDS}
    data.update(input="telemetry", window_s=15, scrammed=[False]*16)
    data["pressure"] = [2700+i for i in range(16)]
    data["level"] = [44+i*.02 for i in range(16)]
    original = copy.deepcopy(data)
    parent = {"kind": "telemetry", "input": data,
              "input_sha256": hashlib.sha256(canonical(data).encode()).hexdigest(),
              "target": {"label": "normal", "score": 0, "reason": "observed"},
              "provenance": {"source_recordings": ["sealed-native-normal"]}}
    rows, counts = stress_rows([parent], {canonical(data)}, {"seed": 3409, "cases_per_variant": 20})
    assert counts == {"frozen_level": 1, "pressure_dropout": 1}
    assert len(rows) == 2 and parent["input"] == original
    assert all(r["provenance"]["independent_support"] is False for r in rows)
    assert all(r["provenance"]["source_recordings"] == ["sealed-native-normal"] for r in rows)
    assert all(r["target"]["label"] == "sensor_fault" for r in rows)


def old_collection_journal(tmp_path, monkeypatch):
    from training import run_evidence_experiment as automation
    from training.collect_slm_evidence import ensure_plan

    plan = ensure_plan(tmp_path)
    source = b"known earlier collector revision"
    old_hash = hashlib.sha256(source).hexdigest()
    monkeypatch.setattr(automation, "KNOWN_PRE_PORT_RETRY_COLLECTORS", {old_hash})
    snapshots = tmp_path / "collector-sources"
    snapshots.mkdir()
    (snapshots / f"{old_hash}.py").write_bytes(source)
    # Invalid UTF-8 makes accidental decoding of sealed measurements fail. The
    # transport repair is allowed to verify bytes/hashes, never interpret them.
    spec = next(s for s in plan["recordings"] if s["split"] == "sealed")
    raw = tmp_path / "raw/sealed" / f"{spec['recording_id']}.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"\xff\x00sealed bytes")
    metadata = {"complete": True, "spec": spec, "sha256": sha(raw),
                "plan_sha256": sha(tmp_path / "collection-plan.json")}
    raw.with_suffix(".completed.json").write_text(json.dumps(metadata))
    pipeline = Pipeline(tmp_path)
    pipeline.status["stages"]["docker_preflight"] = {"state": "completed", "outputs": {},
        "identity": {"command": [sys.executable, "-m", "training.collect_slm_evidence", "preflight"],
                     "inputs": {str(ROOT / "training/collect_slm_evidence.py"): old_hash,
                                str(tmp_path / "collection-plan.json"): sha(tmp_path / "collection-plan.json")}}}
    pipeline.save()
    return pipeline, raw


def test_known_collector_fix_preserves_journal_and_recordings_without_decoding_holdout(tmp_path, monkeypatch):
    pipeline, raw = old_collection_journal(tmp_path, monkeypatch)
    original_raw, original_status = raw.read_bytes(), pipeline.path.read_bytes()
    assert pipeline.repair_collector_resume() is True
    assert raw.read_bytes() == original_raw
    backup = next(pipeline.folder.glob("status.before-collector-fix-*.json"))
    assert backup.read_bytes() == original_status
    stage = pipeline.status["stages"]["docker_preflight"]
    assert stage["prior_identities"]
    assert stage["identity"]["inputs"][str(ROOT / "training/collect_slm_evidence.py")] == sha(ROOT / "training/collect_slm_evidence.py")
    assert pipeline.repair_collector_resume() is False


def test_resume_repair_refuses_modified_recording_instead_of_rehashing_it(tmp_path, monkeypatch):
    pipeline, raw = old_collection_journal(tmp_path, monkeypatch)
    before = pipeline.path.read_bytes()
    raw.write_bytes(b"modified")
    with pytest.raises(ValueError, match="integrity"):
        pipeline.repair_collector_resume()
    assert pipeline.path.read_bytes() == before
