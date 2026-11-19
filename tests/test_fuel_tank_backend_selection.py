"""Check output-selection semantics in the real backend switch controller."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import subprocess

import pytest

from sat_sim.form_schema import capability_form_schema


PREFIX = "propulsion.fuel_tank."


def _switch(outputs):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the Workbench controller")
    source = Path("src/sat_sim/web/app.js").read_text()
    start = source.find("  function matchesOutputSelection(")
    if start == -1:
        start = source.index("  async function selectPhysicsBackend(")
    end = source.index("  async function selectCapability(", start)
    reference = capability_form_schema("component.fuel_tank.v1")
    native = capability_form_schema("component.fuel_tank.v1", physics_backend="basilisk_native")
    form = deepcopy(reference["default_form"])
    form["outputs"].update(outputs)
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const state = {selectedCapabilityId: 'component.fuel_tank.v1', formData: input.form,
  wizard: {validated: true, validatedForm: {}}, activeView: 'creator'};
const context = {state, clone: value => structuredClone(value),
  api: async () => ({form_schema: input.schema}), window: {},
  showBusy() {}, hideBusy() {}, clearAgentChanges() {}, updateTaskEditState() {},
  updateSingleRunGuide() {}, renderEffectSelect() {}, renderP9DPanel() {},
  renderGraph() {}, syncTaskSpecEditor() {}, renderForm() {}, renderWizardParameterControls() {},
  showNotice(message, kind) { this.notice = {message, kind}; }};
vm.createContext(context);
vm.runInContext(input.source, context);
(async () => {
  const ok = await context.selectPhysicsBackend('basilisk_native');
  process.stdout.write(JSON.stringify({ok, outputs: state.formData.outputs, notice: context.notice}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps({"source": source[start:end], "form": form, "schema": native}),
        text=True, capture_output=True, check=True,
    )
    actual = json.loads(result.stdout)
    assert actual["ok"], actual
    return actual["outputs"]


def test_switch_preserves_explicit_empty_plot_selection():
    assert _switch({"plots": []})["plots"] == []


@pytest.mark.parametrize("pattern", [
    PREFIX + "propellant_mass_*", PREFIX + "propellant_mass_k?",
    PREFIX + "propellant_mass_[a-z]g", PREFIX + "propellant_mass_[!a-j]g",
])
def test_switch_preserves_compatible_glob_and_prunes_pressure(pattern):
    outputs = _switch({"plots": [pattern, PREFIX + "pressure_*"]})
    assert outputs["plots"] == [pattern]


def test_switch_keeps_compatible_telemetry_glob():
    stream = {"stream_id": "mass", "sample_s": 1.0, "fields": [PREFIX + "propellant_mass_*"]}
    outputs = _switch({"telemetry_streams": [stream]})
    assert outputs["telemetry_streams"] == [stream]


def test_switch_does_not_replace_removed_pressure_plot_with_defaults():
    assert _switch({"plots": [PREFIX + "pressure_pa"]})["plots"] == []


@pytest.mark.parametrize("pattern", [PREFIX + "propellant_mass_k?", PREFIX + "propellant_*_kg"])
def test_switch_removes_telemetry_pattern_the_writer_cannot_export(pattern):
    stream = {"stream_id": "mass", "sample_s": 1.0, "fields": [pattern]}
    assert _switch({"telemetry_streams": [stream]})["telemetry_streams"] == []
