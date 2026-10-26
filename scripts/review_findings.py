"""Inspect the review findings after hardening, without real plant writes.

Run: python -m scripts.review_findings
The regression tests under tests/test_quality_hardening.py assert the corrected behavior.
"""
import json
import threading
from dataclasses import replace

from fastapi import HTTPException
from contracts import ControlCommand
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from l0_plant.safety import SafeHold, SafetyInterlock
from monitor.observer import Observer
from sim_app.server import Plant


def main():
    findings = {}
    sis = SafetyInterlock(safe_hold=SafeHold())
    sis.evaluate(2700, 4)
    sis.evaluate(3100, 4)
    findings['low_level_then_overpressure'] = sis.safe_state_inputs(3100, 4)
    sis.evaluate(float('nan'), 4)
    findings['low_level_then_invalid_sis'] = sis.safe_state_inputs(float('nan'), 4)

    plant = Plant()
    class FakeClient:
        def write_inputs(self, inputs):
            return {"state": {"e_stop": inputs.get("e_stop", 0)}, "outputs": {"pressure": 2700, "liquid_level": 45}}
    plant._client = FakeClient()
    plant.rescue.active = True  # no worker is started
    try:
        findings['estop_during_ai'] = plant.manipulate('e_stop', None, 1)
    except HTTPException as exc:
        findings['estop_during_ai'] = {'status': exc.status_code, 'detail': exc.detail}

    entered, release = threading.Event(), threading.Event()
    def post(*_):
        entered.set()
        assert release.wait(3)
        return {'message': {'content': json.dumps({'label':'rationale_mismatch','score':0.9,'reason':'old run'})}}
    obs = Observer(http_post=post)
    obs._gate.acquire()
    worker = threading.Thread(target=obs._run, args=(10, 'proposals', '{}'))
    worker.start()
    assert entered.wait(3)
    obs.reset()
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    findings['observer_result_after_reset'] = obs.snapshot()

    key = b'review-only'
    signed = CommandSigner(key).sign(ControlCommand(20,20,20,20))
    changed = replace(signed, f1_valve=20.00001)
    findings['sub_precision_command_change_admitted'] = ProvenanceGate(key).admit(changed)[0]
    print(json.dumps(findings, indent=2))


if __name__ == '__main__':
    main()
