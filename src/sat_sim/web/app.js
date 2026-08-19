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
    clearNotice();
    showBusy('读取能力 Schema', capabilityId);
    try {
      const payload = await api(`/forms/capabilities/${encodeURIComponent(capabilityId)}`);
      const object = objectOverride || presentationObjectForCapability(capabilityId);
      state.selectedObjectId = object?.object_id || state.selectedObjectId || capabilityId;
      state.selectedCapabilityId = capabilityId;
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
        const passed = execution.state === 'SUCCEEDED' && execution.validation_result === 'PASS';
        const displayName = state.activeRunDisplayName || state.activeRun?.task_spec?.task?.name || runId;
        const message = execution.error
          ? `仿真“${displayName}”失败（Run ID：${runId}）：${execution.error}`
          : `仿真“${displayName}”已结束：${execution.state}${execution.validation_result ? ` / ${execution.validation_result}` : ''}（Run ID：${runId}）`;
        showNotice(message, passed ? 'success' : (execution.state === 'CANCELLED' ? 'warning' : 'error'));
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
    try {
      if (document.querySelector('.tab.active')?.dataset.tab === 'agent' && !state.taskSpec) await parseNaturalLanguage();
      else if (document.querySelector('.tab.active')?.dataset.tab === 'taskspec') {
        state.taskSpec = taskSpecFromEditor();
        const valid = await validateEditor();
        if (!valid) return;
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
      el('exportDatasetBtn').classList.add('hidden');
      state.executionState = payload.execution_state || { run_id: runId, state: 'QUEUED' };
      renderExecutionProgress(state.executionState);
      showNotice(`仿真“${displayName}”已提交执行（Run ID：${runId}）。可以继续编辑其他任务，无需进入运行中心。`, 'success');
      await loadRuns();
      hideBusy();
      await pollRunExecution(runId);
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
      showBusy('校验并编译 TaskSpec', '解析 Schema 表单与能力约束');
      try { await previewForm(); } catch (error) { showNotice(error.message, 'error'); } finally { hideBusy(); }
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
    } catch (error) {
      showNotice(`工作台初始化失败：${error.message}`, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
