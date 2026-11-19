"""Rebuild the learning documents without running any plant/training code."""
from __future__ import annotations
import ast
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / '_vendor'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
    Spacer, PageBreak, Image, Preformatted, KeepTogether)
from reportlab.platypus.tableofcontents import TableOfContents
from pypdf import PdfReader

INV = json.loads((HERE/'inventory.json').read_text(encoding='utf-8'))
FIG = HERE/'figures'
FIG.mkdir(exist_ok=True)

ROLES = {
 'contracts.py': 'The cross-layer data contract. PlantState carries observations, SupervisoryTargets carries intent and ControlCommand carries actuations plus authentication. Read this before following imports elsewhere.',
 'l1_regulator.py': 'Current live four-loop PI controller. Includes anti-windup, pressure split-range feed assistance, production feedforward, composition conversion, hold/resume tracking and final command signing.',
 'l1_stub.py': 'Historical placeholder regulator retained by older demonstrations and dataset collection. Its behavior is not interchangeable with the live L1Regulator.',
 'run_demo.py': 'Minimal historical end-to-end entry point wiring baseline L3, L1 stub and L0. It is not the full live control room.',
 'modbus_test.py': 'Standalone inherited Modbus exercise. Reads/writes device registers directly; not imported by the current JSON-based control loop.',
 'l0_plant/grfics_client.py': 'Newline-delimited JSON socket adapter. Manages connection lifetime and synchronous request/response framing; it deliberately contains no safety policy.',
 'l0_plant/plant_service.py': 'Translates raw simulator dictionaries to PlantState, runs the interlock, audits command origin, applies admitted/fallback/protective writes and guards reset with safety-channel readings.',
 'l0_plant/provenance_gate.py': 'Exact-float HMAC signing, increasing sequence admission, last-good fallback and out-of-band actuator/E-stop audit. Does not authenticate the simulator socket itself.',
 'l0_plant/safety.py': 'Trip thresholds, reset hysteresis, optional closed-loop SafeHold and the latched interlock state machine. First cause persists while secondary hazards can escalate protection.',
 'l0_plant/fault_injection.py': 'Names and writes simulator-native ordinary-sensor fault modes/severities, including clearing faults. It does not corrupt the independent safety channel.',
 'l3_brain/operator.py': 'Wraps a swappable Policy, saves unblended last_intent and blends each target according to supplied authority. Zero authority produces None targets.',
 'l3_brain/policy.py': 'Policy protocol, deterministic baseline, strict model-output parsing, cached/background LLM decisions, Ollama transport and availability/fallback selection.',
 'l3_brain/prompt_format.py': 'Single measured-only operator prompt schema shared by training and serving. Defines design targets, field selection, rounding and system text.',
 'enforcer/keel.py': 'Deterministic physical constraint layer, authority budget and chained event log. Uses SisFrame for hazard checks and measured values only for divergence evidence.',
 'live/__main__.py': 'Primary application entry point. CLI, simulator availability/startup, opt-in Ollama setup, Engine lifecycle, HTTP server and optional browser launch.',
 'live/engine.py': 'Full-stack composition and control-thread owner. Queues actions, regulates/signs/applies commands, runs monitors/governance, executes demonstrations, manages recovery and publishes bounded snapshots.',
 'live/server.py': 'Loopback standard-library HTTP server: static assets, incremental state API, validated action submission and same-origin GRFICS proxy.',
 'live/commands.py': 'Deterministic control-room chat routing into protected manual valve overrides, release, canonical target instructions or free-form model notes.',
 'monitor/behavior_monitor.py': 'Measured-only bounds, rate, target-step and stale-sensor checks. Its flag stream is diagnostic; it does not own an actuator.',
 'monitor/sequential_monitor.py': 'Accumulated proposal drift, directional runs, lexical rationale screening and Keel-event corroboration. Audits intent before authority blending.',
 'monitor/governance.py': 'Debounces rule/proposal evidence and turns it into a rate-limited authority ceiling with a nonzero floor and conditional recovery.',
 'monitor/observer.py': 'Background model observer and split-role router, with stale-evidence expiry, reset generation guards, independent fallback chains and telemetry-only specialist correction.',
 'monitor/observer_v2_format.py': 'Exact v2 chat/telemetry/proposal schema and verdict parser. Proposal formatting is reused by v3; tests verify parity with saved examples.',
 'monitor/observer_v3_format.py': 'Longer measured telemetry windows with all flows and command/position pairs. Shares proposal format/labels with v2.',
 'monitor/telemetry_classifier.py': 'Input validation, statistical features and exported-tree inference. Old correction preserves existing alarms; approved_specialist prefers the release-gated physics detector.',
 'monitor/physics_detector.py': 'Checks enabled policy, release-gate result and evidence hashes, then uses the physics forest to correct advisory telemetry while retaining llm_label.',
 'monitor/physics_features.py': 'Measured process-consistency features: command/position gaps, valve/flow residuals, species/inventory balances, quantization tolerances and pressure trends.',
 'sim_app/__main__.py': 'Manual studio entry point on port 8770; checks/starts the simulator and launches the separate FastAPI app.',
 'sim_app/server.py': 'Direct-control Plant and FastAPI routes, serialized actuation, polling, rupture/E-stop latches, reset and command/rescue coordination.',
 'sim_app/ai.py': 'Grounded multi-action command parser plus a separate trained-operator rescue controller. Run identity and cancellation guards prevent late writes after stop/E-stop.',
 'live/static/control-room.js': 'Browser state, poll cursors, actions/chat, tabs, event filtering, recovery and layer status, SVG instruments and Canvas histories. Render helpers project backend state into DOM elements.',
 'live/static/telemetry-workspace.js': 'Read-only telemetry exploration controls: signal choice, table/graph modes, connection state and rendering from the existing backend feed.',
 'live/static/index.html': 'Main control-room document and stable DOM IDs consumed by control-room.js; links layout/style layers and embeds reactor/telemetry views.',
 'live/static/reactor.html': 'Standalone full-bleed Unity reactor wrapper, loaded through the live server/proxy.',
 'sim_app/static/app.js': 'Manual studio browser client: polling, gauge/valve widgets, activity-to-card conversion, charts, command queue, rescue/E-stop/reset controls and 3D sizing.',
 'sim_app/static/index.html': 'Manual studio page structure, controls, status/AI panes and embedded visualization, bound by app.js.',
 'tests/frontend_smoke.cjs': 'Playwright browser regressions using local mock services. Checks rendering/interactions, disconnected behavior and markup handling; separate from pytest.',
 'docker-compose.yml': 'Active deployment configuration: prebuilt simulator image, loopback ports, fixed bridge IP, NET_ADMIN and simulator-only supervisor mount. No local Dockerfile build is invoked.',
 'docker/supervisord.sim-only.conf': 'Active supervisor override omitting Modbus writers so they do not overwrite SCRAM actuator commands.',
 'simulation/Dockerfile': 'Alternative local two-stage image build with C++ compilation, web assets, PHP/nginx, supervisor and Modbus dependencies. Not used by root Compose.',
 'simulation/simulation/main.cc': 'C++ TCP service using select(), per-client newline buffers, JSON request validation, bounded frames and complete-response sending. Supports isolated test-port override.',
 'simulation/simulation/TE_process.cc': 'Basic local reactor implementation: LAPACK steady-state solution, wall-clock Euler integration, species balances, pressure/flow equations, E-stop and JSON outputs. Does not supply the deployed measured/fault channel.',
 'simulation/simulation/TE_process.h': 'TE class declarations: process parameters, inventories, derivatives, valve feedback, measurements, clocks and public lifecycle/state methods.',
 'simulation/simulation/remote_io/modbus/modbusdevice.py': 'Async Modbus server infrastructure and simulator JSON read/write helpers. Device callbacks map registers to individual process signals.',
 'simulation/simulation/remote_io/modbus/helper.py': 'Inherited simulator/Modbus support helpers; inspect callers in the remote-I/O directory rather than assuming current live usage.',
 'simulation/simulation/remote_io/modbus/remote_io.py': 'Legacy Twisted/pymodbus server example with obsolete server.async import syntax. Not a Python 3.13-compatible live entry point.',
 'simulation/simulation/remote_io/modbus/server_async.py': 'Legacy asynchronous Modbus example/configuration. Retains obsolete pymodbus imports and belongs to the inherited lab path.',
 'simulation/web_visualization/Build/UnityLoader.js': 'Generated third-party Unity bootstrap/runtime loader. Loads the built player; it is not authored SCRAM policy or original Unity scene source.',
 'simulation/web_visualization/Build/ChemicalPlant.json': 'Unity build manifest naming player code/data resources and runtime settings. Referenced binaries must be supplied by the relevant image/build.',
 'simulation/web_visualization/data/index.php': 'Inherited HTTP-to-simulator socket bridge for state and E-stop. Separate from the Python API and its protection logic.',
 'simulation/web_visualization/TemplateData/UnityProgress.js': 'Unity loading-progress DOM updates and progress-bar visibility logic.',
 'simulation/web_visualization/version.php': 'Reports the installed simulator image/release version to the inherited web interface.',
 'simulation/web_visualization/versions.php': 'Inherited Docker-oriented service-version view, omitted by the Debian packaging path.',
 'packaging/build-simulation-deb.sh': 'Compiles the local simulator, stages web/service files, substitutes a Git-derived version and builds the Debian package. Separate from active Compose.',
 'packaging/simulation/www/vm-overlay.js': 'VM-oriented web overlay injected during Debian packaging; adapts inherited visualization presentation.',
 'scripts/reset_plant.py': 'Restarts the simulator, waits for connectivity, applies the corrected 0.3926% design purge and confirms stable pressure/level. A reset is an active state-changing operation.',
 'scripts/check_simulator.py': 'Starts a separate local compiled simulator on a free port and checks protocol/validation; does not use the normal application plant.',
 'scripts/collect_accuracy_holdout.py': 'Collects isolated accelerated simulator trajectories, constructs measured windows and records native versus synthetic fault provenance in sealed holdout evidence.',
 'scripts/demo_authority.py': 'Reads one live snapshot and demonstrates authority 1, 0.5 and 0 without driving the plant.',
 'scripts/review_findings.py': 'Focused regression probes for previously fixed behavior using fakes; related assertions are in quality-hardening tests.',
 'training/build_dataset.py': 'Operator chat dataset builder: expert labels, duplicate removal, block-based splits, minority-rule balance and prompt-format checks.',
 'training/build_observer_dataset.py': 'v3 observer dataset builder: preserves v2 proposals, creates command-aware telemetry windows, labels recorded/synthetic faults and reserves scenario holdouts.',
 'training/build_clean_observer_dataset.py': 'Stronger whole-recording split and exact-prompt deduplication, with a reproducible clean-dataset manifest.',
 'training/collect_states.py': 'Real simulator rollout collection at 2 Hz with measured observations, separate evaluation truth and design-point capture. Resets/writes the plant.',
 'training/collect_observer_states.py': 'Collects native sensor/actuator faults with the issued command paired to each measured state. Completed rollouts are appended from temporary files.',
 'training/expert_policy.py': 'Rule-based teacher for operator imitation learning, grounded in saved design targets and production safety thresholds. Emits targets/rationale from measured state.',
 'training/train_local.py': 'Local operator QLoRA training, validation and GGUF export stages; uses Unsloth/TRL and llama.cpp tools under .tools.',
 'training/train_telemetry_classifier.py': 'Earlier CPU statistical-feature forest training/export and comparison against recorded LLM predictions.',
 'training/train_physics_telemetry.py': 'Fits/selects physics-feature forests on training/validation, exports dependency-free JSON arrays and checks runtime prediction parity.',
 'training/evaluate_physics_holdout.py': 'Frozen-candidate independent evaluation and predeclared release gate. Writes evidence, so rerunning is distinct from reading a report.',
 'training/evaluate_telemetry_specialist.py': 'Evaluates the older conservative correction policy using validation and historical replay; not fresh LLM inference.',
 'training/eval_ollama.py': 'Evaluates deployed quantized operator models on held-out states and probes, using parser acceptance, hold correctness and setpoint error.',
 'training/benchmark_command_parser.py': 'Non-actuating authored-command benchmark comparing exact ordered action/valve/amount tuples from real parser inference.',
 'training/benchmark_observer.py': 'Fresh balanced held-out observer/specialist replay with response/model/input-hash recording.',
 'training/benchmark_physics_pair.py': 'Non-actuating paired old/new telemetry-pipeline replay on the same frozen final holdout.',
 'training/scram_observer_finetune.ipynb': 'Observer notebook: environment setup, clean dataset/format checks, training, evaluation and candidate export. Code cells are preserved in source order in the companion.',
 'training/design_point.json': 'Saved settled process targets and valve lineup. The stored purge observation is corrected by reset_plant.py/live startup for the development image bug.',
 '.github/workflows/quality.yml': 'CI definition: Python regressions, JS syntax/browser smoke and isolated Linux C++ compilation/protocol checks.',
 '.gitattributes': 'Line-ending/LFS rules. JSON/JSONL evidence is kept byte-stable so integrity hashes survive checkout.',
 '.gitignore': 'Excludes local weights, environments, tools/caches and generated output from Git. Ignored local files are not missing project source.',
 'pytest.ini': 'Pytest discovery and execution configuration; distinguish the explicitly excluded live integration tests when running offline.',
 'requirements.txt': 'Pinned runtime Python dependencies for FastAPI, Uvicorn and Pydantic; most control-layer code uses the standard library.',
 'requirements-dev.txt': 'Runtime plus test/lint dependencies. Browser packages are installed separately in the CI workflow.',
 'requirements-eval.txt': 'Numpy/scikit-learn dependencies for CPU training/evaluation; the exported model runtime avoids them.',
 'requirements-train.txt': 'GPU fine-tuning dependencies; separate from ordinary app requirements and from the CPU evaluation stack.',
 'ARCHITECTURE.md': 'Original design/trust discussion with historical open items. Read the corrections in this guide before treating it as a current implementation reference.',
 'README.md': 'Current top-level startup, component map, model setup, verification and limitations entry point.',
 'README_GRFICS.md': 'Upstream/inherited GRFICS lab documentation and attribution, broader than the current plant-only SCRAM deployment.',
 'DEMO_RUNBOOK.md': 'Operator demonstration sequence for the live engine and scenarios. Describes active plant manipulations.',
 'ACCURACY_IMPROVEMENTS.md': 'Later parser/physics-detector results, paired evaluation, release scope and remaining weaknesses.',
 'ACCURACY_REPORT.md': 'Earlier accuracy review and historical pipeline evidence; do not subtract scores on different datasets to claim improvement.',
 'QUALITY_IMPROVEMENTS.md': 'Reliability fixes and evidence, including fault combinations, concurrency, provenance and stale-state handling.',
 'PROJECT_REVIEW.md': 'Historical repository review findings and priorities. Tests/current implementation determine which observations remain applicable.',
 'FRONTEND_REDESIGN.md': 'Frontend design rationale and interaction/layout notes for the implemented control-room/studio interfaces.',
 'LICENSE': 'GPL license text retained with the inherited GRFICS/TE source. This entry explains repository provenance, not legal advice.',
 'SECURITY.md': 'Project security reporting and scope documentation.',
 'CODE_OF_CONDUCT.md': 'Contributor/community conduct expectations; no runtime effect.',
}

