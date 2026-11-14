/* P9-E: resolve only contract-backed links from a sealed Run to object-tree nodes. */
((root) => {
  'use strict';

  function indexTree(roots) {
    const nodes = new Map();
    const walk = (node, parentId = null) => {
      if (!node || typeof node !== 'object') return;
      const id = typeof node.object_id === 'string' ? node.object_id : null;
      if (id) nodes.set(id, {node, parentId});
      (Array.isArray(node.children) ? node.children : []).forEach(child => walk(child, id || parentId));
    };
    (Array.isArray(roots) ? roots : []).forEach(node => walk(node));
    return nodes;
  }

  function ancestorOf(nodes, candidateId, ancestorId) {
    let current = nodes.get(candidateId);
    while (current?.parentId) {
      if (current.parentId === ancestorId) return true;
      current = nodes.get(current.parentId);
    }
    return false;
  }

  function seriesBinding(series) {
    if (series?.object_id && ['component','subsystem','whole_spacecraft'].includes(series.association_scope)) {
      return {objectId: series.object_id, scope: series.association_scope, source: series.association_source || 'plot_manifest'};
    }
    // Backward-compatible, conservative fallback for v2 plot manifests:
    // subsystem_id is authoritative enough for subsystem-level grouping,
    // but never infer a component object from a component-shaped group label.
    if (series?.subsystem_id && series.subsystem_id !== 'whole_spacecraft') {
      return {objectId: `subsystem.${series.subsystem_id}`, scope: 'subsystem', source: 'legacy_output_hierarchy'};
    }
    if (series?.scope_id === 'whole_spacecraft' || series?.subsystem_id === 'whole_spacecraft') {
      return {objectId: 'whole_spacecraft', scope: 'whole_spacecraft', source: 'legacy_output_hierarchy'};
    }
    return null;
  }

  function latestSample(rows, field, timeField) {
    let latest = null;
    (Array.isArray(rows) ? rows : []).forEach((row, index) => {
      if (!row || !Object.prototype.hasOwnProperty.call(row, field)) return;
      const raw = row[field];
      if (raw == null || raw === '' || typeof raw === 'boolean') return;
      const value = Number(raw);
      if (!Number.isFinite(value)) return;
      const rawTime = row[timeField] ?? row.time_s ?? row.t_s ?? index;
      const time = Number(rawTime);
      if (!Number.isFinite(time)) return;
      if (!latest || time >= latest.time_s) latest = {time_s: time, value};
    });
    return latest;
  }

  function resolve({tree, selectedId, run, plotManifest, telemetryRows, telemetryOverview} = {}) {
    if (!run) return {status: 'no_run', series: [], relatedSeries: []};
    if (run.sealed !== true || run.integrity?.ok !== true ||
        String(run.run_record?.status || '').toUpperCase() !== 'SUCCEEDED') {
      return {status: 'unverified', runId: run.run_id, series: [], relatedSeries: []};
    }
    const nodes = indexTree(tree?.roots);
    const selected = nodes.get(selectedId);
    if (!selected) return {status: 'object_unavailable', runId: run.run_id, series: [], relatedSeries: []};
    const manifestSeries = Array.isArray(plotManifest?.series) ? plotManifest.series : [];
    if (!manifestSeries.length) return {status: 'no_recorded_series', runId: run.run_id, series: [], relatedSeries: []};

    const bindings = manifestSeries.map(item => ({item, binding: seriesBinding(item)})).filter(row => row.binding);
    const selectedKind = selected.node.kind;
    const matches = bindings.filter(({binding}) => {
      if (selectedId === 'whole_spacecraft') return true;
      if (selectedKind === 'subsystem') {
        return binding.objectId === selectedId || (
          binding.scope === 'component' && ancestorOf(nodes, binding.objectId, selectedId)
        );
      }
      if (selectedKind === 'equipment' || selectedId.startsWith('component.')) {
        return binding.scope === 'component' && binding.objectId === selectedId;
      }
      return false;
    });
    const related = selectedKind === 'equipment' || selectedId.startsWith('component.')
      ? bindings.filter(({binding}) => binding.scope === 'subsystem' && binding.objectId === selected.parentId)
      : [];
    const rows = Array.isArray(telemetryRows) ? telemetryRows : [];
    const timeField = plotManifest?.time_axis?.field || plotManifest?.time_field || 'time_s';
    const series = matches.map(({item, binding}) => ({
      field: item.field,
      label: item.label || item.field,
      unit: item.unit || null,
      object_id: binding.objectId,
      association_scope: binding.scope,
      association_source: binding.source,
      sample: latestSample(rows, item.field, timeField),
    }));
    return {
      status: series.length ? 'available' : related.length ? 'parent_scope_only' : 'no_direct_association',
      runId: run.run_id,
      objectId: selectedId,
      objectName: selected.node.name_zh || selectedId,
      series,
      relatedSeries: related.map(({item, binding}) => ({
        field: item.field, label: item.label || item.field, unit: item.unit || null,
        object_id: binding.objectId, association_scope: binding.scope,
      })),
      telemetryRows: rows.length,
      sampled: Boolean(telemetryOverview?.sampled),
      totalTelemetryRows: telemetryOverview?.total ?? rows.length,
    };
  }

  root.P9EObjectRunResults = Object.freeze({resolve});
})(window);
