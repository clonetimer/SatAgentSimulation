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
  // Only the initial placeholder is empty. app.js also uses empty-state while
  // reading a Run or reporting a read error; those states need the full panel.
  const syncEmptyResults = () => {
    byId('creatorView')?.classList.toggle('compact-empty-results',
      Boolean(runCard?.querySelector('[data-compact-empty-results]')));
  };
  if (runCard) new MutationObserver(syncEmptyResults).observe(runCard, { childList: true, subtree: true });
  syncEmptyResults();
  const jumpTargets = {
    singleRunJumpParametersBtn: 'formFieldSearch',
    singleRunJumpEventsBtn: 'effectKind',
    singleRunJumpOutputsBtn: 'plotOptions',
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (button?.id === 'singleRunSelectBtn' && byId('toggleMobileCatalogBtn')?.getAttribute('aria-expanded') !== 'true') {
      byId('toggleMobileCatalogBtn').click();
    }
    if (button && jumpTargets[button.id]) reveal(byId(jumpTargets[button.id]));
    const more = byId('creatorMoreActions');
    if (more.open && !more.contains(event.target)) more.open = false;
  }, true);
  document.addEventListener('keydown', event => {
    const graphActions = document.activeElement?.closest('.compact-graph-disclosure');
    if (event.key === 'Escape' && graphActions?.open) {
      graphActions.open = false;
      graphActions.querySelector('summary').focus();
      event.preventDefault();
      return;
    }
    const more = byId('creatorMoreActions');
    if (event.key === 'Escape' && more.open && more.contains(document.activeElement)) {
      more.open = false;
      more.querySelector('summary').focus();
    }
    if (event.key === 'Escape') {
      for (const [buttonId, panelId] of [['toggleMobileToolsBtn', 'workbenchTools'], ['toggleMobileCatalogBtn', 'mobileCatalogPanel']]) {
        const button = byId(buttonId);
        const panel = byId(panelId);
        if (button?.getAttribute('aria-expanded') === 'true' &&
            (panel?.contains(document.activeElement) || document.activeElement === button)) {
          // The existing handler owns the single aria/data disclosure state.
          button.click();
          button.focus();
          event.preventDefault();
          break;
        }
      }
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
  // Keep one navigation tree and the original app.js listeners. The guided
  // workspace deliberately hides its catalog sidebar, not these task routes.
  const nav = document.querySelector('.sidebar-nav');
  const frame = document.querySelector('.app-frame');
  if (nav && frame) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet'; stylesheet.href = '/assets/workbench-navigation.css?v=p9g-1';
    document.head.append(stylesheet);
    const host = document.createElement('div'); host.id = 'workbenchNavigation';
    frame.before(host); host.append(nav);
    nav.querySelector(':scope > .sidebar-section-title')?.remove();
    const more = document.createElement('details'); more.id = 'workbenchMoreNavigation';
    const summary = document.createElement('summary'); summary.textContent = '更多功能';
    more.append(summary);
    nav.querySelectorAll(':scope > .sidebar-nav-group').forEach(group => more.append(group));
    nav.append(more);
    const help = document.createElement('button'); help.id = 'workbenchHelpBtn';
    help.type = 'button'; help.className = 'nav-button'; help.textContent = '使用帮助';
    help.setAttribute('aria-haspopup', 'dialog'); nav.append(help);
    const dialog = document.createElement('dialog'); dialog.id = 'workbenchHelpDialog';
    dialog.setAttribute('aria-labelledby', 'workbenchHelpTitle');
    const title = document.createElement('h2'); title.id = 'workbenchHelpTitle'; title.textContent = '使用帮助';
    dialog.append(title);
    for (const [label, text] of [
      ['快速仿真', '选择整星、分系统或单机，按步骤配置参数、校验并运行，完成后查看结果。'],
      ['任务中心', '查找已保存任务和历史记录，继续配置或回看运行结果。'],
      ['卫星数字场景', '查看当前会话的轨道位置或已封存运行的轨迹；没有真实数据时会明确标注演示模式。'],
      ['专业建模', '保留图形建模、模块装配、校验、运行和程序包导出。'],
      ['更多功能', '展开后进入实时仿真、实验与分析或高级工具。切换界面不会自动停止正在运行的仿真。'],
    ]) {
      const heading = document.createElement('h3'); heading.textContent = label;
      const paragraph = document.createElement('p'); paragraph.textContent = text;
      dialog.append(heading, paragraph);
    }
    const close = document.createElement('button'); close.type = 'button';
    close.className = 'button button-primary'; close.textContent = '关闭帮助';
    close.addEventListener('click', () => dialog.close()); dialog.append(close);
    document.body.append(dialog);
    help.addEventListener('click', () => dialog.showModal());
    dialog.addEventListener('close', () => help.focus());
    // The two primary wizard entries share one authoritative state owner.
    // Leaving history uses its existing Back action, never a new reset routine.
    nav.querySelector(':scope > [data-view="wizard"]:not([data-wizard-shortcut])')
      ?.addEventListener('click', () => queueMicrotask(() => {
        if (document.body.dataset.activeView === 'wizard'
            && !byId('wizardTaskWorkspace')?.classList.contains('hidden')) {
          byId('wizardBackBtn')?.click();
        }
      }));
    const syncNavigation = () => {
      more.querySelectorAll('.sidebar-nav-group').forEach(group => {
        const buttons = [...group.querySelectorAll('.nav-button')];
        const available = buttons.some(button => !button.classList.contains('hidden'));
        if (group.hidden === available) group.hidden = !available;
        if (available && buttons.some(button => button.classList.contains('active'))) group.open = true;
      });
      nav.querySelectorAll('.nav-button[data-view]').forEach(button => {
        const current = button.classList.contains('active') ? 'page' : null;
        if (current && button.getAttribute('aria-current') !== current) button.setAttribute('aria-current', current);
        if (!current) button.removeAttribute('aria-current');
      });
      summary.classList.toggle('has-active-view', Boolean(more.querySelector('.nav-button.active')));
    };
    new MutationObserver(syncNavigation).observe(nav, {
      subtree: true, attributes: true, attributeFilter: ['class'],
    });
    nav.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !more.open) return;
      more.open = false; summary.focus(); event.preventDefault();
    });
    syncNavigation();
  }
  updateCounts();
})();
