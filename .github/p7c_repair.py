"""One-shot, checksum-bound source repair. Removed before PR acceptance."""
from hashlib import sha1
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app = ROOT / 'src/sat_sim/web/app.js'
raw = app.read_bytes()
blob = sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
if blob != 'db68df910e3564819c343d21012ce9ffca8dc358':
    raise SystemExit('App baseline changed; refusing to patch: ' + blob)
s = raw.decode('utf-8')

def once(old: str, new: str) -> None:
    global s
    if s.count(old) != 1:
        raise SystemExit('Ambiguous or missing source anchor: ' + old[:100])
    s = s.replace(old, new, 1)

once('    activeTaskVersion: null,', '    activeTaskVersion: null,\n    activeTaskVersionTaskId: null,')
start = s.index('  const SHARED_CONTEXT_VIEWS')
end = s.index('  function switchView(name)', start)
s = s[:start] + (ROOT / '.github/p7c_context_block.js').read_text() + s[end:]
once('  function switchView(name) {\n    noteSharedContextTransition(name);', '  function switchView(name) {')
once('    state.wizardLeavingRealtimeApproved = false;\n    state.activeView = name;',
     '    noteSharedContextTransition(name);\n    state.wizardLeavingRealtimeApproved = false;\n    state.activeView = name;')
once('      state.activeTaskVersion = payload.task?.current_version ?? payload.task?.version ?? null;\n      updateSharedWorkbenchContext({ taskId, taskVersion: state.activeTaskVersion, bundleId: null });',
     '      state.activeTaskVersion = payload.task?.current_version ?? null;\n      state.activeTaskVersionTaskId = taskId;\n      updateSharedWorkbenchContext({ experimentId: null, goalId: null, baseTaskSpecId: null });')
once("    if (mode === 'update') state.activeTaskCenterId=payload.task?.task_id || state.activeTaskCenterId;", """    if (mode === 'update') {
      state.activeTaskCenterId = payload.task?.task_id || state.activeTaskCenterId;
      state.activeTaskVersion = payload.task?.current_version ?? null;
      state.activeTaskVersionTaskId = state.activeTaskCenterId;
      renderSharedWorkbenchContext();
    }""")
once('      updateSharedWorkbenchContext({ taskId, taskVersion: state.activeTaskVersion, runId, bundleId: runId });',
     '      state.activeRunId = runId;\n      updateSharedWorkbenchContext({ runId, bundleId: null, runTaskSpecId: null });')
once('      updateSharedWorkbenchContext({ runId, bundleId: runContext.bundle_id || runContext.run_id || runId, taskId: recordContext.task_id || runContext.task_id || state.sharedWorkbenchContext.taskId, taskVersion: recordContext.task_version ?? runContext.task_version ?? state.sharedWorkbenchContext.taskVersion });',
     """      if (state.activeRunId !== runId) return false;
      updateSharedWorkbenchContext({
        runId, bundleId: runContext.sealed === true ? runId : null,
        runTaskSpecId: runContext.task_spec?.task?.id || recordContext.task_id || null,
      });""")
once('    updateSharedWorkbenchContext({ experimentId: payload.experiment.experiment_id });',
     '    updateSharedWorkbenchContext({ experimentId: payload.experiment.experiment_id, goalId: null, baseTaskSpecId: request.base_task_spec?.task?.id || null });')
once('      updateSharedWorkbenchContext({ goalId: payload.goal.goal_id, taskId: payload.goal.base_task_spec?.task?.id || state.sharedWorkbenchContext.taskId });',
     '      updateSharedWorkbenchContext({ goalId: payload.goal.goal_id, experimentId: null, baseTaskSpecId: payload.goal.base_task_spec?.task?.id || null });')
once('    updateSharedWorkbenchContext({ goalId, taskId: baseSpec?.task?.id || state.sharedWorkbenchContext.taskId, taskVersion: payload.goal?.base_task_version ?? state.sharedWorkbenchContext.taskVersion });',
     '    updateSharedWorkbenchContext({ goalId, experimentId: null, baseTaskSpecId: baseSpec?.task?.id || null });')
once('    updateSharedWorkbenchContext({ experimentId, runId: rows.find((row) => row.run_id)?.run_id || state.sharedWorkbenchContext.runId });',
     '    updateSharedWorkbenchContext({ experimentId, goalId: null, baseTaskSpecId: detail.experiment.base_task_spec?.task?.id || null });')
app.write_text(s, encoding='utf-8')

# Run the behavioral guard from the normal browser CI, not just this repair job.
workflow = ROOT / '.github/workflows/single-run-creator-ux.yml'
yaml = workflow.read_text()
needle = '      - "tests/test_guided_task_workspace_browser.py"'
assert yaml.count(needle) == 1
yaml = yaml.replace(needle, needle + '\n      - "tests/test_p7c_shared_workbench_context.py"\n      - "tests/js/shared_context_vm.js"')
needle = 'tests/test_guided_task_workspace_browser.py tests/interactive/test_single_run_creator_ux.py'
assert yaml.count(needle) == 1
yaml = yaml.replace(needle, 'tests/test_guided_task_workspace_browser.py tests/test_p7c_shared_workbench_context.py tests/interactive/test_single_run_creator_ux.py')
workflow.write_text(yaml)

# Replace brittle string/format assertions with actual JS execution.
(ROOT / 'tests/test_p7c_shared_workbench_context.py').write_text('''from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_p7c_shared_context_is_mounted_in_all_specialist_workspaces():
    html = (ROOT / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert html.count("data-p7-context") == 4
    for view in ("experimentCenterView", "goalRunView", "runCenterView", "diagnosticView"):
        section = html.split('id="' + view + '"', 1)[1].split("</main>", 1)[0]
        assert "data-p7-context" in section, view


def test_p7c_shared_context_behaviour_against_production_javascript():
    result = subprocess.run(
        ["node", "tests/js/shared_context_vm.js"], cwd=ROOT,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
''', encoding='utf-8')
print('Patched app, context regression wrapper and existing browser CI only.')
