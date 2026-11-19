"""Package actual frozen/evaluated weights locally. Never publishes or deletes."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-run04"
MODEL = ROOT / "model/scram-observer-slm-candidate"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(name):
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def main():
    if not (RUN / "final-evaluation.completed.json").exists() or not (RUN / "runtime-integration.json").exists():
        raise RuntimeError("real final evaluation and shared-runtime integration must complete before packaging")
    frozen, selected = read("freeze.json"), read("training/selected.json")
    weights = Path(frozen["model"])
    if sha(weights) != frozen["model_sha256"]:
        raise ValueError("frozen model hash changed")
    MODEL.mkdir(parents=True, exist_ok=True)
    deployed_weights = MODEL / weights.name
    if deployed_weights.exists() and sha(deployed_weights) != frozen["model_sha256"]:
        raise FileExistsError("different candidate already exists; refusing to overwrite")
    if not deployed_weights.exists():
        shutil.copyfile(weights, deployed_weights)
    modelfile = MODEL / "Modelfile"
    modelfile.write_text(f'FROM ./{weights.name}\nPARAMETER num_ctx 3072\nPARAMETER temperature 0\n'
                         'PARAMETER num_predict 128\nPARAMETER repeat_penalty 1.1\nPARAMETER repeat_last_n 3072\n', encoding="utf-8")
    base = ROOT / "artifacts/optimization/candidates/base-qwen2.5-0.5b"
    shutil.copyfile(base / "LICENSE", MODEL / "BASE_LICENSE")
    shutil.copyfile(ROOT / "LICENSE", MODEL / "PROJECT_LICENSE")
    shutil.copyfile(ROOT / "scripts/install_observer_slm.py", MODEL / "install_candidate.py")
    shutil.copyfile(ROOT / "scripts/run_observer_slm.py", MODEL / "run_candidate.py")
    reports = {}
    for name in ("base-validation.json", "adapter-validation.json", "q4-validation.json",
                 "f16-validation.json", "q8-validation.json", "legacy-validation.json", "final-test.json"):
        if (RUN / name).exists():
            reports[name] = read(name)
    with (MODEL / "evaluation-per-class.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["report", "task", "label", "support", "precision", "recall", "invalid", "false_positives", "normal_denominator"])
        for name, report in reports.items():
            for kind in ("proposals", "telemetry"):
                values = report["metrics"][kind]
                for label, numbers in values.get("per_class", {}).items():
                    writer.writerow([name, kind, label, numbers["support"], numbers["precision"], numbers["recall"],
                                     values["unavailable"], values["false_positives"], values["normal_denominator"]])
    summary = {name: {key: report[key] for key in ("metrics", "telemetry_paths", "by_group", "runtime",
                    "batch_size", "temperature", "max_new_tokens", "constrained_decoding") if key in report}
               for name, report in reports.items()}
    (MODEL / "evaluation-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    old_size = sum((ROOT / p).stat().st_size for p in (
        "model/scram-observer-gguf/scram-observer-v2-q4_k_m.gguf",
        "model/scram-observer-v3-gguf/scram-observer-v3-q4_k_m.gguf"))
    new_size = weights.stat().st_size
    manifest = {"alias": "scram-observer-slm-candidate", "weights_file": weights.name,
                "weights_sha256": sha(weights), "weights_bytes": new_size, "modelfile_sha256": sha(modelfile),
                "base": "Qwen/Qwen2.5-0.5B-Instruct", "base_revision": frozen["base_revision"],
                "parameter_count": 494032768, "wire_schema": "slm-v1", "context": 3072,
                "selected_optimizer_step": selected["step"], "release_qualified": False,
                "legacy_observer_weights_bytes": old_size,
                "observer_install_weight_reduction_bytes": old_size-new_size,
                "observer_install_weight_reduction_fraction": 1-new_size/old_size,
                "local_build_artifacts_retained": True, "gpu_savings_claimed": False}
    (MODEL / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    final = reports["final-test.json"]
    recap = []
    for kind in ("proposals", "telemetry"):
        values = final["metrics"][kind]
        recap.append(f"- {kind}: n={values['n']}; invalid={values['unavailable']}; false positives "
                     f"{values['false_positives']}/{values['normal_denominator']}; recall: "+
                     ", ".join(f"{label} {numbers['recall']:.1%} (n={numbers['support']})"
                               for label, numbers in values.get("per_class", {}).items()))
    card = f"""# SCRAM shared observer SLM candidate

