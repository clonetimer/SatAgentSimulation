/* R7 continuous native console: independent UI over the approved interactive REST API.
   No mock telemetry, hidden autoplay, client-side recovery or TC replay. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {
    active: false, timer: null, refreshBusy: false,
    selectedId: null, snapshot: null, policy: null, metrics: null, endurance: null, recovery: null,
    focusSelectedStatus: false,
    lastTelemetrySeq: -1, lastEventSeq: -1,
    telemetry: [], events: [], acks: [], gap: null,
    rwConfiguration: null,
    commandContracts: [], catalogLoading: false,
    history: null,
  };
  const terminal = new Set(['COMPLETED', 'ABORTED', 'FAILED', 'INTERRUPTED']);
  const sessionStateLabels = {
    READY: '准备就绪', PREPARING: '准备中', RUNNING: '运行中', PAUSED: '已暂停',
    STOPPING: '停止中', STOPPED: '已停止', COMPLETED: '已完成',
    ABORTED: '已中止', FAILED: '失败', INTERRUPTED: '已中断',
  };
  const archiveStateLabels = {
    NOT_CONFIGURED: '未配置归档', NOT_READY: '尚未归档', SEALING: '封存中',
    SEALED: '已封存', FAILED: '归档失败', NOT_APPLICABLE: '不适用',
  };
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
  function setStatusText(id, raw, labels, empty) {
    const node = $(id);
    if (!node) return;
    node.dataset.state = raw || '';
    node.title = raw || '';
    node.textContent = raw ? (labels[raw] || `未知状态（${raw}）`) : empty;
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
        state.focusSelectedStatus = true;
        state.snapshot = null;
        state.metrics = null;
        state.recovery = null;
        state.endurance = null;
        state.telemetry = [];
        state.history = null;
        setText("continuousHistoryStatus", "已切换会话；需要重新查询历史。");
        state.events = [];
        state.acks = [];
        state.lastTelemetrySeq = -1;
        state.lastEventSeq = -1;
        state.gap = null;
        state.rwConfiguration = null;
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
  function syncSessionColumnOrder() {
    const grid = document.querySelector('#continuousView .continuous-grid');
    if (!grid) return;
    const primary = grid.querySelector('.continuous-main');
    const sidebar = grid.querySelector('.continuous-sidebar');
    const first = window.matchMedia('(max-width: 850px)').matches && state.selectedId ? primary : sidebar;
    // Reorder DOM as well as presentation, so keyboard and reading order agree.
    if (first && grid.firstElementChild !== first) grid.insertBefore(first, grid.firstElementChild);
    const session = state.snapshot?.session;
    const mobileControlsActive = window.matchMedia('(max-width: 760px)').matches && Boolean(session) && !terminal.has(session.state);
    const mobileSlot = $('continuousMobileControls');
    const toolbar = $('continuousSessionToolbar');
    const slot = mobileControlsActive ? mobileSlot : $('continuousSessionActionSlot');
    const focused = toolbar.contains(document.activeElement) ? document.activeElement : null;
    mobileSlot.classList.toggle('hidden', !mobileControlsActive);
    if (toolbar.parentElement !== slot) {
      slot.appendChild(toolbar);
      if (focused) focused.focus({preventScroll:true});
    }
    $('continuousView').dataset.mobileControlsActive = String(mobileControlsActive);
  }
  function renderStatus() {
    const info = state.snapshot;
    const session = info?.session;
    syncSessionColumnOrder();
    if (state.focusSelectedStatus && session?.session_id === state.selectedId) {
      state.focusSelectedStatus = false;
      if (state.active && window.matchMedia('(max-width: 850px)').matches) {
        requestAnimationFrame(() => {
          const heading = $('continuousStatusHeading');
          heading.focus({preventScroll:true});
          heading.scrollIntoView({block:'start'});
        });
      }
    }
    const live = Boolean(session) && !terminal.has(session.state);
    const mode = info?.runtime_mode || state.policy?.mode || '未连接';
    setText('continuousRuntimeMode', mode === 'SUPERVISED_NATIVE_CHILD'
      ? '原生独立进程' : '原生进程内');
    setStatusText('continuousState', session?.state, sessionStateLabels, '未选择');
    setText('continuousSimTime', valueText(session?.sim_time_s, 2) + ' s');
    setText('continuousLiveRate', valueText(session?.rate || 1, 1) + '×');
    setStatusText('continuousArchive', session?.archive_status, archiveStateLabels, '—');
    setText('continuousChildPid', info?.native_child_pid ?? '进程内运行 / 未启动');
    setText('continuousWriterEpoch', info?.writer_epoch ?? '—');
    const fence = info?.parent_death_fence;
    const fenceLabel = fence?.kind === 'WINDOWS_JOB_KILL_ON_CLOSE'
      ? 'Windows 作业对象父进程退出保护：已安装'
      : fence?.kind === 'LINUX_PR_SET_PDEATHSIG_SIGKILL'
        ? 'Linux 内核父进程死亡栅栏：已安装'
        : '操作系统父进程退出保护：已安装';
    setText('continuousParentFence', fence?.supported
      ? fenceLabel
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
    // Do not advertise RW4 from a UI form alone. Only expose it after an
    // actual stream frame contains the native fourth-wheel speed channel.
    const rw4Option = $('continuousRw4SignalOption');
    if (rw4Option) {
      const hasRw4Telemetry = state.rwConfiguration === 'pyramid_4' &&
        state.telemetry.some((frame) => {
        const value = frame.values?.['adcs.rw.speed_rad_s_3'];
        // Missing/null TM is not a physical zero-speed measurement.
        return typeof value === 'number' && Number.isFinite(value);
      });
      rw4Option.disabled = !hasRw4Telemetry;
      rw4Option.hidden = !hasRw4Telemetry;
      if (!hasRw4Telemetry && $('continuousSignal').value === rw4Option.value) {
        $('continuousSignal').value = 'orbit.radius_m';
      }
    }
    const key = $('continuousSignal').value;
    // A missing/null/non-number TM sample is not a physical zero.
    // Preserve gaps in the drawn line rather than connecting across them.
    const samples = state.telemetry.map((frame) => {
      const t = frame.sim_time_s;
      const raw = frame.values?.[key];
      return typeof t === 'number' && Number.isFinite(t)
        && typeof raw === 'number' && Number.isFinite(raw)
        ? { t, v: raw } : null;
    });
    const points = samples.filter(Boolean);
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
    let connected = false;
    samples.forEach((p) => {
      if (!p) { connected = false; return; }
      const x = x0 + (x1 - x0) * (p.t - minT) / (maxT - minT);
      const y = y0 - (y0 - y1) * (p.v - minV) / (maxV - minV);
      if (!connected) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      connected = true;
    });
    ctx.stroke();
  }
  function renderVerifiedHistory() {
    const canvas = $('continuousHistoryChart');
    const legend = $('continuousHistoryLegend');
    if (!canvas || !legend) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(360, canvas.clientWidth || 800);
    const height = Math.max(180, canvas.clientHeight || 260);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    legend.replaceChildren();
    const record = state.history;
    const x0 = 52, x1 = width - 25, y0 = height - 28, y1 = 20;
    ctx.strokeStyle = 'rgba(130,145,160,.35)';
    ctx.fillStyle = '#7890a6';
    ctx.font = '11px system-ui';
    for (let i = 0; i <= 4; i += 1) {
      const y = y1 + (y0 - y1) * i / 4;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    }
    ctx.fillText('100%', 3, y1 + 7);
    ctx.fillText('0%', 17, y0);
    if (!record || !record.frames?.length) {
      ctx.fillText('尚无经过校验的历史时间窗采样。', x0 + 15, height / 2);
      return;
    }
    const selected = record.selectedFields || [];
    const colors = ['#24a7c8', '#bf7de1', '#e2a14b'];
    selected.forEach((field, index) => {
      const values = record.frames.map(frame => frame.values?.[field])
        .filter(v => typeof v === 'number' && Number.isFinite(v));
      if (!values.length) return;
      const min = Math.min(...values), max = Math.max(...values);
      const line = document.createElement('p');
      line.textContent = field + '：真实 min=' + valueText(min, 6)
        + '；max=' + valueText(max, 6)
        + (min === max ? '（常量）' : '');
      line.style.color = colors[index];
      legend.append(line);
      ctx.strokeStyle = colors[index]; ctx.lineWidth = 2;
      ctx.beginPath();
      let connected = false, previousSeq = null;
      for (const frame of record.frames) {
        const value = frame.values?.[field];
        const valid = typeof value === 'number' && Number.isFinite(value)
          && !['GAP', 'INVALID'].includes(frame.quality);
        if (!valid) {
          connected = false;
          previousSeq = frame.sequence;
          continue;
        }
        const x = x0 + (x1 - x0) * (frame.sim_time_s - record.start_s)
          / (record.end_s - record.start_s);
        const normalized = min === max ? .5 : (value - min) / (max - min);
        const y = y0 - normalized * (y0 - y1);
        if (!connected || (previousSeq !== null && frame.sequence !== previousSeq + 1)) {
          ctx.moveTo(x, y);
        } else ctx.lineTo(x, y);
        connected = true;
        previousSeq = frame.sequence;
      }
      ctx.stroke();
    });
    ctx.fillStyle = '#7890a6';
    ctx.fillText(valueText(record.start_s, 2) + 's', x0, height - 6);
    ctx.fillText(valueText(record.end_s, 2) + 's', Math.max(x0, x1 - 56), height - 6);
  }

  async function queryVerifiedHistory() {
    const sessionId = state.selectedId;
    state.history = null;
    renderVerifiedHistory();
    if (!sessionId) {
      setText('continuousHistoryStatus', '请先选择持续仿真会话。'); return;
    }
    const stateName = state.snapshot?.session?.state;
    if (!['READY', 'PAUSED', 'COMPLETED', 'FAILED', 'ABORTED'].includes(stateName)) {
      setText('continuousHistoryStatus', '请先暂停或结束会话，再从稳定的持久化日志中核验精确历史。'); return;
    }
    const start_s = Number($('continuousHistoryStart').value);
    const end_s = Number($('continuousHistoryEnd').value);
    const fields = Array.from($('continuousHistoryChannels')
      .querySelectorAll('input:checked')).map(node => node.value);
    if (!Number.isFinite(start_s) || !Number.isFinite(end_s) || start_s < 0 || end_s <= start_s) {
      setText('continuousHistoryStatus', '时间区间无效，结束时间必须大于起始时间。'); return;
    }
    if (!fields.length || fields.length > 3) {
      setText('continuousHistoryStatus', '请勾选 1～3 个要对比的参量。'); return;
    }
    const query = new URLSearchParams({
      start_s: String(start_s), end_s: String(end_s),
      fields: fields.join(','), max_rows: '3000',
    });
    setText('continuousHistoryStatus', '正在读取并核验留存遥测的哈希链…');
    try {
      const response = await api('/interactive/sessions/'
        + encodeURIComponent(sessionId) + '/telemetry-history/whole.continuous?'
        + query.toString());
      if (sessionId !== state.selectedId) return;
      state.history = { ...response, selectedFields: fields };
      setText('continuousHistoryStatus', response.frame_count
        + ' 条真实留存采样；时间窗 ' + start_s + '～' + end_s
        + ' s；哈希链已验证；无抽样/插值。'
        + (response.gaps?.length ? ' 检出序号缺口 ' + response.gaps.length + ' 处。' : ''));
      renderVerifiedHistory();
    } catch (error) {
      if (sessionId !== state.selectedId) return;
      const help = error.message === 'HISTORY_TOO_MANY_SAMPLES_NARROW_WINDOW'
        ? '此窗口内超过 3000 条记录，请缩小起止时间。'
        : error.message === 'HISTORY_REQUIRES_PAUSED_OR_TERMINAL_SESSION'
        ? '正在运行或状态发生变化，请先暂停后重试。'
        : '原始日志查询失败：' + error.message;
      setText('continuousHistoryStatus', help);
    }
  }

  function latest() {
    const frame = state.telemetry[state.telemetry.length - 1];
    const vals = frame?.values || {};
    const valid = (key) => typeof vals[key] === 'number' && Number.isFinite(vals[key]);
    setText('continuousOrbit', valid('orbit.radius_m') ?
      valueText(vals['orbit.radius_m'], 1) + ' m' : '—');
    setText('continuousPointing', valid('adcs.pointing_error_deg') ?
      valueText(vals['adcs.pointing_error_deg'], 3) + '°' : '—');
    setText('continuousSoc', valid('eps.battery_soc') ?
      valueText(vals['eps.battery_soc'] * 100, 1) + '%' : '—');
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
        state.rwConfiguration = null;
      }
      setText('continuousConnectionBadge', '服务已连接 · ' +
        (overview.mode === 'SUPERVISED_NATIVE_CHILD' ? '独立原生进程' : '进程内原生'));
      renderSessionList(sessions);
      renderInterrupted(overview.interrupted_workspaces || []);
      if (!state.selectedId) { state.metrics = null; state.endurance = null; state.recovery = null; renderStatus(); renderObservability(); renderEndurance(); renderRecovery(); return; }
      const id = encodeURIComponent(state.selectedId);
      const [info, tm, events, acks, metrics, endurance, nativeConfig] = await Promise.all([
        api('/interactive/continuous/sessions/' + id + '/status'),
        api('/interactive/sessions/' + id + '/telemetry/whole.continuous?after_sequence=' + state.lastTelemetrySeq),
        api('/interactive/sessions/' + id + '/events?after_sequence=' + state.lastEventSeq),
        api('/interactive/sessions/' + id + '/commands'),
        api('/interactive/continuous/sessions/' + id + '/observability'),
        api('/interactive/continuous/sessions/' + id + '/endurance/status'),
        state.rwConfiguration === null
          ? api('/interactive/continuous/sessions/' + id + '/configuration')
          : Promise.resolve(null),
      ]);
      if (info.session.session_id !== state.selectedId) return;
      if (nativeConfig) {
        state.rwConfiguration =
          nativeConfig.native_initial_values?.rw_configuration || 'orthogonal_3';
      }
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
    $('continuousRw4MomentumLabel')?.classList.toggle('hidden', !four);
    const rw4Momentum = document.querySelector('[data-native-key="rw_max_momentum_nms_3"]');
    if (rw4Momentum) {
      rw4Momentum.disabled = !four;
      rw4Momentum.required = four;
    }
    const rw4 = document.querySelector('[data-native-key="rw_initial_speed_rad_s_3"]');
    if (rw4) {
      rw4.disabled = !four;
      rw4.required = four;
    }
    updateRwFrictionNativeInitialLayout();
    updateRwTorqueInitialLayout();
    updateRwElectricalInitialLayout();
    updateRwGeometryInitialLayout();
  }
  function resetRwGeometryAxesForTopology() {
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    const scale = 1 / Math.sqrt(3);
    const pyramids = [[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]];
    for (let i = 0; i < 4; i += 1) {
      for (let j = 0; j < 3; j += 1) {
        const component = ['x', 'y', 'z'][j];
        const field = document.querySelector('[data-native-key="rw_axis_b_' + component + '_' + i + '"]');
        if (field) field.value = four ? String(pyramids[i][j] * scale) : String(Number(i === j));
      }
    }
  }
  function updateRwGeometryInitialLayout() {
    const perWheel = document.querySelector('[data-native-key="rw_geometry_mode"]')?.value === 'per_wheel';
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    $('continuousRwGeometryEditor')?.classList.toggle('hidden', !perWheel);
    const sharedRadius = document.querySelector('[data-native-key="rw_mount_radius_m"]');
    if (sharedRadius) {
      sharedRadius.disabled = perWheel;
      sharedRadius.required = !perWheel;
    }
    for (let i = 0; i < 4; i += 1) {
      const visible = perWheel && (i < 3 || four);
      $('continuousRwGeometryWheel_' + i)?.classList.toggle('hidden', !visible);
      for (const kind of ['axis', 'mount']) for (const component of ['x', 'y', 'z']) {
        const key = kind === 'axis' ? 'rw_axis_b_' + component + '_' + i : 'rw_mount_b_' + component + '_m_' + i;
        const field = document.querySelector('[data-native-key="' + key + '"]');
        if (field) {
          field.disabled = !visible;
          field.required = visible;
        }
      }
    }
  }
  function handleRwTopologyChange() {
    resetRwGeometryAxesForTopology();
    updateRwNativeInitialLayout();
  }
  function updateRwTorqueInitialLayout() {
    const enabled = Boolean($('continuousRwTorqueIndependentToggle')?.checked);
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    for (let i = 0; i < 4; i += 1) {
      const visible = enabled && (i < 3 || four);
      $('continuousRwTorque_' + i)?.classList.toggle('hidden', !visible);
      const control = document.querySelector('[data-native-key="rw_max_torque_nm_' + i + '"]');
      if (control) {
        control.disabled = !visible;
        control.required = visible;
      }
    }
  }
  function updateRwElectricalInitialLayout() {
    const independent = Boolean($('continuousRwElectricalIndependentToggle')?.checked);
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    for (let i = 0; i < 4; i += 1) {
      const visible = independent && (i < 3 || four);
      $('continuousRwElectrical_' + i)?.classList.toggle('hidden', !visible);
      for (const base of ['rw_motor_current_limit_a', 'rw_motor_torque_constant_nm_per_a']) {
        const field = document.querySelector('[data-native-key="' + base + '_' + i + '"]');
        if (field) {
          // Seed each wheel from the operator's *current shared settings*
          // only the first time it is enabled. Subsequent mode/topology
          // toggles must preserve per-wheel edits instead of resetting them.
          if (visible && field.dataset.electricalSeeded !== 'true') {
            const shared = document.querySelector('[data-native-key="' + base + '"]');
            if (shared) field.value = shared.value;
            field.dataset.electricalSeeded = 'true';
          }
          field.disabled = !visible;
          field.required = visible;
        }
      }
    }
  }
  function updateRwFrictionNativeInitialLayout() {
    const mode = document.querySelector('[data-native-key="rw_friction_mode"]')?.value || 'disabled';
    const enabled = mode === 'coulomb_viscous';
    const perWheel = mode === 'per_wheel';
    const four = document.querySelector('[data-native-key="rw_configuration"]')?.value === 'pyramid_4';
    $('continuousRwPerWheelHelp')?.classList.toggle('hidden', !perWheel);
    for (const type of ['coulomb', 'viscous']) {
      for (let i = 0; i < 4; i += 1) {
        const visible = perWheel && (i < 3 || four);
        $('continuousRwFriction_' + type + '_' + i)?.classList.toggle('hidden', !visible);
        const suffix = type === 'coulomb' ? 'nm' : 'nms';
        const control = document.querySelector('[data-native-key="rw_' + type + '_friction_' + suffix + '_' + i + '"]');
        if (control) {
          control.disabled = !visible;
          control.required = visible;
        }
      }
    }
    for (const [key, labelId] of [
      ['rw_coulomb_friction_nm', 'continuousRwCoulombLabel'],
      ['rw_viscous_friction_nms', 'continuousRwViscousLabel'],
    ]) {
      $(labelId)?.classList.toggle('hidden', !enabled);
      const control = document.querySelector('[data-native-key="' + key + '"]');
      if (control) {
        control.disabled = !enabled;
        control.required = enabled;
      }
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
      const value = field.type === 'checkbox' ? field.checked : (field.type === 'number' ? Number(field.value) : field.value);
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
      state.focusSelectedStatus = true;
      state.metrics = null;
      state.endurance = null;
      state.recovery = null;
      state.telemetry = []; state.history = null; state.events = []; state.acks = [];
      state.rwConfiguration = null;
      state.lastTelemetrySeq = -1; state.lastEventSeq = -1;
      setText('continuousInitialConfigStatus',
        '已将运行前参数写入本次原生会话；配置在模型创建后不可修改。');
      notice('已使用当前整星配置创建 Basilisk 原生持续会话；单机和分系统不可作为独立持续会话运行。');
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
  const commandLabels = {
    'session.mode.set': '整星 · 切换运行模式',
    'adcs.target.set': 'ADCS · 设置姿态目标',
    'adcs.control.set': 'ADCS · 控制使能',
    'adcs.fault.inject': 'ADCS · 注入故障',
    'eps.load.set': 'EPS · 负载开关',
    'eps.protection.set': 'EPS · 保护开关',
    'eps.fault.inject': 'EPS · 注入故障',
    'comm_data.generate': '通信数传 · 生成数据',
    'comm_data.downlink.set': '通信数传 · 下行设置',
    'comm_data.fault.inject': '通信数传 · 注入故障',
  };
  const legacyCommands = new Set([
    'session.mode.set', 'adcs.target.set', 'adcs.control.set', 'adcs.fault.inject',
  ]);
  const commandContract = () => state.commandContracts.find(
    (item) => item.operation === $('continuousOperation').value
  );
  async function loadCommandCatalog() {
    if (state.catalogLoading) return;
    state.catalogLoading = true;
    $('continuousCommandSubmit').disabled = true;
    setText('continuousCommandCatalogStatus', '正在从后端获取已注册命令…');
    try {
      const payload = await api('/interactive/commands/catalog');
      const contracts = Array.isArray(payload?.commands) ? payload.commands : [];
      if (!contracts.length) throw new Error('后端未提供已注册命令');
      state.commandContracts = contracts;
      const select = $('continuousOperation');
      const previous = select.value;
      select.replaceChildren();
      contracts.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.operation;
        option.textContent = commandLabels[item.operation] || item.operation;
        select.append(option);
      });
      if (contracts.some((item) => item.operation === previous)) select.value = previous;
      setText('continuousCommandCatalogStatus',
        '已连接服务器遥控目录：' + contracts.length + ' 类指令；最终由后端执行权限与参数校验。');
      changeOperation();
    } catch (error) {
      state.commandContracts = [];
      $('continuousCommandSubmit').disabled = true;
      setText('continuousCommandCatalogStatus', '遥控目录不可用，发送已禁用：' + error.message);
      notice('获取服务器遥控目录失败：' + error.message, true);
    } finally {
      state.catalogLoading = false;
    }
  }
  function changeOperation() {
    const op = $('continuousOperation').value;
    const contract = commandContract();
    const visibility = {
      continuousTargetFields: op === 'adcs.target.set' && Boolean(contract),
      continuousModeFields: op === 'session.mode.set' && Boolean(contract),
      continuousControlFields: op === 'adcs.control.set' && Boolean(contract),
      continuousFaultFields: op === 'adcs.fault.inject' && Boolean(contract),
    };
    Object.entries(visibility).forEach(([id, show]) => $(id).classList.toggle('hidden', !show));
    const host = $('continuousCatalogFields');
    host.replaceChildren();
    const dynamic = Boolean(contract) && !legacyCommands.has(op);
    host.classList.toggle('hidden', !dynamic);
    if (dynamic) {
      Object.entries(contract.parameter_schema?.properties || {}).forEach(([name, schema]) => {
        const label = document.createElement('label');
        label.textContent = name + (contract.units?.[name] ? ' / ' + contract.units[name] : '');
        let field;
        if (Array.isArray(schema.enum)) {
          field = document.createElement('select');
          schema.enum.forEach((value) => {
            const option = document.createElement('option');
            option.value = String(value);
            option.textContent = String(value);
            field.append(option);
          });
        } else {
          field = document.createElement('input');
          field.type = schema.type === 'boolean' ? 'checkbox'
            : ['number', 'integer'].includes(schema.type) ? 'number' : 'text';
          if (field.type === 'number') {
            if (schema.minimum !== undefined) field.min = String(schema.minimum);
            if (schema.maximum !== undefined) field.max = String(schema.maximum);
            field.step = schema.type === 'integer' ? '1' : 'any';
            field.value = String(schema.minimum != null && schema.minimum > 0 ? schema.minimum : 0);
          } else if (schema.type === 'array') {
            field.value = Array(schema.minItems || 3).fill('0').join(', ');
          }
        }
        field.dataset.commandField = name;
        field.dataset.schemaType = schema.type || 'string';
        if (field.type !== 'checkbox') field.required = true;
        label.append(field);
        host.append(label);
      });
    }
    $('continuousCommandSubmit').disabled = !contract;
    setText('continuousCommandHelp', contract
      ? ('控制对象：' + contract.target + ' · 权限：' + contract.required_role
        + ' · 执行后观测：' + (contract.effect_fields || []).join('、'))
      : '尚未取得后端遥控目录；禁止发送。');
  }
  function numericCommandValue(field, schema, label) {
    const raw = field.value.trim();
    if (raw === '') throw new Error(label + ' 不能为空');
    const value = Number(raw);
    if (!Number.isFinite(value)) throw new Error(label + ' 必须是有限数值');
    if (schema.type === 'integer' && !Number.isInteger(value)) {
      throw new Error(label + ' 必须是整数');
    }
    if (schema.minimum !== undefined && value < schema.minimum
        || schema.maximum !== undefined && value > schema.maximum) {
      throw new Error(label + ' 超出允许范围');
    }
    return value;
  }
  function commandParameters(contract) {
    const op = contract.operation;
    const values = {};
    const fields = contract.parameter_schema?.properties || {};
    if (op === 'adcs.target.set') {
      values.sigma_rn = ['continuousSigma1', 'continuousSigma2', 'continuousSigma3']
        .map((id) => numericCommandValue($(id), fields.sigma_rn?.items || {type:'number'}, id));
    } else if (op === 'session.mode.set') {
      values.mode = $('continuousMode').value;
    } else if (op === 'adcs.control.set') {
      values.enabled = $('continuousControlEnabled').value === 'true';
    } else if (op === 'adcs.fault.inject') {
      values.fault_id = $('continuousFault').value;
      values.severity = numericCommandValue($('continuousSeverity'), fields.severity, '严重程度');
    } else {
      $('continuousCatalogFields').querySelectorAll('[data-command-field]').forEach((field) => {
        const name = field.dataset.commandField, schema = fields[name];
        if (schema.type === 'boolean') values[name] = field.checked;
        else if (schema.type === 'number' || schema.type === 'integer') {
          values[name] = numericCommandValue(field, schema, name);
        } else if (schema.type === 'array') {
          const raw = field.value.trim().split(',').map((v) => v.trim());
          if (raw.some((v) => !v)) throw new Error(name + ' 的数组元素不能为空');
          values[name] = raw.map((v) => {
            const tmp = {value:v};
            return numericCommandValue(tmp, schema.items || {type:'number'}, name);
          });
        } else values[name] = field.value;
      });
    }
    for (const [name, schema] of Object.entries(fields)) {
      if (!(name in values)) throw new Error('缺少遥控参数：' + name);
      if (schema.enum && !schema.enum.includes(values[name])) {
        throw new Error('非法遥控枚举值：' + name);
      }
      if (schema.type === 'array') {
        const valuesArray = values[name];
        if (!Array.isArray(valuesArray)
          || valuesArray.length < (schema.minItems || 0)
          || (schema.maxItems != null && valuesArray.length > schema.maxItems)) {
          throw new Error(name + ' 数组长度不符合命令协议');
        }
      }
    }
    return values;
  }
  async function sendCommand(event) {
    event.preventDefault();
    if (!state.snapshot?.session || !state.selectedId) {
      notice('请先选择可操作的持续会话。', true); return;
    }
    const contract = commandContract();
    if (!contract) { notice('后端未注册此指令，拒绝发送。', true); return; }
    let parameters;
    try { parameters = commandParameters(contract); }
    catch (error) { notice('指令参数无效：' + error.message, true); return; }
    const when = Number($('continuousExecuteAt').value);
    if (!$('continuousExecuteAt').value.trim() || !Number.isFinite(when) || when < 0) {
      notice('执行时刻必须是非负仿真秒数。', true); return;
    }
    const command = {
      command_id: 'tc-' + crypto.randomUUID(),
      session_id: state.selectedId,
      session_revision: state.snapshot.session.revision,
      operation: contract.operation, target: contract.target, parameters,
      actor_id: 'browser-operator', actor_role: contract.required_role,
      execute_at_sim_time_s: when,
    };
    if (!confirm('确认发送遥控 ' + contract.operation + '，预定在仿真 '
      + when.toFixed(2) + ' 秒执行？')) return;
    $('continuousCommandSubmit').disabled = true;
    try {
      const ack = await api('/interactive/sessions/' + encodeURIComponent(state.selectedId) + '/commands', {
        method: 'POST', body: JSON.stringify(command),
      });
      notice('遥控 ' + ack.command_id + '：' + ack.state +
        (ack.reason_code ? ' / ' + ack.reason_code : ''), ack.state === 'REJECTED');
      await refresh();
    } catch (error) {
      notice('遥控发送失败：' + error.message, true);
    } finally {
      $('continuousCommandSubmit').disabled = !commandContract();
    }
  }
  function applyNativeConfiguration(grouped) {
    // Apply only server-reviewed fields, before creating a native session.
    // Do not silently ignore unavailable, disabled or invalid form controls.
    if (!grouped || typeof grouped !== 'object') throw new Error('导入配置无效');
    const form = $('continuousCreateForm');
    const fields = Array.from(form.querySelectorAll('[data-native-group][data-native-key]'));
    const saved = fields.map(field => ({
      field, value: field.type === 'checkbox' ? field.checked : field.value,
    }));
    const requested = [];
    for (const [group, values] of Object.entries(grouped)) {
      if (!values || typeof values !== 'object' || Array.isArray(values)) {
        throw new Error('无效配置分组：' + group);
      }
      for (const [key, value] of Object.entries(values)) {
        const field = fields.find(item =>
          item.dataset.nativeGroup === group && item.dataset.nativeKey === key);
        if (!field) throw new Error('界面不支持原生参数：' + group + '.' + key);
        requested.push({field, value, key});
      }
    }
    if (!requested.length) throw new Error('没有可导入参数');
    // Topology/mode controls enable the dependent RW fields; configure first
    // because topology changes can reset axis defaults.
    const first = ['rw_configuration', 'rw_geometry_mode', 'rw_friction_mode'];
    const ordered = [...requested].sort((a, b) =>
      (first.includes(a.key) ? first.indexOf(a.key) : 99)
      - (first.includes(b.key) ? first.indexOf(b.key) : 99));
    function assign(field, value) {
      if (field.type === 'checkbox') {
        if (typeof value !== 'boolean') throw new Error(field.dataset.nativeKey + ' 必须是布尔值');
        field.checked = value;
      } else {
        if (typeof value === 'number' && !Number.isFinite(value)) {
          throw new Error(field.dataset.nativeKey + ' 不可使用非有限数值');
        }
        field.value = String(value);
        if (field.tagName === 'SELECT' && field.value !== String(value)) {
          throw new Error(field.dataset.nativeKey + ' 非法选项');
        }
      }
      field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    try {
      for (const {field, value, key} of ordered) {
        if (field.disabled) throw new Error(key + ' 当前未启用，需先完成对应配置开关');
        assign(field, value);
        if (!field.checkValidity()) throw new Error(key + ' 不满足实时表单参数限制');
      }
      if (!form.checkValidity()) {
        throw new Error('导入参数与实时初始化表单存在约束冲突');
      }
    } catch (error) {
      // All-or-nothing UI import, even if the error occurs after topology toggles.
      saved.filter(item => first.includes(item.field.dataset.nativeKey))
        .forEach(item => assign(item.field, item.value));
      saved.filter(item => !first.includes(item.field.dataset.nativeKey))
        .forEach(item => assign(item.field, item.value));
      throw error;
    }
    $('continuousInitialConfigStatus').textContent =
      '从离线整星导入的参数已在创建前写入实时表单；请核对后再创建会话。';
    form.querySelector('details.continuous-native-config')?.setAttribute('open', '');
    return requested.length;
  }

  function enter() {
    state.active = true;
    clearInterval(state.timer);
    refresh();
    loadCommandCatalog();
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
    $('continuousHistoryQueryBtn')?.addEventListener('click', queryVerifiedHistory);
    $('continuousEnduranceStart')?.addEventListener('click', () => changeEndurance('start'));
    $('continuousEnduranceStop')?.addEventListener('click', () => changeEndurance('stop'));
    $('continuousEnduranceEvidence')?.addEventListener('click', () => exportEndurance('evidence'));
    $('continuousEnduranceSummary')?.addEventListener('click', () => exportEndurance('summary'));
    $('continuousEnduranceAuditBtn')?.addEventListener('click', auditEndurance);
    $('continuousRecoveryAuditBtn')?.addEventListener('click', auditNativeRecovery);
    document.querySelector('[data-native-key="rw_configuration"]')
      ?.addEventListener('change', handleRwTopologyChange);
    resetRwGeometryAxesForTopology();
    updateRwNativeInitialLayout();
    document.querySelector('[data-native-key="rw_geometry_mode"]')
      ?.addEventListener('change', updateRwGeometryInitialLayout);
    updateRwGeometryInitialLayout();
    document.querySelector('[data-native-key="rw_friction_mode"]')
      ?.addEventListener('change', updateRwFrictionNativeInitialLayout);
    updateRwFrictionNativeInitialLayout();
    $('continuousRwTorqueIndependentToggle')?.addEventListener('change', updateRwTorqueInitialLayout);
    updateRwTorqueInitialLayout();
    $('continuousRwElectricalIndependentToggle')?.addEventListener('change', updateRwElectricalInitialLayout);
    updateRwElectricalInitialLayout();
    $('continuousRecoveryExportBtn')?.addEventListener('click', exportNativeRecovery);
    document.querySelectorAll('[data-continuous-action]').forEach((button) => {
      button.addEventListener('click', () => control(button.dataset.continuousAction));
    });
    window.addEventListener('resize', () => {
      syncSessionColumnOrder();
      if (state.active) { renderChart(); renderVerifiedHistory(); }
    });
    changeOperation();
    renderStatus();
    renderObservability();
    renderEndurance();
    renderRecovery();
    renderChart();
    renderVerifiedHistory();
  }
  window.SatContinuousConsole = { enter, exit, applyNativeConfiguration };
  document.addEventListener('DOMContentLoaded', initialize);
})();