def compact(s, n=340):
    s = ' '.join(s.split())
    return s if len(s) <= n else s[:n-3]+'...'

def role(r):
    p = r['path']; q = Path(p)
    if p in ROLES: return ROLES[p]
    if q.name == '__init__.py': return 'Package initializer. Establishes the import namespace; inspect any exports below. An empty initializer contains no control logic.'
    if p.startswith('tests/') and p.endswith('.py'):
        doc = compact(r.get('doc',''), 400)
        return (doc+' ' if doc else '') + 'Regression specification; the test names and assertions below identify the behaviors protected. Fakes/mocks isolate dependencies unless the file explicitly requires the live simulator.'
    if p.startswith('experiments/') and p.endswith('.py'):
        return compact(r.get('doc',''), 1100) + ' Saved result files in the same directory describe particular historical runs.'
    if p.startswith('reference/'):
        return 'Historical shelved simulator/reference material. Not wired into root Compose or python -m live. Compare against the active local source only when investigating project history.'
    if p.startswith('artifacts/accuracy-v2/simulator/'):
        return 'Test-only accelerated/fault-enabled TE variant used for isolated holdout collection. It is neither the active prebuilt image nor the basic local simulator source.'
    if p.endswith('parser-before.py'): return 'Frozen parser implementation from before grounding corrections, preserved for baseline comparison. Current studio code is sim_app/ai.py.'
    if q.suffix == '.css': return 'Presentation stylesheet: layout, typography, color, component states and responsive rules. It does not make control decisions. Cascade/load order in the matching HTML determines overrides.'
    if q.suffix == '.pcap': return 'Binary packet capture retained from Modbus/network experimentation. Contains traffic evidence, not executable application code; packet-level interpretation requires a capture reader.'
    if q.suffix.lower() in ('.png','.ico','.mp4'):
        return 'Visual asset: '+('inherited GRFICS documentation screenshot.' if p.startswith('images/') else 'Unity/browser template artwork or recorded plant video. Referenced by web pages/templates, not the control algorithm.')
    if p.startswith('.github/ISSUE_TEMPLATE/'): return 'Contributor issue-report template used by GitHub, with no application runtime effect.'
    if p == '.github/FUNDING.yml': return 'Repository funding metadata displayed by GitHub.'
    if q.name == 'Modelfile': return 'Ollama serving recipe: references local GGUF weights and configures prompt/template or generation behavior. The weight file itself may be ignored by Git.'
    if q.name == 'tokenizer.json': return 'Large tokenizer vocabulary/normalizer/pretokenizer and token-ID mapping. Generated model infrastructure, not a handwritten controller; printing every token would obscure the implementation.'
    if q.name == 'tokenizer_config.json': return 'Tokenizer options, special tokens and chat-format metadata used by the model stack.'
    if q.name == 'adapter_config.json': return 'LoRA adapter/base-model metadata, including rank, target modules and scaling; required to interpret adapter weights.'
    if q.suffix == '.jinja': return 'Model chat template translating role/content messages into the token layout expected by the base model and fine-tuning.'
    if q.name == 'model.json': return 'Exported CPU forest: feature version/count, ordered class names and per-tree left/right child, feature, threshold and leaf probability arrays. Executed by TelemetryClassifier.predict().' 
    if q.name == 'policy.json': return 'Deployment enablement/integrity policy. Determines whether the corresponding CPU model is eligible to load; referenced SHA-256 values bind it to evidence.'
    if q.name == 'tsconfig.json': return 'Static JavaScript/JSDoc type-checking options for the studio frontend; no generated application bundle is required.'
    if q.suffix == '.jsonl':
        if 'holdout' in p: return 'Sealed telemetry evaluation windows with labels/provenance and measured input. Kept separate from training; the manifest binds the exact bytes.'
        if q.name in ('states.jsonl','observer_states.jsonl'): return 'Recorded simulator observations/rollouts. Evaluation truth and command provenance are distinct from measured model inputs.'
        return 'Line-delimited chat dataset: system instructions, measured user input and labeled assistant response. Directory/name identifies train, validation or held-out split; one line is one example.'
    if q.suffix in ('.json','.csv'):
        if 'manifest' in q.name: return 'Reproducibility/cleanup manifest: records file identity, split/collection provenance or prior housekeeping actions. It is evidence, not runtime code.'
        if 'train_log' in p or 'train_metrics' in p: return 'Saved training configuration, loss history and resource/quality metrics. Describes a historical training run, not a fresh check of this machine.'
        return 'Saved evaluation or experiment data. Read its schema and producing script to distinguish scores, per-example predictions and timeline samples; values are historical evidence.'
    if q.name.lower() in ('makefile',): return 'C++ build rules selecting sources, compiler and link libraries. Building here does not replace the binary inside root Compose automatically.'
    if p.startswith('simulation/simulation/remote_io/modbus/'):
        return 'Inherited remote-I/O device adapter: maps the named process signal/actuator between simulator JSON and scaled Modbus registers. Disabled as a competing writer by the current simulator-only supervisor override.'
    if p.startswith('packaging/'):
        if '/systemd/' in p: return 'Debian/VM systemd unit declaring service startup, dependencies and restart behavior for the named simulator/Modbus/init service.'
        if q.name == 'control': return 'Debian package metadata and dependency declarations.'
        if q.name in ('postinst','prerm'): return 'Debian package lifecycle shell hook, enabling/starting or stopping/removing services as appropriate.'
        if '/nginx/' in p: return 'Nginx site configuration for the Debian/VM visualization and PHP route.'
        return 'Debian/VM initialization support, including network aliases/routing used by inherited Modbus devices; separate from the Python live application.'
    if q.suffix == '.md': return 'Directory-specific setup, background or saved-model documentation. Read in context of the current source and the historical distinctions in this guide.'
    if q.name == 'interfaces': return 'Container network-interface configuration for the inherited GRFICS subnet/aliases.'
    if 'nginx' in q.name: return 'Nginx web/PHP configuration for inherited visualization endpoints.'
    if 'supervisord' in q.name: return 'Supervisor process definitions for the image. Root Compose mounts a different simulator-only override.'
    if q.name == 'entrypoint.sh': return 'Image bootstrap: detects the plant subnet interface, creates network aliases/routes and starts the supplied service command.'
    if q.suffix == '.html': return 'Inherited visualization HTML layout and scripts. Its DOM hosts Unity/loading UI rather than Python control logic.'
    if q.name == 'tsconfig.json': return 'Static JavaScript/JSDoc type-checking options for the studio frontend; no generated application bundle is required.'
    return compact(r.get('doc',''),800) or 'Repository support file; its exact contents are preserved in the source companion when textual, and its identity is listed in inventory.json.'