This is an actually fine-tuned 494,032,768-parameter Qwen2.5-0.5B-Instruct,
with shared weights for `proposal_review` and `telemetry_review`. It is an
experimental candidate; legacy remains the validated default. Quality failures
must be assessed from per-class results, not file size or successful JSON parsing.

The selected adapter is checkpoint {selected['step']} from an actual 180-step,
two-epoch experiment (1,440 cumulative examples). LoRA trained 8,798,208
parameters with BF16, rank 16/alpha 32 and gradient checkpointing on an RTX 3050
6 GB under native Windows. A real 16-step smoke, changed weights, checkpoint
reloads, Transformers inference, merged/GGUF inference and the shared runtime
were exercised. No weights were trained from GGUF or substituted from V2/V3.

Base revision: `{frozen['base_revision']}`. Base license is Apache-2.0;
`BASE_LICENSE` and the project's `PROJECT_LICENSE` are included. No remote
inference, dataset upload, paid compute or publication occurred.

## Training data and limitations

The old 1,432/1,094 preparation was audited. Its copied proposal rows lacked
recording provenance and shared 12 measured-rationale trajectories across splits.
Those rows were excluded from fitting/selection. The new data has 720 balanced
training and 475 validation examples, with disjoint recording groups and complete
six-proposal or sixteen-sample measured/commanded histories. New proposal labels
are specified scenario transformations; historical telemetry labels are scenario/
augmentation labels, not newly human-verified ground truth. Source metadata and
simulator truth are excluded from model inputs. Request IDs are application-only.
Exact per-example historical telemetry recording IDs were not retained; the
manifest uses conservative disjoint split-group attribution.

The sealed historical test was opened only after model/quantization freeze.
Additional proposal scenarios use held-out recordings. Synthetic frozen-level/
dropout stress cases are reported separately and do not create independent gate
support. Small recording counts, template-based proposal variants and imperfect
historical labels limit generalization. Explanations can be numerically or
causally wrong; a score is not a calibrated probability or proof.

One controlled adjustment followed weak early recall: class-label token loss
weight 10, while retaining full assistant-response supervision. Checkpoint
selection used a fixed validation subset and unweighted validation loss, with
no final-test tuning. See `training/SHARED_SLM_RECIPE.md` in the project for the
prepare/train/resume/evaluate/export commands and the run's exact source/package
locks. Full precision/recall/confusion data and raw outputs remain under the run.

## Real evaluation

{chr(10).join(recap)}

These headline counts combine documented groups. The primary historical telemetry
test, new held-out proposal scenarios, and synthetic stress groups are separately
reported in `final-test.json`. `evaluation-per-class.csv` includes the base,
fine-tuned, F16, quantized and specialist comparisons with actual sample counts.
CPU-only/combined telemetry are separate fields; they do not alter SLM-only scores.
Different runtimes and batch sizes make their latency figures non-interchangeable.
Unconstrained quality evaluation is distinct from schema-constrained serving.

The full 475-case Transformers validation had 66/66 proposal classifications
correct, but sensor recall was 26/120, actuator recall 31/120 and excursion recall
0/19, with 29/150 normal telemetry false alarms. A missed command/position gap of
21.44 percentage points and fabricated numeric explanations were inspected.
For example, an actual pressure range of 92.08 kPa was described as 10.0 kPa;
a sensor dropout was explained using an invented 14.4% actuator gap where all
measured/commanded gaps were zero. Historical target explanations also have
ambiguous causal claims. The candidate has learned proposal templates and response
formatting better than general telemetry reasoning. Neither perfect proposal
template scores nor CPU corrections establish general replacement readiness.

## Installation and opt-in

