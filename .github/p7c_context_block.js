  const SHARED_CONTEXT_VIEWS = new Set(['experiments', 'goals', 'runs', 'diagnostics']);
  const SHARED_CONTEXT_LABELS = {
    wizard: '仿真向导', creator: '高级工作台', tasks: '任务中心',
    experiments: '实验中心', goals: '目标实验', runs: '运行中心', diagnostics: '系统诊断',
    interactive: '限时实时控制台', continuous: '持续实时控制台',
  };

  // References are read-only navigation metadata. Never use an inspected
  // TaskSpec ID as the editor's Task Center identity or a comparison row as
  // the selected Run: those controllers own save/cancel/polling targets.
  function sharedContextSnapshot() {
    const context = state.sharedWorkbenchContext || {};
    const taskId = state.activeTaskCenterId || null;
    const runId = state.activeRunId || null;
    const sameRun = Boolean(runId && context.runId === runId);
    return {
      ...context,
      taskId,
      taskVersion: taskId && state.activeTaskVersionTaskId === taskId ? state.activeTaskVersion : null,
      runId,
      bundleId: sameRun ? context.bundleId || null : null,
      runTaskSpecId: sameRun ? context.runTaskSpecId || null : null,
    };
  }

  function updateSharedWorkbenchContext(patch = {}) {
    state.sharedWorkbenchContext = { ...state.sharedWorkbenchContext, ...patch };
    renderSharedWorkbenchContext();
  }

  function renderSharedWorkbenchContext() {
    const context = sharedContextSnapshot();
    const task = context.taskId
      ? `编辑任务 ${context.taskId}${context.taskVersion ? ` · 载入 V${context.taskVersion}` : ' · 版本未确认'}`
      : '当前编辑配置未关联已保存任务';
    const run = context.runId ? `当前查看 Run ${context.runId}` : '尚未选择 Run';
    const references = [
      context.experimentId && `实验 ${context.experimentId}`,
      context.goalId && `目标 ${context.goalId}`,
      context.baseTaskSpecId && `实验基础 TaskSpec ${context.baseTaskSpecId}`,
      context.runTaskSpecId && `运行 TaskSpec ${context.runTaskSpecId}`,
      context.bundleId && `已封存 Run Bundle ${context.bundleId}`,
    ].filter(Boolean).join(' · ');
    const source = context.sourceLabel || SHARED_CONTEXT_LABELS[context.sourceView] || '当前工作区';
    document.querySelectorAll('[data-p7-context]').forEach((host) => {
      host.replaceChildren();
      const summary = document.createElement('div'); summary.className = 'p7-context-summary';
      const title = document.createElement('strong'); title.textContent = '当前工作上下文';
      const taskNode = document.createElement('span'); taskNode.textContent = task;
      const runNode = document.createElement('span'); runNode.textContent = run;
      const extra = document.createElement('small'); extra.textContent = references || '尚无额外结果引用';
      extra.title = '编辑任务和查看结果分别保留；查看结果不会切换保存或取消操作的对象。';
      summary.append(title, taskNode, runNode, extra);
      const actions = document.createElement('div'); actions.className = 'p7-context-actions';
      const sourceNode = document.createElement('small'); sourceNode.textContent = `来源：${source}`;
      const back = document.createElement('button'); back.type = 'button'; back.className = 'button button-secondary';
      back.textContent = `返回${source}`;
      back.disabled = !context.sourceView || context.sourceView === state.activeView;
      back.addEventListener('click', returnToSharedContextSource);
      actions.append(sourceNode, back); host.append(summary, actions);
    });
  }

  function returnToSharedContextSource() {
    const source = state.sharedWorkbenchContext?.sourceView;
    if (!source || source === state.activeView) return;
    switchView(source);
    if (state.activeView !== source) return; // Keep the source if departure was cancelled.
    if (source === 'wizard') {
      wizardMessage('assistant', '已返回仿真向导；当前配置、任务版本和已有运行记录保持不变。');
      renderSimulationWizard();
    }
    renderSharedWorkbenchContext();
  }

  function noteSharedContextTransition(nextView) {
    if (nextView === state.activeView) return;
    if (!SHARED_CONTEXT_VIEWS.has(nextView)) {
      state.sharedWorkbenchContext = { ...state.sharedWorkbenchContext, sourceView: null, sourceLabel: null };
      return;
    }
    const current = state.activeView;
    if (!SHARED_CONTEXT_VIEWS.has(current) && !state.sharedWorkbenchContext?.sourceView) {
      state.sharedWorkbenchContext = {
        ...state.sharedWorkbenchContext, sourceView: current,
        sourceLabel: SHARED_CONTEXT_LABELS[current] || current,
      };
    }
  }