def diagram(name, title, boxes, edges, size=(11,6)):
    fig, ax = plt.subplots(figsize=size)
    ax.set_xlim(0,11); ax.set_ylim(0,7); ax.axis('off')
    ax.set_title(title, loc='left', fontsize=18, fontweight='bold', pad=18, color='#15334b')
    for key,(x,y,w,h,label,color) in boxes.items():
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.08,rounding_size=0.12',facecolor=color,edgecolor='#385b70',linewidth=1.2))
        ax.text(x+w/2,y+h/2,label,ha='center',va='center',fontsize=10,color='#15334b',wrap=True)
    for a,b,label in edges:
        x,y,w,h,*_=boxes[a]; xx,yy,ww,hh,*_=boxes[b]
        start=(x+w/2,y+h/2); end=(xx+ww/2,yy+hh/2)
        dx=end[0]-start[0]; dy=end[1]-start[1]
        if abs(dx)>abs(dy): start=(x+(w if dx>0 else 0),y+h/2); end=(xx+(0 if dx>0 else ww),yy+hh/2)
        else: start=(x+w/2,y+(h if dy>0 else 0)); end=(xx+ww/2,yy+(0 if dy>0 else hh))
        ax.add_patch(FancyArrowPatch(start,end,arrowstyle='-|>',mutation_scale=15,color='#385b70',shrinkA=5,shrinkB=5))
        if label: ax.text((start[0]+end[0])/2+0.07,(start[1]+end[1])/2+0.1,label,fontsize=8,color='#385b70',bbox=dict(facecolor='white',edgecolor='none',pad=1))
    fig.savefig(FIG/name,dpi=170,bbox_inches='tight'); plt.close(fig)

