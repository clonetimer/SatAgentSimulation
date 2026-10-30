/* Presentation only. Existing control IDs, handlers and request values remain. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const reveal = target => {
    for (let node = target?.parentElement; node; node = node.parentElement) {
      if (node instanceof HTMLDetailsElement) node.open = true;
    }
  };
  const readiness = byId('singleRunReadiness');
  const modeHeading = byId('simulationModeChooser')?.querySelector('.single-run-heading');
  if (readiness && modeHeading) {
    const brief = document.createElement('span');
    brief.id = 'compactReadiness'; brief.className = 'compact-readiness';
    brief.setAttribute('role', 'status'); brief.setAttribute('aria-live', 'polite');
    modeHeading.append(brief);
    document.querySelector('.capability-about')?.append(readiness);
    const updateReadiness = () => {
      const ready = readiness.dataset.ready === 'true';
      brief.dataset.ready = String(ready);
      brief.textContent = ready ? '环境已检测' : readiness.textContent;
      brief.title = readiness.textContent;
    };
    new MutationObserver(updateReadiness).observe(readiness, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-ready'] });
    updateReadiness();
  }
  const events = byId('eventList');
  const updateCounts = () => {
    const count = events?.querySelectorAll('.event-card').length || 0;
    byId('compactEventCount').textContent = count ? `${count} 项` : '无';
    if (count) byId('compactEvents').open = true;
    const selected = byId('plotOptions')?.querySelectorAll('input[type="checkbox"]:checked').length || 0;
    byId('compactOutputCount').textContent = `已选 ${selected} 项`;
  };
  if (events) new MutationObserver(updateCounts).observe(events, { childList: true, subtree: true });
  const outputs = byId('plotOptions');
  if (outputs) new MutationObserver(updateCounts).observe(outputs, { childList: true, subtree: true });
  outputs?.addEventListener('change', updateCounts);
  const scaleMode = byId('chartScaleMode');
  const updateScaleLabel = () => {
    const label = byId('compactChartOptionsLabel');
    if (label) label.textContent = `${scaleMode?.value === 'absolute' ? '原始数值' : '归一化趋势'} · 时间与显示`;
  };
  scaleMode?.addEventListener('change', updateScaleLabel);
  updateScaleLabel();
  const runCard = byId('activeRunCard');
  const organizeRunMetadata = () => {
    const metrics = runCard?.querySelector(':scope > .run-details');
    if (!metrics || runCard.querySelector(':scope > .compact-run-metadata')) return;
    const secondary = [...metrics.children].slice(2);
    const identifier = runCard.querySelector('.run-id > small');
    if (!identifier && !secondary.length) return;
    const details = document.createElement('details'); details.className = 'compact-run-metadata';
    const summary = document.createElement('summary'); summary.textContent = '运行信息';
    const body = document.createElement('div'); body.className = 'compact-run-metadata-body';
    if (identifier) body.append(identifier);
    body.append(...secondary);
    details.append(summary, body);
    metrics.after(details);
  };
  if (runCard) new MutationObserver(organizeRunMetadata).observe(runCard, { childList: true, subtree: true });
  organizeRunMetadata();
  const jumpTargets = {
    singleRunJumpParametersBtn: 'formFieldSearch',
    singleRunJumpEventsBtn: 'effectKind',
    singleRunJumpOutputsBtn: 'plotOptions',
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (button && jumpTargets[button.id]) reveal(byId(jumpTargets[button.id]));
    const more = byId('creatorMoreActions');
    if (more.open && !more.contains(event.target)) more.open = false;
  }, true);
  document.addEventListener('keydown', event => {
    const more = byId('creatorMoreActions');
    if (event.key === 'Escape' && more.open && more.contains(document.activeElement)) {
      more.open = false;
      more.querySelector('summary').focus();
    }
  });
  const form = byId('formTab');
  if (form) new MutationObserver(() => {
    form.querySelectorAll('.field-error, .field-error-message').forEach(reveal);
  }).observe(form, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  // Keep the original controls and listeners; reorganize their presentation.
  const disclose = (section, label, id, open = false) => {
    if (!section) return null;
    const details = document.createElement('details');
    details.id = id; details.className = 'compact-workspace-disclosure'; details.open = open;
    const summary = document.createElement('summary');
    summary.className = 'compact-section-summary'; summary.textContent = label;
    section.before(details); details.append(summary, section);
    return details;
  };
  const interactiveCreate = disclose(document.querySelector('.interactive-create'), '新建实时会话', 'compactInteractiveCreate', true);
  const continuousCreate = disclose(byId('continuousCreateForm')?.closest('section'), '新建持续会话', 'compactContinuousCreate', true);
  const continuousList = byId('continuousSessionList')?.closest('section');
  if (continuousList && continuousCreate) continuousCreate.before(continuousList);
  const syncCreationDisclosure = (list, details) => {
    if (!list || !details) return;
    let selected = null;
    new MutationObserver(() => {
      const next = list.querySelector('.active strong')?.textContent || null;
      if (next && next !== selected) details.open = false;
      if (!next && selected) details.open = true;
      selected = next;
    }).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  };
  syncCreationDisclosure(byId('interactiveSessionList'), interactiveCreate);
  syncCreationDisclosure(byId('continuousSessionList'), continuousCreate);
  const syncInteractiveOrder = () => {
    const grid = document.querySelector('.interactive-layout');
    const selected = byId('interactiveSessionList')?.querySelector('.active');
    const first = grid?.querySelector(selected && matchMedia('(max-width: 760px)').matches ? '.interactive-console' : '.interactive-rail');
    if (first && grid.firstElementChild !== first) grid.prepend(first);
  };
  new MutationObserver(syncInteractiveOrder).observe(byId('interactiveSessionList'), { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  window.addEventListener('resize', syncInteractiveOrder);
  syncInteractiveOrder();
  for (const [id, label] of Object.entries({
    interactiveStartBtn: '启动', interactivePauseBtn: '暂停', interactiveResumeBtn: '继续',
    interactiveStepBtn: '单步', interactiveStopBtn: '停止并归档',
  })) {
    const button = byId(id);
    if (button) button.textContent = label;
  }
  const continuousMain = document.querySelector('.continuous-main');
  const telemetry = byId('continuousChart')?.closest('section');
  const status = byId('continuousState')?.closest('section');
  if (telemetry && status) status.after(telemetry);
  const diagnostics = document.createElement('div');
  diagnostics.className = 'compact-runtime-diagnostics';
  const runtimeInfo = document.createElement('section'); runtimeInfo.className = 'continuous-card';
  const infoHeading = document.createElement('h2'); infoHeading.textContent = '运行信息';
  const infoMetrics = document.createElement('div'); infoMetrics.className = 'continuous-metrics';
  for (const id of ['continuousChildPid', 'continuousWriterEpoch']) {
    const metric = byId(id)?.parentElement;
    if (metric) infoMetrics.append(metric);
  }
  runtimeInfo.append(infoHeading, infoMetrics); diagnostics.append(runtimeInfo);
  status?.classList.add('compact-live-status');
  for (const id of ['continuousRecoveryAuditBtn', 'continuousMetricScope']) {
    const section = byId(id)?.closest('section');
    if (section) diagnostics.append(section);
  }
  if (continuousMain && diagnostics.children.length) {
    continuousMain.append(diagnostics);
    disclose(diagnostics, '运行诊断与恢复覆盖', 'compactRuntimeDiagnostics');
  }
  const recoveryHeading = byId('continuousRecoveryAuditBtn')?.closest('section')?.querySelector('h2');
  if (recoveryHeading) recoveryHeading.textContent = '原生恢复覆盖';
  const interrupted = byId('continuousInterruptedList')?.closest('section');
  const interruptedDetails = disclose(interrupted, '中断工作区', 'compactInterruptedWorkspaces');
  if (interrupted && interruptedDetails) {
    new MutationObserver(() => {
      if (interrupted.querySelector('.continuous-interrupted-item')) interruptedDetails.open = true;
    }).observe(interrupted, { childList: true, subtree: true });
  }
  // Native browser validation must reveal invalid inputs in closed groups.
  document.addEventListener('invalid', event => reveal(event.target), true);
  updateCounts();
})();
