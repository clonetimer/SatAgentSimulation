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
  updateCounts();
})();
