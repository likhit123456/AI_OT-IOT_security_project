# Refactor notes

All original files are preserved. The root Python modules, dependency files, historical captures, screenshots and specialist documents have been grouped by purpose. Application imports, CI dependency installation and local Markdown links were updated. The main launch commands remain `python -m live` and `python -m sim_app`. Run from the repository root.

## Manual verification

1. Install `python -m pip install -r requirements/dev.txt -r requirements/eval.txt` in a development environment.
2. Run `python -m pytest -q --ignore=tests/test_interlock_truth.py`.
3. Build/typecheck the frontend in `frontend/` with its existing npm scripts. Review the committed static output before replacing it.
4. Run `node tests/frontend_smoke.cjs` after installing its Playwright prerequisites.
5. Start the supported simulator, then verify the control room and the manual studio separately. Do not run competing plant writers together.
6. Run the existing Linux/WSL simulator compilation and isolated checker described in README.md.
7. If retained, exercise historical demos, training collectors, Debian packaging and the Modbus diagnostic separately. The diagnostic still requires a separate OpenPLC endpoint and its appropriate dependencies.

## Deliberately deferred

No dataset, tokenizer, image, backup, unused component or optional application was removed. No dependencies were upgraded. The simulator was not launched. A full src-layout migration and splitting training datasets from scripts are deferred because runtime paths and research reproducibility need coordinated validation. `training/design_point.json`, deployed static assets and hash-validated model evidence remain where their consumers expect them.

## Automated checks performed

Python syntax parsing, checks for stale imports of moved modules, preservation accounting and model/feature hash verification are recorded in validation.json. No runtime or end-to-end test pass is claimed.

The inherited `simulation/simulation/remote_io/modbus/remote_io.py` already has a syntax error at line 16 in the original ZIP. It is preserved for manual review. All other Python files parsed successfully. Model bundle files and physics_features.py remain byte-for-byte unchanged.