Requires Ollama and the existing SCRAM application dependencies; PyTorch, the
base checkpoint and training caches are not needed for deployment. Start Ollama,
then register only this new alias using the bundled standalone installer:

```powershell
python install_candidate.py --directory .
```

From the project root the equivalent command is
`python scripts/install_observer_slm.py`. It checks the GGUF and never deletes or
overwrites V2, V3 or operator aliases. Activate in a fresh isolated app session:

```powershell
$env:SCRAM_OBSERVER_MODE = 'unified'
$env:SCRAM_OBSERVER_MODEL = 'scram-observer-slm-candidate'
$env:SCRAM_OBSERVER_SCHEMA = 'slm-v1'
$env:SCRAM_OBSERVER_CONTEXT = '3072'
```

For this build's dedicated test server also set
`$env:SCRAM_OBSERVER_URL = 'http://127.0.0.1:11437'` and
`$env:SCRAM_OBSERVER_BACKEND = 'llama_cpp'`. Ollama registration was blocked by
manifest reads returning `Access is denied` inside this agent environment.
Real exported evaluations and runtime integration instead used Ollama's bundled
llama.cpp 0.8.0-dev (39febd2ad), CUDA v12, with 25/25 layers offloaded.
A usual Ollama installation
uses the default localhost:11434. Do not point a new live session at an existing
plant writer: ordinary app startup resets its simulator. Standalone integration
verification performed no plant writes.

For the tested direct-server route, run `python run_candidate.py --directory .`
in one terminal (requires the CUDA v12 llama.cpp server bundled with Ollama).
Then set `SCRAM_OBSERVER_BACKEND=llama_cpp` and
`SCRAM_OBSERVER_URL=http://127.0.0.1:11438` in the SCRAM terminal, along with
the unified/model/schema/context variables above. `--cpu` is available for a
compatible CPU build. This starts only inference; it never starts the plant.
Ollama's ordinary installer command remains supplied, but its successful
registration is not claimed for this restricted build environment.

Both tasks use one loaded observer model and fixed context, with distinct
histories, role schemas, generations, deadlines and output routing. Telemetry
stays advisory; proposal evidence enters the existing governance path. The
operator, CPU detector, deterministic controls, signing and interlock are unchanged.
Missing/malformed/stale responses remain explicit; no legacy models are silently
loaded in unified mode. CPU detection remains independent.

Rollback: set `SCRAM_OBSERVER_MODE=legacy` and clear the candidate model/schema/
URL/backend overrides in the terminal. Original weights were retained. Do not remove them
until replacement quality and integration gates have actually passed.

## Artifact size and integrity

Selected GGUF: `{weights.name}`; {new_size:,} bytes.
SHA-256: `{manifest['weights_sha256']}`.
The two old observer GGUFs total {old_size:,} bytes; the selected observer install
file is {1-new_size/old_size:.1%} smaller. This is a deployment weight comparison,
not freed local build space, GPU/CPU memory savings or a throughput guarantee.
Adapters, higher-precision artifacts, checkpoints and old models are retained.
Nominal Q4_K_M can include fallback Q5/Q8 tensor types; see export inspection/logs.
"""
    (MODEL / "README.md").write_text(card, encoding="utf-8")
    distribution = RUN / "distribution"
    distribution.mkdir(exist_ok=True)
    bundle = distribution / "scram-observer-slm-candidate.zip"
    if bundle.exists():
        raise FileExistsError("distribution bundle already exists; refusing to replace")
    names = [weights.name, "Modelfile", "README.md", "deployment.json", "BASE_LICENSE",
             "PROJECT_LICENSE", "install_candidate.py", "run_candidate.py", "evaluation-per-class.csv", "evaluation-summary.json"]
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name in names:
            archive.write(MODEL / name, arcname=name)
    (bundle.with_suffix(".zip.sha256")).write_text(sha(bundle)+"  "+bundle.name+"\n", encoding="utf-8")
    print(json.dumps({"model": str(MODEL), "bundle": str(bundle), "bundle_bytes": bundle.stat().st_size,
                      "bundle_sha256": sha(bundle), "weights_sha256": manifest["weights_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