def figures():
    b='#e5f2fa'; g='#e4f3eb'; o='#fff0d9'; r='#fbe6e3'
    diagram('architecture.png','SCRAM: supervisory intent to protected actuation',{
      'ui':(.3,5.8,2.2,.8,'Browser / HTTP\nactions + snapshots',b),'brain':(4,5.8,2.5,.8,'L3 policy\nmeasured state -> targets',b),
      'gov':(8,5.8,2.5,.8,'L2 Governor\nproposal/rule evidence',o),'l1':(4,4.2,2.5,.8,'L1 PI regulator\ntargets -> valve requests',b),
      'keel':(4,2.7,2.5,.8,'Keel -> final signer\nphysical constraints',g),'l0':(4,1.2,2.5,.8,'L0 gate + interlock\nadmission / protection',g),
      'plant':(.3,1.2,2.2,.8,'Simulator\nphysical process',b),'tel':(8,2.7,2.5,1.3,'Telemetry LLM +\nphysics forest\nDISPLAY ONLY',o),
      'sis':(.3,2.9,2.2,.8,'Independent simulated\nsafety channel',g)},
      [('ui','brain','engine queue'),('brain','l1','targets'),('gov','brain','authority cap'),('l1','keel','valves'),('keel','l0','signed'),('l0','plant','TCP write'),('plant','sis','outputs'),('sis','keel',''),('sis','l0','')])
    diagram('signals.png','Why measured telemetry and safety truth are separate',{
      'p':(.4,2.8,2,.9,'TE physical state',b),'m':(3.8,4.6,2.6,1,'Ordinary instrumentation\nfaultable measured data',o),
      't':(3.8,1.3,2.6,1,'Independent outputs\nPlantState.truth / SIS',g),'ai':(8,4.6,2.4,1,'L3 / L1 / measured\nobservers + UI',b),
      's':(8,1.3,2.4,1,'Keel + L0\nphysical protection',g)}, [('p','m','sensor faults'),('p','t','independent path'),('m','ai',''),('t','s','')])
    diagram('cycle.png','One normal engine cycle',{
      's':(.2,5.3,2.5,1,'Latest PlantState\nmeasured + safety view',b),'b':(4.1,5.3,2.5,1,'Brain.step()\nintent + authority blend',b),
      'g':(8.1,5.3,2.5,1,'Rules / observers\nGovernor.update()',o),'l':(8.1,3,2.5,1,'L1.regulate()\nmanual overrides',b),
      'k':(4.1,3,2.5,1,'Keel.step()\nthen L1.sign()',g),'a':(.2,3,2.5,1,'PlantService.apply()\ngate / protective override',g),
      'n':(.2,.7,2.5,1,'New PlantState\ntrip / re-arm bookkeeping',b),'h':(4.1,.7,2.5,1,'Snapshots + bounded\nhistory / events -> UI',b)},
      [('s','b',''),('b','g',''),('g','l',''),('l','k',''),('k','a',''),('a','n','write response'),('n','h','')])
    diagram('safety.png','Latched protective action selection (simplified)',{
      'check':(4,5.7,3,1,'Evaluate safety pressure/level\nretain first trip cause',b),'sensor':(.2,3.8,2.7,1,'Invalid safety sensor\nlatched E-stop',r),
      'combo':(4,3.8,3,1,'Pressure + low inventory\nfeeds shut / purge open\nproduct closed',r),'hold':(8,3.8,2.7,1,'Low inventory only\nclose product',o),
      'safe':(4,1.9,3,1,'Pressure / high level\nSafeHold if configured\nrunaway -> E-stop',o),'reset':(4,.05,3,1,'Reset only inside valid band\nlive adds timed auto re-arm\nand repeated-trip lockout',g)},
      [('check','sensor','invalid'),('check','combo','combined'),('check','hold','low only'),('safe','reset','recovery')])
    diagram('training.png','Separate learning and deployment lifecycles',{
      'states':(.2,5.2,2.8,1,'Simulator recordings\nmeasured + eval-only truth',b),'expert':(4.1,5.2,2.8,1,'Expert targets +\nshared chat prompt',b),
      'lora':(8,5.2,2.8,1,'QLoRA -> GGUF\nOllama operator',b),'windows':(.2,2.7,2.8,1,'Command-aware windows\nrecording-level splits',o),
      'forest':(4.1,2.7,2.8,1,'Measured physics features\ntrain / validation selection',o),'release':(8,2.7,2.8,1,'Freeze -> untouched test\npolicy + evidence hashes',g),
      'runtime':(8,.3,2.8,1,'JSON forest inference\nadvisory telemetry only',g)},
      [('states','expert','labels'),('expert','lora','fine-tune'),('states','windows',''),('windows','forest',''),('forest','release','candidate'),('release','runtime','approved')])
    sys.path.insert(0,str(ROOT))
    from monitor.governance import ceiling
    xs=[i/1000 for i in range(1001)]
    fig,ax=plt.subplots(figsize=(9,4.6)); ax.plot(xs,[ceiling(x) for x in xs],label='Other corroborated suspicion',color='#176f83',lw=2.5); ax.plot(xs,[ceiling(x,'provenance_violation') for x in xs],label='Provenance label',color='#b96636',ls='--')
    ax.set(xlabel='Corroborated suspicion',ylabel='Target authority ceiling',ylim=(0,1.05),title='Governance target ceiling (source-derived, not a plant trace)'); ax.grid(alpha=.2); ax.legend(); fig.tight_layout(); fig.savefig(FIG/'governance.png',dpi=170); plt.close(fig)
    report=json.loads((ROOT/'model/telemetry-physics/final-holdout-evaluation.json').read_text())
    names=list(report['score']['per_class_recall']); values=[report['score']['per_class_recall'][x]*100 for x in names]
    counts={'normal':31,'excursion':20,'sensor_fault':117,'actuator_fault':47}
    fig,ax=plt.subplots(figsize=(9,4.6)); bars=ax.bar([x.replace('_',' ') for x in names],values,color=['#176f83','#3c8f7a','#b96636','#6c71a0']); ax.set_ylim(0,115)
    ax.set(ylabel='Recall (%)',title='Saved final holdout: 200 correct / 215 windows')
    for bar,n,v in zip(bars,names,values): ax.text(bar.get_x()+bar.get_width()/2,v+2,f'{v:.2f}%\nn={counts[n]}',ha='center',fontsize=10)
    ax.spines[['top','right']].set_visible(False); fig.tight_layout(); fig.savefig(FIG/'evaluation.png',dpi=170); plt.close(fig)
    shutil.copyfile(ROOT/'images/sim.png',FIG/'plant.png')

