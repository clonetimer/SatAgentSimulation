/* R7 continuous native console: independent UI over the approved interactive REST API.
   No mock telemetry, hidden autoplay, client-side recovery or TC replay. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {
    active: false, timer: null, refreshBusy: false,
    selectedId: null, snapshot: null, policy: null, metrics: null, endurance: null, recovery: null,
    lastTelemetrySeq: -1, lastEventSeq: -1,
    telemetry: [], events: [], acks: [], gap: null,
  };
  const terminal = new Set(['COMPLETED', 'ABORTED', 'FAILED', 'INTERRUPTED']);
  const valueText = (val, digits = 3) => {
    const n = Number(val);
    return Number.isFinite(n) ? n.toFixed(digits) : '—';
  };
  async function api(path, options = {}) {
    const token = sessionStorage.getItem('sat-sim-api-token') || '';
    const response = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
        ...(options.headers || {}),
      },
    });
    const payload = (response.headers.get('content-type') || '').includes('application/json')
      ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = payload?.detail || payload;
      throw new Error(typeof detail === 'string' ? detail : (
        detail?.reason_code || detail?.message || 'HTTP ' + response.status
      ));
    }
    return payload;
  }
  function setText(id, text) {
    const node = $(id);
    if (node) node.textContent = String(text);
  }
  function notice(text, error = false) {
    const target = $('continuousNotice');
    target.classList.remove('hidden');
    target.classList.toggle('error', error);
    target.textContent = text;
  }
  function element(type, text, className) {
    const node = document.createElement(type);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function renderSessionList(sessions) {
    const target = $('continuousSessionList');
    target.replaceChildren();
    setText('continuousSessionCount', sessions.length);
    if (!sessions.length) {
      target.append(element('p', '尚无持续会话，填写上方表单创建。', 'continuous-muted'));
      return;
    }
    sessions.forEach((session) => {
      const node = element('button', '', 'continuous-session' +
        (session.session_id === state.selectedId ? ' active' : ''));
      node.type = 'button';
      node.append(
        element('strong', session.session_id),
        element('span', session.state + ' · ' + valueText(session.sim_time_s, 1) + ' s'),
      );
      node.addEventListener('click', () => {
        state.selectedId = session.session_id;
        state.snapshot = null;
        state.metrics = null;
        state.recovery = null;
        state.endurance = null;
        state.telemetry = [];
        state.events = [];
        state.acks = [];
        state.lastTelemetrySeq = -1;
        state.lastEventSeq = -1;
        state.gap = null;
        renderChart();
        refresh().catch((e) => notice('会话读取失败：' + e.message, true));
      });
      target.append(node);
    });
  }
  function renderInterrupted(items) {
    const target = $('continuousInterruptedList');
    target.replaceChildren();
    if (!items.length) {
      target.append(element('p', '未发现待处理的中断工作区。', 'continuous-muted'));
      return;
    }
    items.forEach((item) => {
      const card = element('div', undefined, 'continuous-interrupted-item');
      card.append(element('strong', item.session_id), element('span',
        item.reason_code || item.state || '不可自动接管'));
      target.append(card);
    });
  }
  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined || !Number.isFinite(Number(bytes))) return '—';
    const val = Math.max(0, Number(bytes));
    if (val < 1024) return val.toFixed(0) + ' B';
    if (val < 1048576) return (val / 1024).toFixed(1) + ' KiB';
    if (val < 1073741824) return (val / 1048576).toFixed(1) + ' MiB';
    return (val / 1073741824).toFixed(2) + ' GiB';
  }
  function renderObservability() {
    const data = state.metrics;
    const live = data && data.session_id === state.selectedId;
    setText('continuousWallElapsed', live ? valueText(data.wall_elapsed_s, 1) + ' s' : '—');
    setText('continuousResidentBytes', live && data.process.available
      ? formatBytes(data.process.resident_bytes) : '—');
    setText('continuousCpuTime', live && data.process.available
      ? valueText(data.process.cpu_time_s, 2) + ' CPU s' : '—');
    setText('continuousTelemetryLag', live && data.telemetry.lag_wall_s !== null
      ? valueText(data.telemetry.lag_wall_s, 2) + ' s' : '—');
    setText('continuousDurableFrames', live && data.telemetry.durable_frame_count !== null
      ? data.telemetry.durable_frame_count : '—');
    setText('continuousDiskStatus', live
      ? (formatBytes(data.telemetry.durable_bytes) + ' / ' +
          formatBytes(data.disk?.free_bytes))
      : '—');
    setText('continuousMonitorHealth', live ? data.telemetry.status : '尚无采样');
    setText('continuousMetricScope', live
      ? ((data.process.scope === 'BASILISK_CHILD'
          ? '采样对象：受管 Basilisk 子进程'
          : '采样对象：API 与 Basilisk 同一进程') +
          '；CPU 为累计核心秒（非使用率）；运行时长从会话对象创建算起，含准备/暂停；' +
          '不可用指标不会使用估算值替代。')
      : '数值来自操作系统与遥测账本；暂无采样。');
  }
  function renderEndurance() {
    const monitor = state.endurance;
    const active = monitor?.recording_status === 'RECORDING';
    const available = Boolean(state.selectedId);
    setText('continuousEnduranceStatus', monitor?.recording_status || '尚未采集');
    setText('continuousEnduranceSamples', monitor?.sample_count ?? 0);
    setText('continuousEnduranceAlerts', Object.values(monitor?.alerts || {})
      .reduce((a, b) => a + Number(b || 0), 0));
    setText('continuousEnduranceEligible', monitor?.summary?.eligible_for_manual_24h_review
      ? '待人工审核' : '未达到');
    setText('continuousEnduranceReason',
      monitor?.reason_code || (active
        ? '真实墙钟定时采样进行中；告警不会自动控制 Basilisk。'
        : monitor?.summary?.observed_wall_span_s !== undefined
          ? '已记录墙钟区间 ' + valueText(monitor.summary.observed_wall_span_s, 1) + ' s；24 小时资格不会自动判为通过。'
          : '证据与原生仿真相互独立，不会停止、恢复或重放遥控。'));
    $('continuousEnduranceStart').disabled = !available || active ||
      (monitor && monitor.recording_status !== 'NOT_STARTED');
    $('continuousEnduranceStop').disabled = !active;
    $('continuousEnduranceEvidence').disabled = !monitor?.evidence_available;
    $('continuousEnduranceSummary').disabled = !monitor?.summary;
  }
  async function changeEndurance(action) {
    if (!state.selectedId) return;
    const id = encodeURIComponent(state.selectedId);
    if (action === 'stop' && !confirm('停止监测并写出长稳验收摘要？这不会停止 Basilisk 持续仿真。')) return;
    const body = action === 'start'
      ? { sample_interval_s: Number($('continuousEnduranceInterval').value) } : {};
    try {
      state.endurance = await api('/interactive/continuous/sessions/' + id +
        '/endurance/' + action, { method: 'POST', body: JSON.stringify(body) });
      renderEndurance();
      notice(action === 'start' ? '长稳证据采集已启动。' :
        '采样已停止，正式摘要已生成；持续仿真不受影响。');
    } catch (e) { notice('长稳采集操作失败：' + e.message, true); }
  }
  async function auditEndurance() {
    if (!state.selectedId) return;
    try {
      const id = encodeURIComponent(state.selectedId);
      const report = await api('/interactive/continuous/sessions/' + id + '/endurance/audit');
      const label = ({
        INCOMPLETE: '采集尚未完成',
        BLOCKED: '证据不可信，禁止验收',
        NOT_QUALIFIED: '证据完整，但尚未满足 24h 验收条件',
        REVIEW_ELIGIBLE: '具备人工复核资格（尚未正式通过）',
      })[report.verdict] || '未知结果';
      const original = report.original_source_audit;
      const sourceInfo = report.original_sources_verified
        ? '；原始状态事件 ' + (original?.state?.events ?? '—') +
            ' 条；SQLite 遥控 ' + (original?.tc_ledger?.accepted_commands ?? '—') +
            ' 条；ACK/Outbox ' + (original?.tc_ledger?.outbox_events ?? '—') +
            ' 条；原始核验 ' + (original?.status || '不可确认')
        : '；原始事件/遥控账本：未完成一致性核验';
      setText('continuousEnduranceAuditResult',
        '独立复核：' + label + '；样本 ' + (report.samples ?? '—') +
        '；真实运行墙钟 ' + valueText(report.observed_running_wall_s, 1) + ' s' +
        '；原生 RSS 增长率 ' + valueText(report.rss_growth_mib_per_hour, 2) +
        ' MiB/h' + sourceInfo +
        '；原因：' + (report.reason_codes || []).join('、'));
    } catch (e) {
      setText('continuousEnduranceAuditResult', '证据核验失败：' + e.message);
    }
  }
  async function exportEndurance(format) {
    if (!state.selectedId) return;
    const id = encodeURIComponent(state.selectedId);
    const token = sessionStorage.getItem('sat-sim-api-token') || '';
    const path = '/interactive/continuous/sessions/' + id +
      '/endurance/' + format;
    try {
      const response = await fetch(path, {
        headers: token ? { Authorization: 'Bearer ' + token } : {},
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const file = await response.blob();
      const url = URL.createObjectURL(file);
      const link = document.createElement('a');
      link.href = url;
      link.download = state.selectedId + (format === 'summary'
        ? '-endurance-summary.json' : '-endurance-evidence.jsonl');
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) { notice('证据导出失败：' + e.message, true); }
  }
  function renderRecovery() {
    const report = state.recovery;
    const sameSession = report?.session_id === state.selectedId;
    const data = sameSession ? report : null;
    const rows = $('continuousRecoveryOwners');
    rows.replaceChildren();
    setText('continuousRecoveryVerdict', data?.restart_readiness || '未检查');
    setText('continuousRecoveryOwnerTotal', data?.mandatory_owner_count ?? '—');
    setText('continuousRecoveryOwnerRestorable', data?.restorable_owner_count ?? '—');
    setText('continuousRecoveryAtSim', data ? valueText(data.sim_time_s, 2) + ' s' : '—');
    setText('continuousRecoveryMissing', data
      ? '恢复禁止原因：' + data.reason_code + '；尚缺 ' +
        (data.blocking_requirements || []).join('、') +
        '。现有部分原生状态仅用于观测，不是完整恢复检查点。'
      : '先暂停已经推进过的原生会话，再检查各状态所有者。自动恢复保持关闭。');
    (data?.owners || []).forEach((item) => {
      const line = element('div', undefined, 'continuous-log-item');
      line.append(element('strong', item.owner), element('small',
        item.status + ' · 原生导出：' + item.native_state_export +
        ' · 导入：' + item.native_state_import +
        ' · 对照：' + item.cross_process_restart_parity));
      rows.append(line);
    });
    $('continuousRecoveryAuditBtn').disabled =
      !state.selectedId || state.snapshot?.session?.state !== 'PAUSED';
    $('continuousRecoveryExportBtn').disabled = !data;
  }
  async function auditNativeRecovery() {
    if (!state.selectedId) return;
    try {
      const id = encodeURIComponent(state.selectedId);
      const report = await api('/interactive/continuous/sessions/' + id + '/recovery/readiness');
      state.recovery = { ...report, session_id: state.selectedId };
      renderRecovery();
      notice('已读取真实 Basilisk 暂停边界状态：自动恢复仍未获得准入。');
    } catch (e) {
      notice('原生恢复覆盖检查失败：' + e.message, true);
    }
  }
  function exportNativeRecovery() {
    if (!state.recovery || state.recovery.session_id !== state.selectedId) return;
    const file = new Blob([JSON.stringify(state.recovery, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = state.selectedId + '-native-recovery-readiness.json';
    link.click();
    URL.revokeObjectURL(url);
  }
  function renderStatus() {
    const info = state.snapshot;
    const session = info?.session;
    const live = Boolean(session) && !terminal.has(session.state);
    const mode = info?.runtime_mode || state.policy?.mode || '未连接';
    setText('continuousRuntimeMode', mode === 'SUPERVISED_NATIVE_CHILD'
      ? '原生独立进程' : '原生进程内');
    setText('continuousState', session?.state || '未选择');
    setText('continuousSimTime', valueText(session?.sim_time_s, 2) + ' s');
    setText('continuousLiveRate', valueText(session?.rate || 1, 1) + '×');
    setText('continuousArchive', session?.archive_status || '—');
    setText('continuousChildPid', info?.native_child_pid ?? '进程内运行 / 未启动');
    setText('continuousWriterEpoch', info?.writer_epoch ?? '—');
    const fence = info?.parent_death_fence;
    setText('continuousParentFence', fence?.supported
      ? 'Linux 内核父进程死亡栅栏：已安装'
      : (mode === 'SUPERVISED_NATIVE_CHILD' ? '未证明有操作系统父进程死亡保护' : '进程内模式：不适用子进程死亡保护'));
    setText('continuousErrorDetail', session?.terminal_error || session?.archive_error || '');
    document.querySelectorAll('[data-continuous-action]').forEach((button) => {
      const action = button.dataset.continuousAction;
      button.disabled = !live || (
        action === 'start' ? session.state !== 'READY'
          : action === 'pause' ? session.state !== 'RUNNING'
          : action === 'resume' ? session.state !== 'PAUSED'
          : action === 'step' ? session.state !== 'PAUSED'
          : !['READY', 'RUNNING', 'PAUSED'].includes(session.state)
      );
    });
    $('continuousCommandForm').querySelector('button[type=submit]').disabled =
      !live || !['READY', 'RUNNING', 'PAUSED'].includes(session.state);
  }
  function renderLog(id, rows, formatter) {
    const node = $(id);
    node.replaceChildren();
    const tail = rows.slice(-20).reverse();
    if (!tail.length) {
      node.append(element('p', '当前尚无记录。', 'continuous-muted'));
      return;
    }
    tail.forEach((row) => {
      const line = element('div', undefined, 'continuous-log-item');
      line.append(element('strong', formatter(row)), element('small',
        [row.reason_code, row.sim_time_s === null || row.sim_time_s === undefined
          ? '' : valueText(row.sim_time_s, 2) + ' s'].filter(Boolean).join(' · ')));
      node.append(line);
    });
  }
  function renderChart() {
    const canvas = $('continuousChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(320, Math.round(canvas.clientWidth || 800));
    const height = Math.max(200, Math.round(canvas.clientHeight || 265));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    const key = $('continuousSignal').value;
    const points = state.telemetry.flatMap((frame) => {
      const t = Number(frame.sim_time_s);
      const v = Number(frame.values?.[key]);
      return Number.isFinite(t) && Number.isFinite(v) ? [{ t, v }] : [];
    });
    const padding = { left: 57, right: 20, top: 20, bottom: 30 };
    const x0 = padding.left, x1 = width - padding.right;
    const y0 = height - padding.bottom, y1 = padding.top;
    ctx.strokeStyle = 'rgba(130,145,160,.3)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = y1 + (y0 - y1) * i / 4;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    }
    ctx.fillStyle = '#7890a6';
    ctx.font = '12px system-ui';
    if (!points.length) {
      ctx.fillText('尚无原生遥测数据，请启动会话。', x0 + 20, height / 2);
      return;
    }
    let minT = points[0].t, maxT = points[points.length - 1].t;
    let minV = Math.min(...points.map((p) => p.v));
    let maxV = Math.max(...points.map((p) => p.v));
    if (maxT <= minT) maxT = minT + 1;
    if (maxV <= minV) { minV -= 1; maxV += 1; }
    const margin = (maxV - minV) * 0.08;
    minV -= margin; maxV += margin;
    ctx.fillText(valueText(maxV, 2), 2, y1 + 8);
    ctx.fillText(valueText(minV, 2), 2, y0);
    ctx.fillText(valueText(minT, 1) + 's', x0, height - 7);
    ctx.fillText(valueText(maxT, 1) + 's', x1 - 38, height - 7);
    ctx.strokeStyle = '#24a7c8';
    ctx.lineWidth = 2.1;
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = x0 + (x1 - x0) * (p.t - minT) / (maxT - minT);
      const y = y0 - (y0 - y1) * (p.v - minV) / (maxV - minV);
      if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  function latest() {
    const frame = state.telemetry[state.telemetry.length - 1];
    const vals = frame?.values || {};
    setText('continuousOrbit', vals['orbit.radius_m'] === undefined ? '—' :
      valueText(vals['orbit.radius_m'], 1) + ' m');
    setText('continuousPointing', vals['adcs.pointing_error_deg'] === undefined ? '—' :
      valueText(vals['adcs.pointing_error_deg'], 3) + '°');
    setText('continuousSoc', vals['eps.battery_soc'] === undefined ? '—' :
      valueText(Number(vals['eps.battery_soc']) * 100, 1) + '%');
    setText('continuousGapBadge', state.gap ? '缓冲缺口 · ' +
      state.gap.dropped_count : (frame ? '遥测已同步' : '暂无遥测'));
    renderChart();
  }
  async function refresh() {
    if (!state.active || state.refreshBusy) return;
    state.refreshBusy = true;
    try {
      const overview = await api('/interactive/continuous/overview');
      state.policy = overview;
      const sessions = overview.sessions || [];
      if (!state.selectedId && sessions.length) state.selectedId = sessions[0].session_id;
      if (state.selectedId && !sessions.some((s) => s.session_id === state.selectedId)) {
        state.selectedId = null;
        state.snapshot = null;
      }
      setText('continuousConnectionBadge', '服务已连接 · ' +
        (overview.mode === 'SUPERVISED_NATIVE_CHILD' ? '独立原生进程' : '进程内原生'));
      renderSessionList(sessions);
      renderInterrupted(overview.interrupted_workspaces || []);
      if (!state.selectedId) { state.metrics = null; state.endurance = null; state.recovery = null; renderStatus(); renderObservability(); renderEndurance(); renderRecovery(); return; }
      const id = encodeURIComponent(state.selectedId);
      const [info, tm, events, acks, metrics, endurance] = await Promise.all([
        api('/interactive/continuous/sessions/' + id + '/status'),
        api('/interactive/sessions/' + id + '/telemetry/whole.continuous?after_sequence=' + state.lastTelemetrySeq),
        api('/interactive/sessions/' + id + '/events?after_sequence=' + state.lastEventSeq),
        api('/interactive/sessions/' + id + '/commands'),
        api('/interactive/continuous/sessions/' + id + '/observability'),
        api('/interactive/continuous/sessions/' + id + '/endurance/status'),
      ]);
      if (info.session.session_id !== state.selectedId) return;
      state.snapshot = info;
      state.metrics = metrics;
      renderRecovery();
      state.endurance = endurance;
      state.gap = tm.gap || null;
      (tm.frames || []).forEach((frame) => {
        if (frame.sequence > state.lastTelemetrySeq) {
          state.telemetry.push(frame);
          state.lastTelemetrySeq = frame.sequence;
        }
      });
      if (state.telemetry.length > 280) state.telemetry.splice(0, state.telemetry.length - 280);
      (events.events || []).forEach((ev) => {
        if (ev.sequence > state.lastEventSeq) {
          state.events.push(ev);
          state.lastEventSeq = ev.sequence;
        }
      });
      if (state.events.length > 60) state.events.splice(0, state.events.length - 60);
      state.acks = (acks.acks || []).slice(-60);
      renderStatus();
      renderObservability();
      renderEndurance();
      latest();
      renderLog('continuousEventList', state.events, (e) => e.state + ' · ' + (e.reason_code || ''));
      renderLog('continuousAckList', state.acks, (a) => a.command_id + ' · ' + a.state);
    } catch (error) {
      setText('continuousConnectionBadge', '连接异常');
      notice('持续会话状态读取失败：' + error.message, true);
    } finally {
      state.refreshBusy = false;
    }
  }
  function updateRwNativeInitialLayout() {
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    $('continuousRw4InitialLabel')?.classList.toggle('hidden', !four);
    const rw4 = document.querySelector('[data-native-key="rw_initial_speed_rad_s_3"]');
    if (rw4) {
      rw4.disabled = !four;
      rw4.required = four;
    }
  }
  async function createSession(event) {
    event.preventDefault();
    const form = $('continuousCreateForm');
    if (!form.reportValidity()) return;
    const initial_configuration = {};
    document.querySelectorAll('#continuousCreateForm [data-native-group][data-native-key]').forEach((field) => {
      if (field.disabled) return;
      const group = field.dataset.nativeGroup;
      initial_configuration[group] ||= {};
      const value = field.tagName === 'SELECT' ? field.value : Number(field.value);
      initial_configuration[group][field.dataset.nativeKey] = value;
    });
    const body = {
      session_id: $('continuousSessionId').value.trim(),
      initial_configuration,
      quantum_s: Number($('continuousQuantum').value),
      rate: Number($('continuousRate').value),
      paced: $('continuousPaced').checked,
      initial_horizon_s: Number($('continuousHorizon').value),
    };
    try {
      const result = await api('/interactive/continuous/sessions', {
        method: 'POST', body: JSON.stringify(body),
      });
      state.selectedId = result.session_id;
      state.metrics = null;
      state.endurance = null;
      state.recovery = null;
      state.telemetry = []; state.events = []; state.acks = [];
      state.lastTelemetrySeq = -1; state.lastEventSeq = -1;
      setText('continuousInitialConfigStatus',
        '已将运行前参数写入本次原生会话；配置在模型创建后不可修改。');
      notice('已使用所选整星／分系统／单机参数创建 Basilisk 原生持续会话。');
      await refresh();
    } catch (e) { notice('创建失败：' + e.message, true); }
  }
  async function control(action) {
    if (!state.selectedId) return;
    if (action === 'stop' && !confirm('确认停止持续仿真并归档？停止后不能继续当前物理状态。')) return;
    try {
      await api('/interactive/sessions/' + encodeURIComponent(state.selectedId) + '/control', {
        method: 'POST', body: JSON.stringify({ action }),
      });
      notice({ start: '仿真已启动', pause: '仿真已暂停', resume: '已继续仿真',
        step: '已推进一个量子', stop: '停止请求已处理' }[action] || '操作完成');
      await refresh();
    } catch (e) { notice('会话控制失败：' + e.message, true); }
  }
  function changeOperation() {
    const op = $('continuousOperation').value;
    const visibility = {
      continuousTargetFields: op === 'adcs.target.set',
      continuousModeFields: op === 'session.mode.set',
      continuousControlFields: op === 'adcs.control.set',
      continuousFaultFields: op === 'adcs.fault.inject',
    };
    Object.entries(visibility).forEach(([id, show]) => $(id).classList.toggle('hidden', !show));
  }
  async function sendCommand(event) {
    event.preventDefault();
    if (!state.snapshot?.session || !state.selectedId) {
      notice('请先选择可操作的持续会话。', true); return;
    }
    const op = $('continuousOperation').value;
    let parameters, target, actor_role = 'operator';
    if (op === 'adcs.target.set') {
      parameters = { sigma_rn: ['continuousSigma1', 'continuousSigma2', 'continuousSigma3']
        .map((id) => Number($(id).value)) };
      target = 'subsystem.adcs';
    } else if (op === 'session.mode.set') {
      parameters = { mode: $('continuousMode').value };
      target = 'whole_spacecraft';
    } else if (op === 'adcs.control.set') {
      parameters = { enabled: $('continuousControlEnabled').value === 'true' };
      target = 'subsystem.adcs';
    } else {
      parameters = { fault_id: $('continuousFault').value, severity: Number($('continuousSeverity').value) };
      target = 'subsystem.adcs'; actor_role = 'fault_operator';
    }
    const when = Number($('continuousExecuteAt').value);
    if (!Number.isFinite(when) || when < 0) {
      notice('执行时刻必须是非负仿真秒数。', true); return;
    }
    if (op === 'adcs.target.set' && parameters.sigma_rn.some((n) => !Number.isFinite(n) || Math.abs(n) > 1)) {
      notice('MRP 三个分量的范围均为 -1 到 1。', true); return;
    }
    const command = {
      command_id: 'tc-' + crypto.randomUUID(),
      session_id: state.selectedId,
      session_revision: state.snapshot.session.revision,
      operation: op, target, parameters, actor_id: 'browser-operator',
      actor_role, execute_at_sim_time_s: when,
    };
    if (!confirm('确认发送遥控 ' + op + '，预定在仿真 ' + when.toFixed(2) + ' 秒执行？')) return;
    try {
      const ack = await api('/interactive/sessions/' + encodeURIComponent(state.selectedId) + '/commands', {
        method: 'POST', body: JSON.stringify(command),
      });
      notice('遥控 ' + ack.command_id + '：' + ack.state +
        (ack.reason_code ? ' / ' + ack.reason_code : ''), ack.state === 'REJECTED');
      await refresh();
    } catch (e) { notice('遥控发送失败：' + e.message, true); }
  }
  function enter() {
    state.active = true;
    clearInterval(state.timer);
    refresh();
    state.timer = setInterval(refresh, 1400);
  }
  function exit() {
    state.active = false;
    clearInterval(state.timer);
    state.timer = null;
  }
  function initialize() {
    $('continuousCreateForm')?.addEventListener('submit', createSession);
    $('continuousCommandForm')?.addEventListener('submit', sendCommand);
    $('continuousRefreshBtn')?.addEventListener('click', () => refresh());
    $('continuousOperation')?.addEventListener('change', changeOperation);
    $('continuousSignal')?.addEventListener('change', renderChart);
    $('continuousEnduranceStart')?.addEventListener('click', () => changeEndurance('start'));
    $('continuousEnduranceStop')?.addEventListener('click', () => changeEndurance('stop'));
    $('continuousEnduranceEvidence')?.addEventListener('click', () => exportEndurance('evidence'));
    $('continuousEnduranceSummary')?.addEventListener('click', () => exportEndurance('summary'));
    $('continuousEnduranceAuditBtn')?.addEventListener('click', auditEndurance);
    $('continuousRecoveryAuditBtn')?.addEventListener('click', auditNativeRecovery);
    document.querySelector('[data-native-key="rw_configuration"]')
      ?.addEventListener('change', updateRwNativeInitialLayout);
    updateRwNativeInitialLayout();
    $('continuousRecoveryExportBtn')?.addEventListener('click', exportNativeRecovery);
    document.querySelectorAll('[data-continuous-action]').forEach((button) => {
      button.addEventListener('click', () => control(button.dataset.continuousAction));
    });
    window.addEventListener('resize', () => {
      if (state.active) renderChart();
    });
    changeOperation();
    renderStatus();
    renderObservability();
    renderEndurance();
    renderRecovery();
    renderChart();
  }
  window.SatContinuousConsole = { enter, exit };
  document.addEventListener('DOMContentLoaded', initialize);
})();
