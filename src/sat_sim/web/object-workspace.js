/* Read-only view models. No simulation submission or configuration writeback. */
((root, factory) => {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ObjectWorkspace = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const required = (condition, message) => { if (!condition) throw new Error(message); };
  const objectId = row => row && typeof row.object_id === 'string' ? row.object_id : null;

  function simulationPanelModel(tree, selectedObjectId) {
    required(tree?.schema_version === 'workbench-object-tree.v2', '对象目录版本不兼容。');
    required(Array.isArray(tree.roots), '对象目录不完整。');
    const pending = [...tree.roots];
    const seen = new Set();
    let selected = null;
    while (pending.length) {
      const node = pending.pop();
      required(node && typeof node === 'object' && !seen.has(node), '对象目录包含重复或循环引用。');
      seen.add(node);
      required(!['summary', 'summary_group'].includes(node.kind), '仿真对象目录不能包含效能评估指标。');
      if (objectId(node) === selectedObjectId) {
        required(selected === null, '对象标识重复。');
        selected = node;
      }
      required(Array.isArray(node.children), '对象节点不完整。');
      pending.push(...node.children);
    }
    required(selected !== null, '未找到所选对象。');
    const rows = (groupKind, leafKind) => selected.children
      .filter(group => group.kind === groupKind)
      .flatMap(group => group.children.map(row => {
        required(row.kind === leafKind && row.owner_object_id === selectedObjectId, '参量归属与所选对象不一致。');
        return {
          id: row.node_id, ownerObjectId: row.owner_object_id,
          label: row.display_name_zh, kind: row.kind, unit: row.unit ?? null,
          sourceField: row.source_field, sourceCapabilityId: row.source_capability_id,
          defaultValue: row.default === undefined ? null : clone(row.default),
          arrayIndex: row.array_index ?? null,
        };
      }));
    return {
      ownerObjectId: selectedObjectId, title: selected.name_zh,
      objectKind: selected.kind,
      parameters: rows('parameter_group', 'parameter'),
      outputs: rows('observable_group', 'observable'),
    };
  }

  const reasons = {
    SOURCE_NOT_RECORDED: '本次运行未记录所需参量。',
    SOURCE_BINDING_MISMATCH: '参量归属、来源或单位与评估方案不一致。',
    INVALID_SERIES: '记录的数据格式不符合评估要求。',
    INVALID_TIME_AXIS: '记录的时间顺序不符合评估要求。',
    INCOMPLETE_WINDOW: '记录未覆盖所选评估时段。',
    NO_SAMPLES: '所选时段内没有记录数据。',
    MINIMUM_SAMPLES: '记录的采样数量不足。',
    SAMPLING_GAP: '采样间隔超过评估方案允许范围。',
  };

  function evaluationPanelModel(report, selectedRunId) {
    required(report?.schema_version === 'evaluation-report.v1', '评估报告版本不兼容。');
    required(report.source_run_id === selectedRunId, '评估报告不属于所选运行记录。');
    required(Array.isArray(report.metrics), '评估报告不完整。');
    const verdicts = {
      NOT_REQUESTED: '未设置判据', WITHIN_LIMITS: '符合设定范围',
      OUTSIDE_LIMITS: '超出设定范围', NOT_EVALUATED: '未评估',
    };
    const seen = new Set();
    const metrics = report.metrics.map(row => {
      required(!seen.has(row.metric_id), '评估指标标识重复。');
      seen.add(row.metric_id);
      required(['COMPUTED', 'NOT_EVALUATED'].includes(row.status), '评估指标状态无效。');
      required(Object.hasOwn(verdicts, row.verdict), '评估判据状态无效。');
      required(row.status !== 'COMPUTED' || (typeof row.value === 'number' && Number.isFinite(row.value)), '评估数值无效。');
      required(row.status !== 'NOT_EVALUATED' || row.value === null, '未评估的指标不能显示数值。');
      return {
        id: row.metric_id, label: row.display_name_zh, value: row.value,
        unit: row.unit, statusText: row.status === 'COMPUTED' ? '已计算' : '未评估',
        verdictText: verdicts[row.verdict],
        reasonText: row.reason_code ? (reasons[row.reason_code] || '当前数据无法用于此项评估。') : '',
      };
    });
    return {title: '效能评估', sourceRunId: report.source_run_id,
      sourceSnapshotId: report.source_snapshot_id, planVersion: report.plan_version, metrics};
  }
  return Object.freeze({simulationPanelModel, evaluationPanelModel});
});
