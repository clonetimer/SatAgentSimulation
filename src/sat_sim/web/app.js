(() => {
  'use strict';

  const state = {
    catalog: [],
    presentationCatalog: null,
    presentationObjects: [],
    selectedObjectId: null,
    selectedCapabilityId: null,
    schema: null,
    formData: null,
    taskSpec: null,
    planning: null,
    activeRunId: null,
    activeRun: null,
    metrics: {},
    metricMetadata: {},
    requestedQoi: [],
    events: null,
    fmea: null,
    traceability: null,
    plotManifest: null,
    telemetry: [],
    artifacts: [],
    dataset: null,
    selectedSeries: [],
    outputPlotQuery: '',
    outputPlotSelectedOnly: false,
    chartSeriesQuery: '',
    chartSelectedOnly: false,
    changedPaths: new Set(),
    agentBeforeForm: null,
    executionState: null,
    pollTimer: null,
    themeMedia: null,
    providers: [],
    selectedProviderId: "local-template",
    activeProviderId: "local-template",
    tasks: [],
    activeTaskCenterId: null,
    experimentBaseSpec: null,
    agentHistory: [],
    formErrors: new Map(),
    discoveredModels: [],
    activeView: "creator",
    scenarioTemplates: [],
    experiments: [],
    apiToken: sessionStorage.getItem('sat-sim-api-token') || '',
    authEnabled: false,
    logSearchTimer: null,
    activeRunDisplayName: null,
    showCompatibilityCapabilities: false,
    interactiveAvailable: false,
    interactiveSessions: [],
    interactiveSession: null,
    interactiveSocket: null,
    interactiveReconnectTimer: null,
    interactivePollTimer: null,
    interactiveFrames: {},
    interactiveGaps: {},
    interactiveEvents: [],
    interactiveAcks: [],
    interactiveCommandCatalog: [],
    runResultsExpanded: false,
    graphNodes: [],
    graphEdges: [],
    graphSelectedNodeId: null,
    graphSelectedEdgeId: null,
    graphPendingConnection: null,
    graphCode: '',
    graphCodeFilename: 'simulation.py',
    graphGeneratedSnapshot: '',
    graphHistory: [],
    graphHistoryIndex: -1,
    graphHistoryApplying: false,
    graphComposerMode: 'flow',
    assemblyCatalog: [],
    moduleLibrary: [],
    assemblyLibrarySearch: '',
    assemblyCompatibleOnly: true,
    assemblyContract: null,
    assemblyGraph: null,
    assemblySelectedNodeId: null,
    assemblySelectedEdgeId: null,
    assemblySelectedScopeId: null,
    assemblySelectedScopeProbeId: null,
    assemblySelection: [],
    assemblyClipboard: [],
    assemblySuppressCanvasClick: false,
    assemblyPendingConnection: null,
    assemblyDragModuleCapabilityId: null,
    assemblyCode: '',
    assemblyCodeFilename: 'assembly.py',
    assemblyGeneratedSnapshot: '',
    graphCodeDiff: null,
    assemblyCodeDiff: null,
    visualDiagnostics: null,
    activeVisualRunContext: null,
    assemblyComparison: null,
    assemblyComparisonRunning: false,
    visualRunOverlay: null,
    visualRunOverlaySnapshot: '',
    visualNodeSeriesField: {},
    visualOverlayTelemetry: [],
    visualTuningOptions: null,
    visualTuningMode: null,
    visualTuningSelections: [],
    visualTuningExperimentId: null,
    visualTuningResult: null,
    visualTuningRunning: false,
    visualTuningContext: null,
    visualTuningBestRunId: null,
  };

  const el = (id) => document.getElementById(id);
  const $all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  async function api(path, options = {}) {
    const authHeaders = state.apiToken ? { Authorization: `Bearer ${state.apiToken}` } : {};
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...authHeaders, ...(options.headers || {}) },
      ...options,
    });
    let payload = null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      payload = await response.json();
    } else {
      payload = await response.text();
    }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : payload;
      const message = typeof detail === 'string' ? detail : (detail?.message || detail?.reason_code || response.statusText);
      const error = new Error(message || `HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function codeDiffSummary(before, after) {
    if (!before || before === after) return before === after ? { changed: false, beforeLines: 0, afterLines: 0, startLine: 0, beforePreview: [], afterPreview: [] } : null;
    const left = String(before).split('\n');
    const right = String(after).split('\n');
    let prefix = 0;
    while (prefix < left.length && prefix < right.length && left[prefix] === right[prefix]) prefix += 1;
    let suffix = 0;
    while (suffix < left.length - prefix && suffix < right.length - prefix && left[left.length - 1 - suffix] === right[right.length - 1 - suffix]) suffix += 1;
    const leftChanged = left.slice(prefix, left.length - suffix);
    const rightChanged = right.slice(prefix, right.length - suffix);
    return {
      changed: true,
      beforeLines: leftChanged.length,
      afterLines: rightChanged.length,
      startLine: prefix + 1,
      beforePreview: leftChanged.slice(0, 4),
      afterPreview: rightChanged.slice(0, 4),
    };
  }

  function changedSnapshotPaths(previousSnapshot, currentSnapshot, limit = 8) {
    if (!previousSnapshot || !currentSnapshot || previousSnapshot === currentSnapshot) return [];
    let previous; let current;
    try { previous = JSON.parse(previousSnapshot); current = JSON.parse(currentSnapshot); } catch (_) { return ['graph / parameters']; }
    const paths = [];
    const walk = (left, right, path) => {
      if (paths.length >= limit) return;
      if (deepEqual(left, right)) return;
      const leftObject = left && typeof left === 'object';
      const rightObject = right && typeof right === 'object';
      if (!leftObject || !rightObject || Array.isArray(left) !== Array.isArray(right)) { paths.push(path || 'root'); return; }
      if (Array.isArray(left)) {
        const size = Math.max(left.length, right.length);
        for (let index = 0; index < size && paths.length < limit; index += 1) walk(left[index], right[index], `${path}[${index}]`);
        return;
      }
      const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort();
      keys.forEach((key) => { if (paths.length < limit) walk(left[key], right[key], path ? `${path}.${key}` : key); });
    };
    walk(previous, current, '');
    return paths;
  }

  function visualDiagnosticTargets(mode = null) {
    const diagnostics = state.visualDiagnostics;
    if (!diagnostics || (mode && diagnostics.mode !== mode)) return [];
    return diagnostics.targets || [];
  }

  function visualNodeDiagnostic(mode, nodeId) {
    return visualDiagnosticTargets(mode).find((item) => item.node_id === nodeId && ['node', 'port'].includes(item.kind)) || null;
  }

  function visualPortDiagnostic(mode, nodeId, portId) {
    return visualDiagnosticTargets(mode).find((item) => item.node_id === nodeId && item.port_id === portId) || null;
  }

  function visualEdgeDiagnostic(mode, edgeId) {
    return visualDiagnosticTargets(mode).find((item) => item.edge_id === edgeId) || null;
  }

  function clearVisualDiagnostics(mode = null) {
    if (!state.visualDiagnostics || (mode && state.visualDiagnostics.mode !== mode)) return;
    state.visualDiagnostics = null;
  }

  function applyVisualDiagnostics(diagnostics) {
    if (!diagnostics) return;
    state.visualDiagnostics = diagnostics;
    const first = (diagnostics.targets || [])[0] || null;
    if (diagnostics.mode === 'assembly') {
      if (first?.node_id) state.assemblySelectedNodeId = first.node_id;
      if (first?.edge_id) state.assemblySelectedEdgeId = first.edge_id;
      assemblyRender();
      assemblySetStatus(`${diagnostics.summary || '运行错误已映射到装配图'}${diagnostics.reason_code ? ` · ${diagnostics.reason_code}` : ''}`, 'error');
    } else if (diagnostics.mode === 'flow') {
      if (first?.node_id) state.graphSelectedNodeId = first.node_id;
      if (first?.edge_id) state.graphSelectedEdgeId = first.edge_id;
      renderGraph();
      graphSetStatus(`${diagnostics.summary || '运行错误已映射到流程图'}${diagnostics.reason_code ? ` · ${diagnostics.reason_code}` : ''}`, 'error');
    }
  }

  async function requestVisualDiagnostics(errorText, context = null, reasonCode = null) {
    const visual = context || state.activeVisualRunContext;
    if (!visual || !errorText) return null;
    try {
      const payload = await api('/visual-composer/diagnose', {
        method: 'POST',
        body: JSON.stringify({
          error_text: String(errorText),
          reason_code: reasonCode || null,
          task_spec: visual.taskSpec || null,
          visual_graph: visual.mode === 'flow' ? visual.graph : null,
          assembly_graph: visual.mode === 'assembly' ? visual.assemblyGraph : null,
        }),
      });
      const diagnostics = payload.diagnostics || null;
      if (diagnostics?.targets?.length) applyVisualDiagnostics(diagnostics);
      return diagnostics;
    } catch (_) { return null; }
  }

  function currentVisualSnapshot(mode) {
    return mode === 'assembly' ? assemblySnapshot() : graphFormSnapshot();
  }

  function visualOverlayForMode(mode) {
    const overlay = state.visualRunOverlay;
    if (!overlay || overlay.mode !== mode) return null;
    return overlay;
  }

  function formatOverlayValue(value, unit = null) {
    if (value == null) return '—';
    if (unit === 'bool') return Number(value) ? 'ON' : 'OFF';
    const numeric = typeof value === 'number' ? value : Number(value);
    let rendered = String(value);
    if (Number.isFinite(numeric)) {
      const magnitude = Math.abs(numeric);
      rendered = magnitude >= 10000 || (magnitude > 0 && magnitude < 0.001) ? numeric.toExponential(2) : Number(numeric.toPrecision(5)).toString();
    }
    return unit && unit !== 'none' ? `${rendered} ${unit}` : rendered;
  }

  function updateVisualOverlayControls() {
    const overlay = state.visualRunOverlay;
    [['graphResultOverlayBtn', 'flow'], ['assemblyResultOverlayBtn', 'assembly']].forEach(([id, mode]) => {
      const button = el(id); if (!button) return;
      const available = Boolean(overlay && overlay.mode === mode);
      button.disabled = !available;
      button.classList.remove('active');
      button.removeAttribute('aria-pressed');
      button.textContent = '查看运行结果';
      button.title = available ? `打开 Run ${overlay.run_id} 的独立结果界面 · ${overlay.numeric_series_count || 0} 条数值序列` : '完成一次图形运行后可打开独立结果界面';
    });
  }

  async function loadVisualRunOverlay(runId, context = null) {
    const visual = context || state.activeVisualRunContext;
    if (!visual || !runId) return null;
    try {
      const payload = await api('/visual-composer/run-overlay', {
        method: 'POST',
        body: JSON.stringify({
          run_id: runId,
          visual_graph: visual.mode === 'flow' ? visual.graph : null,
          assembly_graph: visual.mode === 'assembly' ? visual.assemblyGraph : null,
          telemetry_limit: 2000,
        }),
      });
      state.visualRunOverlay = payload.overlay || null;
      state.visualRunOverlaySnapshot = visual.snapshot || currentVisualSnapshot(visual.mode);
      state.visualNodeSeriesField = {};
      if (state.activeRunId === runId && state.telemetry?.length) state.visualOverlayTelemetry = state.telemetry;
      else { const telemetry = await api(`/runs/${encodeURIComponent(runId)}/telemetry?limit=2000`).catch(() => ({ rows: [] })); state.visualOverlayTelemetry = telemetry.rows || []; }
      updateVisualOverlayControls();
      if (visual.mode === 'assembly') assemblyRender();
      return state.visualRunOverlay;
    } catch (error) {
      updateVisualOverlayControls();
      return null;
    }
  }

  async function openVisualRunResults(mode) {
    const overlay = visualOverlayForMode(mode);
    if (!overlay?.run_id) return;
    if (state.activeRunId !== overlay.run_id) await loadRun(overlay.run_id, { quiet: true });
    switchResultTab('charts');
    renderChartControls();
    drawChart();
    state.runResultsExpanded = true;
    syncRunPanelControls();
    el('creatorRunPanel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function clearVisualRunOverlay() {
    state.visualRunOverlay = null;
    state.visualRunOverlaySnapshot = '';
    state.visualOverlayTelemetry = [];
    state.visualNodeSeriesField = {};
    updateVisualOverlayControls();
  }

  function parseVisualTuningValues(text) {
    return [...new Set(String(text || '').split(/[,，;；\s]+/).map((item) => Number(item)).filter((value) => Number.isFinite(value)))];
  }

  function visualTuningParameterByPath(path) {
    return (state.visualTuningOptions?.parameters || []).find((item) => item.path === path) || null;
  }

  function visualTuningObjectiveByMetric(metric) {
    return (state.visualTuningOptions?.objectives || []).find((item) => item.metric === metric) || null;
  }

  function visualTuningVariantCount() {
    if (!state.visualTuningSelections.length) return 0;
    return state.visualTuningSelections.reduce((count, item) => count * Math.max(0, parseVisualTuningValues(item.values).length), 1);
  }

  function renderVisualTuning() {
    const panel = el('visualTuningPanel');
    if (!panel) return;
    panel.classList.toggle('hidden', !state.visualTuningOptions);
    if (!state.visualTuningOptions) return;
    const objective = el('visualTuningObjective');
    const currentMetric = objective.value;
    objective.replaceChildren();
    (state.visualTuningOptions.objectives || []).forEach((item) => {
      const option = document.createElement('option'); option.value = item.metric; option.textContent = `${item.label || item.metric}${item.unit ? ` · ${item.unit}` : ''}`; objective.appendChild(option);
    });
    if ([...objective.options].some((item) => item.value === currentMetric)) objective.value = currentMetric;
    if (!objective.value && objective.options.length) objective.value = objective.options[0].value;
    const selectedObjective = visualTuningObjectiveByMetric(objective.value);
    if (!el('visualTuningDirection').dataset.userSet && selectedObjective?.suggested_direction) el('visualTuningDirection').value = selectedObjective.suggested_direction;

    const rows = el('visualTuningParameters'); rows.replaceChildren();
    state.visualTuningSelections.forEach((selection, index) => {
      const row = document.createElement('div'); row.className = 'visual-tuning-parameter-row';
      const parameterLabel = document.createElement('label'); const parameterTitle = document.createElement('span'); parameterTitle.textContent = `参数 ${index + 1}`;
      const select = document.createElement('select');
      (state.visualTuningOptions.parameters || []).forEach((item) => { const option = document.createElement('option'); option.value = item.path; option.textContent = `${item.label || item.path}${item.unit ? ` · ${item.unit}` : ''}`; select.appendChild(option); });
      select.value = selection.path;
      const param = visualTuningParameterByPath(selection.path);
      const meta = document.createElement('small'); meta.className = 'visual-tuning-parameter-meta'; meta.textContent = param ? `当前 ${param.current}${param.unit ? ` ${param.unit}` : ''}${param.minimum != null ? ` · min ${param.minimum}` : ''}${param.maximum != null ? ` · max ${param.maximum}` : ''}` : '';
      select.addEventListener('change', () => { const next = visualTuningParameterByPath(select.value); state.visualTuningSelections[index] = { path: select.value, values: (next?.suggested_values || []).join(', ') }; renderVisualTuning(); });
      parameterLabel.append(parameterTitle, select, meta);
      const valuesLabel = document.createElement('label'); const valuesTitle = document.createElement('span'); valuesTitle.textContent = '候选值（逗号分隔）';
      const input = document.createElement('input'); input.type = 'text'; input.value = selection.values; input.placeholder = '例如 0.08, 0.10, 0.12';
      input.addEventListener('input', () => { state.visualTuningSelections[index].values = input.value; const count = visualTuningVariantCount(); el('visualTuningLimit').textContent = `${state.visualTuningSelections.length}/3 参数 · ${count || 0}/12 组合`; });
      valuesLabel.append(valuesTitle, input);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-ghost'; remove.textContent = '移除'; remove.disabled = state.visualTuningSelections.length <= 1;
      remove.addEventListener('click', () => { state.visualTuningSelections.splice(index, 1); renderVisualTuning(); });
      row.append(parameterLabel, valuesLabel, remove); rows.appendChild(row);
    });
    const count = visualTuningVariantCount();
    el('visualTuningLimit').textContent = `${state.visualTuningSelections.length}/3 参数 · ${count || 0}/12 组合`;
    el('visualTuningAddParameterBtn').disabled = state.visualTuningSelections.length >= 3 || !(state.visualTuningOptions.parameters || []).length;
    el('visualTuningStartBtn').disabled = state.visualTuningRunning;
    el('visualTuningStartBtn').textContent = state.visualTuningRunning ? '扫描运行中…' : '开始扫描';
    const status = el('visualTuningStatus');
    const result = state.visualTuningResult;
    if (state.visualTuningRunning) status.textContent = result?.message || `正在运行 ${result?.completed || 0}/${result?.variantCount || count} 个候选…`;
    else if (result?.best) status.textContent = `扫描完成 · 最优 ${result.objectiveMetric} = ${formatComparisonValue(result.best.objective_value)} · Run ${result.best.run_id}`;
    else status.textContent = result?.message || '尚未开始。';
    const applyBest = el('visualTuningApplyBestBtn');
    applyBest.classList.toggle('hidden', !result?.best);
    applyBest.disabled = Boolean(result?.bestApplied);
    applyBest.textContent = result?.bestApplied ? '已应用最优参数' : '应用最优参数';
    const results = el('visualTuningResults'); results.replaceChildren();
    if (result?.ranked?.length) {
      const header = document.createElement('div'); header.className = 'visual-tuning-result-row header'; header.innerHTML = '<span>排名</span><span>参数</span><span>目标值</span><span>Run</span>'; results.appendChild(header);
      result.ranked.slice(0, 12).forEach((item) => {
        const row = document.createElement('div'); row.className = `visual-tuning-result-row${item.is_best ? ' best' : ''}`;
        const rank = document.createElement('span'); rank.textContent = `#${item.rank}`;
        const params = document.createElement('code'); params.textContent = Object.entries(item.parameters || {}).map(([path, value]) => `${path.split('.').pop()}=${value}`).join(' · ');
        const value = document.createElement('strong'); value.textContent = formatComparisonValue(item.objective_value);
        const run = document.createElement('span'); run.textContent = item.run_id || '—';
        row.append(rank, params, value, run); results.appendChild(row);
      });
    }
  }

  async function prepareVisualTuning(mode) {
    if (mode === 'assembly') {
      const ok = await assemblyCompile({ announce: false }); if (!ok) throw new Error('当前装配未通过编译，无法开始调参。');
      return { mode, taskSpec: clone(state.taskSpec), assemblyGraph: clone(state.assemblyGraph), snapshot: assemblySnapshot() };
    }
    const ok = await graphCompile({ requireCode: true, requireRun: true, announce: false }); if (!ok) throw new Error('当前流程未通过编译，无法开始调参。');
    return { mode, taskSpec: clone(state.taskSpec), graph: graphSerialize(), snapshot: graphFormSnapshot() };
  }

  async function openVisualTuning(mode) {
    const context = await prepareVisualTuning(mode);
    const payload = await api('/visual-composer/tuning-options', { method: 'POST', body: JSON.stringify({ task_spec: context.taskSpec }) });
    const tuning = payload.tuning || {};
    if (!(tuning.parameters || []).length) throw new Error('当前 Capability 没有可用于快速调参的注册数值参数。');
    if (!(tuning.objectives || []).length) throw new Error('当前 Capability 没有可用于排序的注册 QoI。');
    state.visualTuningOptions = tuning;
    state.visualTuningMode = mode;
    state.visualTuningContext = context;
    state.visualTuningExperimentId = null;
    state.visualTuningResult = null;
    state.visualTuningBestRunId = null;
    const first = tuning.parameters[0];
    state.visualTuningSelections = [{ path: first.path, values: (first.suggested_values || []).join(', ') }];
    el('visualTuningDirection').dataset.userSet = '';
    renderVisualTuning();
    el('visualTuningPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function closeVisualTuning() {
    if (state.visualTuningRunning) return;
    state.visualTuningOptions = null; state.visualTuningSelections = []; state.visualTuningMode = null; renderVisualTuning();
  }

  async function waitForVisualTuningExperiment(experimentId) {
    for (let attempt = 0; attempt < 1600; attempt += 1) {
      const payload = await api(`/experiments/${encodeURIComponent(experimentId)}`);
      const members = payload.members || [];
      const completed = members.filter((item) => ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(item.state)).length;
      state.visualTuningResult = { ...(state.visualTuningResult || {}), variantCount: members.length, completed, message: `正在运行 ${completed}/${members.length} 个候选…` };
      renderVisualTuning();
      if (members.length && completed === members.length) return payload;
      await waitMs(750);
    }
    throw new Error('快速调参等待运行完成超限。');
  }

  async function startVisualTuning() {
    if (state.visualTuningRunning || !state.visualTuningOptions) return false;
    const mode = state.visualTuningMode || state.graphComposerMode;
    const context = await prepareVisualTuning(mode);
    const parameters = {};
    state.visualTuningSelections.forEach((item) => { parameters[item.path] = parseVisualTuningValues(item.values); });
    const objectiveMetric = el('visualTuningObjective').value;
    const direction = el('visualTuningDirection').value;
    const planPayload = await api('/visual-composer/tuning-plan', { method: 'POST', body: JSON.stringify({ task_spec: context.taskSpec, parameters, objective_metric: objectiveMetric, direction }) });
    const plan = planPayload.plan;
    state.visualTuningRunning = true;
    state.visualTuningContext = context;
    state.visualTuningResult = { variantCount: plan.variant_count, completed: 0, objectiveMetric, direction, message: `正在创建 ${plan.variant_count} 个受约束候选…` };
    renderVisualTuning();
    try {
      const created = await api('/experiments', { method: 'POST', body: JSON.stringify({ name: `Visual tuning · ${context.taskSpec?.task?.name || context.taskSpec?.task?.id || 'simulation'}`, base_task_spec: context.taskSpec, sweep: plan.sweep, assertions: [], experiment_type: 'sweep' }) });
      const experimentId = created.experiment?.experiment_id; if (!experimentId) throw new Error('调参实验未返回 experiment_id');
      state.visualTuningExperimentId = experimentId;
      await api(`/experiments/${encodeURIComponent(experimentId)}/launch`, { method: 'POST', body: JSON.stringify({ max_attempts: 2, hard_timeout: true }) });
      await waitForVisualTuningExperiment(experimentId);
      const ranking = await api('/visual-composer/tuning-rank', { method: 'POST', body: JSON.stringify({ experiment_id: experimentId, objective_metric: objectiveMetric, direction }) });
      state.visualTuningResult = { ...ranking, objectiveMetric, direction, bestApplied: false, message: ranking.best ? '扫描完成，最优候选已叠加；应用参数后即可作为当前模型继续生成/运行。' : '扫描完成，但没有可排序的成功 Run。' };
      state.visualTuningBestRunId = ranking.best?.run_id || null;
      if (ranking.best?.run_id) {
        const bestContext = { ...context, runId: ranking.best.run_id };
        state.activeRunId = ranking.best.run_id; state.activeRunDisplayName = `快速调参最优 · ${context.taskSpec?.task?.name || 'simulation'}`; state.activeVisualRunContext = bestContext;
        await loadRun(ranking.best.run_id, { quiet: true }).catch(() => null);
        await loadVisualRunOverlay(ranking.best.run_id, bestContext);
      }
      renderVisualTuning();
      return true;
    } finally {
      state.visualTuningRunning = false; renderVisualTuning();
    }
  }

  function applyVisualTuningBest() {
    const best = state.visualTuningResult?.best;
    if (!best || !state.formData) return;
    Object.entries(best.parameters || {}).forEach(([path, value]) => setPath(state.formData, path, value));
    renderForm();
    if (state.visualTuningMode === 'assembly') { assemblyInvalidateCompiledState(); state.visualRunOverlaySnapshot = assemblySnapshot(); assemblyRender(); assemblySetStatus('已把最优参数写回当前装配；代码/TaskSpec 已标记需要重新编译。最佳 Run 仍可在结果界面或显式观察器中查看。', 'success'); }
    else { graphInvalidateCompiledState(); graphHistoryCommit(); state.visualRunOverlaySnapshot = graphFormSnapshot(); renderGraph(); graphSetStatus('已把最优参数写回当前流程；代码/TaskSpec 已标记需要重新编译。最佳 Run 请在独立结果界面查看。', 'success'); }
    state.visualTuningResult = { ...(state.visualTuningResult || {}), bestApplied: true };
    renderVisualTuning();
  }

  function drawVisualMiniSeries(canvas, field) {
    if (!canvas || !field) return;
    const rows = state.visualOverlayTelemetry?.length ? state.visualOverlayTelemetry : state.telemetry;
    const points = rows.map((row, index) => ({ t: Number(row.time_s ?? row['spacecraft.time_s'] ?? row.t_s ?? index), y: Number(row[field]) }))
      .filter((item) => Number.isFinite(item.t) && Number.isFinite(item.y));
    const rect = canvas.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1;
    const width = Math.max(250, rect.width || 320); const height = Math.max(105, rect.height || 120);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    const theme = getComputedStyle(document.documentElement);
    const color = (name, fallback) => theme.getPropertyValue(name).trim() || fallback;
    ctx.fillStyle = color('--panel', '#0c1720'); ctx.fillRect(0, 0, width, height);
    if (!points.length) { ctx.fillStyle = color('--muted', '#7890a1'); ctx.font = '10px system-ui'; ctx.textAlign = 'center'; ctx.fillText('该节点没有可绘制的数值序列', width / 2, height / 2); return; }
    const minX = Math.min(...points.map((item) => item.t)); const maxX = Math.max(...points.map((item) => item.t));
    let minY = Math.min(...points.map((item) => item.y)); let maxY = Math.max(...points.map((item) => item.y));
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const pad = { left: 42, right: 8, top: 8, bottom: 22 }; const plotW = width - pad.left - pad.right; const plotH = height - pad.top - pad.bottom;
    const x = (value) => pad.left + ((value - minX) / (maxX - minX || 1)) * plotW;
    const y = (value) => pad.top + (1 - (value - minY) / (maxY - minY || 1)) * plotH;
    ctx.strokeStyle = color('--line', 'rgba(170,199,221,.16)'); ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i += 1) { const yy = pad.top + (i / 3) * plotH; ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke(); }
    ctx.strokeStyle = color('--accent-2', '#64a9ff'); ctx.lineWidth = 1.8; ctx.beginPath();
    points.forEach((item, index) => { if (index === 0) ctx.moveTo(x(item.t), y(item.y)); else ctx.lineTo(x(item.t), y(item.y)); }); ctx.stroke();
    ctx.fillStyle = color('--muted', '#7890a1'); ctx.font = '9px system-ui'; ctx.textAlign = 'right'; ctx.fillText(formatOverlayValue(maxY), pad.left - 5, pad.top + 7); ctx.fillText(formatOverlayValue(minY), pad.left - 5, height - pad.bottom);
    ctx.textAlign = 'left'; ctx.fillText(`${formatOverlayValue(minX)}s`, pad.left, height - 7); ctx.textAlign = 'right'; ctx.fillText(`${formatOverlayValue(maxX)}s`, width - pad.right, height - 7);
  }

  const THEME_STORAGE_KEY = 'sat-sim-theme';
  const FONT_SCALE_STORAGE_KEY = 'sat-sim-font-scale';
  const RUN_RESULTS_EXPANDED_STORAGE_KEY = 'sat-sim-run-results-expanded';
  const FONT_SCALE_OPTIONS = ['90', '100', '115', '130'];

  function resolvedTheme(preference) {
    if (preference === 'light' || preference === 'dark') return preference;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(preference, persist = true) {
    const safePreference = ['system', 'light', 'dark'].includes(preference) ? preference : 'system';
    document.documentElement.dataset.themePreference = safePreference;
    document.documentElement.dataset.theme = resolvedTheme(safePreference);
    if (persist) localStorage.setItem(THEME_STORAGE_KEY, safePreference);
    const select = el('themeSelect');
    if (select) select.value = safePreference;
    requestAnimationFrame(drawChart);
  }

  function initTheme() {
    const preference = localStorage.getItem(THEME_STORAGE_KEY) || document.documentElement.dataset.themePreference || 'system';
    applyTheme(preference, false);
    state.themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    state.themeMedia.addEventListener('change', () => {
      if ((localStorage.getItem(THEME_STORAGE_KEY) || 'system') === 'system') applyTheme('system', false);
    });
  }

  function applyFontScale(value, persist = true) {
    const safeValue = FONT_SCALE_OPTIONS.includes(String(value)) ? String(value) : '100';
    document.documentElement.dataset.fontScale = safeValue;
    document.documentElement.style.setProperty('--ui-scale', String(Number(safeValue) / 100));
    if (persist) localStorage.setItem(FONT_SCALE_STORAGE_KEY, safeValue);
    const select = el('fontScaleSelect');
    if (select) select.value = safeValue;
    requestAnimationFrame(() => {
      drawChart();
      if (state.activeView === 'interactive') renderInteractiveTelemetry();
    });
  }

  function runPanelIsFullscreen() {
    const panel = el('creatorRunPanel');
    return document.fullscreenElement === panel || Boolean(panel?.classList.contains('run-panel-fullscreen-fallback'));
  }

  function syncRunPanelControls() {
    const expanded = Boolean(state.runResultsExpanded);
    const fullscreen = runPanelIsFullscreen();
    const expandButton = el('expandRunPanelBtn');
    const fullscreenButton = el('fullscreenRunPanelBtn');
    el('creatorView')?.classList.toggle('run-results-expanded', expanded);
    if (expandButton) {
      expandButton.setAttribute('aria-pressed', String(expanded));
      expandButton.title = expanded ? '恢复结果栏宽度' : '扩展结果栏';
      expandButton.setAttribute('aria-label', expandButton.title);
    }
    if (fullscreenButton) {
      fullscreenButton.setAttribute('aria-pressed', String(fullscreen));
      fullscreenButton.textContent = fullscreen ? '⤢' : '⛶';
      fullscreenButton.title = fullscreen ? '退出全屏结果' : '全屏显示运行结果';
      fullscreenButton.setAttribute('aria-label', fullscreenButton.title);
    }
    requestAnimationFrame(drawChart);
  }

  function toggleRunResultsExpanded() {
    state.runResultsExpanded = !state.runResultsExpanded;
    localStorage.setItem(RUN_RESULTS_EXPANDED_STORAGE_KEY, String(state.runResultsExpanded));
    syncRunPanelControls();
  }

  async function toggleRunPanelFullscreen() {
    const panel = el('creatorRunPanel');
    if (!panel) return;
    if (document.fullscreenElement === panel) {
      await document.exitFullscreen();
      return;
    }
    if (panel.classList.contains('run-panel-fullscreen-fallback')) {
      panel.classList.remove('run-panel-fullscreen-fallback');
      document.body.classList.remove('run-panel-fallback-open');
      syncRunPanelControls();
      return;
    }
    if (typeof panel.requestFullscreen === 'function') {
      try {
        await panel.requestFullscreen();
        return;
      } catch (_error) {
        // Embedded browsers may deny the native API; use the in-page fallback.
      }
    }
    panel.classList.add('run-panel-fullscreen-fallback');
    document.body.classList.add('run-panel-fallback-open');
    syncRunPanelControls();
  }

  function initDisplayPreferences() {
    applyFontScale(localStorage.getItem(FONT_SCALE_STORAGE_KEY) || '100', false);
    state.runResultsExpanded = localStorage.getItem(RUN_RESULTS_EXPANDED_STORAGE_KEY) === 'true';
    syncRunPanelControls();
  }

  function deepEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function getPath(obj, path) {
    return path.split('.').reduce((value, key) => (value == null ? undefined : value[key]), obj);
  }

  function setPath(obj, path, value) {
    const parts = path.split('.');
    let cursor = obj;
    parts.forEach((part, index) => {
      if (index === parts.length - 1) cursor[part] = value;
      else {
        if (!cursor[part] || typeof cursor[part] !== 'object') cursor[part] = {};
        cursor = cursor[part];
      }
    });
  }

  function deletePath(obj, path) {
    const parts = path.split('.');
    let cursor = obj;
    for (let index = 0; index < parts.length - 1; index += 1) {
      cursor = cursor?.[parts[index]];
      if (!cursor || typeof cursor !== 'object') return;
    }
    if (cursor && typeof cursor === 'object') delete cursor[parts[parts.length - 1]];
  }

  const GRAPH_SCHEMA_VERSION = 'sat-sim.visual-graph.v1';
  const GRAPH_PROJECT_SCHEMA_VERSION = 'sat-sim.visual-composer.project.v1';
  const GRAPH_DRAFT_STORAGE_KEY = 'sat-sim-visual-composer-draft-v1';
  const GRAPH_HISTORY_LIMIT = 60;
  const GRAPH_FLOW_ORDER = ['model', 'config', 'effects', 'outputs', 'code', 'run'];
  const GRAPH_PORT_TYPE_LABELS = {
    capability_ref: 'Capability',
    task_draft: 'Task Draft',
    task_spec: 'TaskSpec',
    python_script: 'Python',
  };
  const GRAPH_BLOCK_META = {
    model: {
      title: '执行能力', icon: '◇', hint: '已注册 Capability',
      inputs: [], outputs: [{ id: 'capability', type: 'capability_ref', label: 'Capability' }],
    },
    config: {
      title: '参数配置', icon: '⚙', hint: '时间与模型参数',
      inputs: [{ id: 'capability', type: 'capability_ref', label: 'Capability', required: true }],
      outputs: [{ id: 'task', type: 'task_draft', label: 'Task Draft' }],
    },
    effects: {
      title: '事件注入', icon: '⚡', hint: '故障 / 退化 / 约束',
      inputs: [{ id: 'task', type: 'task_draft', label: 'Task Draft', required: true }],
      outputs: [{ id: 'task', type: 'task_draft', label: 'Task Draft' }],
    },
    outputs: {
      title: '输出选择', icon: '◫', hint: 'QoI / 曲线 / 遥测',
      inputs: [{ id: 'task', type: 'task_draft', label: 'Task Draft', required: true }],
      outputs: [{ id: 'spec', type: 'task_spec', label: 'TaskSpec' }],
    },
    code: {
      title: 'Python 代码', icon: '</>', hint: '确定性脚本导出',
      inputs: [{ id: 'spec', type: 'task_spec', label: 'TaskSpec', required: true }],
      outputs: [{ id: 'script', type: 'python_script', label: 'Python' }],
    },
    run: {
      title: '运行器', icon: '▶', hint: 'Run Bundle',
      inputs: [
        { id: 'spec', type: 'task_spec', label: 'TaskSpec', required: true },
        { id: 'script', type: 'python_script', label: 'Python', required: false },
      ],
      outputs: [],
    },
  };

  function graphTopologySnapshot() {
    return {
      nodes: state.graphNodes.map((node) => ({ id: node.id, type: node.type, capabilityId: node.capabilityId || null })),
      edges: state.graphEdges.map((edge) => ({
        source: edge.source,
        sourcePort: edge.sourcePort,
        target: edge.target,
        targetPort: edge.targetPort,
      })),
    };
  }

  function graphFormSnapshot() {
    return JSON.stringify({ form: state.formData || {}, topology: graphTopologySnapshot() });
  }

  function graphEditorSnapshot() {
    return {
      nodes: clone(state.graphNodes),
      edges: clone(state.graphEdges),
    };
  }

  function graphHistorySignature(snapshot) {
    return JSON.stringify(snapshot || graphEditorSnapshot());
  }

  function graphUpdateHistoryControls() {
    const undo = el('graphUndoBtn');
    const redo = el('graphRedoBtn');
    if (undo) undo.disabled = state.graphHistoryIndex <= 0;
    if (redo) redo.disabled = state.graphHistoryIndex < 0 || state.graphHistoryIndex >= state.graphHistory.length - 1;
  }

  function graphHistoryReset() {
    state.graphHistory = [graphEditorSnapshot()];
    state.graphHistoryIndex = 0;
    graphUpdateHistoryControls();
  }

  function graphHistoryCommit() {
    if (state.graphHistoryApplying) return;
    const snapshot = graphEditorSnapshot();
    const current = state.graphHistory[state.graphHistoryIndex];
    if (current && graphHistorySignature(current) === graphHistorySignature(snapshot)) {
      graphUpdateHistoryControls();
      return;
    }
    state.graphHistory = state.graphHistory.slice(0, state.graphHistoryIndex + 1);
    state.graphHistory.push(snapshot);
    if (state.graphHistory.length > GRAPH_HISTORY_LIMIT) state.graphHistory.shift();
    state.graphHistoryIndex = state.graphHistory.length - 1;
    graphUpdateHistoryControls();
  }

  function graphInvalidateCompiledState() {
    state.taskSpec = null;
    state.planning = null;
    state.graphPendingConnection = null;
    clearVisualDiagnostics('flow');
  }

  function graphApplyHistorySnapshot(snapshot, message) {
    if (!snapshot) return;
    state.graphHistoryApplying = true;
    try {
      state.graphNodes = clone(snapshot.nodes || []);
      state.graphEdges = clone(snapshot.edges || []);
      state.graphSelectedNodeId = null;
      state.graphSelectedEdgeId = null;
      graphInvalidateCompiledState();
      renderGraph();
      graphUpdateHistoryControls();
      if (message) graphSetStatus(message, 'warning');
    } finally {
      state.graphHistoryApplying = false;
    }
  }

  function graphUndo() {
    if (state.graphHistoryIndex <= 0) return;
    state.graphHistoryIndex -= 1;
    graphApplyHistorySnapshot(state.graphHistory[state.graphHistoryIndex], '已撤销上一步图编辑。');
  }

  function graphRedo() {
    if (state.graphHistoryIndex < 0 || state.graphHistoryIndex >= state.graphHistory.length - 1) return;
    state.graphHistoryIndex += 1;
    graphApplyHistorySnapshot(state.graphHistory[state.graphHistoryIndex], '已重做图编辑。');
  }

  function graphProjectBundle() {
    return {
      schema_version: GRAPH_PROJECT_SCHEMA_VERSION,
      capability_id: state.selectedCapabilityId || null,
      graph: graphSerialize(),
      form_data: clone(state.formData || {}),
    };
  }

  function graphValidateProjectBundle(bundle) {
    if (!bundle || typeof bundle !== 'object' || Array.isArray(bundle)) throw new Error('工程文件必须是 JSON 对象');
    if (bundle.schema_version !== GRAPH_PROJECT_SCHEMA_VERSION) throw new Error(`不支持的工程版本：${bundle.schema_version || '未声明'}`);
    const capabilityId = String(bundle.capability_id || '').trim();
    if (!capabilityId) throw new Error('工程文件缺少 capability_id');
    if (!bundle.graph || typeof bundle.graph !== 'object' || Array.isArray(bundle.graph)) throw new Error('工程文件缺少 graph 对象');
    if (bundle.graph.schema_version !== GRAPH_SCHEMA_VERSION) throw new Error(`不支持的图版本：${bundle.graph.schema_version || '未声明'}`);
    if (!Array.isArray(bundle.graph.nodes) || !Array.isArray(bundle.graph.edges)) throw new Error('graph.nodes 和 graph.edges 必须是数组');
    if (bundle.graph.nodes.length > 32 || bundle.graph.edges.length > 96) throw new Error('工程图规模超出当前编辑器限制');
    if (!bundle.form_data || typeof bundle.form_data !== 'object' || Array.isArray(bundle.form_data)) throw new Error('工程文件缺少 form_data 对象');
    return capabilityId;
  }

  function graphSafeCoordinate(value, fallback, max) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return fallback;
    return Math.max(0, Math.min(max, numeric));
  }

  function graphLoadTopology(graph, capabilityId) {
    state.graphNodes = graph.nodes.map((raw, index) => ({
      id: String(raw?.id || `imported-${index}`),
      type: String(raw?.type || ''),
      x: graphSafeCoordinate(raw?.x, 28 + index * 28, 1100),
      y: graphSafeCoordinate(raw?.y, 80 + index * 24, 520),
      capabilityId: String(raw?.type || '') === 'model' ? capabilityId : null,
    }));
    state.graphEdges = graph.edges.map((raw, index) => ({
      id: String(raw?.id || `imported-edge-${index}`),
      source: String(raw?.source || ''),
      sourcePort: String(raw?.sourcePort || raw?.source_port || ''),
      target: String(raw?.target || ''),
      targetPort: String(raw?.targetPort || raw?.target_port || ''),
    }));
    state.graphSelectedNodeId = state.graphNodes.find((node) => node.type === 'model')?.id || null;
    state.graphSelectedEdgeId = null;
    state.graphPendingConnection = null;
    state.graphCode = '';
    state.graphCodeFilename = 'simulation.py';
    state.graphGeneratedSnapshot = '';
    graphInvalidateCompiledState();
  }

  async function graphLoadProjectBundle(bundle, sourceLabel = '工程') {
    const capabilityId = graphValidateProjectBundle(bundle);
    await selectCapability(capabilityId);
    if (state.selectedCapabilityId !== capabilityId || !state.schema) throw new Error(`无法加载 Capability：${capabilityId}`);
    state.formData = clone(bundle.form_data);
    state.changedPaths = new Set();
    graphLoadTopology(bundle.graph, capabilityId);
    renderForm();
    renderEffectSelect();
    renderEvents();
    renderOutputs();
    syncTaskSpecEditor();
    graphHistoryReset();
    renderGraphPalette();
    renderGraph();
    const structural = graphValidateStructure({ requireCode: true, requireRun: true });
    if (!structural.ok) {
      graphSetStatus(`${sourceLabel}已载入，但图结构需修复：${structural.errors.join('；')}`, 'warning');
      return false;
    }
    try {
      const compiled = await graphCompile({ requireCode: true, requireRun: true, announce: false });
      if (!compiled) return false;
    } catch (error) {
      graphSetStatus(`${sourceLabel}已载入，但后端一致性校验失败：${error.message}`, 'warning');
      return false;
    }
    graphSetStatus(`${sourceLabel}已载入并通过后端端口、拓扑与 TaskSpec 一致性校验：${capabilityId}。`, 'success');
    return true;
  }

  function graphSaveDraft() {
    if (!state.selectedCapabilityId || !state.formData) throw new Error('请先选择可执行 Capability');
    localStorage.setItem(GRAPH_DRAFT_STORAGE_KEY, JSON.stringify(graphProjectBundle()));
    graphSetStatus('图形工程草稿已保存在当前浏览器。', 'success');
  }

  async function graphLoadDraft() {
    const raw = localStorage.getItem(GRAPH_DRAFT_STORAGE_KEY);
    if (!raw) throw new Error('当前浏览器没有已保存的图形工程草稿');
    await graphLoadProjectBundle(JSON.parse(raw), '浏览器草稿');
  }

  function graphExportProject() {
    if (!state.selectedCapabilityId || !state.formData) throw new Error('请先选择可执行 Capability');
    const bundle = graphProjectBundle();
    const safeCapability = String(state.selectedCapabilityId).replace(/[^A-Za-z0-9_.-]/g, '_');
    const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${safeCapability || 'simulation'}.satgraph.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    graphSetStatus(`已导出工程文件 ${anchor.download}；其中不包含任意 Python 源码。`, 'success');
  }

  async function graphImportProjectFile(file) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) throw new Error('工程文件过大，当前限制为 2 MiB');
    const raw = await file.text();
    const bundle = JSON.parse(raw);
    await graphLoadProjectBundle(bundle, `工程文件 ${file.name}`);
  }

  function graphCodeIsFresh() {
    return Boolean(state.graphCode && state.graphGeneratedSnapshot === graphFormSnapshot());
  }

  function graphSetStatus(message, type = '') {
    const node = el('graphStatus');
    if (!node) return;
    node.textContent = message;
    node.className = `graph-status${type ? ` ${type}` : ''}`;
  }

  function graphDefaultNodes() {
    const positions = {
      model: [28, 180], config: [220, 180], effects: [412, 180], outputs: [604, 180], code: [796, 92], run: [982, 180],
    };
    return GRAPH_FLOW_ORDER.map((type) => ({
      id: `graph-${type}`,
      type,
      x: positions[type][0],
      y: positions[type][1],
      capabilityId: type === 'model' ? state.selectedCapabilityId : null,
    }));
  }

  function graphEdgeId(source, sourcePort, target, targetPort) {
    return `${source}.${sourcePort}__${target}.${targetPort}`.replace(/[^A-Za-z0-9_.-]/g, '_');
  }

  function graphDefaultEdges() {
    const present = new Set(state.graphNodes.map((node) => node.type));
    const edges = [];
    const add = (sourceType, sourcePort, targetType, targetPort) => {
      if (!present.has(sourceType) || !present.has(targetType)) return;
      const source = state.graphNodes.find((node) => node.type === sourceType);
      const target = state.graphNodes.find((node) => node.type === targetType);
      edges.push({
        id: graphEdgeId(source.id, sourcePort, target.id, targetPort),
        source: source.id,
        sourcePort,
        target: target.id,
        targetPort,
      });
    };
    add('model', 'capability', 'config', 'capability');
    if (present.has('effects')) {
      add('config', 'task', 'effects', 'task');
      add('effects', 'task', 'outputs', 'task');
    } else {
      add('config', 'task', 'outputs', 'task');
    }
    add('outputs', 'spec', 'code', 'spec');
    add('outputs', 'spec', 'run', 'spec');
    add('code', 'script', 'run', 'script');
    return edges;
  }

  function graphAutoWire({ announce = true } = {}) {
    state.graphEdges = graphDefaultEdges();
    state.graphSelectedEdgeId = null;
    graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
    if (announce) graphSetStatus('已按受约束端口自动布线。你仍可删除连线并手工重新连接。', 'success');
  }

  function graphAutoLayout() {
    const ordered = GRAPH_FLOW_ORDER.map((type) => state.graphNodes.find((node) => node.type === type)).filter(Boolean);
    if (!ordered.length) { graphSetStatus('当前没有可布局的流程块。', 'warning'); return; }
    const left = 24; const right = 970; const span = ordered.length > 1 ? (right - left) / (ordered.length - 1) : 0;
    ordered.forEach((node, index) => { node.x = Math.round((left + span * index) / 20) * 20; node.y = node.type === 'code' ? 100 : 200; });
    graphHistoryCommit();
    renderGraph();
    graphSetStatus('已按流程顺序自动布局并吸附到 20px 网格；仅修改画布坐标，不会使已生成代码过期。', 'success');
  }

  function graphInitialize(reset = false) {
    if (!el('graphCanvas')) return;
    if (reset || !state.graphNodes.length) {
      state.graphNodes = graphDefaultNodes();
      state.graphSelectedNodeId = 'graph-model';
      state.graphSelectedEdgeId = null;
      state.graphEdges = graphDefaultEdges();
    } else {
      const model = state.graphNodes.find((node) => node.type === 'model');
      if (model) model.capabilityId = state.selectedCapabilityId;
    }
    state.graphPendingConnection = null;
    if (reset || !state.graphHistory.length) graphHistoryReset();
    else graphHistoryCommit();
    renderGraphPalette();
    renderGraph();
  }

  function graphClear() {
    state.graphNodes = [];
    state.graphEdges = [];
    state.graphSelectedNodeId = null;
    state.graphSelectedEdgeId = null;
    state.graphPendingConnection = null;
    state.graphCode = '';
    state.graphCodeFilename = 'simulation.py';
    state.graphGeneratedSnapshot = '';
    graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
    graphSetStatus('画布已清空。从左侧拖入模块，然后从输出端口拖线到输入端口。', 'warning');
  }

  function graphNodeSummary(node) {
    if (node.type === 'model') {
      const object = presentationObjectForCapability(state.selectedCapabilityId);
      return {
        title: object?.name_zh || state.schema?.capability?.name || state.selectedCapabilityId || '未选择能力',
        detail: state.selectedCapabilityId || '拖入一个执行能力',
      };
    }
    if (node.type === 'config') {
      const duration = getPath(state.formData || {}, 'simulation.duration_s');
      const sample = getPath(state.formData || {}, 'simulation.sample_s');
      return { title: `${state.schema?.fields?.length || 0} 个可配置字段`, detail: `duration=${duration ?? '—'}s · sample=${sample ?? '—'}s` };
    }
    if (node.type === 'effects') {
      const events = state.formData?.events || {};
      const count = ['faults', 'degradations', 'constraints'].reduce((sum, key) => sum + (events[key]?.length || 0), 0);
      return { title: `${count} 个已配置事件`, detail: '从能力事件目录受约束添加' };
    }
    if (node.type === 'outputs') {
      const outputs = state.formData?.outputs || {};
      return { title: `${outputs.qoi?.length || 0} QoI · ${outputs.plots?.length || 0} 曲线`, detail: `${outputs.telemetry_streams?.length || 0} 个多速率遥测流` };
    }
    if (node.type === 'code') {
      return { title: graphCodeIsFresh() ? state.graphCodeFilename : '等待生成 Python', detail: graphCodeIsFresh() ? '代码与当前图和参数一致' : '使用受约束 deterministic exporter' };
    }
    const planNodes = state.planning?.execution_plan?.nodes?.length || state.planning?.plan?.nodes?.length || 0;
    return { title: state.activeRunId ? `最近：${state.activeRunId}` : '提交 Run Bundle', detail: planNodes ? `执行计划 ${planNodes} 节点` : '校验后异步执行' };
  }

  function graphTransfer(event, payload) {
    const text = JSON.stringify(payload);
    event.dataTransfer.effectAllowed = payload.kind === 'move-node' ? 'move' : 'copy';
    event.dataTransfer.setData('application/x-sat-graph', text);
    event.dataTransfer.setData('text/plain', text);
  }

  function graphReadTransfer(event) {
    const raw = event.dataTransfer.getData('application/x-sat-graph') || event.dataTransfer.getData('text/plain');
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (_) { return null; }
  }

  function graphDropPosition(event) {
    const canvas = el('graphCanvas');
    const rect = canvas.getBoundingClientRect();
    return {
      x: Math.round(Math.max(10, Math.min(1010, event.clientX - rect.left - 90)) / 20) * 20,
      y: Math.round(Math.max(46, Math.min(430, event.clientY - rect.top - 55)) / 20) * 20,
    };
  }

  function graphAddOrMoveBlock(type, x, y) {
    let node = state.graphNodes.find((item) => item.type === type);
    const isNew = !node;
    if (!node) {
      node = { id: `graph-${type}`, type, x, y, capabilityId: type === 'model' ? state.selectedCapabilityId : null };
      state.graphNodes.push(node);
    } else {
      node.x = x;
      node.y = y;
      if (type === 'model') node.capabilityId = state.selectedCapabilityId;
    }
    state.graphSelectedNodeId = node.id;
    state.graphSelectedEdgeId = null;
    if (isNew) graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
    if (isNew) graphSetStatus(`已添加“${GRAPH_BLOCK_META[type]?.title || type}”。请连接其类型化端口，或点击“自动布线”。`, 'success');
    else graphSetStatus('已移动流程块并吸附到 20px 网格；仅修改布局，不会使 TaskSpec / Python 过期。', 'success');
  }

  function graphRemoveNode(nodeId) {
    state.graphNodes = state.graphNodes.filter((node) => node.id !== nodeId);
    state.graphEdges = state.graphEdges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
    if (state.graphSelectedNodeId === nodeId) state.graphSelectedNodeId = null;
    state.graphSelectedEdgeId = null;
    graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
  }

  function graphRemoveEdge(edgeId) {
    const edge = state.graphEdges.find((item) => item.id === edgeId);
    state.graphEdges = state.graphEdges.filter((item) => item.id !== edgeId);
    state.graphSelectedEdgeId = null;
    graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
    if (edge) graphSetStatus('连线已删除。重新连接后再编译。', 'warning');
  }

  function graphPortDefinition(nodeId, direction, portId) {
    const node = state.graphNodes.find((item) => item.id === nodeId);
    const ports = node ? GRAPH_BLOCK_META[node.type]?.[direction === 'input' ? 'inputs' : 'outputs'] : null;
    return ports?.find((port) => port.id === portId) || null;
  }

  function graphConnectionWouldCycle(sourceId, targetId, ignoreEdgeId = null) {
    if (sourceId === targetId) return true;
    const adjacency = new Map();
    state.graphEdges.forEach((edge) => {
      if (ignoreEdgeId && edge.id === ignoreEdgeId) return;
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
      adjacency.get(edge.source).push(edge.target);
    });
    if (!adjacency.has(sourceId)) adjacency.set(sourceId, []);
    adjacency.get(sourceId).push(targetId);
    const stack = [targetId];
    const seen = new Set();
    while (stack.length) {
      const current = stack.pop();
      if (current === sourceId) return true;
      if (seen.has(current)) continue;
      seen.add(current);
      (adjacency.get(current) || []).forEach((next) => stack.push(next));
    }
    return false;
  }

  function graphCreateEdge(sourceId, sourcePort, targetId, targetPort) {
    const sourceDef = graphPortDefinition(sourceId, 'output', sourcePort);
    const targetDef = graphPortDefinition(targetId, 'input', targetPort);
    const rewireEdgeId = state.graphPendingConnection?.rewireEdgeId || null;
    if (!sourceDef || !targetDef) throw new Error('端口不存在或方向不正确');
    if (sourceDef.type !== targetDef.type) throw new Error(`端口类型不兼容：${GRAPH_PORT_TYPE_LABELS[sourceDef.type] || sourceDef.type} → ${GRAPH_PORT_TYPE_LABELS[targetDef.type] || targetDef.type}`);
    if (sourceId === targetId) throw new Error('节点不能连接到自身');
    if (state.graphEdges.some((edge) => edge.id !== rewireEdgeId && edge.target === targetId && edge.targetPort === targetPort)) throw new Error('该输入端口已经有驱动源；请选择其他输入端口');
    if (state.graphEdges.some((edge) => edge.id !== rewireEdgeId && edge.source === sourceId && edge.sourcePort === sourcePort && edge.target === targetId && edge.targetPort === targetPort)) throw new Error('该连线已经存在');
    if (graphConnectionWouldCycle(sourceId, targetId, rewireEdgeId)) throw new Error('该连接会形成环路，已拒绝');
    const edge = {
      id: graphEdgeId(sourceId, sourcePort, targetId, targetPort),
      source: sourceId,
      sourcePort,
      target: targetId,
      targetPort,
    };
    if (rewireEdgeId) state.graphEdges = state.graphEdges.filter((item) => item.id !== rewireEdgeId);
    state.graphEdges.push(edge);
    state.graphSelectedEdgeId = edge.id;
    state.graphSelectedNodeId = null;
    graphInvalidateCompiledState();
    graphHistoryCommit();
    renderGraph();
    graphSetStatus(`${rewireEdgeId ? '已重连' : '已连接'} ${GRAPH_PORT_TYPE_LABELS[sourceDef.type] || sourceDef.type} 端口。`, 'success');
    return edge;
  }

  function graphBeginRewireEdge(edgeId) {
    const edge = state.graphEdges.find((item) => item.id === edgeId);
    if (!edge) return;
    const source = graphPortCenter(edge.source, 'output', edge.sourcePort);
    const target = graphPortCenter(edge.target, 'input', edge.targetPort);
    state.graphPendingConnection = {
      source: edge.source,
      sourcePort: edge.sourcePort,
      x: target?.x ?? source?.x ?? 0,
      y: target?.y ?? source?.y ?? 0,
      drag: false,
      rewireEdgeId: edge.id,
    };
    state.graphSelectedEdgeId = null;
    state.graphSelectedNodeId = edge.source;
    renderGraph();
    graphSetStatus('重连模式：原连线会保留，直到你选择一个新的兼容输入端口。按 Esc 可取消。', 'warning');
  }

  function graphCancelConnection() {
    if (!state.graphPendingConnection) return;
    state.graphPendingConnection = null;
    graphDrawEdges();
    $all('.graph-port-button.pending').forEach((node) => node.classList.remove('pending'));
  }

  function graphCanvasPoint(clientX, clientY) {
    const rect = el('graphCanvas').getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function graphBeginConnection(event, nodeId, portId) {
    event.preventDefault();
    event.stopPropagation();
    const start = graphCanvasPoint(event.clientX, event.clientY);
    state.graphPendingConnection = { source: nodeId, sourcePort: portId, x: start.x, y: start.y, drag: false, startClientX: event.clientX, startClientY: event.clientY };
    state.graphSelectedEdgeId = null;
    state.graphSelectedNodeId = nodeId;
    const move = (moveEvent) => {
      if (!state.graphPendingConnection) return;
      const point = graphCanvasPoint(moveEvent.clientX, moveEvent.clientY);
      state.graphPendingConnection.x = point.x;
      state.graphPendingConnection.y = point.y;
      if (Math.hypot(moveEvent.clientX - state.graphPendingConnection.startClientX, moveEvent.clientY - state.graphPendingConnection.startClientY) > 5) state.graphPendingConnection.drag = true;
      graphDrawEdges();
    };
    const up = (upEvent) => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      if (!state.graphPendingConnection) return;
      const target = document.elementFromPoint(upEvent.clientX, upEvent.clientY)?.closest?.('.graph-port-button.input');
      if (target) {
        try { graphCreateEdge(nodeId, portId, target.dataset.nodeId, target.dataset.portId); }
        catch (error) { graphCancelConnection(); graphSetStatus(error.message, 'error'); }
        return;
      }
      if (state.graphPendingConnection.drag) {
        graphCancelConnection();
        graphSetStatus('连线已取消：请拖到兼容的输入端口。', 'warning');
      } else {
        renderGraph();
        graphSetStatus('已选择输出端口；点击一个兼容的输入端口完成连接。', 'warning');
      }
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    graphDrawEdges();
  }

  function graphHandleInputPortClick(event, nodeId, portId) {
    event.preventDefault();
    event.stopPropagation();
    const pending = state.graphPendingConnection;
    if (!pending) {
      if (state.graphEdges.some((edge) => edge.target === nodeId && edge.targetPort === portId)) return;
      graphSetStatus('先从一个输出端口拖线，或点击输出端口后再点击此输入端口。', 'warning');
      return;
    }
    try { graphCreateEdge(pending.source, pending.sourcePort, nodeId, portId); }
    catch (error) { graphCancelConnection(); graphSetStatus(error.message, 'error'); }
  }

  async function graphHandleDrop(event) {
    event.preventDefault();
    el('graphCanvas')?.classList.remove('drag-over');
    const payload = graphReadTransfer(event);
    if (!payload) return;
    const pos = graphDropPosition(event);
    if (payload.kind === 'move-node') {
      const node = state.graphNodes.find((item) => item.id === payload.nodeId);
      if (node) {
        node.x = pos.x;
        node.y = pos.y;
        state.graphSelectedNodeId = node.id;
        state.graphSelectedEdgeId = null;
        graphInvalidateCompiledState();
        graphHistoryCommit();
        renderGraph();
      }
      return;
    }
    if (payload.kind === 'block') {
      graphAddOrMoveBlock(payload.type, pos.x, pos.y);
      return;
    }
    if (payload.kind === 'capability') {
      try {
        showBusy('切换图形执行能力', payload.capabilityId);
        const object = state.presentationObjects.find((item) => item.object_id === payload.objectId) || null;
        await selectCapability(payload.capabilityId, object);
        graphAddOrMoveBlock('model', pos.x, pos.y);
        graphSetStatus(`已装入执行能力 ${payload.capabilityId}。端口拓扑仍需通过后端图编译器校验。`, 'success');
      } catch (error) {
        graphSetStatus(`能力装入失败：${error.message}`, 'error');
      } finally {
        hideBusy();
      }
    }
  }

  function renderGraphPalette() {
    const host = el('graphCapabilityPalette');
    if (!host) return;
    const query = (el('graphCapabilitySearch')?.value || '').trim().toLowerCase();
    host.replaceChildren();
    let rows;
    if (state.presentationObjects.length) {
      rows = state.presentationObjects.map((object) => ({
        objectId: object.object_id,
        capabilityId: object.primary_capability_id || object.configuration_capability_id,
        label: object.name_zh || object.name_en || object.object_id,
        detail: `${levelLabel(object.level)}${(object.variants || []).length > 1 ? ` · ${(object.variants || []).length} 个变体` : ''}`,
      })).filter((item) => item.capabilityId);
    } else {
      rows = state.catalog.map((item) => ({
        objectId: item.capability_id,
        capabilityId: item.capability_id,
        label: item.name || item.capability_id,
        detail: levelLabel(item.level),
      }));
    }
    rows = rows.filter((item) => `${item.label} ${item.capabilityId} ${item.detail}`.toLowerCase().includes(query));
    rows.slice(0, 80).forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.draggable = true;
      button.className = `graph-palette-item${item.capabilityId === state.selectedCapabilityId ? ' active' : ''}`;
      const icon = document.createElement('span'); icon.className = 'graph-palette-icon'; icon.textContent = '◇';
      const text = document.createElement('span');
      const strong = document.createElement('strong'); strong.textContent = item.label;
      const small = document.createElement('small'); small.textContent = `${item.detail} · ${item.capabilityId}`;
      text.append(strong, small); button.append(icon, text);
      button.addEventListener('dragstart', (event) => graphTransfer(event, { kind: 'capability', ...item }));
      button.addEventListener('dblclick', async () => {
        try {
          showBusy('切换图形执行能力', item.capabilityId);
          const object = state.presentationObjects.find((entry) => entry.object_id === item.objectId) || null;
          await selectCapability(item.capabilityId, object);
          graphAddOrMoveBlock('model', 28, 180);
          graphSetStatus(`已装入执行能力 ${item.capabilityId}。`, 'success');
        } catch (error) { graphSetStatus(error.message, 'error'); } finally { hideBusy(); }
      });
      host.appendChild(button);
    });
    if (!rows.length) {
      const empty = document.createElement('div'); empty.className = 'graph-inspector-empty'; empty.textContent = '没有匹配的可执行能力。'; host.appendChild(empty);
    }
  }

  function graphPortCenter(nodeId, direction, portId) {
    const selector = `.graph-port-button.${direction}[data-node-id="${CSS.escape(nodeId)}"][data-port-id="${CSS.escape(portId)}"]`;
    const port = document.querySelector(selector);
    const canvas = el('graphCanvas');
    if (!port || !canvas) return null;
    const rect = port.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    return { x: rect.left + rect.width / 2 - canvasRect.left, y: rect.top + rect.height / 2 - canvasRect.top };
  }

  function graphPathData(sx, sy, tx, ty) {
    const bend = Math.max(42, Math.abs(tx - sx) * 0.45);
    return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
  }

  function graphDrawEdges() {
    const svg = el('graphEdges');
    if (!svg) return;
    svg.replaceChildren();
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'graphArrow'); marker.setAttribute('viewBox', '0 0 10 10'); marker.setAttribute('refX', '8'); marker.setAttribute('refY', '5'); marker.setAttribute('markerWidth', '5'); marker.setAttribute('markerHeight', '5'); marker.setAttribute('orient', 'auto-start-reverse');
    const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'path'); arrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z'); arrow.setAttribute('class', 'graph-edge-arrow'); marker.appendChild(arrow); defs.appendChild(marker); svg.appendChild(defs);
    state.graphEdges.forEach((edge) => {
      const source = graphPortCenter(edge.source, 'output', edge.sourcePort);
      const target = graphPortCenter(edge.target, 'input', edge.targetPort);
      if (!source || !target) return;
      const d = graphPathData(source.x, source.y, target.x, target.y);
      const selected = edge.id === state.graphSelectedEdgeId;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const diagnostic = visualEdgeDiagnostic('flow', edge.id);
      path.setAttribute('class', `graph-edge${selected ? ' selected' : ''}${diagnostic ? ' diagnostic-error' : ''}`); path.setAttribute('marker-end', 'url(#graphArrow)'); path.setAttribute('d', d); svg.appendChild(path);
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('class', 'graph-edge-hit'); hit.setAttribute('d', d); hit.setAttribute('tabindex', '0');
      hit.addEventListener('click', (event) => { event.stopPropagation(); state.graphSelectedEdgeId = edge.id; state.graphSelectedNodeId = null; renderGraph(); });
      hit.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); state.graphSelectedEdgeId = edge.id; state.graphSelectedNodeId = null; renderGraph(); } });
      svg.appendChild(hit);
      const sourceDef = graphPortDefinition(edge.source, 'output', edge.sourcePort);
      if (sourceDef) {
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('class', `graph-edge-label${selected ? ' selected' : ''}`);
        label.setAttribute('x', String((source.x + target.x) / 2));
        label.setAttribute('y', String((source.y + target.y) / 2 - 7));
        label.textContent = GRAPH_PORT_TYPE_LABELS[sourceDef.type] || sourceDef.type;
        svg.appendChild(label);
      }
    });
    const pending = state.graphPendingConnection;
    if (pending) {
      const source = graphPortCenter(pending.source, 'output', pending.sourcePort);
      if (source) {
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('class', 'graph-edge pending');
        path.setAttribute('d', graphPathData(source.x, source.y, pending.x ?? source.x + 80, pending.y ?? source.y));
        svg.appendChild(path);
      }
    }
  }

  function graphRenderPort(node, port, direction) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `graph-port-button ${direction}`;
    const diagnostic = visualPortDiagnostic('flow', node.id, port.id);
    if (diagnostic) { button.classList.add('diagnostic-error'); button.title = diagnostic.reason || '运行错误定位到该端口'; }
    button.dataset.nodeId = node.id;
    button.dataset.portId = port.id;
    button.dataset.portType = port.type;
    const dot = document.createElement('span'); dot.className = 'graph-port-dot';
    const text = document.createElement('span'); text.className = 'graph-port-label'; text.textContent = port.label || port.id;
    const type = document.createElement('small'); type.textContent = GRAPH_PORT_TYPE_LABELS[port.type] || port.type;
    if (direction === 'input') button.append(dot, text, type); else button.append(type, text, dot);
    const occupied = direction === 'input' && state.graphEdges.some((edge) => edge.target === node.id && edge.targetPort === port.id);
    if (occupied) button.classList.add('connected');
    if (port.required) button.classList.add('required');
    const pending = state.graphPendingConnection;
    if (pending && direction === 'output' && pending.source === node.id && pending.sourcePort === port.id) button.classList.add('pending');
    if (pending && direction === 'input') {
      const sourceDef = graphPortDefinition(pending.source, 'output', pending.sourcePort);
      button.classList.toggle('compatible', Boolean(sourceDef && sourceDef.type === port.type && !occupied));
      button.classList.toggle('incompatible', Boolean(sourceDef && sourceDef.type !== port.type));
    }
    const baseTitle = `${direction === 'input' ? '输入' : '输出'} · ${port.label || port.id} · ${GRAPH_PORT_TYPE_LABELS[port.type] || port.type}${port.required ? ' · 必需' : ''}`;
    button.title = diagnostic ? `${baseTitle}\n运行诊断：${diagnostic.reason || diagnostic.label || '错误定位到该端口'}` : baseTitle;
    if (direction === 'output') {
      button.addEventListener('pointerdown', (event) => graphBeginConnection(event, node.id, port.id));
      button.addEventListener('click', (event) => event.stopPropagation());
    } else {
      button.addEventListener('pointerdown', (event) => event.stopPropagation());
      button.addEventListener('click', (event) => graphHandleInputPortClick(event, node.id, port.id));
    }
    return button;
  }

  function renderGraph() {
    const host = el('graphNodes');
    if (!host) return;
    host.replaceChildren();
    state.graphNodes.forEach((node) => {
      const meta = GRAPH_BLOCK_META[node.type] || { title: node.type, icon: '□', hint: '', inputs: [], outputs: [] };
      const summary = graphNodeSummary(node);
      const card = document.createElement('section');
      const diagnostic = visualNodeDiagnostic('flow', node.id);
      card.className = `graph-node${node.id === state.graphSelectedNodeId ? ' selected' : ''}${diagnostic ? ' diagnostic-error' : ''}`;
      if (diagnostic) card.title = `运行诊断：${diagnostic.reason || diagnostic.label || '错误定位到该节点'}`;
      card.style.left = `${node.x}px`; card.style.top = `${node.y}px`; card.dataset.nodeId = node.id;
      const head = document.createElement('div'); head.className = 'graph-node-head'; head.draggable = true;
      const title = document.createElement('div'); title.className = 'graph-node-title';
      const icon = document.createElement('span'); icon.textContent = meta.icon;
      const strong = document.createElement('strong'); strong.textContent = meta.title;
      title.append(icon, strong);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'graph-node-remove'; remove.title = '移除节点'; remove.textContent = '×';
      remove.addEventListener('click', (event) => { event.stopPropagation(); graphRemoveNode(node.id); });
      head.append(title, remove);
      head.addEventListener('dragstart', (event) => graphTransfer(event, { kind: 'move-node', nodeId: node.id }));
      const body = document.createElement('div'); body.className = 'graph-node-body';
      const main = document.createElement('strong'); main.textContent = summary.title;
      const detail = document.createElement(node.type === 'model' ? 'code' : 'span'); detail.textContent = summary.detail;
      body.append(main, detail);
      const ports = document.createElement('div'); ports.className = 'graph-node-ports';
      const inputs = document.createElement('div'); inputs.className = 'graph-port-column inputs';
      const outputs = document.createElement('div'); outputs.className = 'graph-port-column outputs';
      (meta.inputs || []).forEach((port) => inputs.appendChild(graphRenderPort(node, port, 'input')));
      (meta.outputs || []).forEach((port) => outputs.appendChild(graphRenderPort(node, port, 'output')));
      ports.append(inputs, outputs);
      card.append(head, body, ports);
      card.addEventListener('click', () => { state.graphSelectedNodeId = node.id; state.graphSelectedEdgeId = null; renderGraph(); });
      host.appendChild(card);
    });
    el('graphEmptyHint')?.classList.toggle('hidden', state.graphNodes.length > 0);
    requestAnimationFrame(graphDrawEdges);
    renderGraphInspector();
  }

  function graphInspectorMetaRow(label, value) {
    const row = document.createElement('div'); const left = document.createElement('span'); const right = document.createElement('strong'); left.textContent = label; right.textContent = String(value ?? '—'); row.append(left, right); return row;
  }

  function graphAppendField(host, field) {
    const label = document.createElement('label'); label.className = 'graph-inspector-field';
    const caption = document.createElement('span'); caption.textContent = `${field.label || field.path}${field.unit ? ` · ${field.unit}` : ''}`; label.appendChild(caption);
    let input;
    if (field.widget === 'select') {
      input = document.createElement('select');
      (field.enum || []).forEach((value) => { const option = document.createElement('option'); option.value = String(value); option.textContent = field.enum_labels?.[String(value)] || String(value); input.appendChild(option); });
    } else if (field.widget === 'checkbox') {
      input = document.createElement('input'); input.type = 'checkbox';
    } else {
      input = document.createElement('input'); input.type = ['number', 'integer'].includes(field.type) ? 'number' : 'text';
      if (field.minimum != null) input.min = String(field.minimum); if (field.maximum != null) input.max = String(field.maximum); input.step = field.type === 'integer' ? '1' : 'any';
    }
    const current = getPath(state.formData || {}, field.path);
    if (input.type === 'checkbox') input.checked = Boolean(current); else input.value = String(current ?? field.default ?? '');
    input.addEventListener('change', () => {
      try {
        let value;
        if (input.type === 'checkbox') value = input.checked;
        else if (['number', 'integer'].includes(field.type)) {
          value = Number(input.value); if (!Number.isFinite(value)) throw new Error('必须是数值'); if (field.type === 'integer' && !Number.isInteger(value)) throw new Error('必须是整数');
        } else value = input.value;
        setPath(state.formData, field.path, value);
        state.taskSpec = null; state.planning = null; syncTaskSpecEditor(); validateClientForm({ show: false }); renderForm(); renderGraph();
        graphSetStatus(`已更新 ${field.label || field.path}；重新生成 Python 后代码会同步。`, 'success');
      } catch (error) { graphSetStatus(`${field.label || field.path}：${error.message}`, 'error'); }
    });
    label.appendChild(input); host.appendChild(label);
  }

  function graphInspectorActions(host, actions) {
    const row = document.createElement('div'); row.className = 'graph-inspector-actions';
    actions.forEach(({ label, style = 'button-ghost', action }) => { const button = document.createElement('button'); button.type = 'button'; button.className = `button ${style}`; button.textContent = label; button.addEventListener('click', action); row.appendChild(button); });
    host.appendChild(row);
  }

  function appendCodeChangeInsight(host, mode) {
    const isAssembly = mode === 'assembly';
    const generatedSnapshot = isAssembly ? state.assemblyGeneratedSnapshot : state.graphGeneratedSnapshot;
    const currentSnapshot = isAssembly ? assemblySnapshot() : graphFormSnapshot();
    const codeDiff = isAssembly ? state.assemblyCodeDiff : state.graphCodeDiff;
    const pendingPaths = changedSnapshotPaths(generatedSnapshot, currentSnapshot);
    if (!pendingPaths.length && !codeDiff?.changed) return;
    const card = document.createElement('div'); card.className = 'code-change-card';
    if (pendingPaths.length) {
      const title = document.createElement('strong'); title.textContent = '当前图/参数已变化，下一次生成会更新代码';
      const list = document.createElement('div'); list.className = 'code-change-paths';
      pendingPaths.forEach((path) => { const code = document.createElement('code'); code.textContent = path; list.appendChild(code); });
      card.append(title, list);
    }
    if (codeDiff?.changed) {
      const summary = document.createElement('small');
      summary.textContent = `上一次重新生成：从第 ${codeDiff.startLine} 行附近开始变化，旧 ${codeDiff.beforeLines} 行 / 新 ${codeDiff.afterLines} 行。`;
      card.appendChild(summary);
    }
    host.appendChild(card);
  }

  function graphAddDefaultEffect(kind, effectId) {
    const bucket = ({ fault: 'faults', degradation: 'degradations', constraint: 'constraints' })[kind];
    const catalog = state.schema?.event_catalog?.[bucket] || [];
    const entry = catalog.find((item) => item.effect === effectId);
    if (!bucket || !entry) throw new Error('当前能力没有该事件');
    if (!state.formData.events) state.formData.events = { faults: [], degradations: [], constraints: [] };
    if (!Array.isArray(state.formData.events[bucket])) state.formData.events[bucket] = [];
    const event = clone(entry.default_event || {});
    event.id = `${effectId}_${Date.now().toString(36)}`;
    event.event_type = kind;
    state.formData.events[bucket].push(event);
    state.taskSpec = null; state.planning = null; syncTaskSpecEditor(); renderEvents(); renderGraph();
    graphSetStatus(`已添加事件“${entry.label || effectId}”；可在 Schema 表单中继续精调时间窗和参数。`, 'success');
  }

  function renderGraphInspector() {
    const host = el('graphInspector'); const badge = el('graphInspectorBadge'); if (!host || !badge) return;
    host.replaceChildren();
    const edge = state.graphEdges.find((item) => item.id === state.graphSelectedEdgeId);
    if (edge) {
      badge.textContent = '连线';
      const section = document.createElement('section'); section.className = 'graph-inspector-section';
      const heading = document.createElement('h4'); heading.textContent = '类型化连接'; section.appendChild(heading);
      const sourceNode = state.graphNodes.find((item) => item.id === edge.source);
      const targetNode = state.graphNodes.find((item) => item.id === edge.target);
      const sourceDef = graphPortDefinition(edge.source, 'output', edge.sourcePort);
      const meta = document.createElement('div'); meta.className = 'graph-inspector-meta';
      meta.append(
        graphInspectorMetaRow('源', `${GRAPH_BLOCK_META[sourceNode?.type]?.title || edge.source}.${edge.sourcePort}`),
        graphInspectorMetaRow('目标', `${GRAPH_BLOCK_META[targetNode?.type]?.title || edge.target}.${edge.targetPort}`),
        graphInspectorMetaRow('类型', GRAPH_PORT_TYPE_LABELS[sourceDef?.type] || sourceDef?.type || '—'),
      );
      section.appendChild(meta);
      graphInspectorActions(section, [
        { label: '重连目标', style: 'button-secondary', action: () => graphBeginRewireEdge(edge.id) },
        { label: '删除连线', action: () => graphRemoveEdge(edge.id) },
      ]);
      host.appendChild(section);
      return;
    }
    const node = state.graphNodes.find((item) => item.id === state.graphSelectedNodeId);
    if (!node) { badge.textContent = '未选择'; const empty = document.createElement('div'); empty.className = 'graph-inspector-empty'; empty.textContent = '选择节点或连线。输出端口可拖到兼容输入端口；选中连线后可在这里删除。'; host.appendChild(empty); return; }
    badge.textContent = GRAPH_BLOCK_META[node.type]?.title || node.type;
    const section = document.createElement('section'); section.className = 'graph-inspector-section';
    const heading = document.createElement('h4'); heading.textContent = GRAPH_BLOCK_META[node.type]?.title || node.type; section.appendChild(heading);
    const portsMeta = document.createElement('div'); portsMeta.className = 'graph-port-contract';
    [...(GRAPH_BLOCK_META[node.type]?.inputs || []).map((port) => `IN ${port.label}: ${GRAPH_PORT_TYPE_LABELS[port.type] || port.type}${port.required ? ' *' : ''}`), ...(GRAPH_BLOCK_META[node.type]?.outputs || []).map((port) => `OUT ${port.label}: ${GRAPH_PORT_TYPE_LABELS[port.type] || port.type}`)].forEach((text) => { const row = document.createElement('code'); row.textContent = text; portsMeta.appendChild(row); });
    if (portsMeta.childElementCount) section.appendChild(portsMeta);
    if (node.type === 'model') {
      const meta = document.createElement('div'); meta.className = 'graph-inspector-meta';
      meta.append(graphInspectorMetaRow('Capability', state.selectedCapabilityId), graphInspectorMetaRow('层级', levelLabel(state.schema?.capability?.level)), graphInspectorMetaRow('可信等级', state.schema?.capability?.trust_level || '—'));
      section.appendChild(meta);
      const note = document.createElement('p'); note.textContent = '端口是编排数据类型，不虚构 Basilisk 内部消息连接。多分系统物理耦合仍由整星 Capability 后端负责。'; section.appendChild(note);
      graphInspectorActions(section, [{ label: '打开 Schema 表单', style: 'button-secondary', action: () => switchTab('form') }]);
    } else if (node.type === 'config') {
      const fields = state.schema?.fields || [];
      const preferred = ['simulation.duration_s', 'simulation.sample_s', 'simulation.step_s'];
      const selected = preferred.map((path) => fields.find((field) => field.path === path)).filter(Boolean);
      fields.filter((field) => field.importance === 'common' && field.path.startsWith('parameters.') && ['number', 'integer', 'string', 'boolean'].includes(field.type) && !['array', 'object', 'object_editor'].includes(field.widget)).slice(0, 3).forEach((field) => selected.push(field));
      selected.forEach((field) => graphAppendField(section, field));
      graphInspectorActions(section, [{ label: '全部参数', action: () => switchTab('form') }]);
    } else if (node.type === 'effects') {
      const events = state.formData?.events || {}; const stats = document.createElement('div'); stats.className = 'graph-stat-grid';
      [['故障', events.faults?.length || 0], ['退化', events.degradations?.length || 0], ['约束', events.constraints?.length || 0]].forEach(([label, value]) => { const card = document.createElement('div'); card.className = 'graph-stat'; const a = document.createElement('span'); a.textContent = label; const b = document.createElement('strong'); b.textContent = value; card.append(a, b); stats.appendChild(card); }); section.appendChild(stats);
      const kindLabel = document.createElement('label'); kindLabel.className = 'graph-inspector-field'; const kindText = document.createElement('span'); kindText.textContent = '快速添加事件'; const kind = document.createElement('select'); [['fault','故障'],['degradation','退化'],['constraint','运行约束']].forEach(([value,label])=>{const o=document.createElement('option');o.value=value;o.textContent=label;kind.appendChild(o);}); kindLabel.append(kindText, kind); section.appendChild(kindLabel);
      const effectLabel = document.createElement('label'); effectLabel.className = 'graph-inspector-field'; const effectText = document.createElement('span'); effectText.textContent = '事件类型'; const effect = document.createElement('select'); effectLabel.append(effectText, effect); section.appendChild(effectLabel);
      const fill = () => { effect.replaceChildren(); const bucket=({fault:'faults',degradation:'degradations',constraint:'constraints'})[kind.value]; (state.schema?.event_catalog?.[bucket] || []).forEach((item)=>{const o=document.createElement('option');o.value=item.effect;o.textContent=item.label || item.effect;effect.appendChild(o);}); };
      kind.addEventListener('change', fill); fill();
      graphInspectorActions(section, [{ label: '添加默认事件', style: 'button-secondary', action: () => { if (!effect.value) return graphSetStatus('当前能力没有该类事件。', 'warning'); try { graphAddDefaultEffect(kind.value, effect.value); } catch (error) { graphSetStatus(error.message, 'error'); } } }, { label: '精调事件', action: () => switchTab('form') }]);
    } else if (node.type === 'outputs') {
      const outputs = state.formData?.outputs || {}; const stats = document.createElement('div'); stats.className = 'graph-stat-grid';
      [['QoI', outputs.qoi?.length || 0], ['曲线', outputs.plots?.length || 0], ['遥测流', outputs.telemetry_streams?.length || 0], ['FMEA', outputs.fmea?.enabled ? '开' : '关']].forEach(([label,value])=>{const card=document.createElement('div');card.className='graph-stat';const a=document.createElement('span');a.textContent=label;const b=document.createElement('strong');b.textContent=value;card.append(a,b);stats.appendChild(card);}); section.appendChild(stats);
      graphInspectorActions(section, [{ label: '编辑输出', style: 'button-secondary', action: () => switchTab('form') }]);
    } else if (node.type === 'code') {
      const stale = document.createElement('div'); stale.className = graphCodeIsFresh() ? 'graph-status success' : 'graph-code-stale'; stale.textContent = graphCodeIsFresh() ? `已生成 ${state.graphCodeFilename}` : '尚未生成，或图拓扑 / 参数已发生变化。'; section.appendChild(stale);
      appendCodeChangeInsight(section, 'flow');
      const preview = document.createElement('textarea'); preview.className = 'graph-code-preview'; preview.readOnly = true; preview.spellcheck = false; preview.value = state.graphCode || '# 点击“生成 Python”后，这里显示可执行脚本。'; section.appendChild(preview);
      graphInspectorActions(section, [
        { label: '生成 Python', style: 'button-secondary', action: () => graphGeneratePython().catch((error) => graphSetStatus(error.message, 'error')) },
        { label: '复制', action: () => graphCopyCode().catch((error) => graphSetStatus(error.message, 'error')) },
        { label: '下载 .py', action: () => graphDownloadCode().catch((error) => graphSetStatus(error.message, 'error')) },
      ]);
    } else if (node.type === 'run') {
      const meta = document.createElement('div'); meta.className = 'graph-inspector-meta'; meta.append(graphInspectorMetaRow('TaskSpec', state.taskSpec ? '已编译' : '待编译'), graphInspectorMetaRow('Python', graphCodeIsFresh() ? '已同步' : '待生成'), graphInspectorMetaRow('最近 Run', state.activeRunId || '—')); section.appendChild(meta);
      graphInspectorActions(section, [{ label: '运行当前图', style: 'button-primary', action: () => runSimulation().catch((error) => graphSetStatus(error.message, 'error')) }]);
    }
    host.appendChild(section);
  }

  function graphSerialize() {
    return {
      schema_version: GRAPH_SCHEMA_VERSION,
      nodes: state.graphNodes.map((node) => ({ id: node.id, type: node.type, x: node.x, y: node.y, capabilityId: node.capabilityId || null })),
      edges: state.graphEdges.map((edge) => ({ id: edge.id, source: edge.source, sourcePort: edge.sourcePort, target: edge.target, targetPort: edge.targetPort })),
    };
  }

  function graphValidateStructure({ requireCode = true, requireRun = true } = {}) {
    const errors = [];
    const warnings = [];
    const byId = new Map(state.graphNodes.map((node) => [node.id, node]));
    const typeCounts = new Map();
    state.graphNodes.forEach((node) => typeCounts.set(node.type, (typeCounts.get(node.type) || 0) + 1));
    ['model', 'config', 'outputs'].forEach((type) => { if (!typeCounts.get(type)) errors.push(`缺少${GRAPH_BLOCK_META[type].title}节点`); });
    if (requireCode && !typeCounts.get('code')) errors.push('缺少 Python 代码节点');
    if (requireRun && !typeCounts.get('run')) errors.push('缺少运行器节点');
    for (const [type, count] of typeCounts) if (count > 1) errors.push(`${GRAPH_BLOCK_META[type]?.title || type}节点重复`);
    if (!state.selectedCapabilityId || !state.formData) errors.push('尚未选择可执行 Capability');
    const model = state.graphNodes.find((node) => node.type === 'model');
    if (model?.capabilityId && model.capabilityId !== state.selectedCapabilityId) errors.push('画布执行能力与当前 Schema 不一致');
    const incoming = new Map();
    const adjacency = new Map();
    state.graphEdges.forEach((edge) => {
      const source = byId.get(edge.source); const target = byId.get(edge.target);
      if (!source || !target) { errors.push(`连线 ${edge.id} 引用了不存在的节点`); return; }
      const sourceDef = graphPortDefinition(edge.source, 'output', edge.sourcePort);
      const targetDef = graphPortDefinition(edge.target, 'input', edge.targetPort);
      if (!sourceDef || !targetDef) { errors.push(`连线 ${edge.id} 端口不存在`); return; }
      if (sourceDef.type !== targetDef.type) errors.push(`连线 ${edge.id} 类型不兼容`);
      const key = `${edge.target}.${edge.targetPort}`;
      incoming.set(key, (incoming.get(key) || 0) + 1);
      if (incoming.get(key) > 1) errors.push(`输入端口 ${key} 有多个驱动源`);
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
      adjacency.get(edge.source).push(edge.target);
    });
    state.graphNodes.forEach((node) => {
      (GRAPH_BLOCK_META[node.type]?.inputs || []).filter((port) => port.required).forEach((port) => {
        if ((incoming.get(`${node.id}.${port.id}`) || 0) !== 1) errors.push(`${GRAPH_BLOCK_META[node.type]?.title || node.type}.${port.label} 必须连接`);
      });
    });
    const indegree = new Map(state.graphNodes.map((node) => [node.id, 0]));
    state.graphEdges.forEach((edge) => { if (indegree.has(edge.target) && byId.has(edge.source)) indegree.set(edge.target, indegree.get(edge.target) + 1); });
    const queue = [...indegree].filter(([, value]) => value === 0).map(([id]) => id);
    const topo = [];
    while (queue.length) {
      const current = queue.shift(); topo.push(current);
      (adjacency.get(current) || []).forEach((target) => { indegree.set(target, indegree.get(target) - 1); if (indegree.get(target) === 0) queue.push(target); });
    }
    if (topo.length !== state.graphNodes.length) errors.push('图中存在环路');
    if (!state.graphEdges.length && state.graphNodes.length > 1) warnings.push('当前没有连线');
    return { ok: errors.length === 0, errors: [...new Set(errors)], warnings, topologicalOrder: topo };
  }

  async function graphCompile({ requireCode = true, requireRun = true, announce = true } = {}) {
    const structural = graphValidateStructure({ requireCode, requireRun });
    if (!structural.ok) { graphSetStatus(`图校验失败：${structural.errors.join('；')}`, 'error'); return false; }
    if (!validateClientForm({ show: false })) { graphSetStatus('参数校验失败；请检查时间步长和必填参数。', 'error'); return false; }
    const payload = await api('/tasks/compile-graph', {
      method: 'POST',
      body: JSON.stringify({ graph: graphSerialize(), form_data: state.formData, require_code: requireCode, require_run: requireRun }),
    });
    if (!payload.graph_validation?.ok) {
      const issues = payload.graph_validation?.errors || payload.graph_validation?.issues || [];
      graphSetStatus(`后端拓扑校验失败：${issues.map((item) => item.message || item.code).join('；') || '未知拓扑错误'}`, 'error');
      return false;
    }
    const result = payload.result || {};
    state.taskSpec = result.task_spec || null; state.planning = result.planning || null; syncTaskSpecEditor();
    if (!payload.ok) {
      const issues = [...(result.validation?.errors || []), ...(result.guards?.issues || []), ...(result.planning?.validation?.errors || [])];
      graphSetStatus(`TaskSpec 编译失败：${issues.map(formatValidationIssue).join('；') || '未通过能力边界校验'}`, 'error'); return false;
    }
    const nodes = result.planning?.execution_plan?.nodes?.length || result.planning?.plan?.nodes?.length || 0;
    const topo = (payload.graph_validation?.topological_order || []).map((id) => GRAPH_BLOCK_META[state.graphNodes.find((node) => node.id === id)?.type]?.title || id).join(' → ');
    if (announce) graphSetStatus(`端口类型与拓扑已通过后端校验；${topo || '图已编译'}${nodes ? `；执行计划 ${nodes} 节点` : ''}。`, 'success');
    renderGraph(); return true;
  }

  async function graphGenerateCodeFromTaskSpec() {
    if (!state.taskSpec) throw new Error('请先编译 TaskSpec');
    const payload = await api('/tasks/export-script', { method: 'POST', body: JSON.stringify({ task_spec: state.taskSpec }) });
    const nextCode = payload.code || '';
    state.graphCodeDiff = codeDiffSummary(state.graphCode, nextCode);
    state.graphCode = nextCode; state.graphCodeFilename = payload.filename || 'simulation.py'; state.graphGeneratedSnapshot = graphFormSnapshot(); renderGraph();
    graphSetStatus(`已生成可执行 Python：${state.graphCodeFilename}。拓扑已校验，代码仍走现有 sat_sim 编译与执行链。`, 'success');
    return payload;
  }

  async function graphGeneratePython() {
    const ok = await graphCompile({ requireCode: true, requireRun: false, announce: false }); if (!ok) return false;
    await graphGenerateCodeFromTaskSpec(); return true;
  }

  async function graphCopyCode() {
    if (!graphCodeIsFresh()) { const ok = await graphGeneratePython(); if (!ok) return; }
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(state.graphCode);
    else { const preview = el('graphInspector')?.querySelector('.graph-code-preview'); if (!preview) throw new Error('没有可复制的代码'); preview.focus(); preview.select(); document.execCommand('copy'); }
    graphSetStatus('Python 代码已复制到剪贴板。', 'success');
  }

  async function graphDownloadCode() {
    if (!graphCodeIsFresh()) { const ok = await graphGeneratePython(); if (!ok) return; }
    const blob = new Blob([state.graphCode], { type: 'text/x-python;charset=utf-8' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = state.graphCodeFilename || 'simulation.py'; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    graphSetStatus(`已下载 ${anchor.download}。`, 'success');
  }


  // -------------------------------------------------------------------------
  // Visual Composer V14: clean modeling canvas, separate results, explicit Scope/Display/To Workspace observers
  // -------------------------------------------------------------------------
  function assemblySetStatus(message, type = '') {
    const node = el('assemblyStatus');
    if (!node) return;
    node.textContent = message;
    node.className = `graph-status${type ? ` ${type}` : ''}`;
  }

  function assemblySnapshot() {
    return JSON.stringify({
      form: state.formData || {},
      graph: state.assemblyGraph ? {
        parent_capability_id: state.assemblyGraph.parent_capability_id,
        nodes: (state.assemblyGraph.nodes || []).map((node) => ({ id: node.id, moduleAlias: node.moduleAlias, capabilityId: node.capabilityId || null })),
        edges: (state.assemblyGraph.edges || []).map((edge) => ({
          bindingId: edge.bindingId,
          source: edge.source,
          sourcePort: edge.sourcePort,
          target: edge.target,
          targetPort: edge.targetPort,
          dataType: edge.dataType,
        })),
      } : null,
    });
  }

  function assemblyCodeIsFresh() {
    return Boolean(state.assemblyCode && state.assemblyGeneratedSnapshot === assemblySnapshot());
  }

  function assemblyInvalidateCompiledState({ clearCode = false } = {}) {
    state.taskSpec = null;
    state.planning = null;
    clearVisualDiagnostics('assembly');
    if (state.assemblyComparisonRunning && state.assemblyComparison) state.assemblyComparison = { ...state.assemblyComparison, stale: true };
    else state.assemblyComparison = null;
    if (clearCode) {
      state.assemblyCode = '';
      state.assemblyCodeFilename = 'assembly.py';
      state.assemblyGeneratedSnapshot = '';
      state.assemblyCodeDiff = null;
    }
  }

  async function switchGraphComposerMode(mode) {
    const next = mode === 'assembly' ? 'assembly' : 'flow';
    state.graphComposerMode = next;
    el('graphFlowComposer')?.classList.toggle('hidden', next !== 'flow');
    el('graphAssemblyComposer')?.classList.toggle('hidden', next !== 'assembly');
    el('graphFlowToolbarActions')?.classList.toggle('hidden', next !== 'flow');
    const flowButton = el('graphFlowModeBtn');
    const assemblyButton = el('graphAssemblyModeBtn');
    flowButton?.classList.toggle('active', next === 'flow');
    assemblyButton?.classList.toggle('active', next === 'assembly');
    flowButton?.setAttribute('aria-pressed', String(next === 'flow'));
    assemblyButton?.setAttribute('aria-pressed', String(next === 'assembly'));
    if (next === 'flow') {
      requestAnimationFrame(() => graphInitialize(false));
      return;
    }
    if (!state.assemblyCatalog.length) await loadAssemblyCatalog();
    if (!state.assemblyContract) {
      const capabilityId = el('assemblyCapabilitySelect')?.value || state.assemblyCatalog[0]?.parent_capability_id;
      if (capabilityId) await assemblyLoadContract(capabilityId);
    } else {
      requestAnimationFrame(assemblyRender);
    }
  }

  async function loadAssemblyCatalog() {
    const payload = await api('/visual-composer/assemblies');
    state.assemblyCatalog = payload.assemblies || [];
    const select = el('assemblyCapabilitySelect');
    if (!select) return state.assemblyCatalog;
    const previous = select.value;
    select.replaceChildren();
    state.assemblyCatalog.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.parent_capability_id;
      const contractTag = item.port_contract_source === 'explicit-v5' ? 'V5显式端口' : 'V4兼容推断';
      const replaceableTag = Number(item.replaceable_module_count || 0) ? ` · ${item.replaceable_module_count} 可替换槽位` : '';
      option.textContent = `${item.name || item.parent_capability_id} · ${item.module_count} 模块 / ${item.port_count || 0} 端口 / ${item.binding_count} 连线 · ${contractTag}${replaceableTag}`;
      select.appendChild(option);
    });
    const preferred = state.assemblyCatalog.find((item) => item.parent_capability_id === previous)
      || state.assemblyCatalog.find((item) => item.parent_capability_id === 'whole_spacecraft.power_thermal_orbit_coupled.v1')
      || state.assemblyCatalog[0];
    if (preferred) select.value = preferred.parent_capability_id;
    assemblyRenderQuickTemplates();
    return state.assemblyCatalog;
  }

  function assemblyQuickTemplateDefinitions() {
    const catalogIds = new Set((state.assemblyCatalog || []).map((item) => item.parent_capability_id));
    return [
      {
        id: 'power-thermal-orbit-ready',
        title: '整星电源 / 热控 / 轨道 · 可运行组合',
        description: '自动装入高保真轨道 + source-native EPS/Thermal；适合直接运行并查看耦合结果。',
        parentCapabilityId: 'whole_spacecraft.power_thermal_orbit_coupled.v1',
        selections: {
          orbit_environment: 'orbit_environment.orbit_fidelity.v1',
          eps: 'subsystem.eps.source_native.v1',
          thermal: 'subsystem.thermal.source_native.v1',
        },
      },
      {
        id: 'adcs-rw-ready',
        title: 'ADCS + Reaction Wheel · 可运行组合',
        description: '加载 ADCS fidelity，并插入注册 Reaction Wheel 轮速推进模块。',
        parentCapabilityId: 'subsystem.adcs_fidelity.v1',
        selections: { reaction_wheel: 'component.reaction_wheel.v1' },
      },
      {
        id: 'power-thermal-orbit-baseline',
        title: '整星耦合 · 基线',
        description: '保持父模型注册基线，不启用模块替换；用于快速对照。',
        parentCapabilityId: 'whole_spacecraft.power_thermal_orbit_coupled.v1',
        selections: {},
      },
      {
        id: 'adcs-baseline',
        title: 'ADCS fidelity · 基线',
        description: '使用父 ADCS 内部轮模型，适合与 Reaction Wheel 插拔结果比较。',
        parentCapabilityId: 'subsystem.adcs_fidelity.v1',
        selections: {},
      },
    ].filter((item) => catalogIds.has(item.parentCapabilityId));
  }

  function assemblyRenderQuickTemplates() {
    const container = el('assemblyQuickTemplates');
    if (!container) return;
    container.replaceChildren();
    const templates = assemblyQuickTemplateDefinitions();
    templates.forEach((template) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'assembly-quick-template';
      button.dataset.templateId = template.id;
      const title = document.createElement('strong'); title.textContent = template.title;
      const description = document.createElement('small'); description.textContent = template.description;
      const parent = document.createElement('code'); parent.textContent = template.parentCapabilityId;
      button.append(title, description, parent);
      button.addEventListener('click', () => assemblyApplyQuickTemplate(template).catch((error) => { assemblySetStatus(`模板加载失败：${error.message}`, 'error'); hideBusy(); }));
      container.appendChild(button);
    });
    if (!templates.length) {
      const empty = document.createElement('span'); empty.className = 'graph-inspector-empty'; empty.textContent = '当前没有可用的快速模板；仍可从“注册装配”下拉框加载。'; container.appendChild(empty);
    }
  }

  async function assemblyApplyQuickTemplate(template) {
    showBusy('加载快速装配模板', template.title);
    try {
      await assemblyLoadContract(template.parentCapabilityId);
      for (const [alias, capabilityId] of Object.entries(template.selections || {})) {
        const node = (state.assemblyGraph?.nodes || []).find((item) => item.moduleAlias === alias);
        const module = assemblyModuleByAlias(alias);
        if (!node || !module || !assemblyCompatibleOption(module, capabilityId)) {
          throw new Error(`${alias} 当前不能使用模板候选 ${capabilityId}`);
        }
        node.capabilityId = capabilityId;
      }
      state.assemblySelectedNodeId = state.assemblyGraph?.nodes?.[0]?.id || null;
      state.assemblySelectedEdgeId = null;
      state.assemblyPendingConnection = null;
      assemblyInvalidateCompiledState();
      assemblyRender();
      const ok = await assemblyCompile({ announce: false });
      if (!ok) return false;
      const replacementCount = Object.keys(template.selections || {}).length;
      assemblySetStatus(`快速模板“${template.title}”已就绪${replacementCount ? `，已自动匹配 ${replacementCount} 个注册模块` : ''}。现在可直接“构建并运行”或“导出可运行程序包”。`, 'success');
      return true;
    } finally {
      hideBusy();
    }
  }

  async function loadModuleLibrary() {
    const payload = await api('/visual-composer/module-library');
    state.moduleLibrary = payload.module_library?.modules || [];
    assemblyRenderModuleLibrary();
    assemblyRenderQuickTemplates();
    return state.moduleLibrary;
  }

  async function assemblyLoadContract(capabilityId) {
    if (!capabilityId) throw new Error('没有可加载的注册装配');
    if (state.selectedCapabilityId !== capabilityId) await selectCapability(capabilityId);
    const payload = await api(`/visual-composer/assemblies/${encodeURIComponent(capabilityId)}`);
    clearVisualRunOverlay();
    state.assemblyContract = payload.assembly || null;
    if (!state.assemblyContract) throw new Error('后端没有返回装配合同');
    state.assemblyGraph = clone(state.assemblyContract.default_graph);
    state.assemblyGraph.scopes = Array.isArray(state.assemblyGraph.scopes) ? state.assemblyGraph.scopes : [];
    state.assemblySelectedNodeId = state.assemblyGraph.nodes?.[0]?.id || null;
    state.assemblySelectedEdgeId = null;
    state.assemblySelectedScopeId = null;
    state.assemblySelectedScopeProbeId = null;
    state.assemblySelection = state.assemblySelectedNodeId ? [assemblySelectionKey('node', state.assemblySelectedNodeId)] : [];
    state.assemblyPendingConnection = null;
    assemblyInvalidateCompiledState({ clearCode: true });
    if (!state.moduleLibrary.length) await loadModuleLibrary();
    const select = el('assemblyCapabilitySelect');
    if (select) select.value = capabilityId;
    assemblyRender();
    const portContract = state.assemblyContract.simulation_ports || {};
    const contractLabel = portContract.source === 'explicit-v5' ? '显式 V5 simulation_ports' : 'V4 field_mappings 兼容推断';
    const contractWarnings = portContract.validation?.warnings?.length || 0;
    const replaceableCount = Number(state.assemblyContract.replaceable_module_count || 0);
    assemblySetStatus(
      `已加载 ${state.assemblyContract.name || capabilityId}：${state.assemblyContract.modules.length} 模块 / ${(portContract.ports || []).length} 端口 / ${state.assemblyContract.bindings.filter((item) => item.wireable).length} binding；${contractLabel}；可替换槽位 ${replaceableCount}${contractWarnings ? `，${contractWarnings} 个合同提示` : ''}。`,
      portContract.validation?.ok === false ? 'error' : 'success',
    );
  }

  function assemblyBindingById(bindingId) {
    return (state.assemblyContract?.bindings || []).find((item) => item.binding_id === bindingId) || null;
  }

  function assemblyNodeById(nodeId) {
    return (state.assemblyGraph?.nodes || []).find((item) => item.id === nodeId) || null;
  }

  function assemblyScopes() {
    if (!state.assemblyGraph) return [];
    if (!Array.isArray(state.assemblyGraph.scopes)) state.assemblyGraph.scopes = [];
    return state.assemblyGraph.scopes;
  }

  function assemblyObserverKind(observer) {
    const kind = String(observer?.kind || 'scope').toLowerCase();
    return ['scope', 'display', 'workspace'].includes(kind) ? kind : 'scope';
  }

  function assemblyObserverMeta(observerOrKind) {
    const kind = typeof observerOrKind === 'string' ? observerOrKind : assemblyObserverKind(observerOrKind);
    return {
      scope: { kind: 'scope', title: 'Scope', icon: '▱', role: 'Waveform observer', maxProbes: 16, hint: '波形示波器 · 可接多路信号' },
      display: { kind: 'display', title: 'Display', icon: '▣', role: 'Value observer', maxProbes: 1, hint: '末值显示 · 单路信号' },
      workspace: { kind: 'workspace', title: 'To Workspace', icon: '⇩', role: 'Data export observer', maxProbes: 1, hint: 'CSV / JSON 导出 · 单路信号' },
    }[kind] || { kind: 'scope', title: 'Scope', icon: '▱', role: 'Waveform observer', maxProbes: 16, hint: '波形示波器 · 可接多路信号' };
  }

  function assemblyScopeById(scopeId) {
    return assemblyScopes().find((item) => item.id === scopeId) || null;
  }

  function assemblySelectionKey(kind, id) {
    return `${kind}:${id}`;
  }

  function assemblySelectionHas(kind, id) {
    return state.assemblySelection.includes(assemblySelectionKey(kind, id));
  }

  function assemblySelectionItems() {
    return state.assemblySelection.map((key) => {
      const split = key.indexOf(':');
      const kind = split > 0 ? key.slice(0, split) : '';
      const id = split > 0 ? key.slice(split + 1) : key;
      const item = kind === 'observer' ? assemblyScopeById(id) : assemblyNodeById(id);
      return item ? { kind, id, item } : null;
    }).filter(Boolean);
  }

  function assemblySyncPrimarySelection(kind = null, id = null) {
    state.assemblySelectedEdgeId = null;
    state.assemblySelectedScopeProbeId = null;
    if (kind === 'observer') {
      state.assemblySelectedScopeId = id;
      state.assemblySelectedNodeId = null;
    } else if (kind === 'node') {
      state.assemblySelectedNodeId = id;
      state.assemblySelectedScopeId = null;
    } else {
      state.assemblySelectedNodeId = null;
      state.assemblySelectedScopeId = null;
    }
  }

  function assemblySelectItem(kind, id, { additive = false, toggle = false, render = true } = {}) {
    const key = assemblySelectionKey(kind, id);
    let next = additive ? [...state.assemblySelection] : [];
    const index = next.indexOf(key);
    if (toggle && index >= 0) next.splice(index, 1);
    else if (index < 0) next.push(key);
    state.assemblySelection = next;
    if (state.assemblySelection.includes(key)) assemblySyncPrimarySelection(kind, id);
    else {
      const last = assemblySelectionItems().slice(-1)[0] || null;
      assemblySyncPrimarySelection(last?.kind || null, last?.id || null);
    }
    if (render) assemblyRender();
  }

  function assemblyClearSelection({ render = true } = {}) {
    state.assemblySelection = [];
    assemblySyncPrimarySelection();
    if (render) assemblyRender();
  }

  function assemblySelectAll() {
    if (!state.assemblyGraph) return;
    state.assemblySelection = [
      ...(state.assemblyGraph.nodes || []).map((item) => assemblySelectionKey('node', item.id)),
      ...assemblyScopes().map((item) => assemblySelectionKey('observer', item.id)),
    ];
    const first = assemblySelectionItems()[0] || null;
    assemblySyncPrimarySelection(first?.kind || null, first?.id || null);
    assemblyRender();
    assemblySetStatus(`已选择 ${state.assemblySelection.length} 个画布对象；可整体拖动、对齐或等间距排列。`);
  }

  function assemblySelectionLabel() {
    const total = state.assemblySelection.length;
    const observers = assemblySelectionItems().filter((item) => item.kind === 'observer').length;
    return total ? `已选 ${total}${observers ? ` · 观察器 ${observers}` : ''}` : '未选择';
  }

  function assemblyScopeOverlay(scopeId) {
    const overlay = visualOverlayForMode('assembly') || {};
    const rows = overlay.observer_overlays || overlay.scope_overlays || [];
    return rows.find((item) => item.scope_id === scopeId || item.observer_id === scopeId) || null;
  }

  function assemblyScopeProbeOverlay(scopeId, probeId) {
    return (assemblyScopeOverlay(scopeId)?.probes || []).find((item) => item.probe_id === probeId) || null;
  }

  function assemblyAddObserver(kind = 'scope') {
    if (!state.assemblyGraph || !state.assemblyContract) {
      assemblySetStatus('请先加载一个多模块装配，再添加观察器。', 'warning');
      return;
    }
    const observers = assemblyScopes();
    const meta = assemblyObserverMeta(kind);
    const index = observers.filter((item) => assemblyObserverKind(item) === meta.kind).length + 1;
    const id = `${meta.kind}-${Date.now().toString(36)}-${index}`;
    observers.push({ kind: meta.kind, id, name: `${meta.title} ${index}`, x: 850, y: Math.min(440, 70 + observers.length * 120), probes: [] });
    state.assemblySelectedScopeId = id;
    state.assemblySelectedScopeProbeId = null;
    state.assemblySelectedNodeId = null;
    state.assemblySelectedEdgeId = null;
    state.assemblySelection = [assemblySelectionKey('observer', id)];
    state.assemblyPendingConnection = null;
    assemblyRender();
    assemblySetStatus(`已添加 ${meta.title} 只读观察器。先点击模块输出端口，再点击观察器的 signal 输入；它不改变物理拓扑、TaskSpec 或生成代码。`, 'success');
  }

  function assemblyAddScope() { assemblyAddObserver('scope'); }
  function assemblyAddDisplay() { assemblyAddObserver('display'); }
  function assemblyAddWorkspace() { assemblyAddObserver('workspace'); }

  function assemblyRemoveScope(scopeId) {
    if (!state.assemblyGraph) return;
    const observer = assemblyScopeById(scopeId);
    const meta = assemblyObserverMeta(observer);
    state.assemblyGraph.scopes = assemblyScopes().filter((item) => item.id !== scopeId);
    state.assemblySelection = state.assemblySelection.filter((key) => key !== assemblySelectionKey('observer', scopeId));
    state.assemblySelectedScopeId = null;
    state.assemblySelectedScopeProbeId = null;
    state.assemblyPendingConnection = null;
    assemblyRender();
    assemblySetStatus(`已移除 ${meta.title}；物理装配与生成代码未改变。`, 'success');
  }

  function assemblyRemoveScopeProbe(scopeId, probeId) {
    const scope = assemblyScopeById(scopeId);
    if (!scope) return;
    scope.probes = (scope.probes || []).filter((item) => item.id !== probeId);
    state.assemblySelectedScopeProbeId = null;
    assemblyRender();
  }

  function assemblyModuleByAlias(alias) {
    return (state.assemblyContract?.modules || []).find((item) => item.alias === alias) || null;
  }

  function assemblyBaselineCapability(module) {
    if (!module) return null;
    return module.replacement_baseline_capability_id ?? module.capability_id ?? null;
  }

  function assemblyCompatibleOption(module, capabilityId) {
    if (!module?.replaceable || !capabilityId) return null;
    return (module.replacement_options || []).find((item) => item.capability_id === capabilityId && item.compatible) || null;
  }

  function assemblyCompatibleAliasesForCapability(capabilityId) {
    return (state.assemblyContract?.modules || [])
      .filter((module) => assemblyCompatibleOption(module, capabilityId))
      .map((module) => module.alias);
  }

  function assemblyRenderModuleLibrary() {
    const container = el('assemblyModuleLibrary');
    const summary = el('assemblyLibrarySummary');
    if (!container || !summary) return;
    container.replaceChildren();
    const allModules = state.moduleLibrary || [];
    const query = String(state.assemblyLibrarySearch || '').trim().toLowerCase();
    const selectedNode = assemblyNodeById(state.assemblySelectedNodeId);
    const selectedModule = selectedNode ? assemblyModuleByAlias(selectedNode.moduleAlias) : null;
    const aliasesByCapability = new Map(allModules.map((item) => [item.capability_id, assemblyCompatibleAliasesForCapability(item.capability_id)]));
    const compatibleCount = [...aliasesByCapability.values()].filter((aliases) => aliases.length).length;
    const modules = allModules.filter((libraryModule) => {
      const aliases = aliasesByCapability.get(libraryModule.capability_id) || [];
      if (state.assemblyCompatibleOnly && !aliases.length) return false;
      if (!query) return true;
      const haystack = [libraryModule.name, libraryModule.capability_id, libraryModule.interface_id, libraryModule.domain, ...(aliases || [])].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(query);
    });
    modules.forEach((libraryModule) => {
      const aliases = aliasesByCapability.get(libraryModule.capability_id) || [];
      const selectedCompatible = Boolean(selectedModule && assemblyCompatibleOption(selectedModule, libraryModule.capability_id));
      const card = document.createElement('article');
      card.className = `assembly-library-card${selectedCompatible ? ' compatible-selected' : ''}`;
      card.setAttribute('role', 'listitem');
      card.draggable = aliases.length > 0;
      card.dataset.capabilityId = libraryModule.capability_id;
      const title = document.createElement('strong'); title.textContent = libraryModule.name || libraryModule.capability_id;
      const capability = document.createElement('code'); capability.textContent = libraryModule.capability_id;
      const meta = document.createElement('small'); meta.textContent = `${libraryModule.interface_id || '无接口'} · ${(libraryModule.ports || []).length} ports · ${libraryModule.domain || 'module'}`;
      const match = document.createElement('small');
      match.textContent = aliases.length ? `当前装配可匹配：${aliases.join('、')}` : '当前装配没有兼容槽位';
      const action = document.createElement('button'); action.type = 'button'; action.className = 'button button-ghost'; action.textContent = '替换当前';
      action.disabled = !selectedCompatible;
      action.title = selectedCompatible ? `替换 ${selectedNode.moduleAlias}` : '先选择一个与该模块接口兼容的画布节点';
      action.addEventListener('click', () => {
        if (!selectedNode || !selectedCompatible) return;
        try { assemblyReplaceModule(selectedNode.id, libraryModule.capability_id); } catch (error) { assemblySetStatus(error.message, 'error'); }
      });
      if (aliases.length) {
        card.addEventListener('dragstart', (event) => {
          state.assemblyDragModuleCapabilityId = libraryModule.capability_id;
          event.dataTransfer?.setData('text/plain', libraryModule.capability_id);
          if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy';
        });
        card.addEventListener('dragend', () => {
          state.assemblyDragModuleCapabilityId = null;
          $all('.assembly-node.drop-compatible, .assembly-node.drop-incompatible').forEach((item) => item.classList.remove('drop-compatible', 'drop-incompatible'));
        });
      }
      card.append(title, capability, meta, match, action);
      container.appendChild(card);
    });
    if (!modules.length) {
      const empty = document.createElement('span'); empty.className = 'graph-inspector-empty'; empty.textContent = allModules.length ? '没有匹配当前搜索/兼容筛选的模块。' : '模块库为空；只有具有正式 simulation_module_interface 的可运行 Capability 才会出现。'; container.appendChild(empty);
    }
    summary.textContent = `${modules.length}/${allModules.length} 显示 · ${compatibleCount} 当前可用`;
  }

  function assemblyReplaceModule(nodeId, capabilityId) {
    const node = assemblyNodeById(nodeId);
    if (!node) throw new Error('找不到待替换模块节点');
    const module = assemblyModuleByAlias(node.moduleAlias) || {};
    if (!module.replaceable) throw new Error(`模块 ${node.moduleAlias} 未声明 replacement slot`);
    const baseline = assemblyBaselineCapability(module);
    const restoring = capabilityId === null || capabilityId === undefined || capabilityId === '' || capabilityId === '__baseline__';
    const selected = restoring ? baseline : capabilityId;
    if (!restoring && !assemblyCompatibleOption(module, selected)) throw new Error(`Capability ${selected} 不满足该 replacement slot 的正式接口合同`);
    node.capabilityId = selected;
    state.assemblySelectedNodeId = node.id;
    state.assemblySelectedEdgeId = null;
    state.assemblySelectedScopeId = null;
    state.assemblySelectedScopeProbeId = null;
    state.assemblyPendingConnection = null;
    assemblyInvalidateCompiledState();
    assemblyRender();
    if (selected === baseline) {
      assemblySetStatus(`已恢复 ${node.moduleAlias} 的基线实现 ${baseline || '父适配器内部模块'}；运行前仍会由后端校验 module fingerprint。`, 'success');
    } else {
      assemblySetStatus(`已选择受约束模块 ${selected} → ${node.moduleAlias}。只有接口/单位/时序/求解语义兼容的 allow-list 候选可进入后端编译。`, 'success');
    }
  }

  function assemblyPortsFor(node, direction) {
    const alias = node?.moduleAlias;
    if (!alias) return [];
    const ports = state.assemblyContract?.simulation_ports?.ports || [];
    return ports.filter((port) => port.module_alias === alias && port.direction === direction);
  }

  function assemblyBindingsForPort(portId, direction) {
    const key = direction === 'input' ? 'target_port' : 'source_port';
    return (state.assemblyContract?.bindings || []).filter((binding) => binding.wireable && binding[key] === portId);
  }

  function assemblyEdgeForBinding(bindingId) {
    return (state.assemblyGraph?.edges || []).find((edge) => edge.bindingId === bindingId) || null;
  }

  function assemblyPortEdges(portId, direction) {
    const key = direction === 'input' ? 'targetPort' : 'sourcePort';
    return (state.assemblyGraph?.edges || []).filter((edge) => edge[key] === portId);
  }

  function assemblyPayloadType(payload = {}) {
    const shape = payload.shape || 'scalar';
    const unit = payload.unit || '1';
    if (shape === 'boolean' || payload.dtype === 'bool') return shape === 'timeseries' ? 'timeseries[bool]' : 'bool';
    if (shape === 'object') return 'object';
    if (shape === 'event') return `event[${unit}]`;
    if (shape === 'timeseries') return `timeseries[${unit}]`;
    if (shape === 'scalar_or_series') return `scalar_or_series[${unit}]`;
    return `scalar[${unit}]`;
  }

  function assemblyPortButton(node, port, direction) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `graph-port-button ${direction === 'input' ? 'input' : 'output'}`;
    button.dataset.assemblyNode = node.id;
    button.dataset.assemblyPort = port.port_id;
    const diagnostic = visualPortDiagnostic('assembly', node.id, port.port_id);
    if (diagnostic) button.classList.add('diagnostic-error');
    const connectedEdges = assemblyPortEdges(port.port_id, direction);
    if (connectedEdges.length) button.classList.add('connected');
    const pending = state.assemblyPendingConnection;
    if (pending?.sourcePort === port.port_id && direction === 'output') button.classList.add('pending');
    if (pending && direction === 'input') {
      const compatible = (state.assemblyContract?.bindings || []).some((binding) => (
        binding.wireable
        && binding.source_port === pending.sourcePort
        && binding.target_port === port.port_id
        && !assemblyEdgeForBinding(binding.binding_id)
      ));
      button.classList.add(compatible ? 'compatible' : 'incompatible');
    }
    const dot = document.createElement('span'); dot.className = 'graph-port-dot';
    const label = document.createElement('span'); label.className = 'graph-port-label';
    const localName = String(port.port_id || '').split('.').slice(-2).join('.');
    const bindingCount = assemblyBindingsForPort(port.port_id, direction).length;
    label.textContent = direction === 'output' ? `${localName} → ${bindingCount}` : `${bindingCount} ← ${localName}`;
    const detail = document.createElement('small'); detail.className = 'assembly-port-path';
    detail.textContent = port.path || port.port_id;
    const type = document.createElement('small');
    type.textContent = `${assemblyPayloadType(port.payload)} · ${port.timing?.rate_policy || 'parent-owned'}`;
    if (direction === 'input') button.append(dot, label, detail, type);
    else button.append(label, detail, type, dot);
    button.title = [
      port.port_id,
      port.path || '',
      assemblyPayloadType(port.payload),
      `timing: ${port.timing?.rate_policy || 'parent-owned'}`,
      `state owner: ${port.state_owner || node.moduleAlias}`,
      direction === 'output' ? `fan-out: ${port.fan_out || 'many'}` : `fan-in: ${port.fan_in || 'one'}`,
      direction === 'output' ? `direct feedthrough: ${Boolean(port.direct_feedthrough)}` : '',
      diagnostic ? `运行诊断：${diagnostic.reason || diagnostic.label || '错误定位到该端口'}` : '',
    ].filter(Boolean).join('\n');
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      assemblySelectItem('node', node.id, { render: false });
      if (direction === 'output') assemblyBeginConnection(node.id, port);
      else assemblyCompleteConnection(node.id, port);
    });
    return button;
  }

  function assemblyNodeElement(node) {
    const module = assemblyModuleByAlias(node.moduleAlias) || {};
    const card = document.createElement('article');
    const baselineCapability = assemblyBaselineCapability(module);
    const selectedCapability = node.capabilityId ?? module.capability_id ?? null;
    const isReplacement = Boolean(module.replaceable && selectedCapability !== baselineCapability);
    const diagnostic = visualNodeDiagnostic('assembly', node.id);
    card.className = `graph-node assembly-node${state.assemblySelectedNodeId === node.id ? ' selected' : ''}${assemblySelectionHas('node', node.id) ? ' layout-selected' : ''}${isReplacement ? ' replaced' : ''}${diagnostic ? ' diagnostic-error' : ''}`;
    if (diagnostic) card.title = `运行诊断：${diagnostic.reason || diagnostic.label || '错误定位到该模块'}`;
    card.dataset.assemblyNodeId = node.id;
    card.style.left = `${Number(node.x) || 0}px`;
    card.style.top = `${Number(node.y) || 0}px`;

    const head = document.createElement('div'); head.className = 'graph-node-head';
    const title = document.createElement('div'); title.className = 'graph-node-title';
    const icon = document.createElement('span'); icon.textContent = module.role === 'parent' ? '◆' : module.role === 'dependency' ? '◇' : '○';
    const strong = document.createElement('strong'); strong.textContent = module.name || node.moduleAlias;
    title.append(icon, strong); head.appendChild(title);
    head.addEventListener('pointerdown', (event) => assemblyStartNodeDrag(event, node));

    const body = document.createElement('div'); body.className = 'graph-node-body';
    const role = document.createElement('span'); role.className = `assembly-node-role ${module.role || 'internal'}`;
    role.textContent = module.role === 'parent' ? 'Runtime owner' : module.role === 'dependency' ? 'Registered dependency' : 'Parent-owned internal';
    const alias = document.createElement('strong'); alias.textContent = node.moduleAlias;
    const capability = document.createElement('code'); capability.textContent = selectedCapability || (module.replaceable ? '父适配器内部基线 · 可插拔' : '父适配器内部模块 · 不可替换');
    body.append(role, alias, capability);
    if (isReplacement) { const marker = document.createElement('small'); marker.className = 'assembly-replacement-marker'; marker.textContent = `替代基线 ${baselineCapability || 'parent-managed internal'}`; body.appendChild(marker); }

    const ports = document.createElement('div'); ports.className = 'graph-node-ports';
    const inputs = document.createElement('div'); inputs.className = 'graph-port-column inputs';
    const outputs = document.createElement('div'); outputs.className = 'graph-port-column outputs';
    assemblyPortsFor(node, 'input').forEach((port) => inputs.appendChild(assemblyPortButton(node, port, 'input')));
    assemblyPortsFor(node, 'output').forEach((port) => outputs.appendChild(assemblyPortButton(node, port, 'output')));
    if (!inputs.children.length) { const empty = document.createElement('small'); empty.textContent = '无注册输入'; inputs.appendChild(empty); }
    if (!outputs.children.length) { const empty = document.createElement('small'); empty.textContent = '无注册输出'; outputs.appendChild(empty); }
    ports.append(inputs, outputs);
    card.append(head, body, ports);
    card.addEventListener('click', (event) => {
      if (event.target.closest('.graph-port-button')) return;
      const additive = event.shiftKey || event.metaKey || event.ctrlKey;
      if (additive) assemblySelectItem('node', node.id, { additive: true, toggle: true });
      else if (!assemblySelectionHas('node', node.id) || state.assemblySelection.length <= 1) assemblySelectItem('node', node.id);
      else { assemblySyncPrimarySelection('node', node.id); assemblyRender(); }
    });
    card.addEventListener('dragover', (event) => {
      const dragged = state.assemblyDragModuleCapabilityId || '';
      const compatible = Boolean(dragged && assemblyCompatibleOption(module, dragged));
      if (!dragged) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = compatible ? 'copy' : 'none';
      card.classList.toggle('drop-compatible', compatible);
      card.classList.toggle('drop-incompatible', !compatible);
    });
    card.addEventListener('dragleave', () => card.classList.remove('drop-compatible', 'drop-incompatible'));
    card.addEventListener('drop', (event) => {
      event.preventDefault();
      event.stopPropagation();
      card.classList.remove('drop-compatible', 'drop-incompatible');
      const capabilityId = event.dataTransfer?.getData('text/plain') || state.assemblyDragModuleCapabilityId || '';
      state.assemblyDragModuleCapabilityId = null;
      if (!assemblyCompatibleOption(module, capabilityId)) {
        assemblySetStatus(`${capabilityId || '该模块'} 与槽位 ${node.moduleAlias} 的正式接口不兼容，未执行替换。`, 'error');
        return;
      }
      try { assemblyReplaceModule(node.id, capabilityId); } catch (error) { assemblySetStatus(error.message, 'error'); }
    });
    return card;
  }

  function assemblyScopeInputButton(scope) {
    const meta = assemblyObserverMeta(scope);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'graph-port-button input assembly-scope-input';
    button.dataset.scopeId = scope.id;
    const dot = document.createElement('span'); dot.className = 'graph-port-dot';
    const label = document.createElement('span'); label.className = 'graph-port-label'; label.textContent = 'signal';
    const detail = document.createElement('small'); detail.textContent = `${(scope.probes || []).length}/${meta.maxProbes === 16 ? 'multi' : meta.maxProbes}`;
    button.append(dot, label, detail);
    if (state.assemblyPendingConnection) button.classList.add('compatible');
    button.title = `${meta.title} 观察输入：只读探针，不占用物理 fan-out，不参与求解`;
    button.addEventListener('pointerdown', (event) => event.stopPropagation());
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      assemblyCompleteScopeProbe(scope.id);
    });
    return button;
  }

  function assemblyScopeElement(scope) {
    const meta = assemblyObserverMeta(scope);
    const overlay = assemblyScopeOverlay(scope.id);
    const card = document.createElement('article');
    card.className = `graph-node assembly-node assembly-scope-node assembly-observer-${meta.kind}${state.assemblySelectedScopeId === scope.id ? ' selected' : ''}${assemblySelectionHas('observer', scope.id) ? ' layout-selected' : ''}`;
    card.dataset.scopeId = scope.id;
    card.dataset.observerKind = meta.kind;
    card.style.left = `${Number(scope.x) || 0}px`;
    card.style.top = `${Number(scope.y) || 0}px`;

    const head = document.createElement('div'); head.className = 'graph-node-head';
    const title = document.createElement('div'); title.className = 'graph-node-title';
    const icon = document.createElement('span'); icon.textContent = meta.icon;
    const strong = document.createElement('strong'); strong.textContent = scope.name || meta.title;
    title.append(icon, strong); head.appendChild(title);
    head.addEventListener('pointerdown', (event) => assemblyStartScopeDrag(event, scope));

    const body = document.createElement('div'); body.className = 'graph-node-body';
    const role = document.createElement('span'); role.className = 'assembly-node-role observer'; role.textContent = meta.role;
    const hint = document.createElement('small');
    if (meta.kind === 'display') {
      const primary = overlay?.probes?.[0]?.primary;
      hint.className = 'assembly-display-value';
      hint.textContent = primary ? formatOverlayValue(primary.last, primary.unit) : '运行后显示末值';
    } else if (meta.kind === 'workspace') {
      const resolved = overlay?.resolved_probe_count || 0;
      hint.textContent = resolved ? `${resolved} 路已解析 · 可导出` : '运行后导出 CSV / JSON';
    } else {
      hint.textContent = `${(scope.probes || []).length} 路信号 · 不参与物理求解`;
    }
    body.append(role, hint);

    const ports = document.createElement('div'); ports.className = 'graph-node-ports';
    const inputs = document.createElement('div'); inputs.className = 'graph-port-column inputs';
    inputs.appendChild(assemblyScopeInputButton(scope));
    const outputs = document.createElement('div'); outputs.className = 'graph-port-column outputs';
    const noOutput = document.createElement('small'); noOutput.textContent = '无输出'; outputs.appendChild(noOutput);
    ports.append(inputs, outputs);
    card.append(head, body, ports);
    card.addEventListener('click', (event) => {
      if (event.target.closest('.graph-port-button')) return;
      const additive = event.shiftKey || event.metaKey || event.ctrlKey;
      if (additive) assemblySelectItem('observer', scope.id, { additive: true, toggle: true });
      else if (!assemblySelectionHas('observer', scope.id) || state.assemblySelection.length <= 1) assemblySelectItem('observer', scope.id);
      else { assemblySyncPrimarySelection('observer', scope.id); assemblyRender(); }
    });
    return card;
  }

  function assemblyItemElement(kind, id) {
    if (kind === 'observer') return document.querySelector(`.assembly-scope-node[data-scope-id="${CSS.escape(id)}"]`);
    return document.querySelector(`.assembly-node[data-assembly-node-id="${CSS.escape(id)}"]`);
  }

  function assemblySnap(value, grid = 20) {
    return Math.round((Number(value) || 0) / grid) * grid;
  }

  function assemblyStartItemDrag(event, kind, id) {
    if (event.button !== 0) return;
    event.preventDefault();
    el('assemblyCanvas')?.focus({ preventScroll: true });
    event.stopPropagation();
    if (event.shiftKey || event.metaKey || event.ctrlKey) {
      assemblySelectItem(kind, id, { additive: true, toggle: true });
      return;
    }
    if (!assemblySelectionHas(kind, id)) assemblySelectItem(kind, id, { render: false });
    else assemblySyncPrimarySelection(kind, id);
    const selected = assemblySelectionItems();
    if (!selected.length) return;
    const canvas = el('assemblyCanvas');
    const startX = event.clientX; const startY = event.clientY;
    const origins = selected.map((entry) => {
      const element = assemblyItemElement(entry.kind, entry.id);
      return {
        ...entry,
        x: Number(entry.item.x) || 0,
        y: Number(entry.item.y) || 0,
        width: element?.offsetWidth || (entry.kind === 'observer' ? 205 : 228),
        height: element?.offsetHeight || 150,
      };
    });
    const maxCanvasX = canvas?.clientWidth || 1160;
    const maxCanvasY = canvas?.clientHeight || 620;
    const minDx = Math.max(...origins.map((item) => -item.x));
    const maxDx = Math.min(...origins.map((item) => maxCanvasX - item.width - 8 - item.x));
    const minDy = Math.max(...origins.map((item) => -item.y));
    const maxDy = Math.min(...origins.map((item) => maxCanvasY - item.height - 8 - item.y));
    let moved = false;
    const move = (moveEvent) => {
      let dx = moveEvent.clientX - startX; let dy = moveEvent.clientY - startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      dx = Math.max(minDx, Math.min(maxDx, dx));
      dy = Math.max(minDy, Math.min(maxDy, dy));
      origins.forEach((origin) => {
        origin.item.x = origin.x + dx;
        origin.item.y = origin.y + dy;
        const element = assemblyItemElement(origin.kind, origin.id);
        if (element) { element.style.left = `${origin.item.x}px`; element.style.top = `${origin.item.y}px`; }
      });
      assemblyDrawEdges();
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (moved) origins.forEach((origin) => { origin.item.x = assemblySnap(origin.item.x); origin.item.y = assemblySnap(origin.item.y); });
      assemblyRender();
      if (moved) assemblySetStatus(`已整体移动 ${origins.length} 个对象并吸附到 20px 网格；仅修改工程布局，不影响 TaskSpec 或生成代码。`, 'success');
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up, { once: true });
  }

  function assemblyStartScopeDrag(event, scope) {
    assemblyStartItemDrag(event, 'observer', scope.id);
  }

  function assemblyCompleteScopeProbe(scopeId) {
    const pending = state.assemblyPendingConnection;
    const scope = assemblyScopeById(scopeId);
    if (!scope) return;
    const meta = assemblyObserverMeta(scope);
    if (!pending) {
      assemblySetStatus(`请先点击一个模块输出端口，再点击 ${meta.title} 的 signal 输入。`, 'warning');
      return;
    }
    const sourceNode = assemblyNodeById(pending.sourceId);
    const sourcePort = sourceNode ? assemblyPortsFor(sourceNode, 'output').find((item) => item.port_id === pending.sourcePort) : null;
    if (!sourceNode || !sourcePort) {
      state.assemblyPendingConnection = null;
      assemblySetStatus(`${meta.title} 只能观察正式 simulation_ports 输出端口。`, 'error');
      assemblyRender();
      return;
    }
    scope.probes = Array.isArray(scope.probes) ? scope.probes : [];
    const duplicate = scope.probes.find((item) => item.sourceNode === sourceNode.id && item.sourcePort === sourcePort.port_id);
    if (duplicate) {
      state.assemblyPendingConnection = null;
      state.assemblySelection = [assemblySelectionKey('observer', scope.id)];
      state.assemblySelectedScopeId = scope.id;
      state.assemblySelectedScopeProbeId = duplicate.id;
      assemblySetStatus(`该信号已经接入当前 ${meta.title}。`, 'warning');
      assemblyRender();
      return;
    }
    const probe = {
      id: `probe-${Date.now().toString(36)}-${scope.probes.length + 1}`,
      sourceNode: sourceNode.id,
      sourcePort: sourcePort.port_id,
    };
    if (scope.probes.length >= meta.maxProbes) scope.probes = [probe];
    else scope.probes.push(probe);
    state.assemblyPendingConnection = null;
    state.assemblySelection = [assemblySelectionKey('observer', scope.id)];
    state.assemblySelectedScopeId = scope.id;
    state.assemblySelectedScopeProbeId = probe.id;
    state.assemblySelectedNodeId = null;
    state.assemblySelectedEdgeId = null;
    assemblyRender();
    assemblySetStatus(`${meta.title} 已接入 ${sourceNode.moduleAlias}:${sourcePort.port_id}。这是只读观察连接，不改变物理 binding、TaskSpec 或生成代码。`, 'success');
    const existingRunId = visualOverlayForMode('assembly')?.run_id;
    if (existingRunId) loadVisualRunOverlay(existingRunId, { mode: 'assembly', assemblyGraph: clone(state.assemblyGraph), taskSpec: state.taskSpec ? clone(state.taskSpec) : null, snapshot: assemblySnapshot() }).catch(() => null);
  }

  function assemblyStartNodeDrag(event, node) {
    assemblyStartItemDrag(event, 'node', node.id);
  }

  function assemblyBeginMarquee(event) {
    if (event.button !== 0 || !state.assemblyGraph) return;
    el('assemblyCanvas')?.focus({ preventScroll: true });
    if (event.target.closest('.assembly-node, .graph-port-button, button, input, select')) return;
    const canvas = el('assemblyCanvas'); const box = el('assemblySelectionBox');
    if (!canvas || !box) return;
    const rect = canvas.getBoundingClientRect();
    const startX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const startY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
    const additive = event.shiftKey || event.metaKey || event.ctrlKey;
    let moved = false;
    box.classList.remove('hidden');
    box.style.left = `${startX}px`; box.style.top = `${startY}px`; box.style.width = '0px'; box.style.height = '0px';
    const move = (moveEvent) => {
      const x = Math.max(0, Math.min(rect.width, moveEvent.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, moveEvent.clientY - rect.top));
      const left = Math.min(startX, x); const top = Math.min(startY, y);
      const width = Math.abs(x - startX); const height = Math.abs(y - startY);
      moved = moved || width > 5 || height > 5;
      box.style.left = `${left}px`; box.style.top = `${top}px`; box.style.width = `${width}px`; box.style.height = `${height}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      const br = box.getBoundingClientRect();
      box.classList.add('hidden');
      if (!moved) { if (!additive) assemblyClearSelection(); return; }
      const selected = additive ? [...state.assemblySelection] : [];
      document.querySelectorAll('#assemblyNodes > .assembly-node').forEach((card) => {
        const cr = card.getBoundingClientRect();
        const intersects = !(cr.right < br.left || cr.left > br.right || cr.bottom < br.top || cr.top > br.bottom);
        if (!intersects) return;
        const kind = card.dataset.scopeId ? 'observer' : 'node';
        const id = card.dataset.scopeId || card.dataset.assemblyNodeId;
        const key = assemblySelectionKey(kind, id);
        if (id && !selected.includes(key)) selected.push(key);
      });
      state.assemblySelection = selected;
      const last = assemblySelectionItems().slice(-1)[0] || null;
      assemblySyncPrimarySelection(last?.kind || null, last?.id || null);
      state.assemblySuppressCanvasClick = true;
      assemblyRender();
      assemblySetStatus(selected.length ? `框选 ${selected.length} 个对象；可整体拖动或使用布局工具。` : '框选范围内没有对象。');
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up, { once: true });
  }

  function assemblyClampLayoutItem(entry) {
    const canvas = el('assemblyCanvas'); const element = assemblyItemElement(entry.kind, entry.id);
    const width = element?.offsetWidth || (entry.kind === 'observer' ? 205 : 228);
    const height = element?.offsetHeight || 150;
    const maxX = Math.max(0, (canvas?.clientWidth || 1160) - width - 8);
    const maxY = Math.max(0, (canvas?.clientHeight || 620) - height - 8);
    entry.item.x = assemblySnap(Math.max(0, Math.min(maxX, Number(entry.item.x) || 0)));
    entry.item.y = assemblySnap(Math.max(0, Math.min(maxY, Number(entry.item.y) || 0)));
  }

  function assemblyAlignSelection(axis) {
    const items = assemblySelectionItems();
    if (items.length < 2) { assemblySetStatus('至少选择 2 个对象才能对齐。', 'warning'); return; }
    if (axis === 'left') { const value = Math.min(...items.map((entry) => Number(entry.item.x) || 0)); items.forEach((entry) => { entry.item.x = value; assemblyClampLayoutItem(entry); }); }
    else if (axis === 'top') { const value = Math.min(...items.map((entry) => Number(entry.item.y) || 0)); items.forEach((entry) => { entry.item.y = value; assemblyClampLayoutItem(entry); }); }
    else if (axis === 'center-y') { const value = items.reduce((sum, entry) => sum + (Number(entry.item.y) || 0), 0) / items.length; items.forEach((entry) => { entry.item.y = value; assemblyClampLayoutItem(entry); }); }
    assemblyRender();
    assemblySetStatus(`已${axis === 'left' ? '左对齐' : axis === 'top' ? '顶对齐' : '水平中线对齐'} ${items.length} 个对象；只修改布局。`, 'success');
  }

  function assemblyDistributeSelection(axis) {
    const items = assemblySelectionItems();
    if (items.length < 3) { assemblySetStatus('至少选择 3 个对象才能等间距排列。', 'warning'); return; }
    const key = axis === 'x' ? 'x' : 'y';
    items.sort((a, b) => (Number(a.item[key]) || 0) - (Number(b.item[key]) || 0));
    const first = Number(items[0].item[key]) || 0; const last = Number(items[items.length - 1].item[key]) || 0;
    const step = (last - first) / (items.length - 1);
    items.forEach((entry, index) => { entry.item[key] = first + step * index; assemblyClampLayoutItem(entry); });
    assemblyRender();
    assemblySetStatus(`已按${axis === 'x' ? '水平方向' : '垂直方向'}等间距排列 ${items.length} 个对象；只修改布局。`, 'success');
  }

  function assemblyStronglyConnectedLevels() {
    const nodes = state.assemblyGraph?.nodes || []; const edges = state.assemblyGraph?.edges || [];
    const ids = nodes.map((node) => node.id); const adjacency = new Map(ids.map((id) => [id, []]));
    edges.forEach((edge) => { if (adjacency.has(edge.source) && adjacency.has(edge.target)) adjacency.get(edge.source).push(edge.target); });
    let index = 0; const stack = []; const onStack = new Set(); const indexes = new Map(); const low = new Map(); const components = [];
    const visit = (id) => {
      indexes.set(id, index); low.set(id, index); index += 1; stack.push(id); onStack.add(id);
      (adjacency.get(id) || []).forEach((next) => {
        if (!indexes.has(next)) { visit(next); low.set(id, Math.min(low.get(id), low.get(next))); }
        else if (onStack.has(next)) low.set(id, Math.min(low.get(id), indexes.get(next)));
      });
      if (low.get(id) === indexes.get(id)) {
        const component = []; let current; do { current = stack.pop(); onStack.delete(current); component.push(current); } while (current !== id); components.push(component);
      }
    };
    ids.forEach((id) => { if (!indexes.has(id)) visit(id); });
    const componentByNode = new Map(); components.forEach((component, ci) => component.forEach((id) => componentByNode.set(id, ci)));
    const dag = new Map(components.map((_, ci) => [ci, new Set()])); const indegree = new Map(components.map((_, ci) => [ci, 0]));
    edges.forEach((edge) => {
      const a = componentByNode.get(edge.source); const b = componentByNode.get(edge.target);
      if (a === undefined || b === undefined || a === b || dag.get(a).has(b)) return;
      dag.get(a).add(b); indegree.set(b, indegree.get(b) + 1);
    });
    const queue = [...indegree.entries()].filter(([, degree]) => degree === 0).map(([ci]) => ci); const levels = new Map(queue.map((ci) => [ci, 0]));
    while (queue.length) {
      const ci = queue.shift();
      dag.get(ci).forEach((next) => { levels.set(next, Math.max(levels.get(next) || 0, (levels.get(ci) || 0) + 1)); indegree.set(next, indegree.get(next) - 1); if (indegree.get(next) === 0) queue.push(next); });
    }
    return { components, levels };
  }

  function assemblyAutoLayout() {
    if (!state.assemblyGraph?.nodes?.length) { assemblySetStatus('当前没有可布局的装配模块。', 'warning'); return; }
    const { components, levels } = assemblyStronglyConnectedLevels();
    const byLevel = new Map(); components.forEach((component, ci) => { const level = levels.get(ci) || 0; if (!byLevel.has(level)) byLevel.set(level, []); byLevel.get(level).push(component); });
    [...byLevel.entries()].sort((a, b) => a[0] - b[0]).forEach(([level, groups]) => {
      let row = 0;
      groups.sort((a, b) => Math.min(...a.map((id) => Number(assemblyNodeById(id)?.y) || 0)) - Math.min(...b.map((id) => Number(assemblyNodeById(id)?.y) || 0)));
      groups.forEach((group) => {
        group.sort((a, b) => (Number(assemblyNodeById(a)?.y) || 0) - (Number(assemblyNodeById(b)?.y) || 0)).forEach((id) => {
          const node = assemblyNodeById(id); if (!node) return; node.x = 30 + level * 270; node.y = 45 + row * 175; row += 1;
        });
      });
    });
    const maxLevel = Math.max(0, ...[...levels.values()]);
    assemblyScopes().forEach((scope, index) => { scope.x = Math.min(930, 40 + (maxLevel + 1) * 270); scope.y = 45 + index * 155; });
    [...state.assemblyGraph.nodes.map((node) => ({ kind: 'node', id: node.id, item: node })), ...assemblyScopes().map((scope) => ({ kind: 'observer', id: scope.id, item: scope }))].forEach(assemblyClampLayoutItem);
    assemblyRender();
    assemblySetStatus('已按注册信号拓扑自动布局；反馈环作为同一拓扑组排列，观察器放在右侧。仅修改工程坐标。', 'success');
  }

  function assemblyCopySelectedObservers() {
    const observers = assemblySelectionItems().filter((entry) => entry.kind === 'observer').map((entry) => clone(entry.item));
    if (!observers.length) { assemblySetStatus('物理模块由注册合同唯一约束，不能复制；请选择 Scope / Display / To Workspace 后再复制。', 'warning'); return false; }
    state.assemblyClipboard = observers;
    assemblySetStatus(`已复制 ${observers.length} 个观察器；物理模块不会进入剪贴板。`, 'success');
    return true;
  }

  function assemblyPasteObservers() {
    if (!state.assemblyGraph || !state.assemblyClipboard.length) { assemblySetStatus('观察器剪贴板为空。', 'warning'); return; }
    const added = [];
    state.assemblyClipboard.forEach((source, index) => {
      const meta = assemblyObserverMeta(source); const id = `${meta.kind}-${Date.now().toString(36)}-${index + 1}`;
      const observer = { ...clone(source), id, name: `${source.name || meta.title} 副本`, x: assemblySnap((Number(source.x) || 0) + 40), y: assemblySnap((Number(source.y) || 0) + 40), probes: clone(source.probes || []) };
      assemblyScopes().push(observer); added.push(observer);
    });
    state.assemblySelection = added.map((item) => assemblySelectionKey('observer', item.id));
    const last = added.slice(-1)[0]; assemblySyncPrimarySelection(last ? 'observer' : null, last?.id || null);
    state.assemblyClipboard = added.map((item) => clone(item));
    assemblyRender();
    assemblySetStatus(`已粘贴 ${added.length} 个观察器并保留其探针连接；不改变物理装配。`, 'success');
  }

  function assemblyDuplicateSelectedObservers() {
    if (assemblyCopySelectedObservers()) assemblyPasteObservers();
  }

  function assemblyBeginConnection(sourceId, port) {
    const candidates = assemblyBindingsForPort(port.port_id, 'output');
    const available = candidates.filter((binding) => !assemblyEdgeForBinding(binding.binding_id));
    state.assemblyPendingConnection = { sourceId, sourcePort: port.port_id };
    const targets = available.map((binding) => `${binding.target_alias}:${binding.target_port}`);
    const targetText = targets.length ? `可连接注册目标：${targets.join('、')}；也可接 Scope / Display / To Workspace。` : '物理 binding 已全部建立；仍可接入显式只读观察器。';
    assemblySetStatus(`正在从 ${port.port_id} 接线。${targetText}`);
    assemblyRender();
  }

  function assemblyCompleteConnection(targetId, port) {
    const pending = state.assemblyPendingConnection;
    if (!pending) {
      assemblySetStatus('请先点击一个还有可用 fan-out 的输出端口。', 'warning');
      return;
    }
    const sourceNode = assemblyNodeById(pending.sourceId);
    const targetNode = assemblyNodeById(targetId);
    const binding = (state.assemblyContract?.bindings || []).find((item) => (
      item.wireable
      && item.source_port === pending.sourcePort
      && item.target_port === port.port_id
      && !assemblyEdgeForBinding(item.binding_id)
    ));
    if (!binding || !sourceNode || !targetNode || sourceNode.moduleAlias !== binding.source_alias || targetNode.moduleAlias !== binding.target_alias) {
      assemblySetStatus('该输入端口与当前输出端口之间没有注册 binding；不允许绕过 simulation_ports contract。', 'error');
      return;
    }
    state.assemblyGraph.edges.push({
      id: `edge-${binding.binding_id}`,
      bindingId: binding.binding_id,
      source: sourceNode.id,
      sourcePort: binding.source_port,
      target: targetNode.id,
      targetPort: binding.target_port,
      dataType: binding.data_type,
    });
    state.assemblyPendingConnection = null;
    state.assemblySelectedEdgeId = `edge-${binding.binding_id}`;
    state.assemblySelectedNodeId = null;
    state.assemblySelectedScopeId = null;
    state.assemblySelectedScopeProbeId = null;
    assemblyInvalidateCompiledState();
    assemblySetStatus(`已恢复注册 coupling：${binding.source_path} → ${binding.target_path}`, 'success');
    assemblyRender();
  }

  function assemblyRemoveEdge(edgeId) {
    if (!state.assemblyGraph) return;
    const edge = state.assemblyGraph.edges.find((item) => item.id === edgeId);
    state.assemblyGraph.edges = state.assemblyGraph.edges.filter((item) => item.id !== edgeId);
    state.assemblySelectedEdgeId = null;
    state.assemblyPendingConnection = null;
    assemblyInvalidateCompiledState();
    const binding = edge ? assemblyBindingById(edge.bindingId) : null;
    assemblySetStatus(binding ? `已断开 ${binding.source_path} → ${binding.target_path}；后端校验会阻止缺失 required binding 的装配运行。` : '已断开信号。', 'warning');
    assemblyRender();
  }

  function assemblyRestoreBindings() {
    if (!state.assemblyContract || !state.assemblyGraph) return;
    state.assemblyGraph.edges = clone(state.assemblyContract.default_graph.edges || []);
    state.assemblyPendingConnection = null;
    state.assemblySelectedEdgeId = null;
    assemblyInvalidateCompiledState();
    assemblySetStatus('已恢复当前 simulation_ports contract 声明的全部注册连线。', 'success');
    assemblyRender();
  }

  function assemblyPortElement(nodeId, portId) {
    return $all('.graph-port-button', el('assemblyNodes')).find((item) => item.dataset.assemblyNode === nodeId && item.dataset.assemblyPort === portId) || null;
  }

  function assemblyScopeInputElement(scopeId) {
    return $all('.assembly-scope-input', el('assemblyNodes')).find((item) => item.dataset.scopeId === scopeId) || null;
  }

  function assemblyFeedbackPairs() {
    const edges = state.assemblyGraph?.edges || [];
    const pairs = new Set();
    edges.forEach((edge) => {
      if (edges.some((other) => other.source === edge.target && other.target === edge.source)) pairs.add(`${edge.source}->${edge.target}`);
    });
    return pairs;
  }

  function assemblyDrawEdges() {
    const svg = el('assemblyEdges'); const canvas = el('assemblyCanvas');
    if (!svg || !canvas || !state.assemblyGraph) return;
    svg.replaceChildren();
    svg.setAttribute('viewBox', `0 0 ${canvas.clientWidth || 1160} ${canvas.clientHeight || 620}`);
    const rect = canvas.getBoundingClientRect();
    const feedbackPairs = assemblyFeedbackPairs();
    (state.assemblyGraph.edges || []).forEach((edge) => {
      const source = assemblyPortElement(edge.source, edge.sourcePort);
      const target = assemblyPortElement(edge.target, edge.targetPort);
      if (!source || !target) return;
      const sr = source.getBoundingClientRect(); const tr = target.getBoundingClientRect();
      const sx = sr.right - rect.left; const sy = sr.top + sr.height / 2 - rect.top;
      const tx = tr.left - rect.left; const ty = tr.top + tr.height / 2 - rect.top;
      const bend = Math.max(55, Math.min(180, Math.abs(tx - sx) * 0.45));
      const d = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('d', d); hit.setAttribute('class', 'graph-edge-hit'); hit.setAttribute('tabindex', '0');
      hit.addEventListener('click', () => { state.assemblySelection = []; state.assemblySelectedEdgeId = edge.id; state.assemblySelectedNodeId = null; state.assemblySelectedScopeId = null; state.assemblySelectedScopeProbeId = null; state.assemblyPendingConnection = null; assemblyRender(); });
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const binding = assemblyBindingById(edge.bindingId);
      const solverPolicy = binding?.solver?.policy || 'parent_owned';
      const feedback = feedbackPairs.has(`${edge.source}->${edge.target}`) || ['parent_managed_feedback', 'parent_managed_stateful_feedback', 'delayed_feedback'].includes(solverPolicy);
      path.setAttribute('d', d);
      const diagnostic = visualEdgeDiagnostic('assembly', edge.id);
      path.setAttribute('class', `assembly-edge${state.assemblySelectedEdgeId === edge.id ? ' selected' : ''}${feedback ? ' assembly-edge-feedback' : ''}${diagnostic ? ' diagnostic-error' : ''}`);
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', String((sx + tx) / 2)); label.setAttribute('y', String((sy + ty) / 2 - 5));
      label.setAttribute('class', `graph-edge-label${state.assemblySelectedEdgeId === edge.id ? ' selected' : ''}`);
      label.textContent = `${feedback ? '↻ ' : ''}${binding?.data_type || edge.dataType || 'signal'}`;
      svg.append(path, hit, label);
    });
    assemblyScopes().forEach((scope) => {
      const target = assemblyScopeInputElement(scope.id);
      if (!target) return;
      const tr = target.getBoundingClientRect();
      const tx = tr.left - rect.left; const tyBase = tr.top + tr.height / 2 - rect.top;
      (scope.probes || []).forEach((probe, index) => {
        const source = assemblyPortElement(probe.sourceNode, probe.sourcePort);
        if (!source) return;
        const sr = source.getBoundingClientRect();
        const sx = sr.right - rect.left; const sy = sr.top + sr.height / 2 - rect.top;
        const ty = tyBase + (index - ((scope.probes || []).length - 1) / 2) * 7;
        const bend = Math.max(55, Math.min(180, Math.abs(tx - sx) * 0.45));
        const d = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', d); path.setAttribute('class', 'assembly-scope-probe');
        const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        hit.setAttribute('d', d); hit.setAttribute('class', 'graph-edge-hit'); hit.setAttribute('tabindex', '0');
        hit.addEventListener('click', () => {
          state.assemblySelection = [assemblySelectionKey('observer', scope.id)];
          state.assemblySelectedScopeId = scope.id;
          state.assemblySelectedScopeProbeId = probe.id;
          state.assemblySelectedNodeId = null;
          state.assemblySelectedEdgeId = null;
          assemblyRender();
        });
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', String((sx + tx) / 2)); label.setAttribute('y', String((sy + ty) / 2 - 5));
        label.setAttribute('class', 'assembly-scope-probe-label'); label.textContent = assemblyObserverMeta(scope).title;
        svg.append(path, hit, label);
      });
    });
  }

  function assemblyRenderRuntimeParameters(container) {
    if (!container || !state.formData) return;
    const simulation = state.formData.simulation || (state.formData.simulation = {});
    const section = document.createElement('section'); section.className = 'graph-inspector-section assembly-runtime-parameters';
    const title = document.createElement('h4'); title.textContent = '运行参数';
    const hint = document.createElement('p'); hint.textContent = '像属性面板一样直接修改常用仿真参数；修改后重新“构建并运行”即可。';
    const grid = document.createElement('div'); grid.className = 'assembly-runtime-grid';
    [
      ['duration_s', '仿真时长 (s)', 0.001],
      ['step_s', '积分步长 (s)', 0.000001],
      ['sample_s', '输出采样 (s)', 0.000001],
    ].forEach(([key, labelText, min]) => {
      const label = document.createElement('label');
      const text = document.createElement('span'); text.textContent = labelText;
      const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.min = String(min); input.value = simulation[key] ?? '';
      input.addEventListener('change', () => {
        const value = Number(input.value);
        if (!Number.isFinite(value) || value <= 0) { input.value = simulation[key] ?? ''; assemblySetStatus(`${labelText} 必须为正数。`, 'error'); return; }
        simulation[key] = value;
        assemblyInvalidateCompiledState();
        assemblySetStatus(`已更新 ${labelText}=${value}；点击“构建并运行”会重新编译。`, 'success');
        assemblyRenderInspector();
      });
      label.append(text, input); grid.appendChild(label);
    });
    section.append(title, hint, grid); container.appendChild(section);
  }

  function assemblyObserverResolvedSignals(scope) {
    const overlay = assemblyScopeOverlay(scope.id);
    const fields = [];
    (overlay?.probes || []).forEach((probe) => (probe.signals || []).forEach((signal) => {
      if (signal?.field && !fields.some((item) => item.field === signal.field)) fields.push(signal);
    }));
    return fields;
  }

  async function assemblyExportWorkspace(scope, format = 'csv') {
    const overlay = visualOverlayForMode('assembly');
    if (!overlay?.run_id) throw new Error('当前没有可导出的 Run；请先构建并运行模型。');
    if (assemblyObserverKind(scope) !== 'workspace') throw new Error('只有 To Workspace 观察器可以导出数据。');
    if (!assemblyObserverResolvedSignals(scope).length) throw new Error('当前 To Workspace 没有已解析的运行信号；请先运行模型并确认端口已记录 telemetry。');
    const fallback = `${String(scope.name || 'workspace').replace(/[^A-Za-z0-9_.-]+/g, '_') || 'workspace'}_${overlay.run_id}.${format}`;
    const result = await postDownloadEndpoint('/visual-composer/export-observer-data', {
      run_id: overlay.run_id,
      assembly_graph: clone(state.assemblyGraph),
      observer_id: scope.id,
      format,
    }, fallback);
    assemblySetStatus(`已导出 ${result.filename}；数据来自 Run Bundle 已记录 telemetry，未改变物理模型或 recorder。`, 'success');
  }

  function assemblyRenderScopeInspector(container, scope) {
    const meta = assemblyObserverMeta(scope);
    const section = document.createElement('section'); section.className = `graph-inspector-section assembly-scope-inspector assembly-observer-inspector ${meta.kind}`;
    const title = document.createElement('h4'); title.textContent = meta.kind === 'scope' ? 'Scope 示波器' : meta.kind === 'display' ? 'Display 数值显示' : 'To Workspace 数据导出';
    const descriptions = {
      scope: 'Scope 是只读波形观察器：只读取 Run telemetry，不参与物理求解，不占用 simulation_ports 的 fan-out。',
      display: 'Display 是只读数值观察器：只显示所接信号在最近一次 Run 的末值；普通物理模块仍不承担结果展示。',
      workspace: 'To Workspace 是只读数据导出观察器：从 Run Bundle 已记录 telemetry 中导出所接信号，不改变 recorder、TaskSpec 或求解器。',
    };
    const note = document.createElement('p'); note.textContent = descriptions[meta.kind];
    const nameLabel = document.createElement('label'); nameLabel.className = 'assembly-scope-name';
    const nameText = document.createElement('span'); nameText.textContent = '名称';
    const nameInput = document.createElement('input'); nameInput.type = 'text'; nameInput.value = scope.name || meta.title; nameInput.maxLength = 80;
    nameInput.addEventListener('change', () => { scope.name = nameInput.value.trim() || meta.title; assemblyRender(); });
    nameLabel.append(nameText, nameInput);
    section.append(title, note, nameLabel);

    const probes = document.createElement('div'); probes.className = 'assembly-scope-probes';
    const overlay = assemblyScopeOverlay(scope.id);
    (scope.probes || []).forEach((probe, index) => {
      const sourceNode = assemblyNodeById(probe.sourceNode);
      const sourcePort = sourceNode ? assemblyPortsFor(sourceNode, 'output').find((item) => item.port_id === probe.sourcePort) : null;
      const probeOverlay = (overlay?.probes || []).find((item) => item.probe_id === probe.id) || null;
      const card = document.createElement('div'); card.className = `assembly-scope-probe-card${state.assemblySelectedScopeProbeId === probe.id ? ' selected' : ''}`;
      const heading = document.createElement('div'); heading.className = 'assembly-scope-probe-heading';
      const label = document.createElement('strong'); label.textContent = `${index + 1}. ${sourceNode?.moduleAlias || probe.sourceNode}`;
      const port = document.createElement('code'); port.textContent = sourcePort?.port_id || probe.sourcePort;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-ghost'; remove.textContent = '移除'; remove.addEventListener('click', () => assemblyRemoveScopeProbe(scope.id, probe.id));
      heading.append(label, remove); card.append(heading, port);
      const signals = probeOverlay?.signals || [];
      if (signals.length) {
        if (meta.kind === 'scope') {
          const key = `scope:${scope.id}:${probe.id}`;
          const selectedField = state.visualNodeSeriesField[key] && signals.some((item) => item.field === state.visualNodeSeriesField[key]) ? state.visualNodeSeriesField[key] : signals[0].field;
          state.visualNodeSeriesField[key] = selectedField;
          const chips = document.createElement('div'); chips.className = 'visual-series-selector';
          signals.forEach((item) => {
            const button = document.createElement('button'); button.type = 'button'; button.className = `visual-series-chip${item.field === selectedField ? ' active' : ''}`; button.textContent = item.field.split('.').slice(-2).join('.');
            button.addEventListener('click', () => { state.visualNodeSeriesField[key] = item.field; state.assemblySelectedScopeProbeId = probe.id; assemblyRenderInspector(); }); chips.appendChild(button);
          });
          card.appendChild(chips);
          const selected = signals.find((item) => item.field === selectedField) || signals[0];
          const summary = document.createElement('div'); summary.className = 'visual-series-summary'; summary.textContent = `${selected.field} · last ${formatOverlayValue(selected.last, selected.unit)} · min ${formatOverlayValue(selected.min, selected.unit)} · max ${formatOverlayValue(selected.max, selected.unit)} · samples ${selected.count}`; card.appendChild(summary);
          const canvas = document.createElement('canvas'); canvas.className = 'visual-mini-chart assembly-scope-chart'; canvas.setAttribute('aria-label', `${selected.field} Scope 时间序列`); card.appendChild(canvas); requestAnimationFrame(() => drawVisualMiniSeries(canvas, selected.field));
          const open = document.createElement('button'); open.type = 'button'; open.className = 'button button-ghost'; open.textContent = '在结果界面打开'; open.addEventListener('click', async () => { state.selectedSeries = [selected.field]; await openVisualRunResults('assembly'); renderChartControls(); drawChart(); }); card.appendChild(open);
        } else if (meta.kind === 'display') {
          const values = document.createElement('div'); values.className = 'assembly-display-values';
          signals.forEach((item) => {
            const row = document.createElement('div'); row.className = 'assembly-display-row';
            const field = document.createElement('code'); field.textContent = item.field;
            const value = document.createElement('strong'); value.textContent = formatOverlayValue(item.last, item.unit);
            row.append(field, value); values.appendChild(row);
          });
          card.appendChild(values);
        } else {
          const summary = document.createElement('div'); summary.className = 'visual-series-summary';
          summary.textContent = `${signals.map((item) => item.field).join(' · ')} · ${(signals[0]?.count || 0)} samples 可导出`;
          card.appendChild(summary);
        }
      } else {
        const empty = document.createElement('small'); empty.className = 'assembly-scope-unresolved';
        if (probeOverlay?.unresolved_reason === 'scope_endpoint_mismatch') empty.textContent = `${meta.title} 工程端点不一致：sourceNode 与 sourcePort 不属于同一注册模块。请移除此 probe 后重新连接。`;
        else empty.textContent = overlay ? '本次 Run telemetry 中没有解析到该端口对应的数值序列。' : `运行后在此解析信号；${meta.title} 不会改变仿真输出。`;
        card.appendChild(empty);
      }
      card.addEventListener('click', (event) => { if (event.target.closest('button')) return; state.assemblySelectedScopeProbeId = probe.id; });
      probes.appendChild(card);
    });
    if (!(scope.probes || []).length) {
      const empty = document.createElement('div'); empty.className = 'graph-inspector-empty'; empty.textContent = `尚未接入信号。点击模块输出端口，再点击 ${meta.title} 的 signal 输入。`; probes.appendChild(empty);
    }
    section.appendChild(probes);
    const actions = document.createElement('div'); actions.className = 'graph-inspector-actions';
    const results = document.createElement('button'); results.type = 'button'; results.className = 'button button-secondary'; results.textContent = '打开运行结果'; results.disabled = !visualOverlayForMode('assembly')?.run_id; results.addEventListener('click', () => openVisualRunResults('assembly'));
    actions.appendChild(results);
    if (meta.kind === 'workspace') {
      const csv = document.createElement('button'); csv.type = 'button'; csv.className = 'button button-secondary'; csv.textContent = '导出 CSV'; csv.disabled = !assemblyObserverResolvedSignals(scope).length; csv.addEventListener('click', async () => { try { await assemblyExportWorkspace(scope, 'csv'); } catch (error) { assemblySetStatus(error.message, 'error'); } });
      const json = document.createElement('button'); json.type = 'button'; json.className = 'button button-ghost'; json.textContent = '导出 JSON'; json.disabled = !assemblyObserverResolvedSignals(scope).length; json.addEventListener('click', async () => { try { await assemblyExportWorkspace(scope, 'json'); } catch (error) { assemblySetStatus(error.message, 'error'); } });
      actions.append(csv, json);
    }
    const removeScope = document.createElement('button'); removeScope.type = 'button'; removeScope.className = 'button button-danger'; removeScope.textContent = `删除 ${meta.title}`; removeScope.addEventListener('click', () => assemblyRemoveScope(scope.id));
    actions.appendChild(removeScope); section.appendChild(actions);
    container.appendChild(section);
  }

  function assemblyRenderInspector() {
    const container = el('assemblyInspector'); const badge = el('assemblyInspectorBadge');
    if (!container || !badge) return;
    container.replaceChildren();
    if (!state.assemblyContract || !state.assemblyGraph) {
      badge.textContent = '未加载';
      const empty = document.createElement('div'); empty.className = 'graph-inspector-empty'; empty.textContent = '请选择一个注册装配。'; container.appendChild(empty); return;
    }
    assemblyRenderRuntimeParameters(container);
    if (state.assemblySelection.length > 1) {
      const section = document.createElement('section'); section.className = 'graph-inspector-section assembly-multiselect-summary';
      const title = document.createElement('h4'); title.textContent = assemblySelectionLabel();
      const note = document.createElement('p'); note.textContent = '多选仅用于布局与观察器复制，不改变注册物理模块数量。拖动任一已选对象可整体移动。';
      section.append(title, note); container.appendChild(section);
    }
    const edge = state.assemblyGraph.edges.find((item) => item.id === state.assemblySelectedEdgeId);
    const node = assemblyNodeById(state.assemblySelectedNodeId);
    const scope = assemblyScopeById(state.assemblySelectedScopeId);
    if (scope) {
      badge.textContent = assemblyObserverMeta(scope).title;
      assemblyRenderScopeInspector(container, scope);
    } else if (edge) {
      const binding = assemblyBindingById(edge.bindingId) || {};
      badge.textContent = 'Signal binding';
      const section = document.createElement('section'); section.className = 'graph-inspector-section';
      const title = document.createElement('h4'); title.textContent = `${binding.source_alias || '?'} → ${binding.target_alias || '?'}`;
      const card = document.createElement('div'); card.className = 'assembly-binding-card';
      const source = document.createElement('code'); source.textContent = `${binding.source_port || edge.sourcePort} · ${binding.source_path || ''}`;
      const arrow = document.createElement('span'); arrow.textContent = '↓ registered transform / coupling';
      const target = document.createElement('code'); target.textContent = `${binding.target_port || edge.targetPort} · ${binding.target_path || ''}`;
      const type = document.createElement('strong'); type.textContent = binding.data_type || edge.dataType || 'signal';
      const transform = document.createElement('span'); transform.textContent = `transform: ${binding.transform?.policy || binding.policy || 'registered_mapping'}`;
      const solver = document.createElement('span'); solver.textContent = `solver: ${binding.solver?.policy || 'parent_owned'} · delay=${binding.solver?.delay_steps || 0}`;
      const timing = document.createElement('span'); timing.textContent = `timing: ${binding.source_timing?.rate_policy || 'parent_owned'} → ${binding.target_timing?.rate_policy || 'parent_owned'}`;
      const ownership = document.createElement('span'); ownership.textContent = `state: ${binding.source_state_owner || binding.source_alias || '?'} → ${binding.target_state_owner || binding.target_alias || '?'} · direct-feedthrough=${Boolean(binding.source_direct_feedthrough)}`;
      const desc = document.createElement('p'); desc.textContent = binding.description || '该耦合来自父 Capability contract。';
      card.append(source, arrow, target, type, transform, solver, timing, ownership, desc);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-danger'; remove.textContent = '断开此注册连线'; remove.addEventListener('click', () => assemblyRemoveEdge(edge.id));
      section.append(title, card, remove); container.appendChild(section);
    } else if (node) {
      const module = assemblyModuleByAlias(node.moduleAlias) || {};
      badge.textContent = module.role || 'module';
      const section = document.createElement('section'); section.className = 'graph-inspector-section';
      const title = document.createElement('h4'); title.textContent = module.name || node.moduleAlias;
      const meta = document.createElement('div'); meta.className = 'graph-inspector-meta';
      const baselineCapability = assemblyBaselineCapability(module);
      const selectedCapability = node.capabilityId ?? module.capability_id ?? null;
      const rows = [
        ['alias', node.moduleAlias], ['role', module.role || 'internal'], ['当前 Capability', selectedCapability || '父适配器内部模块'],
        ['基线 Capability', baselineCapability || '父适配器内部模块'],
        ['输入端口', String(assemblyPortsFor(node, 'input').length)], ['输出端口', String(assemblyPortsFor(node, 'output').length)],
        ['槽位类型', module.replacement_slot_kind || (module.role === 'dependency' ? 'dependency' : 'parent-owned')],
        ['替换策略', module.replaceable ? 'module library + interface contract' : '禁止任意替换'],
      ];
      rows.forEach(([key, value]) => { const row = document.createElement('div'); const k = document.createElement('span'); k.textContent = key; const v = document.createElement('strong'); v.textContent = value; row.append(k, v); meta.appendChild(row); });
      section.append(title, meta);
      if (module.replaceable) {
        const replacement = document.createElement('div'); replacement.className = 'assembly-replacement-card';
        const label = document.createElement('label'); label.textContent = '兼容替代模块';
        const select = document.createElement('select'); select.className = 'assembly-replacement-select'; select.setAttribute('aria-label', `${node.moduleAlias} 替代模块`);
        const baselineOption = document.createElement('option'); baselineOption.value = '__baseline__'; baselineOption.textContent = baselineCapability ? `${baselineCapability} · baseline` : '父适配器内部实现 · baseline'; select.appendChild(baselineOption);
        (module.replacement_options || []).filter((item) => item.compatible).forEach((item) => {
          if (item.capability_id === baselineCapability) return;
          const option = document.createElement('option'); option.value = item.capability_id; option.textContent = `${item.name || item.capability_id}${item.is_baseline ? ' · baseline' : ' · compatible'}`; select.appendChild(option);
        });
        select.value = selectedCapability === baselineCapability ? '__baseline__' : (selectedCapability || '__baseline__');
        select.addEventListener('change', () => { try { assemblyReplaceModule(node.id, select.value === '__baseline__' ? null : select.value); } catch (error) { assemblySetStatus(error.message, 'error'); assemblyRender(); } });
        const hint = document.createElement('small'); hint.textContent = `interface ${module.replacement_interface_id || '未声明'} · 注册候选 ${(module.replacement_options || []).filter((item) => item.compatible).length} · 可从上方模块库拖入`;
        replacement.append(label, select, hint); section.appendChild(replacement);
      }
      const note = document.createElement('p'); note.textContent = module.role === 'parent'
        ? '该节点拥有实际 runtime；装配图最终由此注册 Capability adapter 执行。'
        : module.role === 'dependency'
          ? (module.replaceable ? '该依赖开放 受约束替换。选择会进入 TaskSpec runtime selector，并由父 adapter 实际采用；不兼容候选不会出现在选择器中。' : '这是父合同声明的依赖 Capability，但没有开放替换槽位。')
          : (module.replaceable ? '这是 parent-managed internal slot：基线仍由父 adapter 内部实现；拖入兼容注册模块后，父调度器会真实调用该模块的 source-native step API。' : '这是父 adapter 内部架构模块，未开放替换槽位。');
      section.appendChild(note); container.appendChild(section);
    } else {
      badge.textContent = '装配';
      const section = document.createElement('section'); section.className = 'graph-inspector-section';
      const title = document.createElement('h4'); title.textContent = state.assemblyContract.name || state.assemblyContract.parent_capability_id;
      const stats = document.createElement('div'); stats.className = 'graph-stat-grid';
      const portContract = state.assemblyContract.simulation_ports || {};
      const replacementContract = state.assemblyContract.module_replacements || {};
      const baselineByAlias = Object.fromEntries((state.assemblyContract.modules || []).map((item) => [item.alias, assemblyBaselineCapability(item)]));
      const replacementCount = (state.assemblyGraph.nodes || []).filter((item) => {
        const baseline = baselineByAlias[item.moduleAlias] ?? null;
        const selected = item.capabilityId ?? baseline;
        return selected !== baseline;
      }).length;
      [['模块', state.assemblyGraph.nodes.length], ['正式端口', (portContract.ports || []).length], ['已连接', state.assemblyGraph.edges.length], ['注册 binding', state.assemblyContract.bindings.filter((item) => item.wireable).length], ['可替换槽位', state.assemblyContract.replaceable_module_count || 0], ['当前替换', replacementCount], ['端口合同', portContract.validation?.ok === false ? '失败' : '通过'], ['替换合同', replacementContract.validation?.ok === false ? '失败' : '通过']].forEach(([label, value]) => {
        const card = document.createElement('div'); card.className = 'graph-stat'; const span = document.createElement('span'); span.textContent = label; const strong = document.createElement('strong'); strong.textContent = String(value); card.append(span, strong); stats.appendChild(card);
      });
      const fingerprint = document.createElement('code'); fingerprint.className = 'assembly-contract-fingerprint'; fingerprint.textContent = `ports ${String(portContract.fingerprint || '').slice(0, 16)}… · modules ${String(replacementContract.fingerprint || '').slice(0, 16)}…`;
      const note = document.createElement('p'); note.textContent = portContract.source === 'explicit-v5'
        ? '正式端口合同与模块库支持 source-native 模块真实插入：候选必须满足 allow-list、payload/unit、timing、direct-feedthrough 与 fan-in/fan-out 兼容性；实际选择被绑定进 TaskSpec 并在运行前复核。'
        : '该能力仍使用 V4 field_mappings 兼容推断；可运行，但 不会据此开放内部模块插拔。';
      section.append(title, stats, fingerprint, note); container.appendChild(section);
    }
    if (state.assemblyCode) {
      const section = document.createElement('section'); section.className = 'graph-inspector-section';
      const title = document.createElement('h4'); title.textContent = assemblyCodeIsFresh() ? state.assemblyCodeFilename : `${state.assemblyCodeFilename}（已过期）`;
      section.appendChild(title);
      appendCodeChangeInsight(section, 'assembly');
      const preview = document.createElement('textarea'); preview.className = 'assembly-code-preview'; preview.readOnly = true; preview.value = state.assemblyCode;
      const actions = document.createElement('div'); actions.className = 'graph-inspector-actions';
      const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'button button-ghost'; copy.textContent = '复制'; copy.addEventListener('click', () => assemblyCopyCode().catch((error) => assemblySetStatus(error.message, 'error')));
      const download = document.createElement('button'); download.type = 'button'; download.className = 'button button-secondary'; download.textContent = '下载 .py'; download.addEventListener('click', () => assemblyDownloadCode().catch((error) => assemblySetStatus(error.message, 'error')));
      actions.append(copy, download); section.append(preview, actions); container.appendChild(section);
    }
  }

  function assemblyRender() {
    const nodes = el('assemblyNodes'); const empty = el('assemblyEmptyHint');
    if (!nodes || !empty) return;
    state.assemblySelection = assemblySelectionItems().map((entry) => assemblySelectionKey(entry.kind, entry.id));
    const selectionSummary = el('assemblySelectionSummary');
    if (selectionSummary) selectionSummary.textContent = `${assemblySelectionLabel()} · Shift/Ctrl 多选 · 空白处拖框选择`;
    nodes.replaceChildren();
    if (!state.assemblyGraph || !state.assemblyContract) {
      empty.classList.remove('hidden'); assemblyRenderModuleLibrary(); assemblyRenderInspector(); el('assemblyEdges')?.replaceChildren(); return;
    }
    empty.classList.toggle('hidden', Boolean(state.assemblyGraph.nodes?.length));
    (state.assemblyGraph.nodes || []).forEach((node) => nodes.appendChild(assemblyNodeElement(node)));
    assemblyScopes().forEach((scope) => nodes.appendChild(assemblyScopeElement(scope)));
    assemblyRenderModuleLibrary();
    assemblyRenderInspector();
    assemblyRenderComparison();
    requestAnimationFrame(assemblyDrawEdges);
  }

  async function assemblyCompile({ announce = true } = {}) {
    if (!state.assemblyGraph || !state.assemblyContract) {
      assemblySetStatus('请先加载一个注册装配。', 'error'); return false;
    }
    const parentId = state.assemblyContract.parent_capability_id;
    if (state.selectedCapabilityId !== parentId || !state.formData) await selectCapability(parentId);
    if (!validateClientForm({ show: false })) { assemblySetStatus('参数校验失败；请检查当前父 Capability 表单。', 'error'); return false; }
    const payload = await api('/tasks/compile-assembly', {
      method: 'POST',
      body: JSON.stringify({ assembly_graph: state.assemblyGraph, form_data: state.formData, require_all_bindings: true }),
    });
    if (!payload.assembly_validation?.ok) {
      const issues = payload.assembly_validation?.errors || payload.assembly_validation?.issues || [];
      assemblySetStatus(`装配校验失败：${issues.map((item) => item.message || item.code).join('；') || '未知装配错误'}`, 'error');
      return false;
    }
    const result = payload.result || {};
    state.taskSpec = result.task_spec || null; state.planning = result.planning || null; syncTaskSpecEditor();
    if (!payload.ok) {
      const issues = [...(result.validation?.errors || []), ...(result.guards?.issues || []), ...(result.planning?.validation?.errors || [])];
      assemblySetStatus(`父 Capability TaskSpec 编译失败：${issues.map(formatValidationIssue).join('；') || '未通过能力边界校验'}`, 'error');
      return false;
    }
    const validation = payload.assembly_validation || {};
    const feedback = validation.feedback_groups?.length ? `；识别 ${validation.feedback_groups.length} 个注册反馈组` : '';
    if (announce) {
      const source = validation.port_contract_source === 'explicit-v5' ? '显式 V5 端口合同' : 'legacy-inferred 兼容合同';
      const solver = (validation.solver_policies || []).join(', ') || 'parent-owned';
      const replacements = Number(validation.replacement_count || 0);
      assemblySetStatus(`装配已通过后端校验：${validation.node_count} 模块 / ${validation.edge_count} bindings${feedback}；${source}；replacements=${replacements}；solver=${solver}；runtime owner=${parentId}`, 'success');
    }
    assemblyRender();
    return true;
  }

  async function assemblyGenerateCodeFromTaskSpec() {
    if (!state.taskSpec || !state.assemblyGraph) throw new Error('请先编译装配 TaskSpec');
    const payload = await api('/tasks/export-assembly-script', {
      method: 'POST',
      body: JSON.stringify({ assembly_graph: state.assemblyGraph, task_spec: state.taskSpec, require_all_bindings: true }),
    });
    if (!payload.ok) {
      const issues = [...(payload.assembly_validation?.errors || []), ...(payload.assembly_task_binding?.errors || [])];
      throw new Error(issues.map((item) => item.message || item.code).join('；') || '装配脚本导出失败');
    }
    const nextCode = payload.code || '';
    state.assemblyCodeDiff = codeDiffSummary(state.assemblyCode, nextCode);
    state.assemblyCode = nextCode;
    state.assemblyCodeFilename = payload.filename || 'assembly.py';
    state.assemblyGeneratedSnapshot = assemblySnapshot();
    assemblyRender();
    const replacementCount = Number(payload.provenance?.replacement_count || 0);
    assemblySetStatus(`已生成 ${state.assemblyCodeFilename}。运行前会重新校验装配图、端口/模块合同 fingerprint 与 TaskSpec 模块选择；当前 replacements=${replacementCount}。`, 'success');
    return payload;
  }

  async function assemblyGeneratePython() {
    const ok = await assemblyCompile({ announce: false });
    if (!ok) return false;
    await assemblyGenerateCodeFromTaskSpec();
    return true;
  }

  async function assemblyCopyCode() {
    if (!assemblyCodeIsFresh()) { const ok = await assemblyGeneratePython(); if (!ok) return; }
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(state.assemblyCode);
    else {
      const preview = el('assemblyInspector')?.querySelector('.assembly-code-preview');
      if (!preview) throw new Error('没有可复制的装配代码');
      preview.focus(); preview.select(); document.execCommand('copy');
    }
    assemblySetStatus('装配 Python 已复制到剪贴板。', 'success');
  }

  async function assemblyDownloadCode() {
    if (!assemblyCodeIsFresh()) { const ok = await assemblyGeneratePython(); if (!ok) return; }
    const blob = new Blob([state.assemblyCode], { type: 'text/x-python;charset=utf-8' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = state.assemblyCodeFilename || 'assembly.py'; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    assemblySetStatus(`已下载 ${anchor.download}。`, 'success');
  }

  function visualProgramFallbackFilename() {
    const raw = state.taskSpec?.task?.id || state.selectedCapabilityId || 'simulation';
    const safe = String(raw).replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[_\.-]+|[_\.-]+$/g, '') || 'simulation';
    return `${safe.slice(0, 96)}_visual_program.zip`;
  }

  async function graphExportProgram() {
    const ok = await graphCompile({ requireCode: true, requireRun: true, announce: false });
    if (!ok) return false;
    await postDownloadEndpoint('/tasks/export-program', { task_spec: state.taskSpec, visual_graph: graphSerialize() }, visualProgramFallbackFilename());
    graphSetStatus('已导出可运行程序包：包含 Python 入口、TaskSpec、manifest、README 与 Windows/Linux 启动脚本。', 'success');
    return true;
  }

  async function assemblyExportProgram() {
    const ok = await assemblyCompile({ announce: false });
    if (!ok) return false;
    await postDownloadEndpoint(
      '/tasks/export-program',
      { task_spec: state.taskSpec, assembly_graph: state.assemblyGraph, require_all_bindings: true },
      visualProgramFallbackFilename(),
    );
    assemblySetStatus('已导出可运行装配程序包：包含生成 Python、TaskSpec、assembly_graph、合同指纹 manifest 与启动脚本。', 'success');
    return true;
  }

  function assemblyBaselineGraph() {
    if (!state.assemblyGraph || !state.assemblyContract) return null;
    const graph = clone(state.assemblyGraph);
    (graph.nodes || []).forEach((node) => {
      const module = assemblyModuleByAlias(node.moduleAlias);
      if (!module?.replaceable) return;
      node.capabilityId = assemblyBaselineCapability(module);
    });
    return graph;
  }

  async function compileAssemblyGraph(graph) {
    const payload = await api('/tasks/compile-assembly', {
      method: 'POST',
      body: JSON.stringify({ assembly_graph: graph, form_data: state.formData, require_all_bindings: true }),
    });
    if (!payload.ok || !payload.assembly_validation?.ok || !payload.result?.task_spec) {
      const issues = [
        ...(payload.assembly_validation?.errors || []),
        ...(payload.result?.validation?.errors || []),
        ...(payload.result?.guards?.issues || []),
      ];
      throw new Error(issues.map((item) => item.message || item.code).join('；') || '装配编译失败');
    }
    return payload;
  }

  async function submitComparisonRun(taskSpec, role) {
    const raw = String(taskSpec?.task?.id || 'visual_compare').replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 90);
    const runId = `${raw}_${role}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
    const payload = await api('/runs', {
      method: 'POST',
      body: JSON.stringify({ task_spec: taskSpec, run_id: runId, execute: true, execution_mode: 'async', max_attempts: 2 }),
    });
    return payload.prepared_run?.run_id || payload.execution_state?.run_id || runId;
  }

  function waitMs(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }

  async function waitForComparisonRun(runId, role) {
    for (let attempt = 0; attempt < 1600; attempt += 1) {
      const payload = await api(`/runs/${encodeURIComponent(runId)}/execution`);
      const execution = payload.execution_state || {};
      const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(execution.state);
      state.assemblyComparison = { ...(state.assemblyComparison || {}), [`${role}State`]: execution.state || 'QUEUED' };
      assemblyRenderComparison();
      if (terminal) return execution;
      await waitMs(750);
    }
    throw new Error(`${role === 'baseline' ? '基线' : '当前装配'}对比运行等待超限`);
  }

  function comparisonNumber(value) {
    const number = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatComparisonValue(value) {
    const number = comparisonNumber(value);
    if (number == null) return value == null ? '—' : String(value);
    const magnitude = Math.abs(number);
    if (magnitude >= 10000 || (magnitude > 0 && magnitude < 0.001)) return number.toExponential(3);
    return Number(number.toPrecision(6)).toString();
  }

  function assemblyRenderComparison() {
    const panel = el('assemblyComparePanel'); const summary = el('assemblyCompareSummary'); const table = el('assemblyCompareTable');
    if (!panel || !summary || !table) return;
    const comparisonState = state.assemblyComparison;
    panel.classList.toggle('hidden', !comparisonState);
    table.replaceChildren();
    if (!comparisonState) return;
    if (comparisonState.status === 'running') {
      summary.textContent = `基线 ${comparisonState.baselineState || 'QUEUED'} · 当前 ${comparisonState.currentState || 'QUEUED'}；完成后自动计算指标差异。`;
      return;
    }
    if (comparisonState.status === 'error') { summary.textContent = comparisonState.message || '基线对比失败。'; return; }
    const comparison = comparisonState.comparison || {};
    summary.textContent = `基线 ${comparisonState.baselineRunId} ↔ 当前 ${comparisonState.currentRunId}；共 ${comparison.metrics?.length || 0} 个指标${comparisonState.stale ? '；画布在运行期间已修改，本结果对应提交时快照' : ''}。`;
    const baseline = (comparison.rows || []).find((row) => row.run_id === comparisonState.baselineRunId) || comparison.rows?.[0] || null;
    const current = (comparison.rows || []).find((row) => row.run_id === comparisonState.currentRunId) || comparison.rows?.[1] || null;
    const header = document.createElement('div'); header.className = 'assembly-compare-row header';
    ['指标', '基线', '当前', 'Δ 当前-基线'].forEach((label, index) => { const cell = document.createElement(index === 0 ? 'code' : 'strong'); cell.textContent = label; header.appendChild(cell); });
    table.appendChild(header);
    const numeric = (comparison.metrics || []).map((metric) => {
      const before = comparisonNumber(baseline?.values?.[metric]);
      const after = comparisonNumber(current?.values?.[metric]);
      return { metric, before, after, delta: before != null && after != null ? after - before : null };
    }).filter((item) => item.before != null || item.after != null).slice(0, 16);
    if (!numeric.length) {
      const empty = document.createElement('div'); empty.className = 'graph-inspector-empty'; empty.textContent = '运行已完成，但没有共同的数值指标可直接比较。可在运行结果中查看详细输出。'; table.appendChild(empty); return;
    }
    numeric.forEach((item) => {
      const row = document.createElement('div'); row.className = 'assembly-compare-row';
      const name = document.createElement('code'); name.textContent = item.metric;
      const baselineValue = document.createElement('span'); baselineValue.textContent = formatComparisonValue(item.before);
      const currentValue = document.createElement('span'); currentValue.textContent = formatComparisonValue(item.after);
      const delta = document.createElement('strong'); delta.textContent = item.delta == null ? '—' : `${item.delta > 0 ? '+' : ''}${formatComparisonValue(item.delta)}`;
      row.append(name, baselineValue, currentValue, delta); table.appendChild(row);
    });
  }

  async function comparisonMetricSelection(baselineRunId, currentRunId) {
    try {
      const [baselinePayload, currentPayload] = await Promise.all([
        api(`/runs/${encodeURIComponent(baselineRunId)}/metrics`),
        api(`/runs/${encodeURIComponent(currentRunId)}/metrics`),
      ]);
      const baseline = baselinePayload.metrics?.metrics || baselinePayload.metrics || {};
      const current = currentPayload.metrics?.metrics || currentPayload.metrics || {};
      return Object.keys(baseline).filter((key) => Object.prototype.hasOwnProperty.call(current, key))
        .filter((key) => comparisonNumber(baseline[key]) != null && comparisonNumber(current[key]) != null)
        .sort((left, right) => {
          const rank = (key) => key.startsWith('qoi.') ? 0 : key.startsWith('metrics.') ? 1 : key.startsWith('adapter_metadata.') ? 3 : 2;
          return rank(left) - rank(right) || left.localeCompare(right);
        })
        .slice(0, 64);
    } catch (_) { return []; }
  }

  async function assemblyCompareBaseline() {
    if (state.assemblyComparisonRunning) return false;
    const ok = await assemblyCompile({ announce: false });
    if (!ok) return false;
    const currentGraph = clone(state.assemblyGraph);
    const currentTaskSpec = clone(state.taskSpec);
    const submittedSnapshot = assemblySnapshot();
    const baselineGraph = assemblyBaselineGraph();
    if (!baselineGraph) throw new Error('当前没有可比较的装配图');
    const baselineCompiled = await compileAssemblyGraph(baselineGraph);
    const baselineTaskSpec = baselineCompiled.result.task_spec;
    const currentReplacements = Number((currentTaskSpec.metadata?.visual_assembly || {}).replacement_count || 0);
    if (!currentReplacements) assemblySetStatus('当前装配已经是基线；仍会执行双运行，用于验证参数/结果一致性。', 'warning');
    state.assemblyComparisonRunning = true;
    state.assemblyComparison = { status: 'running', baselineState: 'SUBMITTING', currentState: 'SUBMITTING' };
    assemblyRenderComparison();
    try {
      const [baselineRunId, currentRunId] = await Promise.all([submitComparisonRun(baselineTaskSpec, 'baseline'), submitComparisonRun(currentTaskSpec, 'current')]);
      state.assemblyComparison = { ...state.assemblyComparison, baselineRunId, currentRunId, baselineState: 'QUEUED', currentState: 'QUEUED' };
      assemblyRenderComparison();
      const [baselineExecution, currentExecution] = await Promise.all([waitForComparisonRun(baselineRunId, 'baseline'), waitForComparisonRun(currentRunId, 'current')]);
      const metrics = await comparisonMetricSelection(baselineRunId, currentRunId);
      const compared = await api('/runs/compare', { method: 'POST', body: JSON.stringify({ run_ids: [baselineRunId, currentRunId], metrics }) });
      state.assemblyComparison = {
        status: 'done', baselineRunId, currentRunId, baselineState: baselineExecution.state, currentState: currentExecution.state, comparison: compared.comparison || {}, stale: assemblySnapshot() !== submittedSnapshot,
      };
      assemblyRenderComparison();
      state.activeRunId = currentRunId; state.activeRunDisplayName = `${currentTaskSpec.task?.name || '当前装配'} · baseline compare`;
      await Promise.all([loadRun(currentRunId, { quiet: true }), loadRuns()]);
      if (currentExecution.error || ['FAILED', 'TIMED_OUT'].includes(currentExecution.state)) {
        await requestVisualDiagnostics(currentExecution.error || `Run ${currentExecution.state}`, { mode: 'assembly', taskSpec: currentTaskSpec, assemblyGraph: currentGraph });
      } else {
        assemblySetStatus(`基线对比完成：${baselineRunId} ↔ ${currentRunId}。指标差异已显示在画布上方。`, 'success');
      }
      return true;
    } catch (error) {
      state.assemblyComparison = { ...(state.assemblyComparison || {}), status: 'error', message: `基线对比失败：${error.message}` };
      assemblyRenderComparison();
      assemblySetStatus(`基线对比失败：${error.message}`, 'error');
      return false;
    } finally { state.assemblyComparisonRunning = false; }
  }

  function fieldByPath(path) {
    const normalized = String(path || '').replace(/^\$\.?/, '').replace(/\[(\d+)\]/g, '.$1');
    return (state.schema?.fields || []).find((field) => field.path === normalized)
      || (state.schema?.fields || []).find((field) => normalized.endsWith(field.path));
  }

  function clearFieldErrors() {
    state.formErrors = new Map();
    $all('.form-field.field-error').forEach((node) => node.classList.remove('field-error'));
    $all('.field-error-message').forEach((node) => node.remove());
    el('formValidationSummary')?.classList.add('hidden');
  }

  function setFieldError(path, message) {
    const normalized = String(path || '').replace(/^\$\.?/, '').replace(/\[(\d+)\]/g, '.$1');
    state.formErrors.set(normalized, message);
    const field = fieldByPath(normalized);
    const wrapper = field ? document.querySelector(`[data-field-path="${CSS.escape(field.path)}"]`) : null;
    if (!wrapper) return;
    wrapper.classList.add('field-error');
    let error = wrapper.querySelector('.field-error-message');
    if (!error) {
      error = document.createElement('span');
      error.className = 'field-error-message';
      wrapper.appendChild(error);
    }
    error.textContent = message;
  }

  function formatValidationIssue(item) {
    const rawPath = item?.path || item?.loc || item?.field || '';
    const path = Array.isArray(rawPath) ? rawPath.join('.') : String(rawPath);
    const field = fieldByPath(path);
    const label = field?.label || path.replace(/^\$\.?/, '') || '任务配置';
    const message = item?.message || item?.msg || item?.code || item?.type || '校验失败';
    return `${label}${path ? `（${path.replace(/^\$\.?/, '')}）` : ''}：${message}`;
  }

  function showValidationIssues(issues, fallback = 'TaskSpec 校验失败') {
    clearFieldErrors();
    const rows = Array.isArray(issues) ? issues : [];
    rows.forEach((item) => {
      const rawPath = item?.path || item?.loc || '';
      const path = Array.isArray(rawPath) ? rawPath.join('.') : String(rawPath);
      if (path) setFieldError(path, item?.message || item?.msg || item?.code || '校验失败');
    });
    const messages = rows.map(formatValidationIssue);
    const summary = el('formValidationSummary');
    if (summary && messages.length) {
      summary.textContent = messages.join('；');
      summary.classList.remove('hidden');
    }
    showNotice(messages.join('；') || fallback, 'error');
  }

  function updateTaskEditState() {
    const update = el('updateTaskBtn');
    if (!update) return;
    update.classList.toggle('hidden', !state.activeTaskCenterId);
    update.title = state.activeTaskCenterId ? `更新任务中心中的 ${state.activeTaskCenterId}` : '';
  }

  function parseStructuredValue(raw, field) {
    const text = String(raw ?? '').trim();
    if (!text) {
      if (field.required) throw new Error('不能为空');
      return undefined;
    }
    if (['object', 'object_editor'].includes(field.widget)) {
      const value = JSON.parse(text);
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('必须是 JSON 对象');
      return value;
    }
    const needsArray = field.widget === 'array' || String(field.widget).startsWith('array[');
    const numberOrArray = ['number_or_array[number]', 'vector3_or_number'].includes(field.widget);
    if (needsArray || numberOrArray) {
      let value;
      try {
        value = JSON.parse(text);
      } catch (_) {
        value = text.includes(',') ? text.split(',').map((item) => Number(item.trim())) : Number(text);
      }
      if (Array.isArray(value)) {
        if (!value.length && field.required) throw new Error('必须是非空列表');
        if (value.some((item) => typeof item !== 'number' || !Number.isFinite(item))) throw new Error('列表元素必须为数值');
        if (field.widget === 'vector3_or_number' && value.length !== 3) throw new Error('向量形式必须包含 3 个数值');
        return value;
      }
      if (numberOrArray && typeof value === 'number' && Number.isFinite(value)) return value;
      if (needsArray) throw new Error('必须是数值列表，可输入 [1, 2, 3] 或 1,2,3');
      throw new Error('必须是数值或数值列表');
    }
    return text;
  }

  function displayFieldValue(value, field) {
    if (value == null) return '';
    if (['object', 'object_editor'].includes(field.widget) || Array.isArray(value)) return JSON.stringify(value);
    return String(value);
  }

  function validateClientForm({ show = true } = {}) {
    clearFieldErrors();
    if (!state.formData) return false;
    const duration = Number(getPath(state.formData, 'simulation.duration_s'));
    const sample = Number(getPath(state.formData, 'simulation.sample_s'));
    const step = Number(getPath(state.formData, 'simulation.step_s'));
    const issues = [];
    if (!(duration > 0)) issues.push({ path: 'simulation.duration_s', message: '必须大于 0' });
    if (!(sample > 0)) issues.push({ path: 'simulation.sample_s', message: '必须大于 0' });
    if (Number.isFinite(step) && !(step > 0)) issues.push({ path: 'simulation.step_s', message: '必须大于 0' });
    if (Number.isFinite(step) && Number.isFinite(sample) && step > sample) issues.push({ path: 'simulation.sample_s', message: '输出采样间隔必须大于或等于积分步长 step_s' });
    if (Number.isFinite(sample) && Number.isFinite(duration) && sample > duration) issues.push({ path: 'simulation.sample_s', message: '输出采样间隔不能超过仿真时长 duration_s' });
    if (issues.length) {
      issues.forEach((item) => setFieldError(item.path, item.message));
      const summary = el('formValidationSummary');
      if (summary) {
        summary.textContent = issues.map(formatValidationIssue).join('；');
        summary.classList.remove('hidden');
      }
      if (show) showNotice(issues.map(formatValidationIssue).join('；'), 'error');
      return false;
    }
    return true;
  }

  function formDiffPaths(before, after) {
    const changed = new Set();
    (state.schema?.fields || []).forEach((field) => {
      if (!deepEqual(getPath(before, field.path), getPath(after, field.path))) changed.add(field.path);
    });
    ['events.faults', 'events.degradations', 'events.constraints', 'outputs.qoi', 'outputs.plots'].forEach((path) => {
      if (!deepEqual(getPath(before, path), getPath(after, path))) changed.add(path);
    });
    return changed;
  }

  function renderAgentChangePanel(source = 'Agent') {
    const panel = el('agentChangePanel');
    if (!panel) return;
    const count = state.changedPaths.size;
    if (!count) {
      panel.classList.add('hidden');
      return;
    }
    el('agentChangeTitle').textContent = `${source} 已更新表单`;
    el('agentChangeDetail').textContent = `${count} 组字段或配置发生变化；蓝色区域为本次差异。`;
    panel.classList.remove('hidden');
  }

  function clearAgentChanges() {
    state.changedPaths = new Set();
    state.agentBeforeForm = null;
    renderForm();
    renderEvents();
    renderOutputs();
    renderAgentChangePanel();
  }

  function undoAgentChanges() {
    if (!state.agentBeforeForm) return;
    state.formData = clone(state.agentBeforeForm);
    state.taskSpec = null;
    state.planning = null;
    clearAgentChanges();
    syncTaskSpecEditor();
    showNotice('已撤销最近一次 Agent/TaskSpec 表单同步。', 'success');
  }

  async function projectTaskSpecToForm(taskSpec, source = 'Agent') {
    const capabilityId = taskSpec?.model?.capability_id;
    if (!capabilityId) throw new Error('TaskSpec 未声明 capability_id');
    if (capabilityId !== state.selectedCapabilityId) await selectCapability(capabilityId);
    const before = clone(state.formData || state.schema.default_form);
    const payload = await api(`/forms/capabilities/${encodeURIComponent(capabilityId)}/project`, {
      method: 'POST',
      body: JSON.stringify({ task_spec: taskSpec }),
    });
    const projected = payload.projection?.form_data;
    if (!projected) throw new Error('表单投影未返回 form_data');
    state.agentBeforeForm = before;
    state.formData = clone(projected);
    state.changedPaths = formDiffPaths(before, state.formData);
    state.taskSpec = clone(taskSpec);
    renderForm();
    renderEffectSelect();
    renderEvents();
    renderOutputs();
    renderAgentChangePanel(source);
    syncTaskSpecEditor();
    switchTab('form');
    return state.changedPaths.size;
  }

  function formatNumber(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return String(value ?? '—');
    const abs = Math.abs(value);
    if ((abs > 0 && abs < 0.001) || abs >= 1e7) return value.toExponential(3);
    if (Number.isInteger(value)) return value.toLocaleString('zh-CN');
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 5 });
  }

  const VALUE_LABELS_ZH = {
    PASS: '通过', FAIL: '未通过', SUCCEEDED: '已完成', FAILED: '失败',
    INCONCLUSIVE: '结论不足', NOT_EVALUATED: '未评估', QUEUED: '排队中',
    RUNNING: '运行中', PREPARED: '已准备', CANCELLED: '已取消',
    true: '是', false: '否', nominal: '标称', medium: '中等保真',
    basic: '基础保真', high: '高保真', low: '低保真', available: '可用',
    unavailable: '不可用',
  };

  function localizedValue(value) {
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (typeof value === 'number') return formatNumber(value);
    if (value == null || value === '') return '—';
    const raw=String(value);
    return VALUE_LABELS_ZH[raw] || VALUE_LABELS_ZH[raw.toUpperCase()] || raw;
  }

  function localizedValidationResult(value) {
    return VALUE_LABELS_ZH[String(value || 'NOT_EVALUATED').toUpperCase()] || String(value || '未评估');
  }

  function humanBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${(value / 1024 ** 3).toFixed(1)} GB`;
  }

  function levelLabel(level) {
    return ({ whole_spacecraft: '整星', orbit_environment: '轨道环境', integration: '集成', integrated: '轨道环境', subsystem: '分系统', component: '部件' })[level] || level;
  }

  function showNotice(message, type = 'success') {
    const notice = el('notice');
    notice.textContent = message;
    notice.className = `notice ${type}`;
  }

  function clearNotice() {
    el('notice').className = 'notice hidden';
  }

  function showBusy(title, detail = '请勿关闭页面') {
    el('busyTitle').textContent = title;
    el('busyDetail').textContent = detail;
    el('busyOverlay').classList.remove('hidden');
  }

  function hideBusy() {
    el('busyOverlay').classList.add('hidden');
  }

  function switchTab(name) {
    $all('.tab').forEach((button) => {
      const active = button.dataset.tab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    $all('.tab-content').forEach((panel) => panel.classList.toggle('active', panel.dataset.panel === name));
    if (name === 'graph') requestAnimationFrame(() => {
      if (state.graphComposerMode === 'assembly') assemblyRender();
      else graphInitialize(false);
    });
  }

  function switchResultTab(name) {
    $all('.result-tab').forEach((button) => button.classList.toggle('active', button.dataset.resultTab === name));
    $all('.result-content').forEach((panel) => panel.classList.toggle('active', panel.dataset.resultPanel === name));
    if (name === 'charts') requestAnimationFrame(drawChart);
  }

  async function loadAuthStatus() {
    const input = el('apiTokenInput');
    if (input) input.value = state.apiToken;
    const payload = await api('/auth/status');
    const auth = payload.auth || {};
    state.authEnabled = Boolean(auth.enabled);
    const button = el('saveApiTokenBtn');
    if (button) button.textContent = state.apiToken ? '更新' : (state.authEnabled ? '连接' : '本地');
    return auth;
  }

  async function saveApiToken() {
    state.apiToken = (el('apiTokenInput')?.value || '').trim();
    if (state.apiToken) sessionStorage.setItem('sat-sim-api-token', state.apiToken);
    else sessionStorage.removeItem('sat-sim-api-token');
    try {
      const auth = await loadAuthStatus();
      if (auth.enabled && !auth.authenticated) throw new Error('Token 无效或未提供');
      showNotice(auth.enabled ? `已认证：${auth.principal?.principal_id || 'unknown'}` : '当前服务未启用 Token 认证。', 'success');
      await Promise.allSettled([loadHeader(), loadRuns(), loadModelProviders(), loadCapabilities(), loadInteractiveAvailability()]);
    } catch (error) {
      showNotice(`认证失败：${error.message}`, 'error');
    }
  }

  async function loadHeader() {
    try {
      const [health, release] = await Promise.all([api('/health'), api('/release')]);
      el('healthBadge').className = 'status-chip status-ok';
      el('healthBadge').innerHTML = '<span class="status-dot"></span>服务正常';
      const queueStates = health.queue?.states || {};
      const pending = Number(queueStates.QUEUED || 0) + Number(queueStates.RUNNING || 0) + Number(queueStates.CLAIMED || 0);
      el('queueBadge').className = `status-chip ${pending ? 'status-warn' : 'status-ok'}`;
      el('queueBadge').innerHTML = `<span class="status-dot"></span>持久队列 ${pending ? `${pending} 个任务` : '空闲'}`;
      const manifest = release.release || {};
      const serviceVersion=String(manifest.package_version || manifest.release_version || health.package_version || health.api_version || 'unknown');
      const staticVersion=String(window.SAT_SIM_STATIC_VERSION || 'unknown');
      if (staticVersion !== 'unknown' && serviceVersion !== 'unknown' && staticVersion !== serviceVersion) {
        el('releaseBadge').className='status-chip status-fail';
        el('releaseBadge').textContent=`版本不一致：界面 ${staticVersion} / 服务 ${serviceVersion}`;
        showNotice(`检测到前端静态资源与后端服务版本不一致。请停止旧服务、重新执行升级脚本并按 Ctrl+F5 强制刷新。界面版本 ${staticVersion}，服务版本 ${serviceVersion}。`, 'error');
      } else {
        el('releaseBadge').className='status-chip status-ok';
        el('releaseBadge').textContent=`工作台 ${staticVersion} · 服务 ${serviceVersion}`;
      }
    } catch (error) {
      el('healthBadge').className = 'status-chip status-fail';
      el('healthBadge').innerHTML = '<span class="status-dot"></span>服务异常';
      el('queueBadge').className = 'status-chip status-fail';
      el('queueBadge').innerHTML = '<span class="status-dot"></span>队列异常';
      showNotice(`服务检查失败：${error.message}`, 'error');
    }
  }

  async function loadModelProviders(liveProbe = false) {
    const payload = await api(`/models/providers${liveProbe ? '?live_probe=true' : ''}`);
    const catalog = payload.catalog || {};
    state.providers = catalog.providers || [];
    state.activeProviderId = catalog.active_provider_id || 'local-template';
    const select = el('agentBackend');
    select.replaceChildren();
    const selectable = state.providers.filter((provider) => provider.provider_id === 'local-template' || provider.saved || provider.availability_state === 'FULL_TEST_PASSED');
    selectable.forEach((provider) => {
      const option = document.createElement('option');
      option.value = provider.provider_id;
      const location = provider.location === 'local' ? '本地' : (provider.location === 'remote' ? '在线' : '确定性');
      const stateLabel = ({
        FULL_TEST_PASSED: '完整测试通过', FULL_TEST_FAILED: '完整测试失败', ENDPOINT_REACHABLE: '仅端点可连接',
        CONFIGURED_NOT_TESTED: '未完整测试', NOT_CONFIGURED: '未配置',
      })[provider.availability_state] || provider.readiness?.status || '未知';
      option.textContent = `${provider.label || provider.provider_id} · ${location} · ${stateLabel}`;
      option.disabled = provider.availability_state === 'FULL_TEST_FAILED' || provider.availability_state === 'NOT_CONFIGURED';
      select.appendChild(option);
    });
    const preferred = selectable.find((provider) => provider.provider_id === state.activeProviderId)
      || selectable.find((provider) => provider.provider_id === state.selectedProviderId)
      || selectable.find((provider) => provider.provider_id === 'local-template')
      || selectable[0];
    if (preferred) {
      state.selectedProviderId = preferred.provider_id;
      select.value = preferred.provider_id;
    }
    const banner = el('activeModelProviderBanner');
    const active = state.providers.find((provider) => provider.provider_id === state.activeProviderId);
    if (banner) banner.textContent = `当前使用：${active?.label || state.activeProviderId}${active?.model ? ` · ${active.model}` : ''}`;
    renderModelProviderList();
  }

  function providerStateLabel(provider) {
    return ({
      FULL_TEST_PASSED: '完整测试通过', FULL_TEST_FAILED: '完整测试失败', ENDPOINT_REACHABLE: '服务可连接',
      CONFIGURED_NOT_TESTED: '已配置，未完整测试', NOT_CONFIGURED: '未配置',
    })[provider.availability_state] || provider.readiness?.status || '未知';
  }

  async function activateModelProvider(providerId) {
    await api(`/models/providers/${encodeURIComponent(providerId)}/activate`, { method: 'POST', body: '{}' });
    state.activeProviderId = providerId;
    state.selectedProviderId = providerId;
    await loadModelProviders(false);
    showNotice(`已将 ${providerId} 设为当前模型服务。`, 'success');
  }

  async function probeSavedProvider(providerId) {
    const diagnostics = el('localModelDiagnostics');
    const probe = await api(`/models/providers/${encodeURIComponent(providerId)}/probe`, {
      method: 'POST', body: JSON.stringify({ timeout_s: 30, include_taskspec_probe: true }),
    });
    diagnostics.textContent = JSON.stringify(probe, null, 2);
    diagnostics.classList.remove('hidden');
    el('localModelStatus').textContent = probe.ok ? `${providerId} 完整测试通过并已设为当前服务。` : `${providerId} 完整测试失败，请查看诊断。`;
    el('localModelStatus').className = `model-status ${probe.ok ? 'provider-ready' : 'provider-unready'}`;
    await loadModelProviders(false);
  }

  async function deleteSavedProvider(providerId) {
    if (!window.confirm(`确认删除模型服务“${providerId}”？`)) return;
    await api(`/models/local-services/${encodeURIComponent(providerId)}`, { method: 'DELETE' });
    await loadModelProviders(false);
  }

  function renderModelProviderList() {
    const container = el('modelProviderList');
    if (!container) return;
    container.replaceChildren();
    const saved = state.providers.filter((provider) => provider.provider_id === 'local-template' || provider.saved);
    if (!saved.length) {
      container.innerHTML = '<div class="empty-state"><strong>尚未保存模型服务</strong><span>填写上方配置并执行完整测试。</span></div>';
      return;
    }
    saved.forEach((provider) => {
      const item = document.createElement('div');
      item.className = `provider-item${provider.provider_id === state.activeProviderId ? ' active-provider' : ''}`;
      const info = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = `${provider.label || provider.provider_id}${provider.provider_id === state.activeProviderId ? '（当前）' : ''}`;
      const meta = document.createElement('span');
      meta.textContent = `${provider.model || '未指定模型'} · ${provider.base_url || '内置服务'}`;
      const probe = provider.last_probe;
      const detail = document.createElement('small');
      detail.textContent = probe?.tested_at ? `最近完整测试：${formatDateTime(probe.tested_at)}${probe.reason ? ` · ${probe.reason}` : ''}` : '尚无完整测试记录';
      info.append(name, meta, detail);
      const side = document.createElement('div');
      side.className = 'provider-actions';
      const status = document.createElement('span');
      const passed = provider.availability_state === 'FULL_TEST_PASSED';
      status.className = passed ? 'provider-ready' : 'provider-unready';
      status.textContent = providerStateLabel(provider);
      side.appendChild(status);
      if (provider.provider_id !== 'local-template') {
        const test = document.createElement('button'); test.type = 'button'; test.className = 'button button-ghost'; test.textContent = '完整测试';
        test.addEventListener('click', () => probeSavedProvider(provider.provider_id).catch((error) => { el('localModelStatus').textContent = error.message; }));
        const activate = document.createElement('button'); activate.type = 'button'; activate.className = 'button button-secondary'; activate.textContent = provider.provider_id === state.activeProviderId ? '当前服务' : '设为当前'; activate.disabled = provider.provider_id === state.activeProviderId;
        activate.addEventListener('click', () => activateModelProvider(provider.provider_id).catch((error) => { el('localModelStatus').textContent = error.message; }));
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-danger'; remove.textContent = '删除';
        remove.addEventListener('click', () => deleteSavedProvider(provider.provider_id).catch((error) => { el('localModelStatus').textContent = error.message; }));
        side.append(test, activate, remove);
      }
      item.append(info, side);
      container.appendChild(item);
    });
  }

  const LOCAL_MODEL_DEFAULTS = {
    ollama: 'http://127.0.0.1:11434/v1',
    lmstudio: 'http://127.0.0.1:1234/v1',
    vllm: 'http://127.0.0.1:8001/v1',
    openai_compatible: 'http://127.0.0.1:8000/v1',
  };

  function openModelSettings() {
    el('modelSettingsModal').classList.remove('hidden');
    renderModelProviderList();
  }

  function closeModelSettings() {
    el('modelSettingsModal').classList.add('hidden');
  }

  const LOCAL_PROVIDER_IDS = {
    ollama: 'local-ollama',
    lmstudio: 'local-lmstudio',
    vllm: 'local-vllm',
    openai_compatible: 'local-openai-compatible',
  };

  async function discoverLocalModels() {
    const serviceType = el('localModelType').value;
    const status = el('localModelStatus');
    const diagnostics = el('localModelDiagnostics');
    status.textContent = '正在连接服务并读取模型列表…';
    status.className = 'model-status';
    diagnostics.classList.add('hidden');
    const payload = await api('/models/local-services/discover', {
      method: 'POST',
      body: JSON.stringify({
        service_type: serviceType,
        base_url: el('localModelBaseUrl').value.trim() || LOCAL_MODEL_DEFAULTS[serviceType],
        api_key: el('localModelApiKey').value || null,
        timeout_s: 8,
      }),
    });
    const discovery = payload.discovery || {};
    state.discoveredModels = discovery.models || [];
    const select = el('localModelDiscovered');
    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = state.discoveredModels.length ? `发现 ${state.discoveredModels.length} 个模型` : '未发现模型';
    select.appendChild(placeholder);
    state.discoveredModels.forEach((model) => {
      const option = document.createElement('option');
      option.value = model;
      option.textContent = model;
      select.appendChild(option);
    });
    status.textContent = discovery.ok
      ? `连接成功，发现 ${state.discoveredModels.length} 个模型；请选择模型后执行完整测试。`
      : `模型发现失败：${discovery.reason || discovery.status || '未知原因'}`;
    status.className = `model-status ${discovery.ok ? 'provider-ready' : 'provider-unready'}`;
    diagnostics.textContent = JSON.stringify(discovery, null, 2);
    diagnostics.classList.remove('hidden');
    return discovery;
  }

  async function saveAndProbeLocalModel() {
    const serviceType = el('localModelType').value;
    const model = el('localModelName').value.trim();
    if (!model) throw new Error('请先自动发现或填写本地模型名称');
    const status = el('localModelStatus');
    const diagnostics = el('localModelDiagnostics');
    status.textContent = '正在保存配置并执行结构化输出、TaskSpec 全链路测试…';
    status.className = 'model-status';
    const payload = await api('/models/local-services', {
      method: 'POST',
      body: JSON.stringify({
        service_type: serviceType,
        base_url: el('localModelBaseUrl').value.trim() || LOCAL_MODEL_DEFAULTS[serviceType],
        model,
        api_key: el('localModelApiKey').value || null,
      }),
    });
    const providerId = payload.provider_id;
    const probe = await api(`/models/providers/${encodeURIComponent(providerId)}/probe`, {
      method: 'POST',
      body: JSON.stringify({ timeout_s: 30, include_taskspec_probe: true }),
    });
    const taskProbe = probe.taskspec_probe;
    const message = probe.ok
      ? `${providerId} 完整测试通过：连接、JSON结构化输出和 TaskSpec 校验均成功。`
      : probe.service_usable
        ? `${providerId} 服务连接和 JSON 输出正常，但 TaskSpec 语义测试未通过。该服务可用，当前模型暂不建议作为自动配置主模型；请查看缺失字段或能力选择诊断。`
        : `${providerId} 服务测试未通过：${probe.inference_probe?.reason || probe.readiness?.reason || '请查看诊断详情'}`;
    status.textContent = message;
    status.className = `model-status ${probe.ok ? 'provider-ready' : probe.service_usable ? 'provider-warning' : 'provider-unready'}`;
    diagnostics.textContent = JSON.stringify(probe, null, 2);
    diagnostics.classList.remove('hidden');
    await loadModelProviders(false);
    if (probe.ok && (!taskProbe || !taskProbe.fallback_detected)) {
      state.selectedProviderId = providerId;
      el('agentBackend').value = providerId;
    }
    return probe;
  }

  async function loadCapabilities() {
    const previousObjectId = state.selectedObjectId;
    const compatibilityQuery = state.showCompatibilityCapabilities ? '?include_compatibility=true&include_explicit=true' : '';
    const payload = await api(`/forms/capabilities${compatibilityQuery}`);
    state.catalog = payload.catalog.capabilities || [];
    state.presentationCatalog = payload.catalog.presentation || null;
    state.presentationObjects = state.presentationCatalog?.objects || [];
    const counts = state.presentationCatalog?.visible_counts || {};
    if (state.presentationObjects.length) {
      el('capabilityCount').textContent = `${counts.whole_spacecraft || 1} 个整星 · ${counts.subsystem || 6} 个分系统 · ${counts.component || 24} 个部件`;
      renderCapabilityList();
      const preferred = state.presentationObjects.find((item) => item.object_id === previousObjectId) || state.presentationObjects.find((item) => item.object_id === 'whole_spacecraft') || state.presentationObjects[0];
      await selectCatalogObject(preferred.object_id);
      return;
    }
    el('capabilityCount').textContent = `${payload.catalog.count || state.catalog.length} 项可执行能力合同`;
    renderCapabilityList();
    const preferred = state.catalog.find((item) => item.capability_id === 'whole_spacecraft.unified_native.v1');
    if (preferred) await selectCapability(preferred.capability_id);
    else if (state.catalog.length) await selectCapability(state.catalog[0].capability_id);
  }

  function presentationObjectForCapability(capabilityId) {
    return state.presentationObjects.find((item) => (item.variants || []).some((variant) => variant.capability_id === capabilityId)) || null;
  }

  function renderVariantControl(object) {
    const control = el('variantControl');
    const select = el('variantSelect');
    select.replaceChildren();
    const variants = object?.variants || [];
    if (variants.length <= 1) {
      control.classList.add('hidden');
      return;
    }
    variants.forEach((variant) => {
      const option = document.createElement('option');
      option.value = variant.capability_id;
      const tierLabel = variant.recommended ? '推荐' : (variant.product_tier === 'compatibility' ? '兼容' : (variant.product_tier === 'explicit' ? '显式选择' : ''));
      option.textContent = `${variant.name_zh || variant.name_en || variant.capability_id}${tierLabel ? `【${tierLabel}】` : ''}`;
      select.appendChild(option);
    });
    select.value = state.selectedCapabilityId || object.primary_capability_id || variants[0].capability_id;
    control.classList.remove('hidden');
  }

  function renderCapabilityList() {
    const query = el('capabilitySearch').value.trim().toLowerCase();
    const level = el('levelFilter').value;
    const source = state.presentationObjects.length ? state.presentationObjects : state.catalog.map((item) => ({
      object_id: item.capability_id,
      level: item.level,
      name_zh: item.name || item.capability_id,
      name_en: item.target || '',
      summary_zh: '',
      primary_capability_id: item.capability_id,
      variants: [{ capability_id: item.capability_id, name_zh: item.name || item.capability_id }],
      independently_executable: true,
    }));
    const filtered = source.filter((item) => {
      const variants = (item.variants || []).map((variant) => `${variant.capability_id} ${variant.name_zh || ''} ${variant.name_en || ''}`).join(' ');
      const haystack = `${item.object_id} ${item.name_zh || ''} ${item.name_en || ''} ${item.subsystem_zh || ''} ${item.summary_zh || ''} ${variants}`.toLowerCase();
      return (!query || haystack.includes(query)) && (level === 'all' || item.level === level);
    });
    const groups = new Map();
    filtered.forEach((item) => {
      const key = item.level || 'other';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    const container = el('capabilityList');
    container.replaceChildren();
    const order = ['whole_spacecraft', 'orbit_environment', 'subsystem', 'component', 'other'];
    order.forEach((groupName) => {
      const items = groups.get(groupName);
      if (!items?.length) return;
      const title = document.createElement('div');
      title.className = 'cap-group-title';
      title.textContent = `${levelLabel(groupName)} · ${items.length}`;
      container.appendChild(title);
      items.sort((a, b) => (a.name_zh || a.object_id).localeCompare(b.name_zh || b.object_id, 'zh-CN'));
      items.forEach((item) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `capability-card${item.object_id === state.selectedObjectId ? ' active' : ''}${item.integration_only ? ' integration-only' : ''}`;
        button.dataset.objectId = item.object_id;
        const titleEl = document.createElement('div');
        titleEl.className = 'cap-title';
        titleEl.textContent = item.name_zh || item.name_en || item.object_id;
        const meta = document.createElement('div');
        meta.className = 'cap-meta';
        const tags = [];
        if (item.subsystem_zh) tags.push(item.subsystem_zh);
        if ((item.variants || []).length > 1) tags.push(`${item.variants.length} 个模型/场景`);
        else tags.push(item.independently_executable ? '可独立运行' : '组合调用');
        tags.forEach((value) => {
          const tag = document.createElement('span');
          tag.className = 'mini-tag';
          tag.textContent = value;
          meta.appendChild(tag);
        });
        const summary = document.createElement('div');
        summary.className = 'cap-summary';
        summary.textContent = item.summary_zh || '';
        button.append(titleEl, meta, summary);
        button.addEventListener('click', () => selectCatalogObject(item.object_id));
        container.appendChild(button);
      });
    });
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<div class="empty-icon">⌕</div><strong>没有匹配对象</strong><span>调整中文关键词或层级筛选。</span>';
      container.appendChild(empty);
    }
  }

  async function selectCatalogObject(objectId) {
    const object = state.presentationObjects.find((item) => item.object_id === objectId);
    if (!object) return selectCapability(objectId);
    state.selectedObjectId = objectId;
    const configurationCapability = object.primary_capability_id || object.configuration_capability_id;
    if (!configurationCapability) {
      state.selectedCapabilityId = null;
      state.schema = null;
      state.formData = null;
      state.taskSpec = null;
      state.planning = null;
      state.activeTaskCenterId = null;
      updateTaskEditState();
      el('selectedCapabilityName').textContent = object.name_zh;
      el('selectedCapabilitySummary').textContent = `${object.summary_zh} 当前尚未注册可用于配置或执行的能力。`;
      el('previewBtn').disabled = true;
      el('runBtn').disabled = true;
      el('saveTaskBtn').disabled = true;
      renderVariantControl(object);
      renderCapabilityList();
      renderForm();
      renderEvents();
      el('qoiOptions').replaceChildren();
      el('plotOptions').replaceChildren();
      syncTaskSpecEditor();
      showNotice(`“${object.name_zh}”尚未绑定可配置能力。`, 'warning');
      return;
    }
    await selectCapability(configurationCapability, object);
    if (object.integration_only) {
      showNotice(`“${object.name_zh}”通过${object.subsystem_zh || '所属'}分系统组合模型配置和运行；页面已展开其所属分系统参数，当前对象模板会优先显示。`, 'warning');
    }
  }

  async function selectCapability(capabilityId, objectOverride = null) {
    if (capabilityId === state.selectedCapabilityId && state.schema && !objectOverride) return;
    const capabilityChanged = capabilityId !== state.selectedCapabilityId;
    clearNotice();
    showBusy('读取能力 Schema', capabilityId);
    try {
      const payload = await api(`/forms/capabilities/${encodeURIComponent(capabilityId)}`);
      const object = objectOverride || presentationObjectForCapability(capabilityId);
      state.selectedObjectId = object?.object_id || state.selectedObjectId || capabilityId;
      state.selectedCapabilityId = capabilityId;
      if (capabilityChanged) clearVisualRunOverlay();
      state.schema = payload.form_schema;
      state.formData = clone(state.schema.default_form);
      state.outputPlotQuery = '';
      state.outputPlotSelectedOnly = false;
      state.taskSpec = null;
      state.planning = null;
      state.activeTaskCenterId = null;
      updateTaskEditState();
      state.changedPaths = new Set();
      state.agentBeforeForm = null;
      renderAgentChangePanel();
      const variant = object?.variants?.find((item) => item.capability_id === capabilityId);
      el('selectedCapabilityName').textContent = object
        ? `${object.name_zh}${object.variants.length > 1 && variant ? ` · ${variant.name_zh}` : ''}`
        : (state.schema.capability.name || capabilityId);
      const baseSummary = object?.summary_zh || state.schema.capability.summary || `${levelLabel(state.schema.capability.level)} · ${state.schema.capability.target}`;
      const governanceSummary = variant
        ? `运行后端：${variant.backend_type || '未声明'}；产品等级：${variant.recommended ? '推荐' : (variant.product_tier || variant.lifecycle_status || '未声明')}${variant.requires_allow_proxy ? '；需要允许兼容/代理路径' : ''}`
        : '';
      el('selectedCapabilitySummary').textContent = governanceSummary ? `${baseSummary} ${governanceSummary}` : baseSummary;
      el('previewBtn').disabled = false;
      el('runBtn').disabled = false;
      el('saveTaskBtn').disabled = false;
      renderVariantControl(object);
      renderCapabilityList();
      renderForm();
      renderEffectSelect();
      renderEvents();
      renderOutputs();
      renderScenarioTemplateOptions();
      syncTaskSpecEditor();
      const graphModel = state.graphNodes.find((node) => node.type === 'model');
      if (graphModel && graphModel.capabilityId !== capabilityId) {
        graphModel.capabilityId = capabilityId;
        graphInvalidateCompiledState();
        graphHistoryReset();
      }
      renderGraphPalette();
      if (document.querySelector('.tab.active')?.dataset.tab === 'graph') renderGraph();
    } catch (error) {
      showNotice(`读取能力失败：${error.message}`, 'error');
    } finally {
      hideBusy();
    }
  }

  function customFieldShell(field) {
    const wrapper = document.createElement('div');
    wrapper.className = `form-field form-field-wide structured-parameter-editor${state.changedPaths.has(field.path) ? ' agent-changed' : ''}`;
    wrapper.dataset.fieldPath = field.path;
    const heading = document.createElement('div'); heading.className = 'field-label'; heading.textContent = field.label || field.path;
    if (field.required) { const required=document.createElement('span'); required.className='required'; required.textContent='*'; heading.appendChild(required); }
    if (field.unit) { const unit=document.createElement('span'); unit.className='unit'; unit.textContent=field.unit; heading.appendChild(unit); }
    const description = document.createElement('div'); description.className='field-description'; description.textContent=field.description || '';
    wrapper.append(heading, description);
    return wrapper;
  }

  function parseVectorInput(text, fallback = [0, 0, 0]) {
    const value = String(text || '').trim();
    if (!value) return clone(fallback);
    let parsed;
    try { parsed = value.startsWith('[') ? JSON.parse(value) : value.split(/[，,\s]+/).filter(Boolean).map(Number); }
    catch (_) { throw new Error('请输入三个数值，例如 [0.08, 0, 0.02]'); }
    if (!Array.isArray(parsed) || parsed.length !== 3 || parsed.some((item) => !Number.isFinite(Number(item)))) throw new Error('必须填写三个有效数值');
    return parsed.map(Number);
  }

  function createEnvironmentTorqueEditor(field) {
    const wrapper = customFieldShell(field);
    const schema = field.editor_schema || {};
    const value = clone(getPath(state.formData, field.path) || field.default || {});
    const panel = document.createElement('div'); panel.className='environment-torque-editor';
    const commit = () => {
      setPath(state.formData, field.path, value);
      state.taskSpec = null;
      state.changedPaths.delete(field.path);
      wrapper.classList.remove('agent-changed','field-error'); wrapper.querySelector('.field-error-message')?.remove();
      validateClientForm({show:false}); renderAgentChangePanel();
    };
    (schema.groups || []).forEach((group) => {
      const card = document.createElement('section'); card.className='torque-source-card';
      const header = document.createElement('label'); header.className='torque-source-header';
      const toggle = document.createElement('input'); toggle.type='checkbox'; toggle.checked=Boolean(value[group.enabled_path]);
      const title = document.createElement('span'); title.textContent=group.label || group.id;
      header.append(toggle,title); card.appendChild(header);
      const detail = document.createElement('p'); detail.className='field-description'; detail.textContent=group.description || ''; card.appendChild(detail);
      const grid = document.createElement('div'); grid.className='structured-editor-grid';
      const syncDisabled=()=>{ grid.classList.toggle('is-disabled',!toggle.checked); grid.querySelectorAll('input').forEach((input)=>{ input.disabled=!toggle.checked; }); };
      toggle.addEventListener('change',()=>{ value[group.enabled_path]=toggle.checked; syncDisabled(); commit(); });
      (group.fields || []).forEach((item) => {
        const cell=document.createElement('label'); cell.className='compact-structured-field';
        const label=document.createElement('span'); label.className='field-label'; label.textContent=item.label || item.path;
        if(item.unit){const unit=document.createElement('span');unit.className='unit';unit.textContent=item.unit;label.appendChild(unit);}
        const input=document.createElement('input'); input.type=item.type==='number'?'number':'text'; input.step='any';
        if(item.minimum!=null) input.min=String(item.minimum);
        const current=value[item.path] ?? item.default ?? (item.type==='vector3'?[0,0,0]:'');
        input.value=Array.isArray(current)?JSON.stringify(current):String(current);
        input.addEventListener('change',()=>{
          try{
            value[item.path]=item.type==='vector3'?parseVectorInput(input.value,item.default):Number(input.value);
            if(item.type==='number'&&!Number.isFinite(value[item.path])) throw new Error('必须是有效数值');
            commit();
          }catch(error){setFieldError(field.path,`${item.label || item.path}：${error.message}`);}
        });
        input.addEventListener('blur',()=>input.dispatchEvent(new Event('change')),{once:false});
        cell.append(label,input); grid.appendChild(cell);
      });
      card.appendChild(grid); panel.appendChild(card); syncDisabled();
    });
    const note=document.createElement('div'); note.className='inline-guidance'; note.textContent='额外固定外力矩请使用旁边的“扰动力矩”三轴参数；本配置用于按物理来源计算环境力矩。';
    wrapper.append(panel,note);
    return wrapper;
  }

  function createSampleProfileEditor(field) {
    const wrapper = customFieldShell(field);
    const current = getPath(state.formData, field.path);
    const controls=document.createElement('div'); controls.className='profile-editor';
    const mode=document.createElement('select');
    [['fixed','使用固定参数'],['profile','按采样点输入时序']].forEach(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;mode.appendChild(option);});
    mode.value=Array.isArray(current)&&current.length?'profile':'fixed';
    const input=document.createElement('textarea'); input.rows=3; input.spellcheck=false; input.placeholder='例如：300, 302, 305, 307；也可填写 [300, 302, 305, 307]';
    input.value=Array.isArray(current)?current.join(', '):'';
    const preview=document.createElement('div'); preview.className='inline-guidance';
    const refresh=()=>{
      const duration=Number(getPath(state.formData,'simulation.duration_s') || 0);
      const sample=Number(getPath(state.formData,'simulation.sample_s') || 0);
      const count=duration>0&&sample>0?Math.max(1,Math.ceil(duration/sample)):null;
      const fallback=field.fallback_path?getPath(state.formData,field.fallback_path):undefined;
      preview.textContent=mode.value==='fixed'
        ? `当前使用固定参数${fallback!==undefined?`：${fallback}${field.unit?` ${field.unit}`:''}`:''}。`
        : `序列按输出采样点依次使用${count?`，当前配置建议填写约 ${count} 个值`:''}；不足时保持最后一个值，超出部分不使用。`;
      input.hidden=mode.value!=='profile';
    };
    mode.addEventListener('change',()=>{
      if(mode.value==='fixed'){deletePath(state.formData,field.path);state.taskSpec=null;validateClientForm({show:false});}
      refresh();
    });
    const commit=()=>{
      if(mode.value!=='profile') return;
      try{
        const raw=input.value.trim();
        const parsed=raw.startsWith('[')?JSON.parse(raw):raw.split(/[，,\s]+/).filter(Boolean).map(Number);
        if(!Array.isArray(parsed)||!parsed.length||parsed.some((item)=>!Number.isFinite(Number(item)))) throw new Error('请输入至少一个有效数值');
        setPath(state.formData,field.path,parsed.map(Number)); state.taskSpec=null;
        wrapper.classList.remove('field-error');wrapper.querySelector('.field-error-message')?.remove();validateClientForm({show:false});
      }catch(error){setFieldError(field.path,error.message);}
    };
    input.addEventListener('change',commit); input.addEventListener('blur',commit);
    controls.append(mode,input,preview); wrapper.appendChild(controls); refresh();
    return wrapper;
  }

  function renderForm() {
    const form = el('dynamicForm');
    form.replaceChildren();
    if (!state.schema || !state.formData) return;
    const query = (el('formFieldSearch')?.value || '').trim().toLowerCase();
    const showAdvanced = Boolean(el('showAdvancedFields')?.checked);
    const fieldsByPath = new Map((state.schema.fields || []).map((field) => [field.path, field]));
    const sections = (state.schema.sections || []).filter((section) => Array.isArray(section.field_paths));

    function createField(field) {
      if (field.widget === 'environment_torque_editor') return createEnvironmentTorqueEditor(field);
      if (field.widget === 'sample_profile_editor') return createSampleProfileEditor(field);
      const wrapper = document.createElement('label');
      wrapper.className = `form-field${state.changedPaths.has(field.path) ? ' agent-changed' : ''}`;
      wrapper.dataset.fieldPath = field.path;
      const heading = document.createElement('span'); heading.className = 'field-label'; heading.textContent = field.label || field.path;
      if (field.required) { const required=document.createElement('span'); required.className='required'; required.textContent='*'; heading.appendChild(required); }
      if (field.unit) { const unit=document.createElement('span'); unit.className='unit'; unit.textContent=field.unit; heading.appendChild(unit); }
      const description = document.createElement('span'); description.className='field-description'; description.textContent=field.description || '该参数由能力注册表定义。';
      let input;
      if (field.widget === 'select') {
        input=document.createElement('select');
        (field.enum || []).forEach((value)=>{
          const option=document.createElement('option');
          const technicalValue=String(value);
          option.value=technicalValue;
          option.textContent=field.enum_labels?.[technicalValue] || technicalValue;
          option.title=`技术值：${technicalValue}`;
          input.appendChild(option);
        });
      } else if (field.widget === 'checkbox') {
        input=document.createElement('input'); input.type='checkbox';
      } else if (['object', 'object_editor'].includes(field.widget) || String(field.widget).startsWith('array[') || field.widget === 'array') {
        input=document.createElement('textarea'); input.rows=2; input.spellcheck=false;
      } else {
        input=document.createElement('input');
        input.type=['number','integer'].includes(field.type) ? 'number' : 'text';
        if (field.minimum != null) input.min=String(field.minimum);
        if (field.maximum != null) input.max=String(field.maximum);
        input.step=field.type === 'integer' ? '1' : 'any';
        if (['number_or_array[number]','vector3_or_number'].includes(field.widget)) input.placeholder='输入单值，或 [1, 2, 3]';
      }
      input.dataset.path=field.path;
      const current=getPath(state.formData,field.path);
      if (input.type==='checkbox') input.checked=Boolean(current); else input.value=displayFieldValue(current ?? field.default ?? '',field);
      const commit=()=>{
        try {
          let value;
          if (input.type==='checkbox') value=input.checked;
          else if (['number','integer'].includes(field.type)) {
            if (input.value==='') value=undefined;
            else { value=Number(input.value); if (!Number.isFinite(value)) throw new Error('必须是数值'); if (field.type==='integer' && !Number.isInteger(value)) throw new Error('必须是整数'); }
          } else if (['object','object_editor','array','number_or_array[number]','vector3_or_number'].includes(field.widget) || String(field.widget).startsWith('array[')) value=parseStructuredValue(input.value,field);
          else value=input.value;
          if (value === undefined) deletePath(state.formData,field.path); else setPath(state.formData,field.path,value);
          state.taskSpec=null; state.changedPaths.delete(field.path); wrapper.classList.remove('agent-changed','field-error'); wrapper.querySelector('.field-error-message')?.remove();
          validateClientForm({show:false}); renderAgentChangePanel();
        } catch (error) {
          setFieldError(field.path,error.message);
        }
      };
      input.addEventListener('change',commit);
      if (input.tagName==='TEXTAREA' || input.type==='text') input.addEventListener('blur',commit);
      wrapper.append(heading,input,description);
      return wrapper;
    }

    sections.forEach((section)=>{
      const candidates=(section.field_paths || []).map((path)=>fieldsByPath.get(path)).filter(Boolean);
      const filtered=candidates.filter((field)=>{
        const haystack=`${field.label || ''} ${field.path} ${field.description || ''} ${field.unit || ''}`.toLowerCase();
        return !query || haystack.includes(query);
      });
      if (!filtered.length) return;
      const card=document.createElement('section'); card.className='schema-section';
      const header=document.createElement('div'); header.className='section-heading compact';
      const titleWrap=document.createElement('div'); const title=document.createElement('h3'); title.textContent=section.title || section.id; const desc=document.createElement('p'); desc.textContent=section.description || '';
      titleWrap.append(title,desc); header.appendChild(titleWrap); card.appendChild(header);
      const common=document.createElement('div'); common.className='dynamic-form-grid';
      const advanced=document.createElement('details'); advanced.className='advanced-fields'; advanced.open=showAdvanced || Boolean(query);
      const summary=document.createElement('summary'); summary.textContent='专业参数（按需展开）'; const advancedGrid=document.createElement('div'); advancedGrid.className='dynamic-form-grid'; advanced.append(summary,advancedGrid);
      filtered.forEach((field)=>{
        const node=createField(field);
        if (field.importance==='advanced' && !query) advancedGrid.appendChild(node); else common.appendChild(node);
      });
      if (common.children.length) card.appendChild(common);
      if (advancedGrid.children.length) card.appendChild(advanced);
      form.appendChild(card);
    });
    validateClientForm({show:false});
  }

  function effectCatalog(kind) {
    if (!state.schema) return [];
    if (kind === 'fault') return state.schema.event_catalog.faults || [];
    if (kind === 'degradation') return state.schema.event_catalog.degradations || [];
    return state.schema.event_catalog.constraints || [];
  }

  function renderEffectSelect() {
    const kind = el('effectKind').value;
    const select = el('effectSelect');
    select.replaceChildren();
    effectCatalog(kind).forEach((item) => {
      const option = document.createElement('option');
      option.value = item.effect;
      option.textContent = item.label || item.effect;
      select.appendChild(option);
    });
    el('addEffectBtn').disabled = !select.options.length;
  }

  function addEffect() {
    const kind = el('effectKind').value;
    const effect = el('effectSelect').value;
    const template = effectCatalog(kind).find((item) => item.effect === effect);
    if (!template) return;
    const key = kind === 'fault' ? 'faults' : (kind === 'degradation' ? 'degradations' : 'constraints');
    const event = clone(template.default_event);
    event.id = `${effect}_${state.formData.events[key].length + 1}`;
    if (kind === 'degradation' && state.schema.event_catalog.timed_degradation_supported === false) event.start_s = 0;
    state.formData.events[key].push(event);
    state.taskSpec = null;
    state.changedPaths.delete(`events.${key}`);
    renderEvents();
    renderAgentChangePanel();
  }

  function renderEvents() {
    const container = el('eventList');
    container.replaceChildren();
    if (!state.formData) return;
    const events = [
      ...(state.formData.events.faults || []).map((event, index) => ({ event, kind: 'fault', key: 'faults', index })),
      ...(state.formData.events.degradations || []).map((event, index) => ({ event, kind: 'degradation', key: 'degradations', index })),
      ...(state.formData.events.constraints || []).map((event, index) => ({ event, kind: 'constraint', key: 'constraints', index })),
    ];
    if (!events.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.style.minHeight = '84px';
      empty.innerHTML = '<strong>未配置事件</strong><span>当前任务按标称模式运行。</span>';
      container.appendChild(empty);
      return;
    }
    events.forEach(({ event, kind, key, index }) => {
      const card = document.createElement('div');
      card.className = `event-card${state.changedPaths.has(`events.${key}`) ? ' agent-changed' : ''}`;
      const top = document.createElement('div');
      top.className = 'event-top';
      const nameWrap = document.createElement('div');
      const kindEl = document.createElement('div');
      kindEl.className = 'event-kind';
      kindEl.textContent = kind === 'fault' ? '故障' : (kind === 'degradation' ? '退化' : '运行约束');
      const name = document.createElement('div');
      name.className = 'event-name';
      name.textContent = event.label || effectCatalog(event.event_type || event.kind).find((item) => item.effect === event.effect)?.label || event.effect;
      nameWrap.append(kindEl, name);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'remove-event';
      remove.title = '删除事件';
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        state.formData.events[key].splice(index, 1);
        state.taskSpec = null;
        state.changedPaths.delete(`events.${key}`);
        renderEvents();
        renderAgentChangePanel();
      });
      top.append(nameWrap, remove);
      const template = effectCatalog(kind).find((item) => item.effect === event.effect) || {};
      const guidance = document.createElement('div');
      guidance.className = 'event-guidance';
      guidance.textContent = template.instructions || template.description || '请设置事件时间窗和专用参数。';
      const fields = document.createElement('div');
      fields.className = 'event-fields';

      const addNumberField = (host, field, labelText, options = {}) => {
        const label = document.createElement('label');
        const title = document.createElement('span');
        title.textContent = labelText;
        label.appendChild(title);
        const input = document.createElement('input');
        input.type = 'number';
        input.step = options.integer ? '1' : 'any';
        if (options.minimum !== undefined) input.min = String(options.minimum);
        if (options.maximum !== undefined) input.max = String(options.maximum);
        input.value = options.value ?? '';
        if (options.disabled) input.disabled = true;
        if (options.description) input.title = options.description;
        input.addEventListener('change', () => {
          if (input.value === '') options.onChange(undefined);
          else {
            const parsed = Number(input.value);
            if (!Number.isFinite(parsed)) return;
            options.onChange(options.integer ? Math.trunc(parsed) : parsed);
          }
          state.taskSpec = null;
          state.changedPaths.delete(`events.${key}`);
          card.classList.remove('agent-changed');
          renderAgentChangePanel();
        });
        label.appendChild(input);
        if (options.description) {
          const help = document.createElement('small'); help.textContent = options.description; label.appendChild(help);
        }
        host.appendChild(label);
      };

      addNumberField(fields, 'start_s', '开始时间 / s', {
        value: event.start_s ?? 0,
        disabled: kind === 'degradation' && state.schema.event_catalog.timed_degradation_supported === false,
        description: '事件开始生效的仿真时间。',
        onChange: (value) => { if (value === undefined) delete event.start_s; else event.start_s = value; },
      });
      addNumberField(fields, 'end_s', '结束时间 / s', {
        value: event.end_s ?? '',
        description: '可留空，表示从开始时间持续到仿真结束。',
        onChange: (value) => { if (value === undefined) delete event.end_s; else event.end_s = value; },
      });
      if (template.magnitude_applicable !== false) {
        addNumberField(fields, 'magnitude', '通用幅值', {
          value: event.magnitude ?? '', minimum: 0, maximum: 1,
          description: '仅用于采用归一化严重度的事件，0表示无影响，1表示最大影响。若事件提供专用参数，应优先使用专用参数。',
          onChange: (value) => { if (value === undefined) delete event.magnitude; else event.magnitude = value; },
        });
      } else if ('magnitude' in event) {
        delete event.magnitude;
      }

      event.parameters = event.parameters && typeof event.parameters === 'object' ? event.parameters : {};
      (template.parameter_fields || []).forEach((fieldMeta) => {
        const name = fieldMeta.name;
        const unit = fieldMeta.unit ? ` / ${fieldMeta.unit}` : '';
        const type = String(fieldMeta.type || 'number');
        if (type === 'vector3_or_number' || type.startsWith('array')) {
          const label = document.createElement('label');
          const title = document.createElement('span'); title.textContent = `${fieldMeta.label || name}${unit}`;
          const input = document.createElement('input'); input.type = 'text';
          const current = event.parameters[name] ?? fieldMeta.default ?? '';
          input.value = Array.isArray(current) ? JSON.stringify(current) : String(current);
          input.title = fieldMeta.description || '';
          input.addEventListener('change', () => {
            const text = input.value.trim();
            if (!text) delete event.parameters[name];
            else {
              try { event.parameters[name] = text.startsWith('[') ? JSON.parse(text) : Number(text); }
              catch (_) { input.setCustomValidity('请输入数值或JSON数组'); input.reportValidity(); return; }
              input.setCustomValidity('');
            }
            state.taskSpec = null; renderAgentChangePanel();
          });
          label.append(title, input);
          if (fieldMeta.description) { const help=document.createElement('small'); help.textContent=fieldMeta.description; label.appendChild(help); }
          fields.appendChild(label);
        } else {
          addNumberField(fields, `parameters.${name}`, `${fieldMeta.label || name}${unit}`, {
            value: event.parameters[name] ?? fieldMeta.default ?? '',
            minimum: fieldMeta.minimum, maximum: fieldMeta.maximum,
            integer: type === 'integer', description: fieldMeta.description || '',
            onChange: (value) => { if (value === undefined) delete event.parameters[name]; else event.parameters[name] = value; },
          });
        }
      });
      card.append(top, guidance, fields);
      container.appendChild(card);
    });
  }

  function renderOutputs() {
    if (!state.schema || !state.formData) return;
    renderCheckOptions('qoiOptions', state.schema.outputs.summary_options || [], state.formData.outputs.qoi || [], 'qoi');
    renderCheckOptions('plotOptions', state.schema.outputs.trace_options || [], state.formData.outputs.plots || [], 'plots');
    renderAdvancedOutputs();
  }

  function ensureAdvancedOutputs() {
    if (!state.formData) return null;
    state.formData.outputs = state.formData.outputs || {};
    if (!Array.isArray(state.formData.outputs.telemetry_streams)) state.formData.outputs.telemetry_streams = [];
    if (!state.formData.outputs.fmea || typeof state.formData.outputs.fmea !== 'object') {
      state.formData.outputs.fmea = { enabled: false, formats: ['csv', 'json'] };
    }
    if (!Array.isArray(state.formData.outputs.fmea.formats)) state.formData.outputs.fmea.formats = ['csv', 'json'];
    return state.formData.outputs;
  }

  function markOutputChanged() {
    state.taskSpec = null;
    syncTaskSpecEditor();
  }

  function renderAdvancedOutputs() {
    const outputs = ensureAdvancedOutputs();
    const container = el('telemetryStreamList');
    if (!outputs || !container) return;
    container.replaceChildren();
    const streams = outputs.telemetry_streams;
    if (!streams.length) {
      const empty = document.createElement('div');
      empty.className = 'output-coverage-note';
      empty.textContent = '未配置多速率遥测流；基础 telemetry 仍按 simulation.sample_s 输出。';
      container.appendChild(empty);
    }
    streams.forEach((stream, index) => {
      const row = document.createElement('div');
      row.className = 'telemetry-stream-row';
      const idInput = document.createElement('input');
      idInput.type = 'text'; idInput.value = stream.stream_id || ''; idInput.placeholder = 'stream_id'; idInput.setAttribute('aria-label', '遥测流ID');
      idInput.addEventListener('change', () => { stream.stream_id = idInput.value.trim(); markOutputChanged(); });
      const sampleInput = document.createElement('input');
      sampleInput.type = 'number'; sampleInput.min = '0.000000001'; sampleInput.step = 'any'; sampleInput.value = stream.sample_s ?? state.formData.simulation.sample_s;
      sampleInput.setAttribute('aria-label', '采样周期秒');
      sampleInput.addEventListener('change', () => { stream.sample_s = Number(sampleInput.value); markOutputChanged(); });
      const formatSelect = document.createElement('select');
      ['csv', 'jsonl'].forEach((value) => formatSelect.appendChild(new Option(value.toUpperCase(), value)));
      formatSelect.value = stream.format || 'csv';
      formatSelect.addEventListener('change', () => { stream.format = formatSelect.value; markOutputChanged(); });
      const fieldsInput = document.createElement('textarea');
      fieldsInput.rows = 2; fieldsInput.value = (stream.fields || []).join(', '); fieldsInput.placeholder = '字段名，以逗号分隔';
      fieldsInput.setAttribute('aria-label', '遥测字段');
      fieldsInput.addEventListener('change', () => {
        stream.fields = fieldsInput.value.split(/[，,\n]+/).map((item) => item.trim()).filter(Boolean);
        markOutputChanged();
      });
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'button button-danger'; remove.textContent = '删除';
      remove.addEventListener('click', () => { outputs.telemetry_streams.splice(index, 1); markOutputChanged(); renderAdvancedOutputs(); });
      const idLabel = document.createElement('label'); idLabel.innerHTML = '<span>流 ID</span>'; idLabel.appendChild(idInput);
      const sampleLabel = document.createElement('label'); sampleLabel.innerHTML = '<span>周期（s）</span>'; sampleLabel.appendChild(sampleInput);
      const formatLabel = document.createElement('label'); formatLabel.innerHTML = '<span>格式</span>'; formatLabel.appendChild(formatSelect);
      const fieldsLabel = document.createElement('label'); fieldsLabel.className = 'telemetry-fields'; fieldsLabel.innerHTML = '<span>字段</span>'; fieldsLabel.appendChild(fieldsInput);
      row.append(idLabel, sampleLabel, formatLabel, fieldsLabel, remove);
      container.appendChild(row);
    });
    const fmea = outputs.fmea;
    el('fmeaEnabled').checked = Boolean(fmea.enabled);
    el('fmeaFormatCsv').checked = fmea.formats.includes('csv');
    el('fmeaFormatJson').checked = fmea.formats.includes('json');
  }

  function addTelemetryStream() {
    const outputs = ensureAdvancedOutputs();
    if (!outputs) return;
    const used = new Set(outputs.telemetry_streams.map((item) => item.stream_id));
    let index = outputs.telemetry_streams.length + 1;
    while (used.has(`telemetry_${index}`)) index += 1;
    const fields = (state.formData.outputs.plots || state.schema.outputs.trace_fields || []).slice(0, 4);
    outputs.telemetry_streams.push({
      stream_id: `telemetry_${index}`,
      sample_s: Number(state.formData.simulation.sample_s || 1),
      fields: fields.length ? fields : ['time_s'],
      format: 'csv',
    });
    markOutputChanged();
    renderAdvancedOutputs();
  }

  const OUTPUT_SCOPE_ORDER = ['whole_spacecraft', 'subsystem', 'component'];

  function fallbackOutputHierarchy(field, metadata = {}) {
    if (metadata.scope_id && metadata.scope_label) return metadata;
    const name = String(field || '').toLowerCase();
    const componentPrefixes = [
      'adcs.sensor.', 'adcs.rw.', 'eps.battery.', 'eps.solar.', 'eps.loads.', 'eps.pdu.',
      'thermal.node.', 'thermal.heater.', 'thermal.radiator.', 'ground.', 'comm.link.',
      'comm.transmitter.', 'comm.tx.', 'storage.', 'rw.',
    ];
    const wholePrefixes = [
      'orbit.', 'environment.', 'coupled.data.', 'battery_', 'net_power_', 'data_',
      'thermal_', 'attitude_', 'rate_error_', 'payload_', 'downlink_', 'propellant_', 'tank_',
    ];
    const subsystemRows = [
      ['attitude', 'adcs', '姿态控制分系统'], ['control', 'adcs', '姿态控制分系统'],
      ['rw', 'adcs', '姿态控制分系统'],
      ['adcs', 'adcs', '姿态控制分系统'], ['eps', 'eps', '能源分系统'],
      ['thermal', 'thermal', '热控分系统'], ['comm_data', 'comm_data', '通信与数据分系统'],
      ['comm', 'comm_data', '通信与数据分系统'], ['ground', 'comm_data', '通信与数据分系统'],
      ['storage', 'comm_data', '通信与数据分系统'], ['payload', 'payload', '载荷分系统'],
      ['propulsion', 'propulsion', '推进分系统'], ['coupled', 'coupled', '跨分系统耦合'],
    ];
    const prefix = name.split('.', 1)[0];
    const subsystem = subsystemRows.find(([candidate]) => prefix === candidate || name.startsWith(`${candidate}.`));
    const isWhole = wholePrefixes.some((candidate) => name.startsWith(candidate));
    const isComponent = componentPrefixes.some((candidate) => name.startsWith(candidate));
    return {
      ...metadata,
      scope_id: isWhole ? 'whole_spacecraft' : (isComponent ? 'component' : 'subsystem'),
      scope_label: isWhole ? '整星' : (isComponent ? '部件' : '分系统'),
      subsystem_id: isWhole ? 'whole_spacecraft' : (subsystem?.[1] || 'other'),
      subsystem_label: isWhole ? '整星综合' : (subsystem?.[2] || '其他分系统'),
      node_id: metadata.node_id || metadata.group_id || 'other',
      node_label: metadata.node_label || metadata.group_label || '其他可观测量',
    };
  }

  function normalizedOutputOption(optionValue) {
    const option = typeof optionValue === 'string'
      ? { name: optionValue, label: optionValue, description: '', group_id: 'other', group_label: '其他可观测量' }
      : optionValue;
    return fallbackOutputHierarchy(option.name || option.field, option);
  }

  function outputHierarchyTree(options) {
    const scopes = new Map();
    options.forEach((option) => {
      const scopeKey = option.scope_id || 'subsystem';
      if (!scopes.has(scopeKey)) scopes.set(scopeKey, { id: scopeKey, label: option.scope_label || '分系统', options: [], subsystems: new Map() });
      const scope = scopes.get(scopeKey);
      scope.options.push(option);
      const subsystemKey = option.subsystem_id || 'other';
      if (!scope.subsystems.has(subsystemKey)) scope.subsystems.set(subsystemKey, { id: subsystemKey, label: option.subsystem_label || '其他分系统', options: [], nodes: new Map() });
      const subsystem = scope.subsystems.get(subsystemKey);
      subsystem.options.push(option);
      const nodeKey = option.node_id || option.group_id || 'other';
      if (!subsystem.nodes.has(nodeKey)) subsystem.nodes.set(nodeKey, { id: nodeKey, label: option.node_label || option.group_label || '其他可观测量', options: [] });
      subsystem.nodes.get(nodeKey).options.push(option);
    });
    return [...scopes.values()].sort((left, right) => OUTPUT_SCOPE_ORDER.indexOf(left.id) - OUTPUT_SCOPE_ORDER.indexOf(right.id));
  }

  function hierarchyMatches(option, query) {
    if (!query) return true;
    const haystack = [option.name, option.field, option.label, option.description, option.scope_label, option.subsystem_label, option.node_label, option.group_label]
      .filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(query.toLowerCase());
  }

  function hierarchySummary(label, options, selectedSet) {
    const summary = document.createElement('summary');
    const title = document.createElement('span'); title.textContent = label;
    const count = document.createElement('small');
    const selectedCount = options.filter((item) => selectedSet.has(item.name || item.field)).length;
    count.textContent = selectedCount ? `${selectedCount} 已选 / ${options.length}` : `${options.length} 项`;
    summary.append(title, count);
    return summary;
  }

  function renderPlotOptions(container, options, selected, targetKey) {
    const selectedSet = new Set(selected);
    const normalized = options.map(normalizedOutputOption);
    const toolbar = document.createElement('div'); toolbar.className = 'output-hierarchy-toolbar';
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = '搜索输出量、分系统或部件…'; search.value = state.outputPlotQuery;
    const selectedOnly = document.createElement('label'); selectedOnly.className = 'hierarchy-selected-only';
    const selectedInput = document.createElement('input'); selectedInput.type = 'checkbox'; selectedInput.checked = state.outputPlotSelectedOnly;
    const selectedText = document.createElement('span'); selectedText.textContent = '仅看已选'; selectedOnly.append(selectedInput, selectedText);
    const counter = document.createElement('span'); counter.className = 'hierarchy-counter'; counter.textContent = `已选 ${selectedSet.size} / ${normalized.length}`;
    const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'button button-ghost hierarchy-clear'; clear.textContent = '清空'; clear.disabled = selectedSet.size === 0;
    toolbar.append(search, selectedOnly, counter, clear); container.appendChild(toolbar);
    const note = document.createElement('div'); note.className = 'output-coverage-note'; note.textContent = state.schema.outputs.coverage_note; container.appendChild(note);

    const query = state.outputPlotQuery.trim();
    const visible = normalized.filter((option) => hierarchyMatches(option, query) && (!state.outputPlotSelectedOnly || selectedSet.has(option.name)));
    const tree = outputHierarchyTree(visible);
    if (!tree.length) {
      const empty = document.createElement('div'); empty.className = 'hierarchy-empty'; empty.textContent = '没有匹配的输出量，请调整搜索或取消“仅看已选”。'; container.appendChild(empty);
    }
    tree.forEach((scope) => {
      const scopeHost = document.createElement('details'); scopeHost.className = 'output-scope-group';
      scopeHost.open = Boolean(query) || scope.options.some((item) => selectedSet.has(item.name));
      scopeHost.appendChild(hierarchySummary(scope.label, scope.options, selectedSet));
      [...scope.subsystems.values()].forEach((subsystem) => {
        const subsystemHost = document.createElement('details'); subsystemHost.className = 'output-subsystem-group';
        subsystemHost.open = Boolean(query) || subsystem.options.some((item) => selectedSet.has(item.name));
        subsystemHost.appendChild(hierarchySummary(subsystem.label, subsystem.options, selectedSet));
        [...subsystem.nodes.values()].forEach((node) => {
          const nodeHost = document.createElement('details'); nodeHost.className = 'output-node-group';
          nodeHost.open = Boolean(query) || node.options.some((item) => selectedSet.has(item.name));
          nodeHost.appendChild(hierarchySummary(node.label, node.options, selectedSet));
          const grid = document.createElement('div'); grid.className = 'output-option-grid';
          node.options.forEach((option) => {
            const value = option.name;
            const label = document.createElement('label'); label.className = `check-option descriptive${state.changedPaths.has(`outputs.${targetKey}`) ? ' agent-changed' : ''}`; label.title = option.description || '';
            const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selectedSet.has(value);
            input.addEventListener('change', () => {
              const list = state.formData.outputs[targetKey] || [];
              state.formData.outputs[targetKey] = input.checked ? [...new Set([...list, value])] : list.filter((item) => item !== value);
              state.taskSpec = null; state.changedPaths.delete(`outputs.${targetKey}`); renderAgentChangePanel(); renderOutputs();
            });
            const text = document.createElement('span'); const title = document.createElement('strong'); title.textContent = option.label || value;
            const code = document.createElement('code'); code.textContent = value;
            const desc = document.createElement('small'); desc.textContent = option.description || '能力注册表定义的可观测量。';
            text.append(title, code, desc); label.append(input, text); grid.appendChild(label);
          });
          nodeHost.appendChild(grid); subsystemHost.appendChild(nodeHost);
        });
        scopeHost.appendChild(subsystemHost);
      });
      container.appendChild(scopeHost);
    });
    search.addEventListener('input', () => {
      state.outputPlotQuery = search.value;
      renderOutputs();
      requestAnimationFrame(() => { const next = el('plotOptions')?.querySelector('input[type="search"]'); next?.focus(); next?.setSelectionRange(next.value.length, next.value.length); });
    });
    selectedInput.addEventListener('change', () => { state.outputPlotSelectedOnly = selectedInput.checked; renderOutputs(); });
    clear.addEventListener('click', () => { state.formData.outputs[targetKey] = []; state.taskSpec = null; renderOutputs(); syncTaskSpecEditor(); });
  }

  function renderCheckOptions(containerId, options, selected, targetKey) {
    const container=el(containerId); container.replaceChildren();
    if (targetKey === 'plots') { renderPlotOptions(container, options, selected, targetKey); return; }
    const groups=new Map();
    options.forEach((optionValue)=>{
      const option=typeof optionValue==='string' ? {name:optionValue,label:optionValue,description:'',group_id:'other',group_label:'其他'} : optionValue;
      const key='all';
      if (!groups.has(key)) groups.set(key,{label:'',options:[]});
      groups.get(key).options.push(option);
    });
    groups.forEach((group)=>{
      const host=document.createElement('div');
      const grid=document.createElement('div'); grid.className='output-option-grid';
      group.options.forEach((option)=>{
        const value=option.name;
        const label=document.createElement('label'); label.className=`check-option descriptive${state.changedPaths.has(`outputs.${targetKey}`)?' agent-changed':''}`; label.title=option.description || '';
        const input=document.createElement('input'); input.type='checkbox'; input.checked=selected.includes(value);
        input.addEventListener('change',()=>{
          const list=state.formData.outputs[targetKey] || [];
          state.formData.outputs[targetKey]=input.checked ? [...new Set([...list,value])] : list.filter((item)=>item!==value);
          state.taskSpec=null; state.changedPaths.delete(`outputs.${targetKey}`); renderAgentChangePanel(); renderOutputs();
        });
        const text=document.createElement('span'); const title=document.createElement('strong'); title.textContent=option.label || value; const code=document.createElement('code'); code.textContent=value; const desc=document.createElement('small'); desc.textContent=option.description || '能力注册表定义的可观测量。';
        text.append(title,code,desc); label.append(input,text); grid.appendChild(label);
      });
      host.appendChild(grid); container.appendChild(host);
    });
  }

  function syncTaskSpecEditor() {
    const value = state.taskSpec || state.formData || {};
    el('taskSpecEditor').value = JSON.stringify(value, null, 2);
  }

  async function previewForm() {
    if (!state.formData) throw new Error('请先选择能力');
    if (!validateClientForm()) return false;
    const payload=await api('/tasks/parse',{method:'POST',body:JSON.stringify({input_kind:'form',form_data:state.formData,compile_if_valid:true})});
    const result=payload.result; state.taskSpec=result.task_spec || null; state.planning=result.planning || null; syncTaskSpecEditor();
    if (!payload.ok) {
      const errors=[...(result.validation?.errors || []),...(result.guards?.issues || []),...(result.planning?.validation?.errors || [])];
      showValidationIssues(errors,'TaskSpec 校验失败');
    } else {
      clearFieldErrors();
      const nodes=result.planning?.execution_plan?.nodes?.length || result.planning?.plan?.nodes?.length || 0;
      showNotice(`校验通过：Schema、语义、能力兼容和执行计划均有效${nodes?`；计划包含 ${nodes} 个节点`:''}。尚未启动仿真。`,'success');
      switchTab('taskspec');
    }
    return payload.ok;
  }

  function renderAgentConversation() {
    const container=el('agentConversation'); if (!container) return; container.replaceChildren();
    if (!state.agentHistory.length) { const empty=document.createElement('div'); empty.className='agent-conversation-empty'; empty.textContent='可连续补充条件；后续指令将基于当前 TaskSpec 修改，而不是重新开始。'; container.appendChild(empty); return; }
    state.agentHistory.forEach((item)=>{ const bubble=document.createElement('div'); bubble.className=`agent-message ${item.role}`; const role=document.createElement('strong'); role.textContent=item.role==='user'?'用户':'Agent'; const text=document.createElement('span'); text.textContent=item.text; bubble.append(role,text); container.appendChild(bubble); });
    container.scrollTop=container.scrollHeight;
  }

  async function parseNaturalLanguage() {
    const text=el('agentInput').value.trim(); if (!text) throw new Error('请输入仿真需求');
    const refine=Boolean(el('agentRefineCurrent')?.checked && state.taskSpec);
    state.agentHistory.push({role:'user',text}); renderAgentConversation();
    const payload=await api('/tasks/parse',{method:'POST',body:JSON.stringify({
      input_kind:'natural_language',request_text:text,base_task_spec:refine?state.taskSpec:null,
      backend:'auto',local_backend:'template',provider_id:el('agentBackend').value || state.activeProviderId || 'local-template',
      routing_mode:el('agentRoutingMode').value || 'auto',compile_if_valid:true,
    })});
    const generatedTaskSpec=payload.result.task_spec || null; state.taskSpec=generatedTaskSpec; state.planning=payload.result.planning || null; syncTaskSpecEditor();
    if (payload.ok) {
      const capabilityId=generatedTaskSpec?.model?.capability_id; const changedCount=await projectTaskSpecToForm(generatedTaskSpec,refine?'Agent 多轮补充':'Agent');
      const provider=payload.result.route?.provider_id || el('agentBackend').value || 'local-template';
      const response=`已${refine?'更新':'生成'} ${generatedTaskSpec?.task?.name || capabilityId || 'TaskSpec'}；Provider=${provider}；同步 ${changedCount} 组表单差异。`;
      state.agentHistory.push({role:'assistant',text:response}); renderAgentConversation(); el('agentInput').value=''; showNotice(response,'success');
    } else {
      const errors=[...(payload.result.validation?.errors || []),...(payload.result.guards?.issues || [])]; const message=errors.map(formatValidationIssue).join('；') || '需求未通过能力边界校验';
      state.agentHistory.push({role:'assistant',text:`未通过：${message}`}); renderAgentConversation(); showValidationIssues(errors,message);
    }
    return payload.ok;
  }

  function taskSpecFromEditor() {
    try {
      const value = JSON.parse(el('taskSpecEditor').value);
      if (!value || typeof value !== 'object') throw new Error('TaskSpec 必须是 JSON 对象');
      return value;
    } catch (error) {
      throw new Error(`JSON 解析失败：${error.message}`);
    }
  }

  async function validateEditor() {
    const spec = taskSpecFromEditor();
    const payload = await api('/tasks/validate', { method: 'POST', body: JSON.stringify({ task_spec: spec }) });
    state.taskSpec = payload.task_spec || spec;
    syncTaskSpecEditor();
    if (payload.ok) showNotice('TaskSpec Schema、语义和能力边界校验通过。', 'success');
    else {
      const issues = [...(payload.validation?.errors || []), ...(payload.guards?.issues || [])];
      showNotice(issues.map((item) => item.message || item.code).join('；') || 'TaskSpec 校验失败', 'error');
    }
    return payload.ok;
  }

  async function syncEditorToForm() {
    const spec = taskSpecFromEditor();
    const payload = await api('/tasks/validate', { method: 'POST', body: JSON.stringify({ task_spec: spec }) });
    if (!payload.ok) {
      const issues = [...(payload.validation?.errors || []), ...(payload.guards?.issues || [])];
      throw new Error(issues.map((item) => item.message || item.code).join('；') || 'TaskSpec 校验失败');
    }
    const canonical = payload.task_spec || spec;
    const changedCount = await projectTaskSpecToForm(canonical, 'TaskSpec');
    showNotice(`TaskSpec 已同步到表单，共 ${changedCount} 组差异。`, 'success');
    return true;
  }

  function renderExecutionProgress(execution) {
    const stateName = execution?.state || 'QUEUED';
    const card = el('activeRunCard');
    card.className = 'active-run execution-progress';
    card.replaceChildren();
    const titleRow = document.createElement('div');
    titleRow.className = 'run-title-row';
    const runId = document.createElement('div');
    runId.className = 'run-id';
    const technicalRunId = execution?.run_id || state.activeRunId || '—';
    const displayName = state.activeRunDisplayName || state.taskSpec?.task?.name || '仿真任务';
    const displayTitle = document.createElement('strong');
    displayTitle.textContent = displayName;
    const displayTechnicalId = document.createElement('small');
    displayTechnicalId.textContent = `Run ID：${technicalRunId}`;
    runId.replaceChildren(displayTitle, displayTechnicalId);
    const status = document.createElement('span');
    status.className = `run-status ${String(stateName).toLowerCase()}`;
    status.textContent = stateName;
    titleRow.append(runId, status);
    const progress = document.createElement('div');
    progress.className = 'execution-progress-bar';
    progress.innerHTML = '<span></span>';
    const meta = document.createElement('div');
    meta.className = 'execution-progress-meta';
    const left = document.createElement('span');
    const queueHint = execution?.job_id ? ` · ${execution.job_id.slice(0, 12)}` : '';
    left.textContent = (stateName === 'QUEUED' ? '等待持久化队列 Worker' : (stateName === 'CANCEL_REQUESTED' ? '等待安全停止点' : 'Basilisk 正在执行')) + queueHint;
    const right = document.createElement('span');
    right.textContent = execution?.submitted_at ? `提交 ${new Date(execution.submitted_at).toLocaleTimeString('zh-CN')}` : '后台执行';
    meta.append(left, right);
    card.append(titleRow, progress, meta);
    el('cancelRunBtn').classList.toggle('hidden', !['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(stateName));
  }

  function stopExecutionPolling() {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  async function pollRunExecution(runId) {
    stopExecutionPolling();
    const tick = async () => {
      try {
        const payload = await api(`/runs/${encodeURIComponent(runId)}/execution`);
        const execution = payload.execution_state || {};
        state.executionState = execution;
        const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(execution.state);
        if (!terminal) {
          renderExecutionProgress(execution);
          await loadRuns();
          state.pollTimer = window.setTimeout(tick, 750);
          return;
        }
        el('cancelRunBtn').classList.add('hidden');
        await Promise.all([loadRun(runId, { quiet: true }), loadRuns()]);
        if (state.activeVisualRunContext?.runId === runId) await loadVisualRunOverlay(runId, state.activeVisualRunContext);
        const passed = execution.state === 'SUCCEEDED' && execution.validation_result === 'PASS';
        const displayName = state.activeRunDisplayName || state.activeRun?.task_spec?.task?.name || runId;
        const message = execution.error
          ? `仿真“${displayName}”失败（Run ID：${runId}）：${execution.error}`
          : `仿真“${displayName}”已结束：${execution.state}${execution.validation_result ? ` / ${execution.validation_result}` : ''}（Run ID：${runId}）`;
        const noticeType = passed ? 'success' : (execution.state === 'SUCCEEDED' || execution.state === 'CANCELLED' ? 'warning' : 'error');
        showNotice(message, noticeType);
        if ((execution.error || ['FAILED', 'TIMED_OUT'].includes(execution.state)) && state.activeVisualRunContext?.runId === runId) {
          await requestVisualDiagnostics(execution.error || message, state.activeVisualRunContext);
        }
      } catch (error) {
        showNotice(`运行状态查询失败：${error.message}`, 'error');
        state.pollTimer = window.setTimeout(tick, 1500);
      }
    };
    await tick();
  }

  async function cancelActiveRun() {
    if (!state.activeRunId) return;
    const payload = await api(`/runs/${encodeURIComponent(state.activeRunId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'user_requested_from_workbench' }),
    });
    state.executionState = payload.execution_state || state.executionState;
    renderExecutionProgress(state.executionState);
    showNotice('取消请求已提交；仿真将在下一个安全检查点停止。', 'warning');
  }

  async function runSimulation() {
    clearNotice();
    showBusy('正在准备仿真', '校验 TaskSpec 与生成执行计划');
    let visualRunContext = null;
    try {
      const activeTab = document.querySelector('.tab.active')?.dataset.tab;
      if (activeTab === 'agent' && !state.taskSpec) await parseNaturalLanguage();
      else if (activeTab === 'taskspec') {
        state.taskSpec = taskSpecFromEditor();
        const valid = await validateEditor();
        if (!valid) return;
      } else if (activeTab === 'graph') {
        clearVisualDiagnostics();
        if (state.graphComposerMode === 'assembly') {
          const valid = await assemblyCompile({ announce: false });
          if (!valid) return;
          if (!assemblyCodeIsFresh()) await assemblyGenerateCodeFromTaskSpec();
          visualRunContext = { mode: 'assembly', taskSpec: clone(state.taskSpec), assemblyGraph: clone(state.assemblyGraph), snapshot: assemblySnapshot() };
        } else {
          const valid = await graphCompile({ requireCode: true, requireRun: true, announce: false });
          if (!valid) return;
          if (!graphCodeIsFresh()) await graphGenerateCodeFromTaskSpec();
          visualRunContext = { mode: 'flow', taskSpec: clone(state.taskSpec), graph: graphSerialize(), snapshot: graphFormSnapshot() };
        }
      } else if (!state.taskSpec) {
        const valid = await previewForm();
        if (!valid) return;
      }
      const displayName = state.taskSpec?.task?.name || state.taskSpec?.task?.id || '未命名仿真';
      const requestedId = state.taskSpec?.task?.id ? `${state.taskSpec.task.id}_${Date.now().toString(36)}` : null;
      const payload = await api('/runs', {
        method: 'POST',
        body: JSON.stringify({
          task_spec: state.taskSpec,
          run_id: requestedId,
          execute: true,
          execution_mode: 'async',
          max_attempts: 2,
        }),
      });
      const prepared = payload.prepared_run || {};
      const runId = prepared.run_id || payload.execution_state?.run_id;
      if (!runId) throw new Error('运行已返回，但未提供 run_id');
      state.activeRunId = runId;
      state.activeRunDisplayName = displayName;
      state.activeRun = null;
      state.dataset = null;
      state.artifacts = [];
      if (visualRunContext) { state.activeVisualRunContext = { ...visualRunContext, runId }; clearVisualRunOverlay(); }
      else state.activeVisualRunContext = null;
      el('exportDatasetBtn').classList.add('hidden');
      state.executionState = payload.execution_state || { run_id: runId, state: 'QUEUED' };
      renderExecutionProgress(state.executionState);
      showNotice(`仿真“${displayName}”已提交执行（Run ID：${runId}）。可以继续编辑其他任务，无需进入运行中心。`, 'success');
      await loadRuns();
      hideBusy();
      await pollRunExecution(runId);
    } catch (error) {
      if (visualRunContext) await requestVisualDiagnostics(error.message, visualRunContext, error.payload?.detail?.reason_code || error.payload?.reason_code || null);
      throw error;
    } finally {
      hideBusy();
    }
  }

  async function loadRuns() {
    try {
      const payload = await api('/runs?limit=30');
      renderRunList(payload.runs || []);
    } catch (error) {
      el('runList').textContent = `运行列表读取失败：${error.message}`;
    }
  }

  function renderRunList(runs) {
    const container = el('runList');
    container.replaceChildren();
    if (!runs.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.style.minHeight = '80px';
      empty.innerHTML = '<strong>暂无运行记录</strong><span>运行首个 TaskSpec 后将显示在这里。</span>';
      container.appendChild(empty);
      return;
    }
    runs.forEach((run) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'run-list-item';
      const main = document.createElement('div');
      main.className = 'run-list-main';
      const title = document.createElement('strong');
      title.textContent = run.task_name || run.task_id || run.run_id;
      const subtitle = document.createElement('span');
      subtitle.textContent = `${run.run_id} · ${run.status || 'UNKNOWN'} · ${run.validation_result || '—'}`;
      main.append(title, subtitle);
      const status = document.createElement('span');
      status.className = `run-status ${String(run.status || '').toLowerCase()}`;
      status.textContent = run.status || 'UNKNOWN';
      button.append(main, status);
      button.addEventListener('click', () => loadRun(run.run_id));
      container.appendChild(button);
    });
  }

  async function loadRun(runId, options = {}) {
    const quiet = Boolean(options.quiet);
    if (!quiet) showBusy('读取运行制品', runId);
    try {
      const [runPayload, metricsPayload, eventsPayload, plotsPayload, telemetryPayload, artifactsPayload, datasetPayload, fmeaPayload, traceabilityPayload] = await Promise.all([
        api(`/runs/${encodeURIComponent(runId)}`),
        api(`/runs/${encodeURIComponent(runId)}/metrics`).catch(() => ({ metrics: {} })),
        api(`/runs/${encodeURIComponent(runId)}/events`).catch(() => ({ events: null })),
        api(`/runs/${encodeURIComponent(runId)}/plots`).catch(() => ({ plot_manifest: null })),
        api(`/runs/${encodeURIComponent(runId)}/telemetry?limit=2000`).catch(() => ({ rows: [] })),
        api(`/runs/${encodeURIComponent(runId)}/artifacts`).catch(() => ({ artifacts: [] })),
        api(`/runs/${encodeURIComponent(runId)}/dataset`).catch(() => ({ dataset: null })),
        api(`/runs/${encodeURIComponent(runId)}/fmea`).catch(() => ({ table: null, manifest: null })),
        api(`/runs/${encodeURIComponent(runId)}/fault-traceability`).catch(() => ({ traceability: null })),
      ]);
      state.activeRunId = runId;
      state.activeRun = runPayload.run;
      state.metrics = metricsPayload.metrics?.metrics || metricsPayload.metrics || {};
      state.metricMetadata = metricsPayload.metric_metadata || {};
      state.requestedQoi = metricsPayload.requested_qoi || state.activeRun?.task_spec?.outputs?.qoi || [];
      state.events = eventsPayload.events;
      state.fmea = fmeaPayload;
      state.traceability = traceabilityPayload.traceability;
      state.plotManifest = plotsPayload.plot_manifest;
      state.telemetry = telemetryPayload.rows || [];
      state.artifacts = artifactsPayload.artifacts || [];
      state.dataset = datasetPayload.dataset || null;
      state.chartSeriesQuery = '';
      state.chartSelectedOnly = false;
      const manifestSeries = (state.plotManifest?.series || []).map((item) => item.field);
      state.selectedSeries = manifestSeries.slice(0, 4);
      renderActiveRun();
      renderMetrics();
      renderValidation();
      renderChartControls();
      renderEventsResult();
      renderTraceability();
      renderArtifacts();
      switchResultTab('overview');
    } catch (error) {
      showNotice(`读取运行失败：${error.message}`, 'error');
    } finally {
      if (!quiet) hideBusy();
    }
  }

  function renderActiveRun() {
    el('cancelRunBtn').classList.add('hidden');
    el('openRunReportBtn').classList.toggle('hidden', !state.activeRunId || !state.activeRun?.sealed);
    const datasetButton = el('exportDatasetBtn');
    datasetButton.classList.toggle('hidden', !state.activeRunId || !state.dataset?.available);
    datasetButton.title = state.dataset?.available
      ? `${state.dataset.file_count} 个文件，${humanBytes(state.dataset.size_bytes)}`
      : '当前运行尚未生成数据集';
    el('downloadRunDiagnosticBtn').classList.toggle('hidden', !state.activeRunId);
    const run = state.activeRun || {};
    const record = run.run_record || {};
    const validation = run.validation_outcome || {};
    const card = el('activeRunCard');
    card.className = 'active-run';
    card.replaceChildren();
    const titleRow = document.createElement('div');
    titleRow.className = 'run-title-row';
    const runId = document.createElement('div');
    runId.className = 'run-id';
    const taskName = run.task_spec?.task?.name || state.activeRunDisplayName || '仿真任务';
    const technicalRunId = state.activeRunId || record.run_id || '—';
    const taskTitle = document.createElement('strong');
    taskTitle.textContent = taskName;
    const technicalId = document.createElement('small');
    technicalId.textContent = `Run ID：${technicalRunId}`;
    runId.replaceChildren(taskTitle, technicalId);
    const status = document.createElement('span');
    status.className = `run-status ${String(record.status || '').toLowerCase()}`;
    status.textContent = record.status || 'UNKNOWN';
    titleRow.append(runId, status);
    const details = document.createElement('div');
    details.className = 'run-details';
    const items = [
      ['验证结果', validation.result || record.validation_result || '—'],
      ['制品完整性', run.integrity?.ok ? 'VERIFIED' : 'NOT VERIFIED'],
      ['任务 ID', record.task_id || '—'],
      ['尝试次数', Array.isArray(record.attempts) ? record.attempts.length : (record.attempts ?? '—')],
    ];
    items.forEach(([label, value]) => {
      const item = document.createElement('div');
      item.className = 'run-detail';
      const labelEl = document.createElement('span');
      labelEl.textContent = label;
      const valueEl = document.createElement('strong');
      valueEl.textContent = String(value);
      item.append(labelEl, valueEl);
      details.appendChild(item);
    });
    card.append(titleRow, details);
  }

  function renderMetrics() {
    const container = el('metricGrid');
    container.replaceChildren();
    const internalKeys = new Set([
      'schema_version', 'fidelity_level', 'model_family', 'can_claim_high_fidelity',
      'reason_high_fidelity_still_blocked', 'task_id', 'case_id', 'run_id',
      'status', 'mode', 'domain', 'backend', 'adapter_id', 'capability_id',
    ]);
    const available=Object.keys(state.metrics).filter((key)=>!internalKeys.has(key));
    const requested=(state.requestedQoi || []).filter((key)=>Object.hasOwn(state.metrics,key));
    const preferred=[
      'mission_success_score', 'qoi.adcs.final_pointing_error_deg', 'qoi.adcs.max_pointing_error_deg',
      'qoi.adcs.settled_time_s', 'battery_soc', 'energy_conservation_status', 'data_conservation_status',
      'attitude_error_deg', 'thermal_temp_c', 'payload_generated_bps', 'downlink_delivered_bps',
      'runtime_fault_triggered_count',
    ];
    const ordered=[...requested,...preferred.filter((key)=>available.includes(key)),...available];
    const keys=[...new Set(ordered)].slice(0,12);
    if (!keys.length) {
      container.textContent = '当前运行没有可展示的中文标量指标。';
      return;
    }
    keys.forEach((key) => {
      const metadata=state.metricMetadata?.[key] || {};
      const card = document.createElement('div');
      card.className = 'metric-card';
      card.title = metadata.description_zh || `技术字段：${key}`;
      const label = document.createElement('span');
      label.textContent = metadata.label_zh || '运行指标';
      const value = document.createElement('strong');
      const suffix=metadata.unit ? ` ${metadata.unit}` : '';
      value.textContent = `${localizedValue(state.metrics[key])}${suffix}`;
      card.title = `${metadata.description_zh || '本次运行计算得到的指标。'}\n技术字段：${key}`;
      card.append(label, value);
      container.appendChild(card);
    });
  }

  function renderValidation() {
    const validation = state.activeRun?.validation_outcome || {};
    const card = el('validationCard');
    const result = validation.result || state.activeRun?.run_record?.validation_result || 'NOT_EVALUATED';
    card.className = `validation-card ${result === 'PASS' ? 'pass' : 'fail'}`;
    card.replaceChildren();
    const strong = document.createElement('strong');
    strong.textContent = `验证：${localizedValidationResult(result)}`;
    strong.title=`技术状态：${result}`;
    const text = document.createElement('div');
    const reason=validation.message_zh || validation.message || '运行记录未提供进一步说明。';
    text.textContent = reason;
    text.style.marginTop = '4px';
    if (validation.reason_code) text.title=`原因码：${validation.reason_code}`;
    card.append(strong, text);
  }

  function numericSeries() {
    if (!state.telemetry.length) return [];
    const first = state.telemetry[0];
    const excluded = new Set(['time_s', 't_s', 'times', 'sample_index', 'task_id', 'case_id', 'utc']);
    return Object.keys(first).filter((key) => !excluded.has(key) && state.telemetry.some((row) => typeof row[key] === 'number' && Number.isFinite(row[key])));
  }

  function seriesMetadata(field) {
    const manifestItem = (state.plotManifest?.series || []).find((item) => item.field === field);
    if (manifestItem) return manifestItem;
    const schemaItem = (state.schema?.outputs?.trace_options || []).find((item) => (typeof item === 'string' ? item : item.name) === field);
    return typeof schemaItem === 'string' ? { field, label: schemaItem } : (schemaItem || { field, label: field });
  }

  function seriesDisplayLabel(field) {
    const metadata = seriesMetadata(field);
    return state.metricMetadata?.[field]?.label_zh || metadata.label || metadata.name || '运行曲线';
  }

  function renderChartControls() {
    const container = el('chartControls');
    container.replaceChildren();
    const available = new Set(numericSeries());
    const fields = [...new Set((state.plotManifest?.series || []).map((item) => item.field))]
      .filter((field) => available.has(field));
    state.selectedSeries = state.selectedSeries.filter((field) => fields.includes(field));
    if (!fields.length) {
      const empty = document.createElement('div');
      empty.className = 'chart-empty-note';
      empty.textContent = '本次运行未请求可绘制的纵轴曲线；仿真时间仍作为横轴写入遥测数据。';
      container.appendChild(empty);
      drawChart();
      return;
    }
    const selectedSet = new Set(state.selectedSeries);
    const options = fields.map((field) => fallbackOutputHierarchy(field, {
      ...seriesMetadata(field), field, name: field, label: seriesDisplayLabel(field),
    }));
    const toolbar = document.createElement('div'); toolbar.className = 'output-hierarchy-toolbar chart-hierarchy-toolbar';
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = '搜索曲线、分系统或部件…'; search.value = state.chartSeriesQuery;
    const selectedOnly = document.createElement('label'); selectedOnly.className = 'hierarchy-selected-only';
    const selectedInput = document.createElement('input'); selectedInput.type = 'checkbox'; selectedInput.checked = state.chartSelectedOnly;
    const selectedText = document.createElement('span'); selectedText.textContent = '仅看已选'; selectedOnly.append(selectedInput, selectedText);
    const counter = document.createElement('span'); counter.className = 'hierarchy-counter'; counter.textContent = `显示 ${state.selectedSeries.length} / 4`;
    toolbar.append(search, selectedOnly, counter); container.appendChild(toolbar);

    const query = state.chartSeriesQuery.trim();
    const visible = options.filter((option) => hierarchyMatches(option, query) && (!state.chartSelectedOnly || selectedSet.has(option.field)));
    const tree = outputHierarchyTree(visible);
    if (!tree.length) {
      const empty = document.createElement('div'); empty.className = 'hierarchy-empty'; empty.textContent = '没有匹配的曲线。'; container.appendChild(empty);
    }
    tree.forEach((scope) => {
      const scopeHost = document.createElement('details'); scopeHost.className = 'output-scope-group chart-scope-group';
      scopeHost.open = Boolean(query) || scope.options.some((item) => selectedSet.has(item.field));
      scopeHost.appendChild(hierarchySummary(scope.label, scope.options, selectedSet));
      [...scope.subsystems.values()].forEach((subsystem) => {
        const subsystemHost = document.createElement('details'); subsystemHost.className = 'output-subsystem-group';
        subsystemHost.open = Boolean(query) || subsystem.options.some((item) => selectedSet.has(item.field));
        subsystemHost.appendChild(hierarchySummary(subsystem.label, subsystem.options, selectedSet));
        [...subsystem.nodes.values()].forEach((node) => {
          const nodeHost = document.createElement('details'); nodeHost.className = 'output-node-group';
          nodeHost.open = Boolean(query) || node.options.some((item) => selectedSet.has(item.field));
          nodeHost.appendChild(hierarchySummary(node.label, node.options, selectedSet));
          const buttons = document.createElement('div'); buttons.className = 'chart-toggle-grid';
          node.options.forEach((option) => {
            const field = option.field;
            const button = document.createElement('button'); button.type = 'button';
            button.className = `chart-toggle${selectedSet.has(field) ? ' active' : ''}`;
            button.textContent = option.label || field; button.title = `技术字段：${field}`;
            button.addEventListener('click', () => {
              if (state.selectedSeries.includes(field)) state.selectedSeries = state.selectedSeries.filter((item) => item !== field);
              else if (state.selectedSeries.length < 4) state.selectedSeries.push(field);
              else showNotice('曲线区域最多同时显示 4 条，请先取消一条已选曲线。', 'warning');
              renderChartControls(); drawChart();
            });
            buttons.appendChild(button);
          });
          nodeHost.appendChild(buttons); subsystemHost.appendChild(nodeHost);
        });
        scopeHost.appendChild(subsystemHost);
      });
      container.appendChild(scopeHost);
    });
    search.addEventListener('input', () => {
      state.chartSeriesQuery = search.value;
      renderChartControls();
      requestAnimationFrame(() => { const next = el('chartControls')?.querySelector('input[type="search"]'); next?.focus(); next?.setSelectionRange(next.value.length, next.value.length); });
    });
    selectedInput.addEventListener('change', () => { state.chartSelectedOnly = selectedInput.checked; renderChartControls(); });
    drawChart();
  }

  function drawChart() {
    const canvas = el('telemetryChart');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(300, rect.width || 760);
    const height = Math.max(160, rect.height || 360);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    const theme = getComputedStyle(document.documentElement);
    const themeColor = (name, fallback) => theme.getPropertyValue(name).trim() || fallback;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = themeColor('--bg', '#071019');
    ctx.fillRect(0, 0, width, height);
    const rows = state.telemetry;
    const series = state.selectedSeries.filter((field) => rows.some((row) => typeof row[field] === 'number' && Number.isFinite(row[field])));
    if (!rows.length || !series.length) {
      ctx.fillStyle = themeColor('--muted', '#7890a1');
      ctx.font = '12px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(rows.length ? '请选择至少一条数值曲线' : '当前运行没有遥测数据', width / 2, height / 2);
      return;
    }
    const timeField = state.plotManifest?.time_axis?.field || state.plotManifest?.time_field || (Object.hasOwn(rows[0], 'time_s') ? 'time_s' : 't_s');
    const times = rows.map((row, index) => Number(row[timeField] ?? index));
    const minX = Math.min(...times);
    const maxX = Math.max(...times);
    const allValues = series.flatMap((field) => rows.map((row) => row[field]).filter((value) => typeof value === 'number' && Number.isFinite(value)));
    let minY = Math.min(...allValues);
    let maxY = Math.max(...allValues);
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const pad = { left: 48, right: 16, top: 16, bottom: 30 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const x = (value) => pad.left + ((value - minX) / (maxX - minX || 1)) * plotW;
    const y = (value) => pad.top + (1 - (value - minY) / (maxY - minY || 1)) * plotH;
    ctx.strokeStyle = themeColor('--line', 'rgba(170,199,221,.12)');
    ctx.lineWidth = 1;
    ctx.font = '9px system-ui';
    ctx.fillStyle = themeColor('--muted', '#7890a1');
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i += 1) {
      const yy = pad.top + (i / 4) * plotH;
      ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
      const value = maxY - (i / 4) * (maxY - minY);
      ctx.fillText(formatNumber(value), pad.left - 6, yy + 3);
    }
    ctx.textAlign = 'center';
    for (let i = 0; i <= 5; i += 1) {
      const xx = pad.left + (i / 5) * plotW;
      const value = minX + (i / 5) * (maxX - minX);
      ctx.fillText(formatNumber(value), xx, height - 10);
    }
    const colors = [themeColor('--accent', '#53d6b5'), themeColor('--accent-2', '#64a9ff'), themeColor('--warning', '#f2bd63'), themeColor('--danger', '#ff7b83')];
    series.forEach((field, index) => {
      ctx.strokeStyle = colors[index % colors.length];
      ctx.lineWidth = 1.7;
      ctx.beginPath();
      let started = false;
      rows.forEach((row, rowIndex) => {
        const value = row[field];
        if (typeof value !== 'number' || !Number.isFinite(value)) return;
        const xx = x(times[rowIndex]);
        const yy = y(value);
        if (!started) { ctx.moveTo(xx, yy); started = true; }
        else ctx.lineTo(xx, yy);
      });
      ctx.stroke();
    });
    const timeLabel = state.plotManifest?.time_axis?.label || '仿真时间';
    el('chartLegend').textContent = `${timeLabel}（${timeField}） · ${series.map((field) => `${seriesDisplayLabel(field)}（${field}）`).join(' · ')}`;
  }

  function renderEventsResult() {
    const container = el('resultEventList');
    container.replaceChildren();
    const declared = state.events?.declared || [];
    if (!declared.length) {
      container.textContent = '当前任务没有声明故障或退化事件。';
      return;
    }
    declared.forEach((event) => {
      const item = document.createElement('div');
      item.className = 'timeline-item';
      const title = document.createElement('strong');
      title.textContent = `${event.kind === 'fault' ? '故障' : '退化'} · ${event.label || event.effect}`;
      const meta = document.createElement('span');
      const range = event.end_s != null ? `${event.start_s ?? 0}s–${event.end_s}s` : `${event.start_s ?? 0}s 起`;
      meta.textContent = `${range} · ${event.target || 'whole_spacecraft'}`;
      item.append(title, meta);
      container.appendChild(item);
    });
    const observed = state.events?.observed || {};
    if (Object.keys(observed).length) {
      const item = document.createElement('div');
      item.className = 'timeline-item';
      const title = document.createElement('strong');
      title.textContent = '运行时观测';
      const meta = document.createElement('span');
      meta.textContent = Object.entries(observed).map(([key, value]) => `${key}=${value}`).join(' · ');
      item.append(title, meta);
      container.appendChild(item);
    }
  }

  function currentFmeaFilters() {
    return {
      search: el('fmeaSearch')?.value.trim() || '',
      category: el('fmeaCategory')?.value || '',
      evidence_status: el('fmeaEvidenceStatus')?.value || '',
      rating_status: el('fmeaRatingStatus')?.value || '',
      physical_effect_verified: Boolean(el('fmeaVerifiedOnly')?.checked),
    };
  }

  function filteredFmeaRows() {
    const filters = currentFmeaFilters();
    return (state.fmea?.table?.rows || []).filter((row) => {
      if (filters.category && row.category !== filters.category) return false;
      if (filters.evidence_status && row.evidence_status !== filters.evidence_status) return false;
      if (filters.rating_status && row.rating_status !== filters.rating_status) return false;
      if (filters.physical_effect_verified && !row.physical_effect_verified) return false;
      if (filters.search) {
        const haystack = [row.event_id, row.failure_mode, row.target, row.local_effect, row.system_effect, row.detection_method, row.expected_observables, row.telemetry_stream_ids].join(' ').toLowerCase();
        if (!haystack.includes(filters.search.toLowerCase())) return false;
      }
      return true;
    });
  }

  function renderSimpleTable(container, rows, columns) {
    container.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state compact-empty';
      empty.textContent = '没有符合当前筛选条件的数据。';
      container.appendChild(empty);
      return;
    }
    const table = document.createElement('table');
    table.className = 'evidence-table';
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    columns.forEach((column) => { const th = document.createElement('th'); th.textContent = column.label; headRow.appendChild(th); });
    head.appendChild(headRow);
    const body = document.createElement('tbody');
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      columns.forEach((column) => {
        const td = document.createElement('td');
        const value = typeof column.value === 'function' ? column.value(row) : row[column.value];
        td.textContent = localizedValue(value);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.append(head, body);
    container.appendChild(table);
  }

  async function loadTraceabilityWindow(eventId) {
    const preview = el('traceabilityEvidenceWindow');
    preview.classList.remove('hidden');
    preview.replaceChildren();
    preview.textContent = '正在读取原生遥测证据窗口…';
    try {
      const payload = await api(`/runs/${encodeURIComponent(state.activeRunId)}/fault-traceability/${encodeURIComponent(eventId)}/evidence-window`);
      preview.replaceChildren();
      const heading = document.createElement('div');
      heading.className = 'evidence-window-heading';
      const title = document.createElement('strong');
      title.textContent = `事件 ${eventId} 的原生遥测证据窗口`;
      const meta = document.createElement('span');
      meta.textContent = `${payload.link?.evidence_status || '未评估'} · ${payload.link?.physical_effect_verified ? '物理效果已验证' : '物理效果未验证'} · 变化字段：${(payload.link?.changed_physical_fields || []).join('、') || '无'}`;
      heading.append(title, meta);
      preview.appendChild(heading);
      (payload.windows || []).forEach((item) => {
        const section = document.createElement('section');
        section.className = 'evidence-stream-section';
        const streamTitle = document.createElement('h4');
        streamTitle.textContent = `${item.stream?.stream_id || 'telemetry'} · 周期 ${item.stream?.sample_s ?? '—'} s · ${item.count || 0} 行`;
        const tableHost = document.createElement('div');
        tableHost.className = 'evidence-table-scroll';
        const rows = item.rows || [];
        const keys = rows.length ? Object.keys(rows[0]).slice(0, 10) : [];
        renderSimpleTable(tableHost, rows, keys.map((key) => ({ label: key, value: key })));
        section.append(streamTitle, tableHost);
        preview.appendChild(section);
      });
    } catch (error) {
      preview.textContent = `证据窗口读取失败：${error.message}`;
    }
  }

  function renderFmeaTable(rows) {
    const host = el('fmeaTable');
    if (!host) return;
    renderSimpleTable(host, rows, [
      { label: '类别', value: (row) => row.category === 'fault' ? '故障' : row.category === 'degradation' ? '退化' : '约束' },
      { label: '失效模式', value: 'failure_mode' },
      { label: '目标', value: 'target' },
      { label: 'RPN', value: (row) => row.rpn == null ? '未评分' : row.rpn },
      { label: '证据状态', value: 'evidence_status' },
      { label: '物理效果', value: (row) => row.physical_effect_verified ? '已验证' : '未验证' },
      { label: '遥测流', value: (row) => row.telemetry_stream_ids || '—' },
    ]);
  }

  function fmeaDownloadQuery(format) {
    const filters = currentFmeaFilters();
    const params = new URLSearchParams({ format });
    Object.entries(filters).forEach(([key, value]) => {
      if (key === 'physical_effect_verified') {
        if (value) params.set(key, 'true');
      } else if (value) params.set(key, String(value));
    });
    return params.toString();
  }

  function renderTraceability() {
    const summary = el('traceabilitySummary');
    const list = el('traceabilityList');
    const preview = el('traceabilityEvidenceWindow');
    list.replaceChildren();
    preview.classList.add('hidden');
    preview.replaceChildren();
    const trace = state.traceability;
    const allRows = state.fmea?.table?.rows || [];
    const rows = filteredFmeaRows();
    renderFmeaTable(rows);
    if (!trace || !(trace.links || []).length) {
      summary.textContent = allRows.length ? `FMEA ${rows.length}/${allRows.length} 行；当前运行没有可关联的 Fault Episode 证据。` : '当前运行没有可关联的 FMEA/Fault Episode 证据。';
      return;
    }
    summary.textContent = `FMEA ${rows.length}/${allRows.length} 行 · Episode 已关联 ${trace.episode_linked_count}/${trace.link_count} · 原生遥测覆盖 ${trace.telemetry_covered_count}/${trace.link_count} · 物理效果验证 ${trace.physical_effect_verified_count}/${trace.link_count}`;
    const visibleEvents = new Set(rows.map((row) => row.event_id));
    const fmeaMap = new Map(allRows.map((row) => [row.event_id, row]));
    (trace.links || []).filter((link) => !allRows.length || visibleEvents.has(link.event_id)).forEach((link) => {
      const row = fmeaMap.get(link.event_id) || {};
      const item = document.createElement('div');
      item.className = 'timeline-item';
      const title = document.createElement('strong');
      title.textContent = `${row.failure_mode || link.effect} · ${link.target}`;
      const meta = document.createElement('span');
      const rpn = row.rpn != null ? `RPN=${row.rpn}` : 'RPN未评分';
      const streams = (link.telemetry_evidence || []).map((ref) => ref.stream_id).join('、') || '无匹配多速率流';
      meta.textContent = `${link.evidence_status} · ${link.physical_effect_verified ? '物理效果已验证' : '物理效果未验证'} · ${rpn} · ${streams}`;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'button button-ghost';
      button.textContent = '查看证据窗口';
      button.disabled = !(link.telemetry_evidence || []).length;
      button.addEventListener('click', () => loadTraceabilityWindow(link.event_id));
      item.append(title, meta, button);
      list.appendChild(item);
    });
  }

  async function downloadArtifact(artifact) {
    const path = `/runs/${encodeURIComponent(state.activeRunId)}/artifacts/${artifact.path.split('/').map(encodeURIComponent).join('/')}`;
    const headers = state.apiToken ? { Authorization: `Bearer ${state.apiToken}` } : {};
    const response = await fetch(path, { headers });
    if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = artifact.path.split('/').pop() || 'artifact';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function downloadEndpoint(path, filename) {
    const headers = state.apiToken ? { Authorization: `Bearer ${state.apiToken}` } : {};
    const response = await fetch(path, { headers });
    if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename || 'download';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function postDownloadEndpoint(path, body, fallbackFilename = 'download.zip') {
    const authHeaders = state.apiToken ? { Authorization: `Bearer ${state.apiToken}` } : {};
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      const contentType = response.headers.get('content-type') || '';
      let detail = null;
      try { detail = contentType.includes('application/json') ? await response.json() : await response.text(); } catch (_) { detail = null; }
      const payload = detail?.detail || detail;
      const message = typeof payload === 'string' ? payload : (payload?.message || payload?.reason_code || `HTTP ${response.status}`);
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const basicMatch = disposition.match(/filename=\"?([^\";]+)\"?/i);
    let filename = fallbackFilename;
    try {
      if (utf8Match?.[1]) filename = decodeURIComponent(utf8Match[1]);
      else if (basicMatch?.[1]) filename = basicMatch[1];
    } catch (_) { /* keep fallback */ }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { filename, blob };
  }

  async function exportActiveDataset() {
    if (!state.activeRunId || !state.dataset?.available) return;
    const button = el('exportDatasetBtn');
    button.disabled = true;
    showBusy('正在导出数据集', `${state.dataset.file_count} 个文件 · ${humanBytes(state.dataset.size_bytes)}`);
    try {
      await downloadEndpoint(
        `/runs/${encodeURIComponent(state.activeRunId)}/dataset/download`,
        `${state.activeRunId}_dataset.zip`,
      );
      showNotice('数据集已导出。ZIP 内保留 dataset 清单、轨迹、标签及扩展制品。', 'success');
    } finally {
      button.disabled = false;
      hideBusy();
    }
  }

  async function openReport(runId) {
    const headers = state.apiToken ? { Authorization: `Bearer ${state.apiToken}` } : {};
    const response = await fetch(`/runs/${encodeURIComponent(runId)}/report.html`, { headers });
    if (!response.ok) throw new Error(`报告读取失败：HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener');
    setTimeout(() => URL.revokeObjectURL(url), 120000);
  }

  function renderArtifacts() {
    const container = el('artifactList');
    container.replaceChildren();
    if (!state.artifacts.length) {
      container.textContent = '当前运行没有可下载制品。';
      return;
    }
    state.artifacts.slice(0, 120).forEach((artifact) => {
      const item = document.createElement('div');
      item.className = 'artifact-item';
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'artifact-download';
      link.textContent = artifact.path;
      link.addEventListener('click', () => downloadArtifact(artifact));
      const size = document.createElement('span');
      size.textContent = humanBytes(artifact.size_bytes);
      item.append(link, size);
      container.appendChild(item);
    });
  }

  const INTERACTIVE_CAPABILITIES = {
    'whole_spacecraft.unified_native.v1': {
      scope: 'whole',
      streams: [
        { id: 'whole.fast', label: '实时', sampleMultiplier: 1 },
        { id: 'whole.housekeeping', label: '慢速概览', sampleMultiplier: 10, overviewOnly: true },
      ],
      targets: ['whole_spacecraft', 'subsystem.adcs', 'subsystem.eps', 'subsystem.comm_data'],
      telemetryGroups: [
        { id: 'overview', label: '整星概览', fields: ['orbit.radius_m', 'adcs.pointing_error_deg', 'eps.battery_soc', 'data.storage_bits', 'thermal.safe', 'payload.active', 'comm.active', 'propulsion.fuel_mass_kg'] },
        { id: 'orbit', label: '轨道与环境', fields: ['orbit.radius_m', 'orbit.phase_rad', 'orbit.eclipse_factor'] },
        { id: 'adcs', label: '姿轨控', fields: ['adcs.pointing_error_deg', 'adcs.body_rate_rad_s_x', 'adcs.body_rate_rad_s_y', 'adcs.body_rate_rad_s_z', 'adcs.rw.speed_rad_s_0', 'adcs.rw.speed_rad_s_1', 'adcs.rw.speed_rad_s_2'] },
        { id: 'eps', label: '能源', fields: ['eps.battery_soc', 'eps.net_power_w', 'eps.solar_array_power_w', 'eps.loads.adcs_power_w', 'eps.loads.payload_power_w', 'eps.loads.comm_power_w', 'eps.pdu.payload_enabled', 'eps.pdu.comm_enabled'] },
        { id: 'thermal', label: '热控', fields: ['thermal.safe', 'thermal.battery_temp_k', 'thermal.electronics_temp_k', 'thermal.payload_temp_k', 'thermal.adcs_temp_k', 'thermal.comm_temp_k', 'thermal.propulsion_temp_k'] },
        { id: 'payload', label: '有效载荷', fields: ['payload.active', 'payload.generated_bps', 'payload.generated_data_bits', 'data.storage_bits', 'data.storage_capacity_bits'] },
        { id: 'comm', label: '通信数传', fields: ['comm.command_permitted', 'comm.geometric_access', 'comm.active', 'comm.downlink_bps', 'comm.ber', 'comm.per', 'data.storage_bits'] },
        { id: 'propulsion', label: '推进', fields: ['propulsion.enabled', 'propulsion.burn_active', 'propulsion.burn_permitted', 'propulsion.fuel_mass_kg', 'propulsion.total_thrust_n', 'propulsion.electrical_power_w'] },
      ],
    },
    'subsystem.adcs_unified_native.v1': {
      scope: 'subsystem', streams: [{ id: 'adcs.fast', label: '实时', sampleMultiplier: 1 }, { id: 'adcs.housekeeping', label: '慢速', sampleMultiplier: 10, overviewOnly: true }],
      targets: ['subsystem.adcs'], telemetryGroups: [{ id: 'adcs', label: '姿轨控', fields: ['adcs.pointing_error_deg', 'adcs.body_rate_rad_s_x', 'adcs.body_rate_rad_s_y', 'adcs.body_rate_rad_s_z', 'adcs.rw.speed_rad_s_0', 'adcs.rw.speed_rad_s_1', 'adcs.rw.speed_rad_s_2'] }],
    },
    'subsystem.eps.unified_native.v1': {
      scope: 'subsystem', streams: [{ id: 'eps.fast', label: '实时', sampleMultiplier: 1 }, { id: 'eps.housekeeping', label: '慢速', sampleMultiplier: 10, overviewOnly: true }],
      targets: ['subsystem.eps'], telemetryGroups: [{ id: 'eps', label: '能源', fields: ['eps.battery_soc', 'eps.net_power_w', 'eps.solar_power_w', 'eps.bus_load_w'] }],
    },
    'subsystem.comm_data.unified_native.v1': {
      scope: 'subsystem', streams: [{ id: 'comm_data.fast', label: '实时', sampleMultiplier: 1 }, { id: 'comm_data.housekeeping', label: '慢速', sampleMultiplier: 10, overviewOnly: true }],
      targets: ['subsystem.comm_data'], telemetryGroups: [{ id: 'comm', label: '通信数传', fields: ['comm_data.storage_level_bits', 'comm_data.instrument_baud_bps', 'comm_data.transmitter_storage_node_baud_bps'] }],
    },
  };

  const INTERACTIVE_TARGET_LABELS = {
    whole_spacecraft: '整星',
    'subsystem.adcs': '姿轨控分系统',
    'subsystem.eps': '能源分系统',
    'subsystem.comm_data': '通信数传分系统',
  };

  const INTERACTIVE_OPERATION_LABELS = {
    'session.mode.set': '设置整星工作模式',
    'adcs.target.set': '设置目标姿态',
    'adcs.control.set': '姿态控制开关',
    'adcs.fault.inject': '注入姿轨控故障',
    'eps.load.set': '负载开关',
    'eps.protection.set': '能源保护开关',
    'eps.fault.inject': '注入能源故障',
    'comm_data.generate': '产生载荷数据',
    'comm_data.downlink.set': '设置下行链路',
    'comm_data.fault.inject': '注入通信故障',
  };

  const INTERACTIVE_FIELD_LABELS = {
    mode: '工作模式', sigma_rn: '目标姿态 MRP（三轴）', enabled: '启用', fault_id: '故障类型', severity: '严重度',
    load_id: '负载对象', bits: '数据量', rate_bps: '下行速率',
  };

  function interactiveNotice(message, kind = 'success') {
    const node = el('interactiveNotice');
    node.textContent = message;
    node.className = `notice ${kind}`;
  }

  async function loadInteractiveAvailability() {
    try {
      const [sessions, catalog] = await Promise.all([api('/interactive/sessions'), api('/interactive/commands/catalog')]);
      state.interactiveAvailable = true;
      state.interactiveSessions = sessions.sessions || [];
      state.interactiveCommandCatalog = catalog.commands || [];
      el('interactiveNavButton').classList.remove('hidden');
      renderInteractiveSessions();
      renderInteractiveCapabilityHint();
      renderInteractiveCommandTargets();
    } catch (error) {
      state.interactiveAvailable = false;
      el('interactiveNavButton').classList.add('hidden');
      if (state.activeView === 'interactive') switchView('creator');
    }
  }

  async function loadInteractiveSessions() {
    if (!state.interactiveAvailable) return;
    const payload = await api('/interactive/sessions');
    state.interactiveSessions = payload.sessions || [];
    if (state.interactiveSession) {
      const current = state.interactiveSessions.find(item => item.session_id === state.interactiveSession.session_id);
      if (current) state.interactiveSession = current;
    }
    renderInteractiveSessions();
    renderInteractiveSnapshot();
  }

  function renderInteractiveSessions() {
    const container = el('interactiveSessionList');
    if (!container) return;
    container.replaceChildren();
    el('interactiveSessionCount').textContent = String(state.interactiveSessions.length);
    state.interactiveSessions.forEach((session) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `interactive-session-item${state.interactiveSession?.session_id === session.session_id ? ' active' : ''}`;
      const title = document.createElement('strong');
      title.textContent = session.session_id;
      const meta = document.createElement('span');
      const scopeLabel = session.capability_id === 'whole_spacecraft.unified_native.v1' ? '整星' : '分系统独立';
      meta.textContent = `${scopeLabel} · ${session.state} · ${Number(session.sim_time_s).toFixed(3)} s · ${session.rate}x`;
      button.append(title, meta);
      button.addEventListener('click', () => selectInteractiveSession(session.session_id));
      container.appendChild(button);
    });
    if (!state.interactiveSessions.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<strong>暂无会话</strong>';
      container.appendChild(empty);
    }
  }

  async function selectInteractiveSession(sessionId) {
    closeInteractiveSocket(false);
    const snapshot = await api(`/interactive/sessions/${encodeURIComponent(sessionId)}`);
    state.interactiveSession = snapshot;
    state.interactiveFrames = {};
    state.interactiveGaps = {};
    state.interactiveEvents = [];
    state.interactiveAcks = [];
    renderInteractiveStreams();
    renderInteractiveCommandTargets();
    await refreshInteractiveDetails();
    renderInteractiveSessions();
    connectInteractiveSocket();
  }

  function interactiveDescriptor() {
    const sessionId = state.interactiveSession?.session_id;
    const selected = state.interactiveSessions.find(item => item.session_id === sessionId);
    const capabilityId = selected?.capability_id || state.interactiveSession?.capability_id || el('interactiveCapability').value;
    return INTERACTIVE_CAPABILITIES[capabilityId] || INTERACTIVE_CAPABILITIES[el('interactiveCapability').value];
  }

  function renderInteractiveCapabilityHint() {
    const descriptor = INTERACTIVE_CAPABILITIES[el('interactiveCapability').value];
    const hint = el('interactiveCapabilityHint');
    if (!hint || !descriptor) return;
    hint.textContent = descriptor.scope === 'whole'
      ? '整星会话共享同一时钟和状态，可控制姿轨控、能源与通信数传并观察跨系统影响。'
      : '独立分系统会话用于专项调试，不包含完整整星耦合；一般任务建议选择“整星”。';
    hint.classList.toggle('is-subsystem', descriptor.scope !== 'whole');
  }

  async function createInteractiveSession() {
    const capabilityId = el('interactiveCapability').value;
    const descriptor = INTERACTIVE_CAPABILITIES[capabilityId];
    const duration = Number(el('interactiveDuration').value);
    const quantum = Number(el('interactiveQuantum').value);
    const rate = Number(el('interactiveInitialRate').value);
    const sessionId = `live-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}`;
    const spec = {
      session_id: sessionId,
      capability_id: capabilityId,
      task_spec: {
        simulation: { duration_s: duration, sample_s: quantum, solver: { step_s: Math.min(quantum, 0.2) } },
        parameters: { values: {} },
        outputs: { telemetry_streams: descriptor.streams.map((stream) => ({
          stream_id: stream.id,
          sample_s: Math.max(quantum, Math.min(duration, quantum * stream.sampleMultiplier)),
          fields: stream.overviewOnly
            ? descriptor.telemetryGroups[0].fields
            : Array.from(new Set(descriptor.telemetryGroups.flatMap(group => group.fields))),
        })) },
      },
      quantum_s: quantum,
      rate,
      paced: el('interactivePaced').checked,
      max_sim_time_s: duration,
    };
    const created = await api('/interactive/sessions', { method: 'POST', body: JSON.stringify(spec) });
    await loadInteractiveSessions();
    await selectInteractiveSession(created.session_id);
    interactiveNotice(`会话 ${created.session_id} 已就绪。`);
  }

  function renderInteractiveSnapshot() {
    const snapshot = state.interactiveSession;
    el('interactiveState').textContent = snapshot?.state || '未选择';
    el('interactiveSimTime').textContent = `${Number(snapshot?.sim_time_s || 0).toFixed(3)} s`;
    el('interactiveRateValue').textContent = `${snapshot?.rate || 1}x`;
    el('interactiveDrift').textContent = `${Math.round(Number(snapshot?.last_drift_s || 0) * 1000)} ms`;
    if (snapshot) el('interactiveRate').value = String(snapshot.rate);
    if (snapshot) {
      el('interactiveCommandTime').min = String(snapshot.sim_time_s);
      if (Number(el('interactiveCommandTime').value) < Number(snapshot.sim_time_s)) el('interactiveCommandTime').value = String(snapshot.sim_time_s);
    }
    const terminal = ['COMPLETED', 'FAILED', 'ABORTED', 'INTERRUPTED'].includes(snapshot?.state);
    ['interactiveStartBtn', 'interactivePauseBtn', 'interactiveResumeBtn', 'interactiveStepBtn', 'interactiveStopBtn', 'interactiveSendCommandBtn'].forEach((id) => {
      el(id).disabled = !snapshot || terminal;
    });
    el('interactiveStartBtn').disabled = !snapshot || snapshot.state !== 'READY';
    el('interactivePauseBtn').disabled = !snapshot || snapshot.state !== 'RUNNING';
    el('interactiveResumeBtn').disabled = !snapshot || snapshot.state !== 'PAUSED';
    el('interactiveStepBtn').disabled = !snapshot || snapshot.state !== 'PAUSED';
  }

  async function interactiveControl(action, extra = {}) {
    if (!state.interactiveSession) return;
    if ((action === 'stop' || action === 'abort') && !confirm('确认停止当前实时会话并归档？')) return;
    const sessionId = state.interactiveSession.session_id;
    state.interactiveSession = await api(`/interactive/sessions/${encodeURIComponent(sessionId)}/control`, {
      method: 'POST', body: JSON.stringify({ action, ...extra }),
    });
    renderInteractiveSnapshot();
    await refreshInteractiveDetails();
  }

  async function refreshInteractiveDetails() {
    if (!state.interactiveSession) return;
    const id = encodeURIComponent(state.interactiveSession.session_id);
    const [snapshot, events, acks] = await Promise.all([
      api(`/interactive/sessions/${id}`), api(`/interactive/sessions/${id}/events`), api(`/interactive/sessions/${id}/commands`),
    ]);
    state.interactiveSession = snapshot;
    state.interactiveEvents = events.events || [];
    state.interactiveAcks = acks.acks || [];
    renderInteractiveSnapshot();
    renderInteractiveEvents();
    renderInteractiveAcks();
  }

  function renderInteractiveEvents() {
    const container = el('interactiveEventList');
    container.replaceChildren();
    state.interactiveEvents.slice(-80).reverse().forEach((event) => {
      const item = document.createElement('div');
      item.className = `interactive-timeline-item ${String(event.state).toLowerCase()}`;
      const title = document.createElement('strong');
      title.textContent = `${event.previous_state} → ${event.state}`;
      const meta = document.createElement('span');
      meta.textContent = `#${event.sequence} · t=${Number(event.sim_time_s).toFixed(3)} s · ${event.reason_code}`;
      item.append(title, meta);
      container.appendChild(item);
    });
  }

  function renderInteractiveAcks() {
    const container = el('interactiveAckList');
    container.replaceChildren();
    state.interactiveAcks.slice(-120).reverse().forEach((ack) => {
      const item = document.createElement('div');
      item.className = `interactive-timeline-item ${String(ack.state).toLowerCase()}`;
      const title = document.createElement('strong');
      title.textContent = `${ack.command_id} · ${ack.state}`;
      const meta = document.createElement('span');
      meta.textContent = `#${ack.sequence} · ${ack.reason_code}${ack.sim_time_s == null ? '' : ` · t=${Number(ack.sim_time_s).toFixed(3)} s`}`;
      item.append(title, meta);
      container.appendChild(item);
    });
  }

  function websocketProtocols() {
    if (!state.apiToken) return ['sat-sim-v1'];
    const bytes = new TextEncoder().encode(state.apiToken);
    let binary = '';
    bytes.forEach(value => { binary += String.fromCharCode(value); });
    const encoded = btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
    return ['sat-sim-v1', `sat-auth.${encoded}`];
  }

  function connectInteractiveSocket() {
    if (!state.interactiveSession || !state.interactiveAvailable) return;
    closeInteractiveSocket(false);
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const id = encodeURIComponent(state.interactiveSession.session_id);
    const socket = new WebSocket(`${scheme}//${location.host}/interactive/sessions/${id}/telemetry`, websocketProtocols());
    state.interactiveSocket = socket;
    socket.addEventListener('open', () => {
      el('interactiveConnectionBadge').className = 'status-chip status-ok';
      el('interactiveConnectionBadge').innerHTML = '<span class="status-dot"></span>已连接';
      const streams = Array.from(el('interactiveStream').options).map(option => option.value).filter(Boolean);
      const afterSequences = {};
      streams.forEach((stream) => {
        const frames = state.interactiveFrames[stream] || [];
        afterSequences[stream] = frames.length ? frames[frames.length - 1].sequence : -1;
      });
      socket.send(JSON.stringify({ action: 'subscribe', streams, after_sequences: afterSequences }));
    });
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'telemetry') acceptInteractiveFrame(message.payload);
      if (message.type === 'gap') {
        state.interactiveGaps[message.payload.stream] = message.payload;
        renderInteractiveTelemetry();
      }
      if (message.type === 'session_event') {
        const existing = state.interactiveEvents.find(item => item.sequence === message.payload.sequence);
        if (!existing) state.interactiveEvents.push(message.payload);
        renderInteractiveEvents();
      }
      if (message.type === 'heartbeat') {
        state.interactiveSession = message.payload;
        renderInteractiveSnapshot();
      }
    });
    socket.addEventListener('close', () => {
      if (state.interactiveSocket === socket) state.interactiveSocket = null;
      el('interactiveConnectionBadge').className = 'status-chip status-warn';
      el('interactiveConnectionBadge').innerHTML = '<span class="status-dot"></span>已断开';
      const terminal = ['COMPLETED', 'FAILED', 'ABORTED', 'INTERRUPTED'].includes(state.interactiveSession?.state);
      if (!terminal && state.activeView === 'interactive') {
        clearTimeout(state.interactiveReconnectTimer);
        state.interactiveReconnectTimer = setTimeout(connectInteractiveSocket, 1200);
      }
    });
  }

  function closeInteractiveSocket(sendUnsubscribe = true) {
    clearTimeout(state.interactiveReconnectTimer);
    const socket = state.interactiveSocket;
    state.interactiveSocket = null;
    if (socket && socket.readyState === WebSocket.OPEN && sendUnsubscribe) socket.send(JSON.stringify({ action: 'unsubscribe' }));
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, 'CLIENT_NAVIGATION');
  }

  function acceptInteractiveFrame(frame) {
    const frames = state.interactiveFrames[frame.stream] || [];
    if (!frames.length || frame.sequence > frames[frames.length - 1].sequence) frames.push(frame);
    state.interactiveFrames[frame.stream] = frames.slice(-600);
    if (el('interactiveStream').value === frame.stream) renderInteractiveTelemetry();
  }

  function renderInteractiveStreams() {
    const streamSelect = el('interactiveStream');
    const objectSelect = el('interactiveTelemetryObject');
    const currentStream = streamSelect.value;
    const currentObject = objectSelect.value;
    streamSelect.replaceChildren();
    objectSelect.replaceChildren();
    const capabilityId = state.interactiveSessions.find(item => item.session_id === state.interactiveSession?.session_id)?.capability_id;
    const descriptor = INTERACTIVE_CAPABILITIES[capabilityId] || interactiveDescriptor();
    descriptor?.streams.forEach((stream) => {
      const option = document.createElement('option');
      option.value = stream.id; option.textContent = stream.label; streamSelect.appendChild(option);
    });
    descriptor?.telemetryGroups.forEach((group) => {
      const option = document.createElement('option');
      option.value = group.id; option.textContent = group.label; objectSelect.appendChild(option);
    });
    if (Array.from(streamSelect.options).some(option => option.value === currentStream)) streamSelect.value = currentStream;
    if (Array.from(objectSelect.options).some(option => option.value === currentObject)) objectSelect.value = currentObject;
    renderInteractiveTelemetry();
  }

  function renderInteractiveTelemetry() {
    const stream = el('interactiveStream').value;
    const frames = state.interactiveFrames[stream] || [];
    const gap = state.interactiveGaps[stream];
    el('interactiveGapBadge').className = `status-chip ${gap ? 'status-warn' : 'status-muted'}`;
    el('interactiveGapBadge').textContent = gap ? `缺口 ${gap.dropped_count}` : '无缺口';
    const latest = frames[frames.length - 1]?.values || {};
    const group = interactiveDescriptor()?.telemetryGroups.find(item => item.id === el('interactiveTelemetryObject').value);
    const visibleFields = group?.fields || Object.keys(latest);
    const values = el('interactiveLatestValues');
    values.replaceChildren();
    visibleFields.filter(name => Object.hasOwn(latest, name)).forEach((name) => {
      const value = latest[name];
      const item = document.createElement('div'); item.className = 'interactive-value';
      const label = document.createElement('span'); label.textContent = name;
      const strong = document.createElement('strong');
      strong.textContent = typeof value === 'number' ? String(Math.round(value * 1e6) / 1e6) : String(value);
      item.append(label, strong); values.appendChild(item);
    });
    if (!frames.length) values.innerHTML = '<div class="empty-state"><strong>等待遥测数据</strong></div>';
    drawInteractiveChart(frames, visibleFields);
  }

  function drawInteractiveChart(frames, visibleFields = []) {
    const canvas = el('interactiveTelemetryChart');
    const width = Math.max(canvas.clientWidth, 320);
    const height = Math.max(canvas.clientHeight, 180);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    const styles = getComputedStyle(document.documentElement);
    const line = styles.getPropertyValue('--line-strong').trim();
    const muted = styles.getPropertyValue('--muted').trim();
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    for (let index = 1; index < 5; index += 1) {
      const y = 18 + (height - 40) * index / 5; ctx.beginPath(); ctx.moveTo(42, y); ctx.lineTo(width - 12, y); ctx.stroke();
    }
    const availableNumericFields = new Set(frames.flatMap(frame => Object.entries(frame.values).filter(([, value]) => typeof value === 'number').map(([name]) => name)));
    const numericFields = visibleFields.filter(field => availableNumericFields.has(field)).slice(0, 3);
    const colors = ['#53d6b5', '#64a9ff', '#f2bd63'];
    numericFields.forEach((field, fieldIndex) => {
      const points = frames.map(frame => [Number(frame.sim_time_s), Number(frame.values[field])]).filter(item => Number.isFinite(item[1]));
      if (!points.length) return;
      const times = points.map(item => item[0]); const data = points.map(item => item[1]);
      const tMin = Math.min(...times); const tMax = Math.max(...times); const vMin = Math.min(...data); const vMax = Math.max(...data);
      ctx.strokeStyle = colors[fieldIndex]; ctx.lineWidth = 1.8; ctx.beginPath();
      points.forEach(([t, value], index) => {
        const x = 42 + (width - 56) * (tMax === tMin ? index / Math.max(points.length - 1, 1) : (t - tMin) / (tMax - tMin));
        const y = height - 22 - (height - 42) * (vMax === vMin ? 0.5 : (value - vMin) / (vMax - vMin));
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke(); ctx.fillStyle = colors[fieldIndex]; ctx.font = '10px sans-serif'; ctx.fillText(field, 46, 14 + fieldIndex * 13);
    });
    ctx.fillStyle = muted; ctx.font = '9px sans-serif'; ctx.fillText('sim time', Math.max(44, width - 52), height - 6);
  }

  function renderInteractiveCommandTargets() {
    const select = el('interactiveCommandTarget');
    if (!select) return;
    const selected = select.value;
    select.replaceChildren();
    interactiveDescriptor()?.targets.forEach((target) => {
      const option = document.createElement('option');
      option.value = target; option.textContent = INTERACTIVE_TARGET_LABELS[target] || target; select.appendChild(option);
    });
    if (Array.from(select.options).some(option => option.value === selected)) select.value = selected;
    renderInteractiveCommandOperations();
  }

  function renderInteractiveCommandOperations() {
    const select = el('interactiveCommandOperation');
    if (!select) return;
    const target = el('interactiveCommandTarget').value;
    const selected = select.value;
    select.replaceChildren();
    state.interactiveCommandCatalog
      .filter(command => command.target === target)
      .sort((left, right) => Number(left.required_role === 'fault_operator') - Number(right.required_role === 'fault_operator') || left.operation.localeCompare(right.operation))
      .forEach((command) => {
      const option = document.createElement('option'); option.value = command.operation; option.textContent = INTERACTIVE_OPERATION_LABELS[command.operation] || command.operation; select.appendChild(option);
      });
    if (Array.from(select.options).some(option => option.value === selected)) select.value = selected;
    renderInteractiveCommandForm();
  }

  function renderInteractiveCommandForm() {
    const command = state.interactiveCommandCatalog.find(item => item.operation === el('interactiveCommandOperation').value);
    const container = el('interactiveCommandForm'); container.replaceChildren();
    const help = el('interactiveCommandHelp');
    if (help) help.textContent = command
      ? `${command.required_role === 'fault_operator' ? '故障操作员权限 · 需要确认' : '操作员权限'} · 生效观测：${(command.effect_fields || []).join('、')}`
      : '当前对象暂无可用指令。';
    Object.entries(command?.parameter_schema?.properties || {}).forEach(([name, schema]) => {
      const label = document.createElement('label'); label.className = 'interactive-command-field'; label.textContent = `${INTERACTIVE_FIELD_LABELS[name] || name}${command.units?.[name] ? ` / ${command.units[name]}` : ''}`;
      let input;
      if (schema.enum) {
        input = document.createElement('select'); schema.enum.forEach(value => { const option = document.createElement('option'); option.value = String(value); option.textContent = String(value); input.appendChild(option); });
      } else if (schema.type === 'boolean') {
        input = document.createElement('input'); input.type = 'checkbox';
      } else {
        input = document.createElement('input'); input.type = schema.type === 'number' || schema.type === 'integer' ? 'number' : 'text';
        if (schema.minimum != null) input.min = String(schema.minimum);
        if (schema.maximum != null) input.max = String(schema.maximum);
        input.step = schema.type === 'integer' ? '1' : 'any';
        if (schema.type === 'array') input.value = '0, 0, 0';
        else if (input.type === 'number') input.value = String(schema.minimum != null && schema.minimum > 0 ? schema.minimum : 0);
      }
      input.dataset.parameter = name; input.dataset.schemaType = schema.type || 'string'; label.appendChild(input); container.appendChild(label);
    });
  }

  async function sendInteractiveCommand() {
    const session = state.interactiveSession;
    const contract = state.interactiveCommandCatalog.find(item => item.operation === el('interactiveCommandOperation').value);
    if (!session || !contract) return;
    if (contract.required_role === 'fault_operator' && !confirm(`确认执行故障指令 ${contract.operation}？`)) return;
    const parameters = {};
    $all('[data-parameter]', el('interactiveCommandForm')).forEach((input) => {
      const type = input.dataset.schemaType;
      let value = input.value;
      if (type === 'boolean') value = input.checked;
      if (type === 'number') value = Number(input.value);
      if (type === 'integer') value = Number.parseInt(input.value, 10);
      if (type === 'array') value = input.value.split(',').map(item => Number(item.trim()));
      parameters[input.dataset.parameter] = value;
    });
    const payload = {
      command_id: `tc-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 6)}`,
      session_id: session.session_id,
      session_revision: session.revision,
      operation: contract.operation,
      target: contract.target,
      parameters,
      actor_id: 'workbench-operator',
      actor_role: contract.required_role,
      execute_at_sim_time_s: Number(el('interactiveCommandTime').value || session.sim_time_s),
    };
    const ack = await api(`/interactive/sessions/${encodeURIComponent(session.session_id)}/commands`, { method: 'POST', body: JSON.stringify(payload) });
    interactiveNotice(`${ack.command_id}: ${ack.state}`, ack.state === 'REJECTED' ? 'error' : 'success');
    await refreshInteractiveDetails();
  }

  function switchView(name) {
    state.activeView = name;
    document.body.dataset.activeView = name;
    const viewIds = {
      creator: 'creatorView', tasks: 'taskCenterView', experiments: 'experimentCenterView',
      interactive: 'interactiveView', runs: 'runCenterView', diagnostics: 'diagnosticView', logs: 'logCenterView',
    };
    Object.entries(viewIds).forEach(([key, id]) => el(id)?.classList.toggle('hidden', key !== name));
    $all('.nav-button[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
    if (name === 'tasks') loadTasks().catch((error) => { el('taskSummary').textContent = `任务中心读取失败：${error.message}`; });
    if (name === 'experiments') loadExperiments().catch((error) => { el('experimentSummary').textContent = `实验中心读取失败：${error.message}`; });
    if (name === 'runs') loadRunCenter().catch((error) => { el('runCenterSummary').textContent = `运行中心读取失败：${error.message}`; });
    if (name === 'diagnostics') loadDiagnostics().catch((error) => { el('diagnosticDetails').textContent = `诊断失败：${error.message}`; });
    if (name === 'logs') loadLogs().catch((error) => { el('logSummary').textContent = `日志读取失败：${error.message}`; });
    if (name === 'interactive') {
      loadInteractiveSessions().then(() => {
        if (!state.interactiveSession && state.interactiveSessions.length) selectInteractiveSession(state.interactiveSessions[0].session_id);
        else if (state.interactiveSession && !state.interactiveSocket) connectInteractiveSocket();
      }).catch((error) => interactiveNotice(error.message, 'error'));
      clearInterval(state.interactivePollTimer);
      state.interactivePollTimer = setInterval(() => {
        loadInteractiveSessions().then(refreshInteractiveDetails).catch(() => null);
      }, 500);
    } else {
      clearInterval(state.interactivePollTimer);
      state.interactivePollTimer = null;
      closeInteractiveSocket();
    }
  }

  async function loadRunCenter() {
    const payload = await api('/runs?limit=200');
    const runs = payload.runs || [];
    el('runCenterSummary').textContent = `共 ${payload.total || runs.length} 次运行；自动报告来自密封 Run Bundle。`;
    const body = el('runCenterTableBody');
    body.replaceChildren();
    runs.forEach((run) => {
      const tr = document.createElement('tr');
      const values = [`${run.task_name || run.task_id || '未命名任务'}\n${run.run_id}`, run.status || '—', run.validation_result || '—', run.primary_capability_id || run.task_id || '—', formatDateTime(run.updated_at)];
      values.forEach((value, index) => {
        const td = document.createElement('td');
        td.textContent = String(value);
        if (index === 1) td.innerHTML = `<span class="run-status ${String(run.status || '').toLowerCase()}">${String(value)}</span>`;
        tr.appendChild(td);
      });
      const actions = document.createElement('td');
      actions.className = 'table-actions';
      const open = document.createElement('button'); open.className = 'button button-ghost'; open.textContent = '查看';
      open.addEventListener('click', async () => { await loadRun(run.run_id); switchView('creator'); });
      const report = document.createElement('button'); report.className = 'button button-secondary'; report.textContent = '报告'; report.disabled = !run.sealed;
      report.addEventListener('click', () => openReport(run.run_id).catch((error) => showNotice(error.message, 'error')));
      const diag = document.createElement('button'); diag.className = 'button button-ghost'; diag.textContent = '诊断包';
      diag.addEventListener('click', () => downloadEndpoint(`/diagnostics/bundle?run_id=${encodeURIComponent(run.run_id)}`, `diagnostic_${run.run_id}.zip`).catch((error) => showNotice(error.message, 'error')));
      actions.append(open, report, diag); tr.appendChild(actions); body.appendChild(tr);
    });
  }

  async function loadDiagnostics() {
    const payload = await api('/diagnostics/summary');
    const doctor = payload.doctor || {};
    const checks = doctor.checks || doctor.results || [];
    const cards = [
      ['环境状态', payload.ok ? 'PASS' : 'FAIL'],
      ['队列任务', payload.queue?.total ?? payload.queue?.job_count ?? 0],
      ['任务中心', payload.task_center?.task_count ?? payload.task_center?.count ?? 0],
      ['模型服务', payload.providers?.providers?.length ?? 0],
    ];
    const summary = el('diagnosticSummary'); summary.replaceChildren();
    cards.forEach(([label, value]) => { const card=document.createElement('div'); card.className='diagnostic-summary-card'; const span=document.createElement('span'); span.textContent=label; const strong=document.createElement('strong'); strong.textContent=String(value); card.append(span,strong); summary.appendChild(card); });
    const list = el('doctorCheckList'); list.replaceChildren();
    (Array.isArray(checks) ? checks : []).forEach((check) => {
      const row=document.createElement('div'); row.className='doctor-check';
      const text=document.createElement('span'); text.textContent=check.name || check.check_id || check.id || '检查项';
      const status=document.createElement('strong'); const value=String(check.status || (check.ok ? 'PASS':'FAIL')).toUpperCase(); status.textContent=value; status.className=value==='PASS'?'pass':value==='WARN'?'warn':'fail'; row.append(text,status); list.appendChild(row);
    });
    if (!list.children.length) list.textContent = 'Doctor 未返回逐项结果，请查看下方详情。';
    el('diagnosticDetails').textContent = JSON.stringify({ queue: payload.queue, task_center: payload.task_center, experiments: payload.experiments, providers: payload.providers }, null, 2);
  }

  async function loadLogs() {
    const level = el('logLevelFilter')?.value || '';
    const search = el('logSearch')?.value.trim() || '';
    const params = new URLSearchParams({ limit: '500' });
    if (level) params.set('level', level);
    if (search) params.set('search', search);
    const payload = await api(`/logs?${params.toString()}`);
    const logs = payload.logs || {};
    el('logSummary').textContent = `匹配 ${logs.total || 0} 条日志，当前显示 ${logs.rows?.length || 0} 条。`;
    const body = el('logTableBody'); body.replaceChildren();
    (logs.rows || []).forEach((row) => {
      const tr=document.createElement('tr');
      const values=[formatDateTime(row.timestamp), row.level, row.category, row.event, row.message, row.details?.elapsed_ms ?? '—'];
      values.forEach((value,index)=>{const td=document.createElement('td'); if(index===1){td.innerHTML=`<span class="log-level ${String(value).toLowerCase()}">${String(value)}</span>`;}else td.textContent=String(value ?? '—'); tr.appendChild(td);});
      body.appendChild(tr);
    });
  }

  async function saveCurrentTask(mode = 'new') {
    if (!state.formData && !state.taskSpec) throw new Error('请先选择可执行的仿真对象');
    if (!state.taskSpec) { const ok=await previewForm(); if (!ok) throw new Error('TaskSpec 未通过校验，不能保存到任务中心'); }
    if (mode === 'update' && !state.activeTaskCenterId) throw new Error('当前未打开任务中心中的任务；请使用“另存为新任务”。');
    let spec=clone(state.taskSpec); let target='/task-center/tasks'; let method='POST'; let taskId=null;
    if (mode === 'update') { target=`/task-center/tasks/${encodeURIComponent(state.activeTaskCenterId)}`; method='PUT'; }
    else {
      const base=String(spec.task?.id || 'simulation_task').replace(/[^A-Za-z0-9_.-]+/g,'_').slice(0,72) || 'simulation_task';
      taskId=`${base}_${Date.now().toString(36)}`;
      spec.task={...(spec.task || {}),id:taskId};
    }
    const payload=await api(target,{method,body:JSON.stringify({
      task_spec:spec,task_id:taskId,source:'web_workbench',status:'READY',force_new_version:mode==='update',
      change_summary:mode==='update'?'工作台更新已打开任务':'从工作台另存为新任务',
    })});
    if (mode === 'update') state.activeTaskCenterId=payload.task?.task_id || state.activeTaskCenterId;
    updateTaskEditState();
    showNotice(mode==='update' ? `任务“${payload.task?.name || payload.task?.task_id}”已生成新版本。` : `已另存为新任务“${payload.task?.name || payload.task?.task_id}”，不会覆盖现有任务。`,'success');
    await loadTasks(); return payload.task;
  }

  function formatDateTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
  }

  async function loadTasks() {
    const query = el('taskSearch')?.value.trim() || '';
    const status = el('taskStatusFilter')?.value || '';
    const params = new URLSearchParams({ limit: '200' });
    if (query) params.set('q', query);
    if (status) params.set('status', status);
    const payload = await api(`/task-center/tasks?${params.toString()}`);
    state.tasks = payload.tasks || [];
    renderTaskCenter(payload);
  }

  function renderTaskCenter(payload = {}) {
    const body = el('taskTableBody');
    if (!body) return;
    body.replaceChildren();
    el('taskSummary').textContent = `共 ${payload.total ?? state.tasks.length} 个仿真任务；任务定义持久保存，运行记录仍由不可变 Run Bundle 管理。`;
    if (!state.tasks.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.className = 'empty-state';
      cell.innerHTML = '<strong>暂无任务</strong><span>在“仿真创建”页面配置后点击“另存为新任务”，或直接运行一次仿真。</span>';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    state.tasks.forEach((task) => {
      const row = document.createElement('tr');
      const nameCell = document.createElement('td');
      const name = document.createElement('div');
      name.className = 'task-name';
      name.textContent = task.name || task.task_id;
      const id = document.createElement('div');
      id.className = 'task-id';
      id.textContent = task.task_id;
      nameCell.append(name, id);
      const capabilityCell = document.createElement('td');
      const object = presentationObjectForCapability(task.capability_id);
      capabilityCell.textContent = object?.name_zh || task.capability_id || '—';
      const statusCell = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `task-status ${String(task.status || '').toLowerCase()}`;
      badge.textContent = task.status || 'UNKNOWN';
      statusCell.appendChild(badge);
      const countsCell = document.createElement('td');
      countsCell.className = 'task-counts';
      const versionCount = document.createElement('span');
      versionCount.innerHTML = `<strong>V${task.current_version || 1}</strong> / ${task.version_count || 1} 个版本`;
      const runCount = document.createElement('span');
      runCount.textContent = `${task.run_count || 0} 次运行`;
      countsCell.append(versionCount, runCount);
      const runCell = document.createElement('td');
      runCell.textContent = task.last_run_id ? `${task.last_run_id} · ${task.last_run_status || '—'}` : '尚未运行';
      const timeCell = document.createElement('td');
      timeCell.textContent = formatDateTime(task.updated_at);
      const actionCell = document.createElement('td');
      actionCell.className = 'task-actions-cell';
      const details = document.createElement('button');
      details.className = 'button button-ghost';
      details.type = 'button';
      details.textContent = '详情';
      details.addEventListener('click', () => openTaskDetails(task.task_id));
      const open = document.createElement('button');
      open.className = 'button button-ghost';
      open.type = 'button';
      open.textContent = '编辑';
      open.addEventListener('click', () => openTaskFromCenter(task.task_id));
      const run = document.createElement('button');
      run.className = 'button button-secondary';
      run.type = 'button';
      run.textContent = '运行';
      run.addEventListener('click', () => runTaskFromCenter(task.task_id));
      const remove = document.createElement('button');
      remove.className = 'button button-danger';
      remove.type = 'button';
      remove.textContent = '删除';
      remove.addEventListener('click', () => deleteTaskFromCenter(task.task_id));
      actionCell.append(details, open, run, remove);
      row.append(nameCell, capabilityCell, statusCell, countsCell, runCell, timeCell, actionCell);
      body.appendChild(row);
    });
  }

  function closeTaskDetails() {
    el('taskDetailsModal').classList.add('hidden');
  }

  async function openTaskDetails(taskId) {
    state.activeTaskCenterId = taskId;
    const [taskPayload, versionPayload, runPayload] = await Promise.all([
      api(`/task-center/tasks/${encodeURIComponent(taskId)}`),
      api(`/task-center/tasks/${encodeURIComponent(taskId)}/versions`),
      api(`/task-center/tasks/${encodeURIComponent(taskId)}/runs`),
    ]);
    const task = taskPayload.task || {};
    el('taskDetailsTitle').textContent = task.name || taskId;
    el('taskDetailsSummary').textContent = `当前 V${task.current_version || 1} · ${task.version_count || 1} 个版本 · ${task.run_count || 0} 次运行${task.baseline_version ? ` · 基线 V${task.baseline_version}` : ''}`;
    const versionBody = el('taskVersionTableBody');
    versionBody.replaceChildren();
    (versionPayload.versions || []).forEach((version) => {
      const row = document.createElement('tr');
      const versionCell = document.createElement('td');
      versionCell.innerHTML = `<strong>V${version.version}</strong>${version.is_baseline ? '<div class="version-baseline">基线</div>' : ''}`;
      const summaryCell = document.createElement('td');
      summaryCell.textContent = version.change_summary || '—';
      const timeCell = document.createElement('td');
      timeCell.textContent = formatDateTime(version.created_at);
      const hashCell = document.createElement('td');
      hashCell.className = 'task-id';
      hashCell.textContent = (version.spec_sha256 || '').slice(0, 12);
      const actionCell = document.createElement('td');
      actionCell.className = 'task-actions-cell';
      const open = document.createElement('button');
      open.className = 'button button-ghost';
      open.textContent = '打开';
      open.addEventListener('click', async () => {
        const payload = await api(`/task-center/tasks/${encodeURIComponent(taskId)}/versions/${version.version}`);
        await projectTaskSpecToForm(payload.version.task_spec, `任务 V${version.version}`);
        state.activeTaskCenterId = taskId;
        updateTaskEditState();
        closeTaskDetails();
        switchView('creator');
      });
      const baseline = document.createElement('button');
      baseline.className = 'button button-ghost';
      baseline.textContent = version.is_baseline ? '已为基线' : '设为基线';
      baseline.disabled = version.is_baseline;
      baseline.addEventListener('click', async () => {
        await api(`/task-center/tasks/${encodeURIComponent(taskId)}/versions/${version.version}/baseline`, { method: 'POST' });
        await openTaskDetails(taskId);
        await loadTasks();
      });
      const restore = document.createElement('button');
      restore.className = 'button button-secondary';
      restore.textContent = '恢复为新版本';
      restore.addEventListener('click', async () => {
        await api(`/task-center/tasks/${encodeURIComponent(taskId)}/versions/${version.version}/restore`, {
          method: 'POST', body: JSON.stringify({ change_summary: `从 V${version.version} 恢复` }),
        });
        await openTaskDetails(taskId);
        await loadTasks();
      });
      actionCell.append(open, baseline, restore);
      row.append(versionCell, summaryCell, timeCell, hashCell, actionCell);
      versionBody.appendChild(row);
    });
    const runBody = el('taskRunTableBody');
    runBody.replaceChildren();
    (runPayload.runs || []).forEach((run) => {
      const row = document.createElement('tr');
      [run.run_id, `V${run.task_version}`, run.status, run.validation_result || '—', formatDateTime(run.updated_at)].forEach((value, index) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        if (index === 0) cell.className = 'task-id';
        row.appendChild(cell);
      });
      runBody.appendChild(row);
    });
    el('taskDetailsModal').classList.remove('hidden');
  }

  async function cloneActiveTask() {
    if (!state.activeTaskCenterId) return;
    const payload = await api(`/task-center/tasks/${encodeURIComponent(state.activeTaskCenterId)}/clone`, {
      method: 'POST', body: JSON.stringify({}),
    });
    closeTaskDetails();
    await loadTasks();
    showNotice(`已克隆为新任务“${payload.task?.name || payload.task?.task_id}”。`, 'success');
  }

  async function openTaskFromCenter(taskId) {
    showBusy('打开任务', taskId);
    try {
      const payload = await api(`/task-center/tasks/${encodeURIComponent(taskId)}`);
      const spec = payload.task?.task_spec;
      if (!spec) throw new Error('任务中心没有返回 TaskSpec');
      await projectTaskSpecToForm(spec, '任务中心');
      state.activeTaskCenterId = taskId;
      updateTaskEditState();
      switchView('creator');
      showNotice(`已打开任务“${payload.task.name || taskId}”。`, 'success');
    } catch (error) {
      el('taskSummary').textContent = `打开任务失败：${error.message}`;
    } finally {
      hideBusy();
    }
  }

  async function runTaskFromCenter(taskId) {
    showBusy('提交任务运行', taskId);
    try {
      const payload = await api(`/task-center/tasks/${encodeURIComponent(taskId)}/run`, {
        method: 'POST',
        body: JSON.stringify({ max_attempts: 2, hard_timeout: true }),
      });
      const runId = payload.prepared_run?.run_id || payload.execution_state?.run_id;
      if (!runId) throw new Error('未返回 run_id');
      state.activeRunId = runId;
      state.executionState = payload.execution_state;
      switchView('creator');
      renderExecutionProgress(state.executionState);
      showNotice(`任务 ${taskId} 已提交，运行 ID：${runId}`, 'success');
      hideBusy();
      await pollRunExecution(runId);
      await loadTasks();
    } catch (error) {
      hideBusy();
      el('taskSummary').textContent = `运行任务失败：${error.message}`;
    }
  }

  async function deleteTaskFromCenter(taskId) {
    if (!window.confirm(`确认删除任务“${taskId}”？已生成的 Run Bundle 不会被删除。`)) return;
    await api(`/task-center/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
    await loadTasks();
  }


  async function loadScenarioTemplates() {
    const compatibilityQuery=state.showCompatibilityCapabilities?'?include_compatibility=true&include_explicit=true':'';
    const payload=await api(`/scenario-templates${compatibilityQuery}`); state.scenarioTemplates=payload.templates || []; renderScenarioTemplateOptions();
  }

  function renderScenarioTemplateOptions() {
    const select=el('scenarioTemplateSelect');
    if (!select) return;
    const previous=select.value;
    select.replaceChildren(new Option('选择模板…',''));
    if (!state.selectedCapabilityId || !state.schema) return;
    const level=state.schema.capability.level;
    const objectRows=state.scenarioTemplates.filter((item)=>item.object_id===state.selectedObjectId);
    const exact=state.scenarioTemplates.filter((item)=>item.capability_id===state.selectedCapabilityId && item.object_id!==state.selectedObjectId);
    const sameLevel=state.scenarioTemplates.filter((item)=>item.level===level && item.object_id!==state.selectedObjectId && item.capability_id!==state.selectedCapabilityId);
    const seen=new Set();
    const rows=[...objectRows,...exact,...sameLevel].filter((item)=>{
      if (seen.has(item.template_id)) return false;
      seen.add(item.template_id);
      return true;
    });
    if (!rows.length) { select.appendChild(new Option('当前对象暂无模板','')); select.disabled=true; return; }
    select.disabled=false;
    rows.forEach((item)=>{
      let prefix='同层级';
      if (item.object_id===state.selectedObjectId) prefix=item.execution_scope==='integrated'?'当前对象（组合运行）':'当前对象';
      else if (item.capability_id===state.selectedCapabilityId) prefix='当前能力';
      const option=new Option(`${prefix} · ${item.name}`,item.template_id);
      option.title=`${item.summary || ''}${item.execution_scope==='integrated'?'；该模板通过所属分系统组合模型执行。':''}`;
      select.appendChild(option);
    });
    if (rows.some((item)=>item.template_id===previous)) select.value=previous;
  }

  async function applyScenarioTemplate() {
    const templateId=el('scenarioTemplateSelect').value; if (!templateId) throw new Error('请先选择场景模板');
    const payload=await api(`/scenario-templates/${encodeURIComponent(templateId)}/instantiate`,{method:'POST',body:JSON.stringify({})});
    await projectTaskSpecToForm(payload.task_spec,'场景模板'); state.activeTaskCenterId=null; updateTaskEditState(); state.planning=payload.planning;
    showNotice(`已应用场景模板：${state.scenarioTemplates.find((item)=>item.template_id===templateId)?.name || templateId}`,'success');
  }

  function parseExperimentValue(raw) {
    const text = String(raw).trim();
    if (text === 'true') return true;
    if (text === 'false') return false;
    const number = Number(text);
    return Number.isNaN(number) || text === '' ? text : number;
  }

  async function openExperimentModal() {
    if (!state.tasks.length) await loadTasks();
    const select=el('experimentBaseTaskSelect'); select.replaceChildren(new Option(state.taskSpec ? `当前编辑配置：${state.taskSpec.task?.name || state.taskSpec.task?.id}` : '当前编辑配置（尚未校验）','current'));
    state.tasks.forEach((task)=>select.appendChild(new Option(`任务中心 · ${task.name || task.task_id} · V${task.current_version || 1}`,task.task_id)));
    state.experimentBaseSpec=state.taskSpec ? clone(state.taskSpec) : null;
    if (!state.experimentBaseSpec && state.tasks.length) select.value=state.tasks[0].task_id;
    await selectExperimentBaseTask(select.value);
    updateExperimentTypeFields();
    el('experimentModal').classList.remove('hidden');
  }

  async function selectExperimentBaseTask(value) {
    if (value==='current') {
      if (!state.taskSpec && state.formData) { const ok=await previewForm(); if (!ok) throw new Error('当前编辑配置未通过校验'); }
      state.experimentBaseSpec=state.taskSpec ? clone(state.taskSpec) : null;
    } else {
      const payload=await api(`/task-center/tasks/${encodeURIComponent(value)}`); state.experimentBaseSpec=clone(payload.task?.task_spec || null);
    }
    if (!state.experimentBaseSpec) throw new Error('请选择一个有效的基础任务');
    await refreshExperimentSweepOptions();
    el('experimentModalStatus').textContent=`基础任务：${state.experimentBaseSpec.task?.name || state.experimentBaseSpec.task?.id}`;
  }

  async function refreshExperimentSweepOptions() {
    const select=el('sweepPath'); select.replaceChildren(new Option('正在读取可扫描参数…',''));
    const capabilityId=state.experimentBaseSpec?.model?.capability_id || state.experimentBaseSpec?.capability_id;
    if (!capabilityId) { select.replaceChildren(new Option('基础任务缺少能力标识','')); return; }
    let payload;
    try {
      payload=await api('/experiments/options',{method:'POST',body:JSON.stringify({base_task_spec: state.experimentBaseSpec})});
    } catch (error) {
      payload=await api('/experiments/dispersion-options',{method:'POST',body:JSON.stringify({base_task_spec: state.experimentBaseSpec})});
    }
    const fields=(payload.dispersion_options || payload.options || []).filter((field)=>{
      const path=String(field.path || ''); const type=String(field.type || '');
      return (path.startsWith('simulation.') || path.startsWith('parameters.values.')) && ['number','integer','boolean','string'].includes(type) && field.widget !== 'object_editor';
    });
    select.replaceChildren();
    fields.forEach((field)=>{
      const option=new Option(`${field.label || field.path}${field.unit ? `（${field.unit}）` : ''}`,field.path);
      option.dataset.description=field.description || ''; option.dataset.unit=field.unit || ''; option.dataset.type=field.type || '';
      option.dataset.minimum=field.minimum ?? ''; option.dataset.maximum=field.maximum ?? '';
      option.dataset.current=JSON.stringify(getPath(state.experimentBaseSpec,field.path) ?? field.default ?? null);
      select.appendChild(option);
    });
    if (!fields.length) select.appendChild(new Option('当前能力没有可扫描的标量参数',''));
    const preferred=fields.find((item)=>item.path==='simulation.duration_s') || fields[0];
    if (preferred) select.value=preferred.path;
    updateSweepParameterHelp();
  }

  function updateSweepParameterHelp() {
    const select=el('sweepPath'); const option=select.options[select.selectedIndex];
    if (!option || !option.value) { el('sweepParameterHelp').textContent='当前基础任务没有可扫描参数。'; return; }
    const limits=[option.dataset.minimum!=='' ? `最小值 ${option.dataset.minimum}` : '',option.dataset.maximum!=='' ? `最大值 ${option.dataset.maximum}` : ''].filter(Boolean).join('，');
    el('sweepParameterHelp').textContent=`技术路径：${option.value}
当前值：${option.dataset.current || '未设置'}${option.dataset.unit ? ` ${option.dataset.unit}` : ''}
${option.dataset.description || '能力注册表定义的可扫描参数。'}${limits ? `
约束：${limits}` : ''}`;
  }


  function parseMonteCarloDistribution(distribution, rawText) {
    const text = String(rawText || '').trim();
    if (distribution === 'choice') {
      const values = text.split(',').map(parseExperimentValue).filter(value => value !== '');
      return { distribution, values: values.length ? values : [0] };
    }
    const pairs = {};
    text.split(',').forEach((part) => {
      const [key, value] = part.split('=').map((item) => String(item || '').trim());
      if (key) pairs[key] = parseExperimentValue(value);
    });
    if (distribution === 'uniform') return { distribution, min: Number(pairs.min ?? pairs.low ?? 0), max: Number(pairs.max ?? pairs.high ?? 1) };
    if (distribution === 'triangular') return { distribution, min: Number(pairs.min ?? 0), max: Number(pairs.max ?? 1), mode: Number(pairs.mode ?? 0.5) };
    return { distribution: 'normal', mean: Number(pairs.mean ?? 0), std: Number(pairs.std ?? 1) };
  }

  function updateExperimentTypeFields() {
    const isMc = el('experimentType')?.value === 'monte_carlo';
    document.querySelectorAll('.mc-only').forEach((node) => node.classList.toggle('hidden', !isMc));
    document.querySelectorAll('.sweep-only').forEach((node) => node.classList.toggle('hidden', isMc));
    if (el('experimentModalTitle')) el('experimentModalTitle').textContent = isMc ? '创建 Monte Carlo 实验' : '创建参数扫描实验';
  }

  function closeExperimentModal() { el('experimentModal').classList.add('hidden'); }

  function buildExperimentPayload() {
    if (!state.experimentBaseSpec) throw new Error('缺少基础 TaskSpec');
    const path = el('sweepPath').value;
    const experimentType = el('experimentType')?.value || 'sweep';
    const values = el('sweepValues').value.split(',').map(parseExperimentValue).filter(value => value !== '');
    if (!path) throw new Error('请选择扫描或随机参数');
    if (experimentType === 'sweep' && !values.length) throw new Error('扫描取值不能为空');
    const metric = el('assertionMetric').value.trim();
    const operator = el('assertionOperator').value;
    let expected = parseExperimentValue(el('assertionValue').value);
    if (operator === 'within') {
      const parts = el('assertionValue').value.split(',').map(parseExperimentValue);
      if (parts.length !== 2) throw new Error('区间值必须为“最小值,最大值”');
      expected = parts;
    }
    return {
      name: el('experimentName').value.trim() || (experimentType === 'monte_carlo' ? 'Monte Carlo实验' : '参数扫描实验'),
      base_task_spec: state.experimentBaseSpec,
      experiment_type: experimentType,
      sweep: experimentType === 'sweep' ? { [path]: values } : {},
      sampling_plan: experimentType === 'monte_carlo' ? {
        seed: Number(el('mcSeed').value || 1),
        sample_count: Number(el('mcSampleCount').value || 16),
        dispersions: { [path]: parseMonteCarloDistribution(el('mcDistribution').value, el('mcDistributionValues').value) },
      } : {},
      assertions: metric ? [{ metric, operator, value: expected }] : [],
    };
  }

  async function previewExperimentPlan() {
    const request = buildExperimentPayload();
    const payload = await api('/experiments/plan', { method: 'POST', body: JSON.stringify({ ...request, preview_limit: 8 }) });
    const box = el('experimentPlanPreview');
    box.classList.remove('hidden');
    box.textContent = JSON.stringify({
      类型: payload.experiment_type === 'monte_carlo' ? 'Monte Carlo随机采样' : '枚举参数扫描',
      变体数量: payload.variant_count,
      预览: payload.preview,
      验收指标: payload.assertions,
    }, null, 2);
    el('experimentModalStatus').textContent = `已预览 ${payload.variant_count} 个变体；未保存、未运行。`;
  }

  async function createExperiment() {
    const request = buildExperimentPayload();
    const payload = await api('/experiments', {
      method: 'POST',
      body: JSON.stringify(request),
    });
    closeExperimentModal();
    switchView('experiments');
    await loadExperiments();
    showNotice(`实验 ${payload.experiment.experiment_id} 已创建，共 ${payload.experiment.variant_count} 个变体。`, 'success');
  }

  async function loadExperiments() {
    const payload = await api('/experiments?limit=100');
    state.experiments = payload.experiments || [];
    renderExperiments();
  }

  function renderExperiments() {
    const container = el('experimentList');
    if (!container) return;
    container.replaceChildren();
    el('experimentSummary').textContent = `共 ${state.experiments.length} 个实验；支持枚举参数扫描和 Monte Carlo 随机采样，每个变体均生成独立 Run Bundle。`;
    if (!state.experiments.length) {
      container.innerHTML = '<div class="empty-state"><strong>暂无实验</strong><span>点击“新建实验”，可选择当前编辑配置或任务中心已保存的任务。</span></div>';
      return;
    }
    state.experiments.forEach((experiment) => {
      const card = document.createElement('article');
      card.className = 'experiment-item';
      const title = document.createElement('div');
      title.innerHTML = `<strong>${experiment.name}</strong><span>${experiment.experiment_id} · ${(experiment.experiment_type || 'sweep') === 'monte_carlo' ? 'Monte Carlo' : '参数扫描'} · ${experiment.variant_count} 个变体 · ${experiment.status}</span>`;
      const actions = document.createElement('div');
      actions.className = 'toolbar-actions';
      const launch = document.createElement('button');
      launch.className = 'button button-primary'; launch.textContent = '启动批量运行';
      launch.addEventListener('click', async () => { await api(`/experiments/${experiment.experiment_id}/launch`, { method: 'POST', body: JSON.stringify({}) }); await loadExperiments(); });
      const detail = document.createElement('button');
      detail.className = 'button button-secondary'; detail.textContent = '查看对比';
      detail.addEventListener('click', () => showExperimentComparison(experiment.experiment_id));
      const remove = document.createElement('button');
      remove.className = 'button button-danger'; remove.textContent = '删除';
      remove.addEventListener('click', async () => { if (confirm('删除实验定义？Run Bundle 不会删除。')) { await api(`/experiments/${experiment.experiment_id}`, { method: 'DELETE' }); await loadExperiments(); } });
      actions.append(launch, detail, remove);
      card.append(title, actions);
      container.appendChild(card);
    });
  }

  async function showExperimentComparison(experimentId) {
    const [detail, comparison] = await Promise.all([
      api(`/experiments/${experimentId}`), api(`/experiments/${experimentId}/comparison`),
    ]);
    const rows = comparison.comparison?.rows || [];
    const metrics = (comparison.comparison?.metrics || []).slice(0, 10);
    el('experimentComparisonTitle').textContent = `${detail.experiment.name} · 运行对比`;
    el('experimentComparisonSummary').textContent = rows.length
      ? `已汇总 ${rows.length} 个运行；指标来自各自不可变 Run Bundle。`
      : '尚无已完成运行。启动实验并等待变体运行结束后再刷新。';
    const head = el('experimentComparisonHead');
    const body = el('experimentComparisonBody');
    head.replaceChildren(); body.replaceChildren();
    const header = document.createElement('tr');
    ['变体', 'Run', '状态', '验收', ...metrics].forEach(label => { const th = document.createElement('th'); th.textContent = label; header.appendChild(th); });
    head.appendChild(header);
    rows.forEach(row => {
      const tr = document.createElement('tr');
      const values = [row.variant_index + 1, row.run_id || '—', row.status || '—', row.assertion_status || '—', ...metrics.map(name => row.values?.[name] ?? '—')];
      values.forEach(value => { const td = document.createElement('td'); td.textContent = typeof value === 'number' ? String(Math.round(value * 1e6) / 1e6) : String(value); tr.appendChild(td); });
      body.appendChild(tr);
    });
    el('experimentComparisonPanel').classList.remove('hidden');
    await loadExperiments();
  }

  async function openFaultCatalog() {
    const capabilityId = state.selectedCapabilityId || state.taskSpec?.model?.capability_id || '';
    const payload = await api(`/fault-environment/catalog${capabilityId ? `?capability_id=${encodeURIComponent(capabilityId)}` : ''}`);
    const container = el('faultCatalogList');
    container.replaceChildren();
    const groups = {};
    (payload.fault_models || []).forEach((item) => {
      const group = item.ui_group || '其他事件';
      groups[group] = groups[group] || [];
      groups[group].push(item);
    });
    Object.entries(groups).forEach(([group, rows]) => {
      const section = document.createElement('article');
      section.className = 'experiment-item';
      const detail = document.createElement('div');
      const items = rows.map((item) => `<li><strong>${item.display_name}</strong><br><span>${item.trigger_semantics}</span><br><code>${item.effect}</code></li>`).join('');
      detail.innerHTML = `<strong>${group}</strong><span>${rows.length} 个模型；参数和证据来自 fault environment contract。</span><ul class="compact-list">${items}</ul>`;
      section.append(detail);
      container.appendChild(section);
    });
    if (!Object.keys(groups).length) container.innerHTML = '<div class="empty-state"><strong>暂无目录</strong><span>当前能力没有登记故障、退化或运行约束。</span></div>';
    el('faultCatalogModal').classList.remove('hidden');
  }

  function resetForm() {
    if (!state.schema) return;
    state.formData = clone(state.schema.default_form);
    state.taskSpec = null;
    state.planning = null;
    state.changedPaths = new Set();
    state.agentBeforeForm = null;
    state.activeTaskCenterId = null;
    updateTaskEditState();
    renderAgentChangePanel();
    renderForm();
    renderEvents();
    renderOutputs();
    syncTaskSpecEditor();
    showNotice('表单已恢复为能力注册表默认值。', 'success');
  }

  function bindEvents() {
    el('themeSelect').addEventListener('change', (event) => applyTheme(event.target.value));
    el('fontScaleSelect').addEventListener('change', (event) => applyFontScale(event.target.value));
    el('expandRunPanelBtn').addEventListener('click', toggleRunResultsExpanded);
    el('fullscreenRunPanelBtn').addEventListener('click', () => toggleRunPanelFullscreen().catch((error) => showNotice(`无法切换全屏：${error.message}`, 'error')));
    document.addEventListener('fullscreenchange', syncRunPanelControls);
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const panel = el('creatorRunPanel');
      if (panel?.classList.contains('run-panel-fullscreen-fallback')) toggleRunPanelFullscreen().catch(() => null);
    });
    el('saveApiTokenBtn').addEventListener('click', saveApiToken);
    el('apiTokenInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') saveApiToken(); });
    el('agentBackend').addEventListener('change', (event) => { state.selectedProviderId = event.target.value; });
    el('variantSelect').addEventListener('change', (event) => selectCapability(event.target.value));
    el('applyScenarioTemplateBtn').addEventListener('click', () => applyScenarioTemplate().catch(error => showNotice(error.message, 'error')));
    el('capabilitySearch').addEventListener('input', renderCapabilityList);
    el('levelFilter').addEventListener('change', renderCapabilityList);
    el('showCompatibilityCapabilities').addEventListener('change', async (event) => {
      state.showCompatibilityCapabilities = Boolean(event.target.checked);
      showBusy('刷新能力目录', state.showCompatibilityCapabilities ? '载入兼容、历史和显式能力' : '恢复推荐与标准能力');
      try { await Promise.all([loadCapabilities(), loadScenarioTemplates()]); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
    });
    el('addTelemetryStreamBtn').addEventListener('click', addTelemetryStream);
    el('fmeaEnabled').addEventListener('change', (event) => {
      const outputs = ensureAdvancedOutputs(); if (!outputs) return;
      outputs.fmea.enabled = Boolean(event.target.checked); markOutputChanged();
    });
    ['fmeaFormatCsv', 'fmeaFormatJson'].forEach((id) => el(id).addEventListener('change', () => {
      const outputs = ensureAdvancedOutputs(); if (!outputs) return;
      const selected = [];
      if (el('fmeaFormatCsv').checked) selected.push('csv');
      if (el('fmeaFormatJson').checked) selected.push('json');
      outputs.fmea.formats = selected.length ? selected : ['csv'];
      renderAdvancedOutputs(); markOutputChanged();
    }));
    ['fmeaSearch', 'fmeaCategory', 'fmeaEvidenceStatus', 'fmeaRatingStatus', 'fmeaVerifiedOnly'].forEach((id) => {
      const node = el(id); const eventName = id === 'fmeaSearch' ? 'input' : 'change';
      node.addEventListener(eventName, renderTraceability);
    });
    el('downloadFmeaCsvBtn').addEventListener('click', () => state.activeRunId && downloadEndpoint(`/runs/${encodeURIComponent(state.activeRunId)}/fmea/download?${fmeaDownloadQuery('csv')}`, `${state.activeRunId}_fmea.csv`).catch((error) => showNotice(error.message, 'error')));
    el('downloadFmeaJsonBtn').addEventListener('click', () => state.activeRunId && downloadEndpoint(`/runs/${encodeURIComponent(state.activeRunId)}/fmea/download?${fmeaDownloadQuery('json')}`, `${state.activeRunId}_fmea.json`).catch((error) => showNotice(error.message, 'error')));
    el('graphFlowModeBtn').addEventListener('click', () => switchGraphComposerMode('flow').catch((error) => graphSetStatus(error.message, 'error')));
    el('graphAssemblyModeBtn').addEventListener('click', () => switchGraphComposerMode('assembly').catch((error) => assemblySetStatus(error.message, 'error')));
    el('graphTuneBtn').addEventListener('click', async () => { showBusy('打开快速调参', '读取 Schema 注册参数与 QoI'); try { await openVisualTuning('flow'); } catch (error) { graphSetStatus(`快速调参不可用：${error.message}`, 'error'); } finally { hideBusy(); } });
    el('assemblyTuneBtn').addEventListener('click', async () => { showBusy('打开快速调参', '读取 Schema 注册参数与 QoI'); try { await openVisualTuning('assembly'); } catch (error) { assemblySetStatus(`快速调参不可用：${error.message}`, 'error'); } finally { hideBusy(); } });
    el('visualTuningCloseBtn').addEventListener('click', closeVisualTuning);
    el('visualTuningAddParameterBtn').addEventListener('click', () => {
      if (!state.visualTuningOptions || state.visualTuningSelections.length >= 3) return;
      const used = new Set(state.visualTuningSelections.map((item) => item.path));
      const next = (state.visualTuningOptions.parameters || []).find((item) => !used.has(item.path));
      if (!next) return;
      state.visualTuningSelections.push({ path: next.path, values: (next.suggested_values || []).join(', ') }); renderVisualTuning();
    });
    el('visualTuningObjective').addEventListener('change', (event) => { const target = visualTuningObjectiveByMetric(event.target.value); if (!el('visualTuningDirection').dataset.userSet && target?.suggested_direction) el('visualTuningDirection').value = target.suggested_direction; renderVisualTuning(); });
    el('visualTuningDirection').addEventListener('change', () => { el('visualTuningDirection').dataset.userSet = 'true'; });
    el('visualTuningStartBtn').addEventListener('click', async () => { try { await startVisualTuning(); } catch (error) { state.visualTuningResult = { ...(state.visualTuningResult || {}), message: `扫描失败：${error.message}` }; renderVisualTuning(); showNotice(`快速调参失败：${error.message}`, 'error'); } });
    el('visualTuningApplyBestBtn').addEventListener('click', applyVisualTuningBest);
    el('assemblyLoadBtn').addEventListener('click', () => {
      const capabilityId = el('assemblyCapabilitySelect').value;
      showBusy('加载注册装配', capabilityId);
      assemblyLoadContract(capabilityId).catch((error) => assemblySetStatus(`加载失败：${error.message}`, 'error')).finally(hideBusy);
    });
    el('assemblyRestoreBtn').addEventListener('click', assemblyRestoreBindings);
    el('assemblyValidateBtn').addEventListener('click', async () => {
      showBusy('校验多模块装配', '注册模块 → signal binding → 父 Capability TaskSpec');
      try { await assemblyCompile(); } catch (error) { assemblySetStatus(error.message, 'error'); } finally { hideBusy(); }
    });
    el('assemblyGenerateCodeBtn').addEventListener('click', async () => {
      showBusy('生成装配 Python', '内嵌装配图并绑定受信任 runtime owner');
      try { await assemblyGeneratePython(); } catch (error) { assemblySetStatus(error.message, 'error'); } finally { hideBusy(); }
    });
    el('assemblyResultOverlayBtn').addEventListener('click', () => openVisualRunResults('assembly').catch((error) => assemblySetStatus(`打开结果失败：${error.message}`, 'error')));
    el('assemblyAddScopeBtn').addEventListener('click', assemblyAddScope);
    el('assemblyAddDisplayBtn').addEventListener('click', assemblyAddDisplay);
    el('assemblyAddWorkspaceBtn').addEventListener('click', assemblyAddWorkspace);
    el('assemblyAutoLayoutBtn').addEventListener('click', assemblyAutoLayout);
    el('assemblyAlignLeftBtn').addEventListener('click', () => assemblyAlignSelection('left'));
    el('assemblyAlignTopBtn').addEventListener('click', () => assemblyAlignSelection('top'));
    el('assemblyDistributeHBtn').addEventListener('click', () => assemblyDistributeSelection('x'));
    el('assemblyDistributeVBtn').addEventListener('click', () => assemblyDistributeSelection('y'));
    el('assemblyCopyObserversBtn').addEventListener('click', assemblyCopySelectedObservers);
    el('assemblyPasteObserversBtn').addEventListener('click', assemblyPasteObservers);
    el('assemblyCompareBtn').addEventListener('click', async () => {
      if (state.assemblyComparisonRunning) return;
      assemblySetStatus('正在提交基线与当前装配对比运行；完成后自动汇总共同指标。', 'warning');
      try { await assemblyCompareBaseline(); } catch (error) { assemblySetStatus(`基线对比失败：${error.message}`, 'error'); }
    });
    el('assemblyCompareCloseBtn').addEventListener('click', () => { state.assemblyComparison = null; assemblyRenderComparison(); });
    el('assemblyExportProgramBtn').addEventListener('click', async () => {
      showBusy('导出可运行程序包', 'TaskSpec + 装配图 + Python + 启动脚本');
      try { await assemblyExportProgram(); } catch (error) { assemblySetStatus(`程序包导出失败：${error.message}`, 'error'); } finally { hideBusy(); }
    });
    el('assemblyRunBtn').addEventListener('click', () => runSimulation().catch((error) => { assemblySetStatus(`运行失败：${error.message}`, 'error'); showNotice(`仿真失败：${error.message}`, 'error'); hideBusy(); }));
    el('assemblyModuleSearch').addEventListener('input', (event) => { state.assemblyLibrarySearch = event.target.value || ''; assemblyRenderModuleLibrary(); });
    el('assemblyCompatibleOnly').addEventListener('change', (event) => { state.assemblyCompatibleOnly = Boolean(event.target.checked); assemblyRenderModuleLibrary(); });
    el('assemblyCanvas').addEventListener('dragover', (event) => {
      const capabilityId = state.assemblyDragModuleCapabilityId || '';
      if (!capabilityId || event.target.closest('.assembly-node')) return;
      const aliases = assemblyCompatibleAliasesForCapability(capabilityId);
      if (!aliases.length) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
      el('assemblyCanvas').classList.add('drop-module-target');
    });
    el('assemblyCanvas').addEventListener('dragleave', (event) => {
      if (!el('assemblyCanvas').contains(event.relatedTarget)) el('assemblyCanvas').classList.remove('drop-module-target');
    });
    el('assemblyCanvas').addEventListener('drop', (event) => {
      if (event.target.closest('.assembly-node')) return;
      event.preventDefault();
      el('assemblyCanvas').classList.remove('drop-module-target');
      const capabilityId = event.dataTransfer?.getData('text/plain') || state.assemblyDragModuleCapabilityId || '';
      state.assemblyDragModuleCapabilityId = null;
      const aliases = assemblyCompatibleAliasesForCapability(capabilityId);
      if (!aliases.length) { assemblySetStatus(`${capabilityId || '该模块'} 在当前装配中没有兼容槽位。`, 'error'); return; }
      const canvas = el('assemblyCanvas');
      const rect = canvas.getBoundingClientRect();
      const dropX = event.clientX - rect.left;
      const dropY = event.clientY - rect.top;
      const candidates = aliases.map((alias) => {
        const node = (state.assemblyGraph?.nodes || []).find((item) => item.moduleAlias === alias);
        const dx = (Number(node?.x) || 0) + 114 - dropX;
        const dy = (Number(node?.y) || 0) + 71 - dropY;
        return { alias, node, distance: dx * dx + dy * dy };
      }).filter((item) => item.node).sort((a, b) => a.distance - b.distance);
      const target = candidates[0];
      if (!target) { assemblySetStatus('没有找到可插入目标节点。', 'error'); return; }
      try {
        assemblyReplaceModule(target.node.id, capabilityId);
        assemblySetStatus(`已将 ${capabilityId} 自动匹配到 ${target.alias}；无需手工重接端口。`, 'success');
      } catch (error) { assemblySetStatus(error.message, 'error'); }
    });
    el('assemblyCanvas').addEventListener('pointerdown', assemblyBeginMarquee);
    el('assemblyCanvas').addEventListener('click', (event) => {
      if (state.assemblySuppressCanvasClick) { state.assemblySuppressCanvasClick = false; return; }
      if (event.target === el('assemblyCanvas') || event.target === el('assemblyNodes')) { state.assemblyPendingConnection = null; assemblyClearSelection(); }
    });
    el('assemblyCanvas').addEventListener('keydown', (event) => {
      const modifier = event.ctrlKey || event.metaKey;
      if (event.key === 'Escape') { event.preventDefault(); state.assemblyPendingConnection = null; assemblyClearSelection({ render: false }); assemblySetStatus('已取消当前 signal 接线并清除选择。'); assemblyRender(); }
      if ((event.key === 'Delete' || event.key === 'Backspace') && state.assemblySelectedEdgeId) { event.preventDefault(); assemblyRemoveEdge(state.assemblySelectedEdgeId); return; }
      if ((event.key === 'Delete' || event.key === 'Backspace') && state.assemblySelection.some((key) => key.startsWith('observer:'))) {
        event.preventDefault(); const ids = assemblySelectionItems().filter((item) => item.kind === 'observer').map((item) => item.id);
        ids.forEach((id) => { state.assemblyGraph.scopes = assemblyScopes().filter((item) => item.id !== id); });
        state.assemblySelection = state.assemblySelection.filter((key) => !key.startsWith('observer:'));
        const last = assemblySelectionItems().slice(-1)[0] || null; assemblySyncPrimarySelection(last?.kind || null, last?.id || null); assemblyRender();
        assemblySetStatus(`已删除 ${ids.length} 个观察器；物理模块受注册合同保护，未删除。`, 'success'); return;
      }
      if (modifier && event.key.toLowerCase() === 'a') { event.preventDefault(); assemblySelectAll(); }
      if (modifier && event.key.toLowerCase() === 'c') { event.preventDefault(); assemblyCopySelectedObservers(); }
      if (modifier && event.key.toLowerCase() === 'v') { event.preventDefault(); assemblyPasteObservers(); }
      if (modifier && event.key.toLowerCase() === 'd') { event.preventDefault(); assemblyDuplicateSelectedObservers(); }
      if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && state.assemblySelection.length) {
        event.preventDefault(); const step = event.shiftKey ? 5 : 20;
        const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
        const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
        assemblySelectionItems().forEach((entry) => { entry.item.x = (Number(entry.item.x) || 0) + dx; entry.item.y = (Number(entry.item.y) || 0) + dy; assemblyClampLayoutItem(entry); });
        assemblyRender();
      }
    });
    el('graphCapabilitySearch').addEventListener('input', renderGraphPalette);
    $all('[data-graph-block]').forEach((button) => button.addEventListener('dragstart', (event) => graphTransfer(event, { kind: 'block', type: button.dataset.graphBlock })));
    el('graphCanvas').addEventListener('dragover', (event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; el('graphCanvas').classList.add('drag-over'); });
    el('graphCanvas').addEventListener('dragleave', (event) => { if (!el('graphCanvas').contains(event.relatedTarget)) el('graphCanvas').classList.remove('drag-over'); });
    el('graphCanvas').addEventListener('drop', (event) => graphHandleDrop(event).catch((error) => graphSetStatus(error.message, 'error')));
    el('graphCanvas').addEventListener('click', (event) => { if (event.target === el('graphCanvas') || event.target === el('graphNodes')) { graphCancelConnection(); state.graphSelectedNodeId = null; state.graphSelectedEdgeId = null; renderGraph(); } });
    el('graphCanvas').addEventListener('keydown', (event) => {
      const modifier = event.ctrlKey || event.metaKey;
      if (event.key === 'Escape') { event.preventDefault(); graphCancelConnection(); graphSetStatus('已取消当前接线。'); }
      if ((event.key === 'Delete' || event.key === 'Backspace') && state.graphSelectedEdgeId) { event.preventDefault(); graphRemoveEdge(state.graphSelectedEdgeId); }
      if (modifier && event.key.toLowerCase() === 'z' && !event.shiftKey) { event.preventDefault(); graphUndo(); }
      if (modifier && ((event.key.toLowerCase() === 'z' && event.shiftKey) || event.key.toLowerCase() === 'y')) { event.preventDefault(); graphRedo(); }
    });
    el('graphUndoBtn').addEventListener('click', graphUndo);
    el('graphRedoBtn').addEventListener('click', graphRedo);
    el('graphSaveDraftBtn').addEventListener('click', () => { try { graphSaveDraft(); } catch (error) { graphSetStatus(`保存草稿失败：${error.message}`, 'error'); } });
    el('graphLoadDraftBtn').addEventListener('click', () => graphLoadDraft().catch((error) => graphSetStatus(`加载草稿失败：${error.message}`, 'error')));
    el('graphExportProjectBtn').addEventListener('click', () => { try { graphExportProject(); } catch (error) { graphSetStatus(`导出工程失败：${error.message}`, 'error'); } });
    el('graphImportProjectBtn').addEventListener('click', () => el('graphImportProjectInput').click());
    el('graphImportProjectInput').addEventListener('change', (event) => {
      const file = event.target.files?.[0] || null;
      graphImportProjectFile(file).catch((error) => graphSetStatus(`导入工程失败：${error.message}`, 'error')).finally(() => { event.target.value = ''; });
    });
    el('graphClearBtn').addEventListener('click', graphClear);
    el('graphResetBtn').addEventListener('click', () => { graphInitialize(true); graphSetStatus('已恢复带类型端口的基础 DAG；可直接校验，也可手工重新接线。', 'success'); });
    el('graphAutoWireBtn').addEventListener('click', () => graphAutoWire());
    el('graphAutoLayoutBtn').addEventListener('click', graphAutoLayout);
    el('graphValidateBtn').addEventListener('click', async () => { showBusy('校验图形流程', '节点结构 → TaskSpec → 执行计划'); try { await graphCompile({ requireCode: true, requireRun: true }); } catch (error) { graphSetStatus(error.message, 'error'); } finally { hideBusy(); } });
    el('graphResultOverlayBtn').addEventListener('click', () => openVisualRunResults('flow').catch((error) => graphSetStatus(`打开结果失败：${error.message}`, 'error')));
    el('graphGenerateCodeBtn').addEventListener('click', async () => { showBusy('生成可执行 Python', '使用受约束 deterministic script exporter'); try { await graphGeneratePython(); } catch (error) { graphSetStatus(error.message, 'error'); } finally { hideBusy(); } });
    el('graphExportProgramBtn').addEventListener('click', async () => { showBusy('导出可运行程序包', 'TaskSpec + Python + 启动脚本'); try { await graphExportProgram(); } catch (error) { graphSetStatus(`程序包导出失败：${error.message}`, 'error'); } finally { hideBusy(); } });
    el('graphRunBtn').addEventListener('click', () => runSimulation().catch((error) => { graphSetStatus(`运行失败：${error.message}`, 'error'); showNotice(`仿真失败：${error.message}`, 'error'); hideBusy(); }));
    el('effectKind').addEventListener('change', renderEffectSelect);
    el('addEffectBtn').addEventListener('click', addEffect);
    el('resetFormBtn').addEventListener('click', resetForm);
    el('saveTaskBtn').addEventListener('click', async () => {
      showBusy('保存仿真任务', '写入任务中心');
      try { await saveCurrentTask('new'); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
    });
    el('updateTaskBtn').addEventListener('click', async () => { showBusy('更新仿真任务','写入任务中心新版本'); try { await saveCurrentTask('update'); } catch (error) { showNotice(error.message,'error'); } finally { hideBusy(); } });
    el('formFieldSearch').addEventListener('input', renderForm);
    el('showAdvancedFields').addEventListener('change', renderForm);
    el('acceptAgentChangesBtn').addEventListener('click', clearAgentChanges);
    el('undoAgentChangesBtn').addEventListener('click', undoAgentChanges);
    el('previewBtn').addEventListener('click', async () => {
      const graphMode = document.querySelector('.tab.active')?.dataset.tab === 'graph';
      showBusy(graphMode ? '校验图形流程' : '校验并编译 TaskSpec', graphMode ? '检查节点结构并编译 Canonical TaskSpec' : '解析 Schema 表单与能力约束');
      try {
        if (graphMode && state.graphComposerMode === 'assembly') await assemblyCompile();
        else if (graphMode) await graphCompile({ requireCode: true, requireRun: true });
        else await previewForm();
      } catch (error) { showNotice(error.message, 'error'); graphSetStatus(error.message, 'error'); } finally { hideBusy(); }
    });
    el('runBtn').addEventListener('click', async () => {
      try { await runSimulation(); } catch (error) { showNotice(`仿真失败：${error.message}`, 'error'); hideBusy(); }
    });
    el('clearAgentConversationBtn').addEventListener('click', () => { state.agentHistory=[]; renderAgentConversation(); });
    el('agentParseBtn').addEventListener('click', async () => {
      showBusy('Agent 正在解析需求', '检索能力并生成受约束 TaskSpec');
      try { await parseNaturalLanguage(); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
    });
    el('applyJsonToFormBtn').addEventListener('click', async () => {
      showBusy('同步 TaskSpec 到表单', '校验能力并投影 Registry 字段');
      try { await syncEditorToForm(); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
    });
    el('validateJsonBtn').addEventListener('click', async () => {
      showBusy('校验 TaskSpec', '执行 Schema、语义和能力边界检查');
      try { await validateEditor(); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
    });
    el('refreshRunsBtn').addEventListener('click', loadRuns);
    el('cancelRunBtn').addEventListener('click', async () => {
      try { await cancelActiveRun(); } catch (error) { showNotice(`取消失败：${error.message}`, 'error'); }
    });
    el('openRunReportBtn').addEventListener('click', () => state.activeRunId && openReport(state.activeRunId).catch((error) => showNotice(error.message, 'error')));
    el('exportDatasetBtn').addEventListener('click', () => exportActiveDataset().catch((error) => showNotice(`导出数据集失败：${error.message}`, 'error')));
    el('downloadRunDiagnosticBtn').addEventListener('click', () => state.activeRunId && downloadEndpoint(`/diagnostics/bundle?run_id=${encodeURIComponent(state.activeRunId)}`, `diagnostic_${state.activeRunId}.zip`).catch((error) => showNotice(error.message, 'error')));
    el('refreshRunCenterBtn').addEventListener('click', () => loadRunCenter().catch((error) => { el('runCenterSummary').textContent = error.message; }));
    el('refreshDiagnosticsBtn').addEventListener('click', () => loadDiagnostics().catch((error) => { el('diagnosticDetails').textContent = error.message; }));
    el('downloadDiagnosticBtn').addEventListener('click', () => downloadEndpoint('/diagnostics/bundle', 'sat_sim_diagnostic.zip').catch((error) => showNotice(error.message, 'error')));
    el('refreshLogsBtn').addEventListener('click', () => loadLogs().catch((error) => { el('logSummary').textContent = error.message; }));
    el('downloadLogsBtn').addEventListener('click', () => downloadEndpoint('/logs/download', 'sat_sim_workbench.jsonl').catch((error) => showNotice(error.message, 'error')));
    el('logLevelFilter').addEventListener('change', () => loadLogs().catch(() => null));
    el('logSearch').addEventListener('input', () => { clearTimeout(state.logSearchTimer); state.logSearchTimer = setTimeout(() => loadLogs().catch(() => null), 250); });
    el('refreshInteractiveBtn').addEventListener('click', () => loadInteractiveSessions().catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveCapability').addEventListener('change', () => {
      renderInteractiveCapabilityHint();
      if (!state.interactiveSession) renderInteractiveCommandTargets();
    });
    el('createInteractiveSessionBtn').addEventListener('click', () => createInteractiveSession().catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveStartBtn').addEventListener('click', () => interactiveControl('start').catch(error => interactiveNotice(error.message, 'error')));
    el('interactivePauseBtn').addEventListener('click', () => interactiveControl('pause').catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveResumeBtn').addEventListener('click', () => interactiveControl('resume').catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveStepBtn').addEventListener('click', () => interactiveControl('step', { quanta: 1 }).catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveStopBtn').addEventListener('click', () => interactiveControl('stop').catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveRate').addEventListener('change', event => interactiveControl('rate', { rate: Number(event.target.value) }).catch(error => interactiveNotice(error.message, 'error')));
    el('interactiveStream').addEventListener('change', renderInteractiveTelemetry);
    el('interactiveTelemetryObject').addEventListener('change', renderInteractiveTelemetry);
    el('interactiveCommandTarget').addEventListener('change', renderInteractiveCommandOperations);
    el('interactiveCommandOperation').addEventListener('change', renderInteractiveCommandForm);
    el('interactiveSendCommandBtn').addEventListener('click', () => sendInteractiveCommand().catch(error => interactiveNotice(error.message, 'error')));
    $all('.nav-button[data-view]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
    el('openModelSettingsBtn').addEventListener('click', openModelSettings);
    el('closeModelSettingsBtn').addEventListener('click', closeModelSettings);
    el('modelSettingsModal').addEventListener('click', (event) => { if (event.target === el('modelSettingsModal')) closeModelSettings(); });
    el('localModelType').addEventListener('change', (event) => { el('localModelBaseUrl').value = LOCAL_MODEL_DEFAULTS[event.target.value] || ''; });
    el('discoverLocalModelBtn').addEventListener('click', async () => {
      try { await discoverLocalModels(); } catch (error) { el('localModelStatus').textContent = `发现失败：${error.message}`; el('localModelStatus').className = 'model-status provider-unready'; }
    });
    el('localModelDiscovered').addEventListener('change', (event) => { if (event.target.value) el('localModelName').value = event.target.value; });
    el('probeLocalModelBtn').addEventListener('click', async () => {
      try { await saveAndProbeLocalModel(); } catch (error) { el('localModelStatus').textContent = `配置失败：${error.message}`; el('localModelStatus').className = 'model-status provider-unready'; }
    });
    el('closeTaskDetailsBtn').addEventListener('click', closeTaskDetails);
    el('taskDetailsModal').addEventListener('click', (event) => { if (event.target === el('taskDetailsModal')) closeTaskDetails(); });
    el('cloneTaskBtn').addEventListener('click', () => cloneActiveTask().catch((error) => showNotice(error.message, 'error')));
    el('refreshTasksBtn').addEventListener('click', () => loadTasks().catch(() => null));
    el('taskSearch').addEventListener('input', () => loadTasks().catch(() => null));
    el('taskStatusFilter').addEventListener('change', () => loadTasks().catch(() => null));
    el('newExperimentBtn').addEventListener('click', () => openExperimentModal().catch(error => showNotice(error.message, 'error')));
    el('refreshExperimentsBtn').addEventListener('click', () => loadExperiments().catch(() => null));
    el('experimentBaseTaskSelect').addEventListener('change', (event) => selectExperimentBaseTask(event.target.value).catch((error) => { el('experimentModalStatus').textContent=error.message; }));
    el('experimentType').addEventListener('change', updateExperimentTypeFields);
    el('sweepPath').addEventListener('change', updateSweepParameterHelp);
    el('closeExperimentBtn').addEventListener('click', closeExperimentModal);
    el('experimentModal').addEventListener('click', event => { if (event.target === el('experimentModal')) closeExperimentModal(); });
    el('previewExperimentPlanBtn').addEventListener('click', () => previewExperimentPlan().catch(error => { el('experimentModalStatus').textContent = error.message; }));
    el('createExperimentBtn').addEventListener('click', () => createExperiment().catch(error => { el('experimentModalStatus').textContent = error.message; }));
    el('faultCatalogBtn').addEventListener('click', () => openFaultCatalog().catch(error => showNotice(`故障环境目录读取失败：${error.message}`, 'error')));
    el('closeFaultCatalogBtn').addEventListener('click', () => el('faultCatalogModal').classList.add('hidden'));
    el('faultCatalogModal').addEventListener('click', event => { if (event.target === el('faultCatalogModal')) el('faultCatalogModal').classList.add('hidden'); });
    el('closeExperimentComparisonBtn').addEventListener('click', () => el('experimentComparisonPanel').classList.add('hidden'));
    $all('.tab').forEach((button) => button.addEventListener('click', () => switchTab(button.dataset.tab)));
    $all('.result-tab').forEach((button) => button.addEventListener('click', () => switchResultTab(button.dataset.resultTab)));
    window.addEventListener('resize', () => {
      if (document.querySelector('.result-tab.active')?.dataset.resultTab === 'charts') drawChart();
      if (state.activeView === 'interactive') renderInteractiveTelemetry();
      if (state.graphComposerMode === 'assembly') assemblyDrawEdges();
    });
  }

  async function init() {
    initTheme();
    initDisplayPreferences();
    bindEvents();
    await loadAuthStatus().catch(() => null);
    await Promise.allSettled([loadHeader(), loadRuns(), loadModelProviders(), loadTasks(), loadScenarioTemplates(), loadExperiments(), loadInteractiveAvailability()]);
    try {
      await loadCapabilities();
      await loadAssemblyCatalog();
      await loadModuleLibrary();
    } catch (error) {
      showNotice(`工作台初始化失败：${error.message}`, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
