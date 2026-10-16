"""
Fast, pure-logic tests for l0_plant/provenance_gate.py and its PlantService
wiring (fake plant client, no container): accept authorized, reject injected.
"""
from dataclasses import replace

import pytest

from contracts import ControlCommand
from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate

KEY = b"unit-test-key"
OP = {"f1_valve_pos": 50.0, "f2_valve_pos": 50.0, "purge_valve_pos": 10.0,
      "product_valve_pos": 60.0, "e_stop": False}
BENIGN = ControlCommand(f1_valve=55.0, f2_valve=45.0, purge_valve=20.0, product_valve=65.0)


def make_gate(strict=True):
    g = ProvenanceGate(KEY, strict=strict)
    g.seed(OP)
    return g


# ---- admit() ----------------------------------------------------------------

def test_authorized_signed_command_is_accepted():
    ok, _ = make_gate().admit(CommandSigner(KEY).sign(BENIGN))
    assert ok


def test_accepted_command_becomes_last_good():
    g = make_gate()
    g.admit(CommandSigner(KEY).sign(BENIGN))
    assert (g.last_good.f1_valve, g.last_good.product_valve) == (55.0, 65.0)


def test_unsigned_command_rejected_in_strict_mode():
    g = make_gate()
    ok, why = g.admit(BENIGN)
    assert not ok and "unsigned" in why
    assert g.last_good.f1_valve == 50.0          # last-good untouched


def test_unsigned_command_trusted_in_legacy_mode():
    ok, _ = make_gate(strict=False).admit(BENIGN)
    assert ok


def test_wrong_key_rejected():
    ok, why = make_gate().admit(CommandSigner(b"attacker-key").sign(BENIGN))
    assert not ok and "invalid" in why


def test_tampered_values_rejected():
    signed = CommandSigner(KEY).sign(BENIGN)
    ok, _ = make_gate().admit(replace(signed, product_valve=0.0))
    assert not ok


def test_replayed_command_rejected():
    g, signer = make_gate(), CommandSigner(KEY)
    signed = signer.sign(BENIGN)
    assert g.admit(signed)[0]
    ok, why = g.admit(signed)
    assert not ok and "replayed" in why


def test_clamped_preserves_provenance_fields():
    c = CommandSigner(KEY).sign(BENIGN).clamped()
    assert c.seq == 1 and c.auth_tag


def test_legacy_construction_still_works():
    c = ControlCommand(1.0, 2.0, 3.0, 4.0)
    assert c.seq == 0 and c.auth_tag == ""


# ---- audit() ----------------------------------------------------------------

def test_audit_clean_when_plant_matches_last_good():
    assert make_gate().audit(OP) == []


def test_audit_flags_out_of_band_valve_write():
    found = make_gate().audit({**OP, "product_valve_pos": 5.0})
    assert len(found) == 1 and "product_valve_pos" in found[0]


def test_audit_flags_injected_e_stop():
    assert make_gate().audit({**OP, "e_stop": True})


def test_audit_expects_e_stop_after_safe_state_note():
    g = make_gate()
    g.note_safe_state({"e_stop": 1})
    assert g.audit({**OP, "e_stop": True}) == []


def test_audit_expects_closed_product_for_low_level_safe_state():
    g = make_gate()
    g.note_safe_state({"e_stop": 0, "product_valve_sp": 0.0})
    assert g.audit({**OP, "product_valve_pos": 0.0}) == []


# ---- PlantService wiring (fake client) ---------------------------------------

class FakePlant:
    """Tiny stand-in for GrficsClient: holds valve state, applies writes."""
    def __init__(self):
        self.state = dict(OP)
        self.writes = []

    def connect(self): return self
    def close(self): pass

    def _raw(self):
        return {"measured": {}, "outputs": {"pressure": 2700.0, "liquid_level": 44.0},
                "state": dict(self.state)}

    def read(self): return self._raw()

    def write_inputs(self, inputs):
        self.writes.append(dict(inputs))
        for k, v in inputs.items():
            if k.endswith("_valve_sp"):
                self.state[k.replace("_sp", "_pos")] = v
            elif k == "e_stop":
                self.state["e_stop"] = bool(v)
        return self._raw()


def make_service():
    svc = PlantService(gate=make_gate())
    svc.client = FakePlant()
    return svc


def test_service_applies_authorized_command_and_rejects_identical_unsigned_one():
    svc = make_service()
    svc.apply(CommandSigner(KEY).sign(BENIGN))
    assert svc.client.state["product_valve_pos"] == 65.0       # authorized: applied
    injected = replace(BENIGN, product_valve=5.0)               # unsigned, in-limits
    svc.apply(injected)
    assert svc.client.state["product_valve_pos"] == 65.0       # last-good re-asserted
    assert any(e.kind == "rejected_command" for e in svc.gate.events)
    assert not svc.safety.tripped                               # physics never objected


def test_service_detects_out_of_band_write_and_reasserts_last_good():
    svc = make_service()
    signer = CommandSigner(KEY)
    svc.apply(signer.sign(BENIGN))
    svc.client.state["product_valve_pos"] = 5.0                 # direct write, bypassing enforcer
    svc.apply(signer.sign(BENIGN))                              # next legit cycle
    assert [e.kind for e in svc.gate.events] == ["out_of_band_write"]
    assert svc.client.state["product_valve_pos"] == 65.0


def test_service_without_gate_is_unchanged_legacy_behaviour():
    svc = PlantService()
    svc.client = FakePlant()
    svc.apply(BENIGN)                                           # unsigned, no gate -> trusted
    assert svc.client.state["product_valve_pos"] == 65.0


# ---- non-finite commands (NaN used to clamp to 100% open) ---------------------

NAN_CMD = ControlCommand(f1_valve=float("nan"), f2_valve=45.0, purge_valve=20.0, product_valve=65.0)


def test_clamped_refuses_non_finite():
    with pytest.raises(ValueError):
        NAN_CMD.clamped()


def test_gate_rejects_signed_non_finite_command():
    ok, why = make_gate().admit(CommandSigner(KEY).sign(NAN_CMD))
    assert not ok and "non-finite" in why


def test_service_never_applies_nan_valve_with_gate():
    svc = make_service()
    svc.apply(CommandSigner(KEY).sign(NAN_CMD))
    assert svc.client.state["f1_valve_pos"] == 50.0              # last-good, not 100% open


def test_service_never_applies_nan_valve_without_gate():
    svc = PlantService()
    svc.client = FakePlant()
    svc.apply(NAN_CMD)
    assert svc.client.state["f1_valve_pos"] == 50.0              # held where it was
    assert all(not k.endswith("_valve_sp") for k in svc.client.writes[-1])