def expr(n, limit=180):
    return compact(ast.unparse(n),limit)

def structural_summary(n):
    if isinstance(n,ast.ClassDef):
        bases=', '.join(ast.unparse(x) for x in n.bases) or 'no explicit base class'
        fields=[expr(x.target) for x in n.body if isinstance(x,ast.AnnAssign)]
        return f'Class ({bases}). '+('Declared data: '+', '.join(fields)+'.' if fields else 'Groups related methods/state.')
    if n.name.startswith('test_'): return 'Regression case: '+n.name[5:].replace('_',' ')+'. Read its setup and assertions to see the exact invariant.'
    facts=[]
    returns=[x for x in ast.walk(n) if isinstance(x,ast.Return) and x.value is not None]
    if returns: facts.append('Returns '+ '; '.join(dict.fromkeys(expr(x.value,100) for x in returns))[:270]+'.')
    calls=[ast.unparse(x.func) for x in ast.walk(n) if isinstance(x,ast.Call)]
    special=[x for x in dict.fromkeys(calls) if any(y in x.lower() for y in ('write','read','sign','apply','submit','reset','predict','request','open','start','post','fit','regulate','evaluate','save'))]
    if special: facts.append('Key operations: '+', '.join(special[:9])+'.')
    if not facts:
        assignments=[expr(x.targets[0],70) for x in n.body if isinstance(x,ast.Assign)]
        facts.append('Updates '+', '.join(assignments[:8])+'.' if assignments else 'Coordinates the statements shown in the source companion.')
    return ' '.join(facts)

def parse_python(s):
    return ast.parse(s.replace('server.async import','server.async_compat import'))

def symbol_nodes(tree):
    out={}
    def walk(body,prefix=''):
        for n in body:
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                out[prefix+n.name]=n; walk(n.body,prefix+n.name+'.')
    walk(tree.body); return out

def trace(body,indent=0):
    lines=[]
    for n in body:
        lead='  '*indent+f'L{n.lineno}: '
        if isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str): continue
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            lines.append(lead+'Define '+n.name+' (documented separately).'); continue
        if isinstance(n,ast.If):
            lines.append(lead+'Branch when '+expr(n.test)+'.'); lines+=trace(n.body,indent+1)
            if n.orelse: lines.append('  '*indent+'Otherwise:'); lines+=trace(n.orelse,indent+1)
        elif isinstance(n,(ast.For,ast.AsyncFor)):
            lines.append(lead+'Iterate '+expr(n.target)+' over '+expr(n.iter)+'.'); lines+=trace(n.body,indent+1)
            if n.orelse: lines.append('  '*indent+'If the loop finishes without break:'); lines+=trace(n.orelse,indent+1)
        elif isinstance(n,ast.While):
            lines.append(lead+'Repeat while '+expr(n.test)+'.'); lines+=trace(n.body,indent+1)
        elif isinstance(n,ast.Try):
            lines.append(lead+'Try the protected operations:'); lines+=trace(n.body,indent+1)
            for h in n.handlers: lines.append('  '*indent+'On '+(expr(h.type) if h.type else 'any exception')+':'); lines+=trace(h.body,indent+1)
            if n.orelse: lines.append('  '*indent+'On successful completion:'); lines+=trace(n.orelse,indent+1)
            if n.finalbody: lines.append('  '*indent+'Always clean up:'); lines+=trace(n.finalbody,indent+1)
        elif isinstance(n,(ast.With,ast.AsyncWith)):
            lines.append(lead+'Enter managed context '+', '.join(expr(x.context_expr) for x in n.items)+'.'); lines+=trace(n.body,indent+1)
        elif isinstance(n,ast.Return): lines.append(lead+'Return '+(expr(n.value) if n.value is not None else 'to the caller')+'.')
        elif isinstance(n,ast.Raise): lines.append(lead+'Raise '+(expr(n.exc) if n.exc else 'the current exception')+'.')
        elif isinstance(n,ast.Assert): lines.append(lead+'Require '+expr(n.test)+('. Failure detail: '+expr(n.msg) if n.msg else '')+'.')
        elif isinstance(n,(ast.Assign,ast.AnnAssign,ast.AugAssign)): lines.append(lead+'Compute/store '+expr(n)+'.')
        elif isinstance(n,ast.Expr): lines.append(lead+'Execute '+expr(n.value)+'.')
        elif isinstance(n,(ast.Import,ast.ImportFrom)): lines.append(lead+'Load dependency: '+expr(n)+'.')
        elif isinstance(n,ast.Break): lines.append(lead+'Leave this loop immediately.')
        elif isinstance(n,ast.Continue): lines.append(lead+'Skip to the next loop iteration.')
        else: lines.append(lead+expr(n)+'.')
    return lines

