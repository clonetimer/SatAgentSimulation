(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    entered: false, runs: [], runId: null, sources: [], metrics: [],
    reports: [], requestId: 0, busy: false,
  };
  let nextMetricSequence = 1;
  const formulas = [
    ['sample_mean.v1', '平均值'],
    ['sample_rms.v1', '均方根'],
    ['max_abs.v1', '最大绝对值'],
    ['final.v1', '末值'],
  ];
  const messages = {
    EVALUATION_RUN_NOT_FOUND: '找不到这条仿真记录。',
    EVALUATION_RUN_NOT_SEALED: '这条仿真记录尚未完成归档，暂时不能评估。',
    EVALUATION_RUN_INTEGRITY_FAILED: '仿真数据完整性检查未通过，暂时不能评估。',
    EVALUATION_RUN_NOT_ELIGIBLE: '仅支持已完成且通过验证的仿真记录。',
    EVALUATION_RUN_CAPABILITY_MISSING: '这条仿真记录缺少可识别的仿真对象信息。',
    EVALUATION_TELEMETRY_NOT_RECORDED: '这条仿真记录没有可用的过程数据。',
    EVALUATION_TELEMETRY_TOO_LARGE: '本次数据量超出当前评估处理范围。',
    EVALUATION_SOURCE_BINDING_MISMATCH: '所选参量与本次运行记录不匹配，请重新选择。',
    EVALUATION_TELEMETRY_INVALID: '仿真记录中的过程数据格式异常。',
    EVALUATION_TELEMETRY_EMPTY: '所选参量没有可用采样。',
    EVALUATION_SAMPLE_LIMIT_EXCEEDED: '采样数量超出当前评估处理范围。',
    EVALUATION_INVALID_WINDOW: '请检查评估时段的起止时间。',
    EVALUATION_INVALID_GAP_LIMIT: '最大采样间隔必须大于零。',
    EVALUATION_INVALID_LIMITS: '请检查目标范围。',
    EVALUATION_REVERSED_LIMITS: '目标下限不能大于目标上限。',
    EVALUATION_MINIMUM_SAMPLES: '采样数量未达到设定要求。',
    EVALUATION_INCOMPLETE_WINDOW: '本次记录没有覆盖完整的评估时段。',
    EVALUATION_SAMPLING_GAP: '实际采样间隔超过设定范围。',
    AUTHENTICATION_REQUIRED: '请先连接仿真服务。',
  };

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = sessionStorage.getItem('sat-sim-api-token') || '';
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body && typeof options.body !== 'string') {
      headers.set('Content-Type', 'application/json');
      options = {...options, body: JSON.stringify(options.body)};
    }
    const response = await fetch(path, {...options, headers});
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) {
      const detail = payload.detail || {};
      const code = typeof detail === 'object' ? detail.reason_code : detail;
      throw new Error(messages[code] || '暂时无法完成操作，请检查所选记录后重试。');
    }
    return payload;
  }

  function notice(message = '', kind = '') {
    const node = $('evaluationFormNotice');
    if (!node) return;
    node.textContent = message;
    node.dataset.kind = kind;
  }

  function runLabel(run) {
    const title = run.task_name || run.task_id || '仿真任务';
    return `${title} · ${run.run_id}`;
  }

  function emptyState(title, detail) {
    const wrapper = document.createElement('div');
    wrapper.className = 'empty-state';
    const strong = document.createElement('strong');
    strong.textContent = title;
    const span = document.createElement('span');
    span.textContent = detail;
    wrapper.append(strong, span);
    return wrapper;
  }

  function setBusy(value) {
    state.busy = value;
    $('createEvaluationBtn').disabled = value || !state.runId || state.metrics.length === 0;
    $('refreshEvaluationBtn').disabled = value;
    $('evaluationRunSelect').disabled = value;
  }

  async function enter() {
    if (state.entered) return refresh();
    state.entered = true;
    $('evaluationRunSelect').addEventListener('change', () => {
      state.runId = $('evaluationRunSelect').value || null;
      loadRun();
    });
    $('refreshEvaluationBtn').addEventListener('click', () => refresh());
    $('evaluationSourceList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-add-source]');
      if (button) addSource(button.dataset.addSource);
    });
    $('evaluationMetricList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove-metric]');
      if (!button) return;
      state.metrics = state.metrics.filter((row) => row.metricId !== button.dataset.removeMetric);
      renderMetrics();
    });
    $('evaluationMetricList').addEventListener('change', (event) => updateMetric(event.target));
    $('createEvaluationBtn').addEventListener('click', () => createReport());
    $('createEvaluationDraftBtn').addEventListener('click', () => createConfigurationDraft());
    $('closeEvaluationReportBtn').addEventListener('click', () => $('evaluationReportPanel').classList.add('hidden'));
    $('evaluationHistoryList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-open-report]');
      if (button) openReport(button.dataset.openReport);
    });
    return refresh();
  }

  async function refresh() {
    notice('');
    $('evaluationRunSummary').textContent = '正在读取仿真记录…';
    try {
      const payload = await request('/runs?limit=200');
      state.runs = (payload.runs || []).filter((row) =>
        row.status === 'SUCCEEDED' && row.validation_result === 'PASS' && row.sealed === true
      );
      const select = $('evaluationRunSelect');
      const preferred = state.runId || select.value;
      select.replaceChildren();
      state.runs.forEach((run) => {
        const option = document.createElement('option');
        option.value = run.run_id;
        option.textContent = runLabel(run);
        select.appendChild(option);
      });
      if (!state.runs.length) {
        state.runId = null;
        state.sources = [];
        state.metrics = [];
        state.reports = [];
        $('evaluationRunSummary').textContent = '暂无可评估的仿真记录。完成一次仿真并通过验证后，即可开展效能评估。';
        $('evaluationSourceList').replaceChildren(emptyState('暂无仿真数据', '完成一次仿真后，可在这里选择需要评估的输出参量。'));
        $('evaluationMetricList').replaceChildren(emptyState('尚未选择评估参量', '从左侧选择本次运行实际记录的参量。'));
        $('evaluationHistoryList').replaceChildren(emptyState('暂无历史评估', '生成评估报告后，会保存在这里。'));
        $('evaluationReportPanel').classList.add('hidden');
        renderCounts();
        return;
      }
      const selected = state.runs.find((row) => row.run_id === preferred) || state.runs[0];
      state.runId = selected.run_id;
      select.value = selected.run_id;
      await loadRun();
    } catch (error) {
      $('evaluationRunSummary').textContent = error.message;
      notice(error.message, 'error');
    }
  }

  async function loadRun() {
    if (!state.runId) return;
    const requestId = ++state.requestId;
    state.metrics = [];
    state.sources = [];
    $('evaluationReportPanel').classList.add('hidden');
    $('evaluationRunSummary').textContent = '正在读取本次运行的可用参量…';
    $('evaluationSourceList').replaceChildren(emptyState('正在读取', '请稍候。'));
    $('evaluationMetricList').replaceChildren(emptyState('尚未选择评估参量', '从左侧选择需要分析的参量。'));
    renderCounts();
    try {
      const [sourcePayload, historyPayload] = await Promise.all([
        request(`/runs/${encodeURIComponent(state.runId)}/evaluation-sources`),
        request(`/runs/${encodeURIComponent(state.runId)}/evaluations`),
      ]);
      if (requestId !== state.requestId) return;
      state.sources = sourcePayload.sources || [];
      state.reports = historyPayload.reports || [];
      const range = sourcePayload.time_range_s;
      if (Array.isArray(range) && range.length === 2) {
        $('evaluationWindowStart').value = String(range[0]);
        $('evaluationWindowEnd').value = String(range[1]);
      }
      const selected = state.runs.find((row) => row.run_id === state.runId);
      const availableCount = state.sources.filter((row) => row.evaluable).length;
      $('evaluationRunSummary').textContent = `${selected ? runLabel(selected) : state.runId} · 已完成并通过验证 · ${availableCount} 项参量可用于评估。`;
      renderSources();
      renderMetrics();
      renderHistory();
    } catch (error) {
      if (requestId !== state.requestId) return;
      $('evaluationRunSummary').textContent = error.message;
      $('evaluationSourceList').replaceChildren(emptyState('无法读取仿真数据', error.message));
      renderHistory();
      notice(error.message, 'error');
    }
  }

  function renderSources() {
    const host = $('evaluationSourceList');
    host.replaceChildren();
    if (!state.sources.length) {
      host.appendChild(emptyState('本次运行没有可评估的输出参量', '请选择其他仿真记录，或检查本次运行记录的输出内容。'));
      renderCounts();
      return;
    }
    const groups = new Map();
    state.sources.forEach((source) => {
      const owner = source.display_name_zh.split(' · ')[0] || source.owner_object_id;
      if (!groups.has(owner)) groups.set(owner, []);
      groups.get(owner).push(source);
    });
    groups.forEach((items, owner) => {
      const section = document.createElement('section');
      section.className = 'evaluation-source-group';
      const heading = document.createElement('h3');
      heading.textContent = owner;
      section.appendChild(heading);
      items.forEach((source) => {
        const card = document.createElement('article');
        card.className = 'evaluation-source-card';
        const info = document.createElement('div');
        info.className = 'evaluation-source-info';
        const title = document.createElement('strong');
        title.textContent = source.display_name_zh;
        const meta = document.createElement('span');
        meta.textContent = `${source.unit} · ${Number(source.sample_count || 0).toLocaleString()} 个采样`;
        info.append(title, meta);
        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'button button-ghost';
        add.textContent = '添加';
        add.dataset.addSource = source.source_id;
        const selected = state.metrics.some((row) => row.source.source_id === source.source_id);
        add.disabled = !source.evaluable || selected || state.busy;
        if (!source.evaluable) {
          const reason = document.createElement('span');
          reason.className = 'evaluation-source-reason';
          reason.textContent = source.availability_reason || '本次记录不可用于评估。';
          info.appendChild(reason);
        }
        card.append(info, add);
        section.appendChild(card);
      });
      host.appendChild(section);
    });
    renderCounts();
  }

  function addSource(sourceId) {
    const source = state.sources.find((row) => row.source_id === sourceId);
    if (!source || !source.evaluable || state.metrics.some((row) => row.source.source_id === sourceId)) return;
    state.metrics.push({
      metricId: `metric-${nextMetricSequence++}`, source,
      formula: 'sample_rms.v1', minimumSamples: 1, lower: '', upper: '',
    });
    renderSources();
    renderMetrics();
  }

  function renderMetrics() {
    const host = $('evaluationMetricList');
    host.replaceChildren();
    if (!state.metrics.length) {
      host.appendChild(emptyState('尚未选择评估参量', '从左侧选择需要分析的参量。'));
    }
    state.metrics.forEach((metric) => {
      const card = document.createElement('article');
      card.className = 'evaluation-metric-card';
      const top = document.createElement('div');
      top.className = 'evaluation-metric-top';
      const title = document.createElement('strong');
      title.textContent = metric.source.display_name_zh;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'evaluation-remove-button';
      remove.textContent = '移除';
      remove.dataset.removeMetric = metric.metricId;
      top.append(title, remove);
      const fields = document.createElement('div');
      fields.className = 'evaluation-metric-fields';
      const formulaLabel = document.createElement('label');
      formulaLabel.textContent = '统计方式';
      const formula = document.createElement('select');
      formula.dataset.metricFormula = metric.metricId;
      formulas.forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        formula.appendChild(option);
      });
      formula.value = metric.formula;
      formulaLabel.appendChild(formula);
      const sampleLabel = document.createElement('label');
      sampleLabel.textContent = '最少采样数';
      const samples = document.createElement('input');
      samples.type = 'number'; samples.min = '1'; samples.max = '250000'; samples.step = '1';
      samples.value = String(metric.minimumSamples);
      samples.dataset.metricSamples = metric.metricId;
      sampleLabel.appendChild(samples);
      const lowerLabel = document.createElement('label');
      lowerLabel.textContent = `目标下限（${metric.source.unit}，可选）`;
      const lower = document.createElement('input');
      lower.type = 'number'; lower.step = 'any'; lower.placeholder = '不设置';
      lower.value = metric.lower; lower.dataset.metricLower = metric.metricId;
      lowerLabel.appendChild(lower);
      const upperLabel = document.createElement('label');
      upperLabel.textContent = `目标上限（${metric.source.unit}，可选）`;
      const upper = document.createElement('input');
      upper.type = 'number'; upper.step = 'any'; upper.placeholder = '不设置';
      upper.value = metric.upper; upper.dataset.metricUpper = metric.metricId;
      upperLabel.appendChild(upper);
      fields.append(formulaLabel, sampleLabel, lowerLabel, upperLabel);
      card.append(top, fields);
      host.appendChild(card);
    });
    renderSources();
    renderCounts();
  }

  function updateMetric(input) {
    const key = input.dataset.metricFormula || input.dataset.metricSamples
      || input.dataset.metricLower || input.dataset.metricUpper;
    if (!key) return;
    const metric = state.metrics.find((row) => row.metricId === key);
    if (!metric) return;
    if (input.dataset.metricFormula) metric.formula = input.value;
    if (input.dataset.metricSamples) metric.minimumSamples = input.value;
    if (input.dataset.metricLower) metric.lower = input.value;
    if (input.dataset.metricUpper) metric.upper = input.value;
  }

  function renderCounts() {
    $('evaluationSourceCount').textContent = `${state.sources.filter((row) => row.evaluable).length} 项可用`;
    $('evaluationSelectedCount').textContent = `${state.metrics.length} 项`;
    $('createEvaluationBtn').disabled = state.busy || !state.runId || !state.metrics.length;
  }

  function renderReport(report, createdAt = null) {
    state.currentReportId = report.report_id;
    const draftButton = $('createEvaluationDraftBtn');
    draftButton.textContent = '根据评估创建配置草稿';
    draftButton.disabled = false;
    draftButton.title = '';
    $('evaluationReportPanel').classList.remove('hidden');
    $('evaluationReportMeta').textContent = `${report.plan_name_zh || report.plan_id} · 来源运行：${report.source_run_id}`;
    const summary = $('evaluationReportSummary');
    summary.replaceChildren();
    const status = document.createElement('span');
    status.className = `evaluation-report-status ${String(report.status || '').toLowerCase()}`;
    status.textContent = report.status === 'COMPUTED' ? '评估完成'
      : report.status === 'PARTIAL' ? '部分参量已完成' : '暂无法评估';
    const detail = document.createElement('span');
    detail.textContent = `评估时段：${report.window_s[0]}–${report.window_s[1]} s${createdAt ? ` · ${new Date(createdAt).toLocaleString()}` : ''}`;
    summary.append(status, detail);
    const host = $('evaluationReportMetrics');
    host.replaceChildren();
    const verdicts = {
      NOT_REQUESTED: '未设置目标范围',
      WITHIN_LIMITS: '符合设定范围',
      OUTSIDE_LIMITS: '超出设定范围',
      NOT_EVALUATED: '未评估',
    };
    const reasons = {
      SOURCE_NOT_RECORDED: '本次运行未记录该参量。',
      SOURCE_BINDING_MISMATCH: '参量来源或归属与评估方案不一致。',
      INVALID_SERIES: '记录的数据格式不符合评估要求。',
      INVALID_TIME_AXIS: '记录的时间顺序异常。',
      INCOMPLETE_WINDOW: '记录未覆盖完整评估时段。',
      NO_SAMPLES: '所选时段内没有记录数据。',
      MINIMUM_SAMPLES: '采样数量未达到设定要求。',
      SAMPLING_GAP: '采样间隔超过允许范围。',
    };
    report.metrics.forEach((metric) => {
      const card = document.createElement('article');
      card.className = 'evaluation-result-card';
      const title = document.createElement('strong');
      title.textContent = metric.display_name_zh;
      const value = document.createElement('div');
      value.className = 'evaluation-result-value';
      value.textContent = metric.value === null ? '—'
        : `${Number(metric.value).toLocaleString('zh-CN', {maximumFractionDigits: 6})} ${metric.unit}`;
      const verdict = document.createElement('div');
      verdict.className = 'evaluation-result-verdict';
      verdict.textContent = verdicts[metric.verdict] || '未评估';
      const detail = document.createElement('p');
      detail.textContent = metric.reason_code ? reasons[metric.reason_code] || '当前数据无法用于此项评估。'
        : `采用 ${metric.sample_count} 个实际采样点计算。`;
      card.append(title, value, verdict, detail);
      host.appendChild(card);
    });
    $('evaluationReportPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  async function createConfigurationDraft() {
    const reportId = state.currentReportId;
    if (!reportId || state.busy) return;
    const button = $('createEvaluationDraftBtn');
    button.disabled = true;
    notice('正在创建新的配置草稿…');
    try {
      const payload = await request(`/evaluations/${encodeURIComponent(reportId)}/configuration-draft`, {
        method: 'POST', body: {},
      });
      notice(`已创建配置草稿“${payload.task.name}”。原仿真和评估报告均保持不变；请前往任务中心检查并调整参数，再执行校验。`, 'success');
      button.textContent = '已创建配置草稿';
      button.disabled = true;
      button.title = `任务：${payload.task.task_id}`;
    } catch (error) {
      notice(error.message, 'error');
      button.disabled = false;
    }
  }

  function renderHistory() {
    const host = $('evaluationHistoryList');
    host.replaceChildren();
    if (!state.reports.length) {
      host.appendChild(emptyState('暂无历史评估', '生成评估报告后，会保存在这里。'));
      return;
    }
    state.reports.forEach((row) => {
      const report = row.report;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'evaluation-history-item';
      button.dataset.openReport = report.report_id;
      const title = document.createElement('strong');
      title.textContent = report.plan_name_zh || report.plan_id;
      const meta = document.createElement('span');
      meta.textContent = `${report.status === 'COMPUTED' ? '评估完成' : report.status === 'PARTIAL' ? '部分完成' : '未完成'} · ${new Date(row.created_at).toLocaleString()}`;
      button.append(title, meta);
      host.appendChild(button);
    });
  }

  async function loadHistory() {
    if (!state.runId) return;
    const payload = await request(`/runs/${encodeURIComponent(state.runId)}/evaluations`);
    state.reports = payload.reports || [];
    renderHistory();
  }

  async function openReport(reportId) {
    try {
      const payload = await request(`/evaluations/${encodeURIComponent(reportId)}`);
      if (payload.report.source_run_id !== state.runId) throw new Error('这份评估报告不属于当前仿真记录。');
      renderReport(payload.report, payload.created_at);
    } catch (error) {
      notice(error.message, 'error');
    }
  }

  async function createReport() {
    if (!state.runId || !state.metrics.length || state.busy) return;
    const start = Number($('evaluationWindowStart').value);
    const end = Number($('evaluationWindowEnd').value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
      notice('请检查评估时段的起止时间。', 'error');
      return;
    }
    const metrics = [];
    for (const row of state.metrics) {
      const minimumSamples = Number(row.minimumSamples);
      if (!Number.isInteger(minimumSamples) || minimumSamples < 1 || minimumSamples > 250000) {
        notice('最少采样数必须是 1 至 250000 之间的整数。', 'error');
        return;
      }
      const limits = {};
      if (row.lower !== '') limits.lower = Number(row.lower);
      if (row.upper !== '') limits.upper = Number(row.upper);
      if (Object.values(limits).some((value) => !Number.isFinite(value))) {
        notice('请检查目标范围。', 'error');
        return;
      }
      if (limits.lower !== undefined && limits.upper !== undefined && limits.lower > limits.upper) {
        notice('目标下限不能大于目标上限。', 'error');
        return;
      }
      metrics.push({
        metric_id: row.metricId, source_id: row.source.source_id,
        formula: row.formula, minimum_samples: minimumSamples,
        limits: Object.keys(limits).length ? limits : null,
      });
    }
    const gapValue = $('evaluationMaxGap').value;
    const gap = gapValue === '' ? null : Number(gapValue);
    if (gap !== null && (!Number.isFinite(gap) || gap <= 0)) {
      notice('最大采样间隔必须大于零。', 'error');
      return;
    }
    const body = {
      schema_version: 'evaluation-plan.v1',
      plan_id: `evaluation-${state.runId.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 70)}`,
      version: '1',
      display_name_zh: $('evaluationPlanName').value.trim() || '仿真效能评估',
      window_s: [start, end], max_sample_gap_s: gap, metrics,
    };
    setBusy(true);
    notice('正在生成评估报告…');
    try {
      const payload = await request(`/runs/${encodeURIComponent(state.runId)}/evaluations`, {
        method: 'POST', body,
      });
      renderReport(payload.report, payload.created_at);
      notice(payload.created ? '评估报告已生成。' : '已读取相同方案的既有报告。', 'success');
      await loadHistory();
    } catch (error) {
      notice(error.message, 'error');
    } finally {
      setBusy(false);
      renderSources();
      renderCounts();
    }
  }

  function selectRun(runId) {
    state.runId = typeof runId === 'string' ? runId : null;
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('evaluationView')) return;
    window.SatEvaluationWorkspace = Object.freeze({enter, selectRun});
  });
})();