def source_file(r):
    p=Path(r['path'])
    return r['kind']=='text' and (p.suffix in ('.py','.js','.cjs','.cc','.h','.sh','.php','.html','.css','.yml','.ini','.jinja','.md','.txt','.conf','.service') or p.name in ('Dockerfile','Modelfile','makefile','interfaces','nginx_config','control','postinst','prerm','grfics-simulation','.gitignore','.gitattributes','LICENSE','tsconfig.json','adapter_config.json','tokenizer_config.json','policy.json','design_point.json','ChemicalPlant.json'))

def catalogue():
    out=[]
    for r in INV:
        p=r['path']; out+=['### '+p,'',role(r),'',f"Snapshot: {r['bytes']:,} bytes"+(f"; {r['lines']:,} lines." if 'lines' in r else '; binary asset.'),'']
        if 'parse_note' in r: out += [r['parse_note']+'. The structural index substitutes only the obsolete import name for parsing; the companion retains original source.','']
        if 'symbols' in r:
            s=(ROOT/p).read_text(encoding='utf-8-sig'); nodes=symbol_nodes(parse_python(s))
            imports=[x for x in r['imports'] if not x.startswith('from __future__')]
            if imports: out+=['Dependencies: '+ '; '.join(imports)+'.','']
            if r.get('doc') and p not in ROLES and not p.startswith(('experiments/','tests/')): out+=['Source documentation: '+compact(r['doc'],1000),'']
            for sym in r['symbols']:
                node=nodes[sym['name']]
                note=('Source docstring: '+compact(sym['doc'],420)) if sym['doc'] else ('Structural note: '+structural_summary(node))
                out += [f"- {sym['name']} (L{sym['line']}-{sym['end']}): {note}",'']
        elif 'schema' in r:
            schema=r['schema']; keys=', '.join(str(x) for x in schema) if isinstance(schema,list) else json.dumps(schema)
            out+=['Data structure: '+compact(keys,1200)+'.'+(f" Records: {r['rows']:,}." if 'rows' in r else ''),'']
        elif r['kind']=='text' and Path(p).suffix in ('.js','.cjs','.cc','.h','.css','.html','.php','.sh'):
            s=(ROOT/p).read_text(encoding='utf-8-sig')
            landmarks=[]
            for i,line in enumerate(s.splitlines(),1):
                if re.match(r'\s*(?:async\s+)?function\s+\w+|\s*(?:void|double|bool|Json::Value)\s+TE::|TE::TE\(|\s*//\s*-{3,}|\s*<section|\s*<script',line): landmarks.append(f'L{i}: {compact(line,130)}')
            if landmarks: out+=['Code landmarks: '+'; '.join(landmarks)+'.','']
    return '\n'.join(out)

def workspace_summary():
    groups=defaultdict(lambda:dict(files=0,bytes=0)); local=[]; tracked={r['path'] for r in INV}
    errors=[]
    for base,dirs,files in os.walk(ROOT,onerror=lambda e:errors.append(str(e))):
        rel=Path(base).relative_to(ROOT)
        if rel.parts[:2]==('artifacts','project-guide'): dirs[:]=[]; continue
        for f in files:
            p=Path(base)/f; name=p.relative_to(ROOT).as_posix()
            try: size=p.stat().st_size
            except OSError as e: errors.append(str(e)); continue
            top=name.split('/')[0] if '/' in name else '(root files)'
            groups[top]['files']+=1; groups[top]['bytes']+=size
            if name not in tracked and not name.startswith(('.git/','.tools/','.venv-train/')):
                local.append(dict(path=name,bytes=size))
    result=dict(groups=dict(groups),local_nontracked_files=local,scan_errors=errors,excluded='This generated project-guide directory. Dependency/Git file contents were not analyzed.')
    (HERE/'workspace-summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

styles=getSampleStyleSheet()
styles.add(ParagraphStyle('TitleGuide',fontName='Helvetica-Bold',fontSize=28,leading=34,textColor=colors.HexColor('#15334b'),spaceAfter=20))
styles.add(ParagraphStyle('TextGuide',fontName='Helvetica',fontSize=9.4,leading=14,spaceAfter=8,splitLongWords=True))
styles.add(ParagraphStyle('H1Guide',fontName='Helvetica-Bold',fontSize=17,leading=22,textColor=colors.HexColor('#15334b'),spaceBefore=15,spaceAfter=12,keepWithNext=True))
styles.add(ParagraphStyle('H2Guide',fontName='Helvetica-Bold',fontSize=11,leading=15,textColor=colors.HexColor('#176f83'),spaceBefore=10,spaceAfter=7,keepWithNext=True,splitLongWords=True))
styles.add(ParagraphStyle('CaptionGuide',fontName='Helvetica-Oblique',fontSize=8.5,leading=12,textColor=colors.HexColor('#4d626f'),spaceAfter=12))
styles.add(ParagraphStyle('CodeGuide',fontName='Courier',fontSize=6.5,leading=8.2,spaceAfter=4))

def clean(s):
    return s.replace('\u2011','-').replace('\u2192','->').replace('\u2264','<=').replace('\u2265','>=').replace('\u2014',' - ').replace('\u2013','-').replace('\u2019',"'").replace('\u2018',"'").replace('\u201c','"').replace('\u201d','"').replace('\u2026','...')

def par(s,sty='TextGuide'):
    return Paragraph(html.escape(clean(s)),styles[sty])

class Book(BaseDocTemplate):
    def __init__(self,path,title):
        super().__init__(str(path),pagesize=(595.28,841.89),leftMargin=44,rightMargin=44,topMargin=48,bottomMargin=44,title=title,author='SCRAM project documentation')
        self.book_title=title; self.heading_counter=0
        self.addPageTemplates(PageTemplate('main',frames=[Frame(44,44,507.28,749.89,id='body')],onPage=self.footer))
    def beforeDocument(self): self.heading_counter=0
    def footer(self,c,doc):
        c.saveState(); c.setStrokeColor(colors.HexColor('#d6e2e9')); c.line(44,34,551,34); c.setFont('Helvetica',8); c.setFillColor(colors.HexColor('#4d626f')); c.drawString(44,23,self.book_title); c.drawRightString(551,23,str(doc.page)); c.restoreState()
    def afterFlowable(self,f):
        if isinstance(f,Paragraph) and f.style.name in ('H1Guide','H2Guide'):
            level=0 if f.style.name=='H1Guide' else 1
            text=f.getPlainText(); key=f'heading-{self.heading_counter}'; self.heading_counter+=1
            self.canv.bookmarkPage(key); self.canv.addOutlineEntry(text,key,level,False)
            if level==0: self.notify('TOCEntry',(level,text,self.page,key))

def cover(title,subtitle):
    toc=TableOfContents(); toc.levelStyles=[ParagraphStyle('toc',fontName='Helvetica',fontSize=10,leading=15,leftIndent=0,spaceBefore=6)]
    return [Spacer(1,70),par('SCRAM','TitleGuide'),par(title,'TitleGuide'),par(subtitle),Spacer(1,18),par('Workspace snapshot: 1 November 2026'),par('Read the Study Guide first. Use the Source Companion for exact implementation, line numbers and structural walkthroughs.'),PageBreak(),par('Contents','H1Guide'),toc,PageBreak()]

def render_markdown(md):
    story=[]; paragraph=[]
    def flush():
        if paragraph: story.append(par(' '.join(paragraph))); paragraph.clear()
    for line in md.splitlines():
        if not line.strip(): flush(); continue
        if line.startswith('# '): flush(); continue
        if line.startswith('## '):
            flush(); story.append(PageBreak()); story.append(par(line[3:],'H1Guide'))
        elif line.startswith('### '): flush(); story.append(par(line[4:],'H2Guide'))
        elif line.startswith('@figure '):
            flush(); file,caption=line[8:].split(' | ',1); path=FIG/file
            w,h=ImageReader(str(path)).getSize(); width=495; height=width*h/w
            story.extend([Image(str(path),width=width,height=height),par(caption,'CaptionGuide')])
        elif line.startswith('- '): flush(); story.append(par(line[2:]))
        else: paragraph.append(line)
    flush(); return story

def wrapped_code(lines,width=111):
    out=[]
    for line in lines:
        line=clean(line).expandtabs(4)
        parts=textwrap.wrap(line,width=width,replace_whitespace=False,drop_whitespace=False,break_long_words=True,break_on_hyphens=False) or ['']
        out.append(parts[0]); out.extend('       > '+x for x in parts[1:])
    return out

def add_code(story,lines):
    lines=wrapped_code(lines)
    for pos in range(0,len(lines),65): story.append(Preformatted('\n'.join(lines[pos:pos+65]),styles['CodeGuide']))

def source_companion():
    text=['SCRAM SOURCE COMPANION','Snapshot: 1 November 2026','',
          'Exact numbered source plus generated structural walkthroughs. The Study Guide explains meaning and runtime relationships.',
          'AST notes identify computations, branches, calls and returns; they do not constitute execution or a formal correctness proof.',
          'Legacy async imports are normalized only for AST parsing, never in the reproduced source. Long PDF lines wrap with > continuation markers.','']
    story=cover('Source Companion','Complete numbered code/configuration, notebook cells and function-by-function structural notes. Large numeric datasets/model tables are catalogued in the Study Guide.')
    files=[]; syms=0
    for r in INV:
        if not source_file(r): continue
        p=r['path']; s=(ROOT/p).read_text(encoding='utf-8-sig'); files.append(p)
        text.extend(['','='*96,p,role(r),'='*96,''])
        if len(files)>1: story.append(PageBreak())
        story += [par(p,'H1Guide'),par(role(r))]
        if 'symbols' in r:
            tree=parse_python(s); nodes=symbol_nodes(tree)
            text+=['STRUCTURAL WALKTHROUGH (source-derived; original code follows)','']
            story.append(par('Structural walkthrough','H2Guide'))
            top=[n for n in tree.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
            topnotes=trace(top)
            if topnotes: text+=['Module-level execution:']+topnotes+['']; add_code(story,['Module-level execution:']+topnotes)
            for sym in r['symbols']:
                syms+=1; n=nodes[sym['name']]; heading=f"{sym['name']} | L{sym['line']}-{sym['end']}"
                note=ast.get_docstring(n) or structural_summary(n)
                text += [heading,note]
                story.append(par(heading,'H2Guide')); story.append(par(compact(note,1600)))
                if isinstance(n,ast.ClassDef):
                    notes=['Data/member declaration: '+expr(x,240) for x in n.body if isinstance(x,(ast.Assign,ast.AnnAssign))]
                else:
                    notes=['Parameters: '+ast.unparse(n.args)]+trace(n.body)
                text+=notes+['']; add_code(story,notes)
        elif Path(p).suffix in ('.js','.cjs','.cc','.h','.php','.sh','.css','.html'):
            text+=['Reading approach: follow declarations and setup first, then event handlers/loops, branch conditions and cleanup. The file role above and Study Guide describe the subsystem-specific semantics.','']
        numbered=[f'{i:5d} | {line}' for i,line in enumerate(s.splitlines(),1)]
        text+=['NUMBERED ORIGINAL SOURCE','']+numbered
        story.append(par('Numbered original source','H2Guide'))
        if p.endswith('UnityLoader.js'): story.append(par('Generated minified Unity loader: full bytes decoded as text are included in the TXT companion. PDF omits its long minified source. See the build manifest and wrapper for integration.'))
        else: add_code(story,numbered)
    p='training/scram_observer_finetune.ipynb'; nb=json.loads((ROOT/p).read_text(encoding='utf-8'))
    files.append(p); text+=['','='*96,p,'='*96]; story += [PageBreak(),par(p,'H1Guide'),par(ROLES[p])]
    for i,cell in enumerate(nb['cells'],1):
        src=''.join(cell.get('source',[])); title=f"Cell {i}: {cell['cell_type']}"
        text += [title,src,'']; story.append(par(title,'H2Guide'))
        add_code(story,src.splitlines())
    (HERE/'SCRAM_Source_Companion.txt').write_text('\n'.join(text),encoding='utf-8')
    Book(HERE/'SCRAM_Source_Companion.pdf','SCRAM - Source Companion').multiBuild(story)
    return files,syms

def main():
    print('Generating figures...',flush=True); figures()
    print('Scanning local workspace metadata...',flush=True); ws=workspace_summary()
    md=(HERE/'chapters.md').read_text(encoding='utf-8')+'\n\n'+catalogue()
    md+='\n\n## 20. Local workspace and document coverage\n\n'
    md+='The following counts describe on-disk folders, including ignored local material. Dependency/tool/Git internals are counted but not reproduced as SCRAM code. This generated documentation directory is excluded.\n\n'
    for key,value in sorted(ws['groups'].items()): md+=f"- {key}: {value['files']:,} files; {value['bytes']:,} bytes.\n\n"
    md+='The .venv-train directory is an installed training environment; .tools contains third-party llama.cpp tooling; .git contains version history/index/objects. observer_merged_v2 and ignored GGUF/adapter files are preserved local model artifacts. .local-only holds local release material. Generated logs/binaries under artifacts are outputs rather than additional application source. workspace-summary.json lists nontracked paths outside dependency/Git internals and records any scan errors.\n\n'
    commit=subprocess.check_output(['git','-c',f'safe.directory={ROOT.as_posix()}','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    md+=f"Source commit identity: {commit}. The inventory hashes describe actual working-tree bytes; a commit ID alone does not imply an unchanged working tree. No application source, model evidence or dataset was modified to generate these documents.\n"
    (HERE/'SCRAM_Study_Guide.md').write_text(md,encoding='utf-8')
    plain=re.sub(r'^@figure (.*?) \| (.*)$',r'[Figure: figures/\1] \2',md,flags=re.M)
    (HERE/'SCRAM_Study_Guide.txt').write_text(plain,encoding='utf-8')
    print('Rendering study PDF...',flush=True)
    Book(HERE/'SCRAM_Study_Guide.pdf','SCRAM - Study Guide').multiBuild(cover('Complete Project Study Guide','Architecture, control algorithms, safety, AI models, frontend, simulator, training, tests and every tracked file.')+render_markdown(md))
    print('Rendering source companion...',flush=True); files,syms=source_companion()
    coverage=dict(tracked_files=len(INV),catalogue_entries=len(INV),python_files=sum('symbols' in r for r in INV),python_symbols=syms,source_companion_files=files,commit=commit,workspace_scan_errors=ws['scan_errors'])
    coverage['pdfs']={}
    for name in ('SCRAM_Study_Guide.pdf','SCRAM_Source_Companion.pdf'):
        reader=PdfReader(HERE/name)
        coverage['pdfs'][name]=dict(pages=len(reader.pages),bytes=(HERE/name).stat().st_size,first_page_extractable=bool(reader.pages[0].extract_text()))
    (HERE/'coverage.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in coverage.items() if k!='source_companion_files'},indent=2),flush=True)

def validate():
    import pypdfium2 as pdfium
    from PIL import Image as PILImage, ImageOps, ImageDraw
    coverage=json.loads((HERE/'coverage.json').read_text())
    guide=(HERE/'SCRAM_Study_Guide.md').read_text(encoding='utf-8')
    source=(HERE/'SCRAM_Source_Companion.txt').read_text(encoding='utf-8')
    missing=[r['path'] for r in INV if '\n### '+r['path']+'\n' not in guide]
    changed=[r['path'] for r in INV if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['sha256']]
    missing_symbols=[r['path']+':'+s['name'] for r in INV for s in r.get('symbols',[]) if f"{s['name']} | L{s['line']}-{s['end']}" not in source]
    assert not missing and not changed and not missing_symbols,(missing,changed,missing_symbols)
    result=dict(tracked_paths_covered=298,python_symbols_covered=coverage['python_symbols'],original_files_unchanged=True,pdfs={})
    preview=HERE/'preview'; preview.mkdir(exist_ok=True)
    for file in ('SCRAM_Study_Guide.pdf','SCRAM_Source_Companion.pdf'):
        reader=PdfReader(HERE/file)
        texts=[p.extract_text() for p in reader.pages]
        blank=[i+1 for i,t in enumerate(texts) if len(t.strip())<40]
        # Footer-only pages are undesirable. All page text is checked, including TOC and the final page.
        result['pdfs'][file]=dict(pages=len(texts),short_pages=blank,outline_entries=len(reader.outline),extractable_characters=sum(map(len,texts)))
        pdf=pdfium.PdfDocument(str(HERE/file))
        if file.startswith('SCRAM_Study'):
            selected=[0,1,3,len(texts)-1]
            for needle in ('Main runtime and trust boundaries.','One physical process, two different','Actual static ceiling() mapping','Saved final-holdout class recall','Complete file-by-file catalogue'):
                selected.extend(i for i,t in enumerate(texts) if needle in t)
        else: selected=[0,1,9,50,150,len(texts)-1]
        selected=sorted(set(selected))
        thumbs=[]
        for i in selected:
            page=pdf[i]; bitmap=page.render(scale=1.25); img=bitmap.to_pil().convert('RGB')
            img.save(preview/f'{Path(file).stem}-page-{i+1}.png')
            thumb=ImageOps.contain(img,(360,510)); canvas=PILImage.new('RGB',(380,545),'#dce4e9'); canvas.paste(thumb,((380-thumb.width)//2,25))
            ImageDraw.Draw(canvas).text((12,7),f'{Path(file).stem} / {i+1}',fill='black'); thumbs.append(canvas)
            bitmap.close(); page.close()
        sheet=PILImage.new('RGB',(380*3,545*((len(thumbs)+2)//3)),'white')
        for i,thumb in enumerate(thumbs): sheet.paste(thumb,((i%3)*380,(i//3)*545))
        sheet.save(preview/f'{Path(file).stem}-contact-sheet.png'); pdf.close()
    (HERE/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    readme=f'''SCRAM PROJECT DOCUMENTATION - START HERE

1. SCRAM_Study_Guide.pdf ({coverage['pdfs']['SCRAM_Study_Guide.pdf']['pages']} pages)
   Read this first. Architecture, source-grounded explanations, diagrams,
   equations, worked examples, model/data lifecycle, APIs, tests and all 298 files.

2. SCRAM_Source_Companion.pdf ({coverage['pdfs']['SCRAM_Source_Companion.pdf']['pages']} pages)
   Searchable numbered code/configuration, notebook cells and structural
   walkthroughs for all {coverage['python_symbols']} Python symbols. Bookmarks identify files.
   Source docstrings are labeled; some preserve historical wording.

3. TXT versions contain the same study text and the complete source companion.
   Diagrams are in figures/. The generated minified Unity loader is included
   only in the TXT source companion; its PDF entry explains its integration.

4. inventory.json, coverage.json and validation.json record source coverage,
   byte hashes, PDF checks and the fact that original tracked files did not change.
   workspace-summary.json describes ignored/local dependencies and artifacts.

Scope: all tracked project files; full readable code, configuration and notebook
cells. Repeated datasets, model parameter arrays, binary weights, packet captures
and generated vendor assets are explained/catalogued rather than decoded as
handwritten code. Third-party environments/tool repositories are not reproduced.

Saved evaluation charts are historical evidence, not fresh benchmark runs.
No simulator, training job or deployment was started for this task.

The PDF outline panel and contents pages support navigation. Source line numbers
refer to the snapshot identified by inventory.json. Search a file path or a
function name in either PDF/TXT when you want to return to a specific component.
'''
    (HERE/'START_HERE.txt').write_text(readme,encoding='utf-8')
    members=['START_HERE.txt','SCRAM_Study_Guide.pdf','SCRAM_Study_Guide.txt','SCRAM_Study_Guide.md','SCRAM_Source_Companion.pdf','SCRAM_Source_Companion.txt','inventory.json','coverage.json','validation.json','workspace-summary.json']
    with zipfile.ZipFile(HERE/'SCRAM_Documentation.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in members: z.write(HERE/name,name)
        for p in FIG.glob('*.png'): z.write(p,'figures/'+p.name)
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':
    if '--validate' in sys.argv: validate()
    else:
        main()
        if '--finish' in sys.argv: validate()
