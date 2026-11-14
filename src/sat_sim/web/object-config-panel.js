/* P9-D: read-only object tree plus editor bound to the existing capability form. */
((root) => {
  'use strict';
  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const byId = id => document.getElementById(id);
  const nodeChildren = node => Array.isArray(node?.children) ? node.children : [];

  function findNode(roots, id) {
    const stack = [...(roots || [])];
    while (stack.length) {
      const node = stack.pop();
      if (node?.object_id === id) return node;
      stack.push(...nodeChildren(node));
    }
    return null;
  }

  function renderTree(host, roots, selectedId, onSelect) {
    host.replaceChildren();
    const walk = (node, depth = 0) => {
      const children = nodeChildren(node);
      const isObject = typeof node.object_id === 'string' && !['context_group','external_group','relationship_group'].includes(node.kind);
      if (isObject) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'p9d-tree-item' + (node.object_id === selectedId ? ' is-selected' : '');
        button.style.setProperty('--tree-depth', String(Math.min(depth, 6)));
        button.textContent = node.name_zh || node.object_id;
        button.title = node.summary_zh || node.object_id;
        button.setAttribute('role', 'treeitem');
        button.setAttribute('aria-current', node.object_id === selectedId ? 'true' : 'false');
        button.addEventListener('click', () => onSelect(node.object_id));
        host.appendChild(button);
      } else if (node.name_zh) {
        const heading = document.createElement('div');
        heading.className = 'p9d-tree-group';
        heading.textContent = node.name_zh;
        heading.style.setProperty('--tree-depth', String(Math.min(depth, 6)));
        host.appendChild(heading);
      }
      children.forEach(child => walk(child, depth + (isObject ? 1 : 0)));
    };
    (roots || []).forEach(node => walk(node));
  }

  function renderParameters(host, {node, schema, formData, onChange, onSelect}) {
    host.replaceChildren();
    if (!node) {
      host.textContent = '请选择整星、分系统或单机。';
      return;
    }
    const capabilityId = node.execution_capability_id || node.configuration_capability_id || node.primary_capability_id;
    if (!capabilityId || !schema || schema.capability?.capability_id !== capabilityId) {
      const empty = document.createElement('p');
      empty.className = 'p9d-empty';
      empty.textContent = node.configuration_scope === 'parent'
        ? '该单机通过所属分系统组合配置；请在所属分系统中编辑其模型参数。'
        : node.independently_executable === false
          ? '该对象当前没有独立配置能力。'
          : '该对象尚无可用的参数配置合同。';
      host.appendChild(empty);
      if (node.configuration_scope === 'parent' && node.parent_object_id && onSelect) {
        const parent = document.createElement('button');
        parent.type = 'button';
        parent.className = 'button button-secondary';
        parent.textContent = '查看所属分系统参数';
        parent.addEventListener('click', () => onSelect(node.parent_object_id));
        host.appendChild(parent);
      }
      return;
    }
    const parameterNodes = nodeChildren(node).filter(group => group.kind === 'parameter_group')
      .flatMap(group => nodeChildren(group));
    const fieldsBySource = new Map((schema.fields || [])
      .filter(field => String(field.path || '').startsWith('parameters.values.'))
      .map(field => [field.path.slice('parameters.values.'.length), field]));
    const rows = parameterNodes.map(parameter => {
      const field = fieldsBySource.get(parameter.source_field);
      return field ? {parameter, field} : null;
    }).filter(Boolean);
    if (!rows.length) {
      const empty = document.createElement('p');
      empty.className = 'p9d-empty';
      empty.textContent = node.configuration_scope === 'parent'
        ? '该单机通过所属分系统组合配置；请在所属分系统中编辑其模型参数。'
        : '当前对象没有已审定且可编辑的物理配置参数。';
      host.appendChild(empty);
      if (node.configuration_scope === 'parent' && node.parent_object_id && onSelect) {
        const parent = document.createElement('button');
        parent.type = 'button';
        parent.className = 'button button-secondary';
        parent.textContent = '查看所属分系统参数';
        parent.addEventListener('click', () => onSelect(node.parent_object_id));
        host.appendChild(parent);
      }
      return;
    }
    const groups = new Map();
    rows.forEach(row => {
      const key = row.field.group_id || 'general';
      if (!groups.has(key)) groups.set(key, {title: row.field.group_title || '物理配置参数', rows: []});
      groups.get(key).rows.push(row);
    });
    groups.forEach(group => {
      const section = document.createElement('section');
      section.className = 'p9d-parameter-group';
      const heading = document.createElement('h3');
      heading.textContent = group.title;
      section.appendChild(heading);
      group.rows.forEach(({parameter, field}) => {
        const label = document.createElement('label');
        label.className = 'p9d-parameter';
        const title = document.createElement('span');
        title.className = 'p9d-parameter-title';
        title.textContent = parameter.display_name_zh || field.label || field.path;
        const unit = document.createElement('span');
        unit.className = 'p9d-unit';
        unit.textContent = parameter.unit || field.unit || '';
        title.appendChild(unit);
        const current = formData?.parameters?.values?.[parameter.source_field];
        const value = parameter.array_index == null ? current : current?.[parameter.array_index];
        let input;
        if (field.widget === 'checkbox') {
          input = document.createElement('input');
          input.type = 'checkbox';
          input.checked = Boolean(value);
        } else if (field.widget === 'select') {
          input = document.createElement('select');
          (field.enum || []).forEach(optionValue => {
            const option = document.createElement('option');
            option.value = String(optionValue);
            option.textContent = field.enum_labels?.[String(optionValue)] || String(optionValue);
            input.appendChild(option);
          });
          input.value = String(value ?? field.default ?? '');
        } else if (['object','object_editor','array'].includes(field.widget) || String(field.widget).startsWith('array[')) {
          input = document.createElement('textarea');
          input.rows = 2;
          input.spellcheck = false;
          input.value = value == null ? '' : JSON.stringify(value);
        } else {
          input = document.createElement('input');
          input.type = parameter.array_index != null || ['number','integer'].includes(field.type) ? 'number' : 'text';
          input.step = field.type === 'integer' ? '1' : 'any';
          if ((parameter.minimum ?? field.minimum) != null) input.min = String(parameter.minimum ?? field.minimum);
          if ((parameter.maximum ?? field.maximum) != null) input.max = String(parameter.maximum ?? field.maximum);
          input.placeholder = ['number_or_array[number]','vector3_or_number'].includes(field.widget) ? '输入单值，或 [1, 2, 3]' : '';
          input.value = Array.isArray(value) ? JSON.stringify(value) : (value ?? '');
        }
        input.className = 'p9d-parameter-input';
        input.setAttribute('aria-label', title.textContent);
        const commit = () => {
          let next;
          if (input.type === 'checkbox') next = input.checked;
          else if (input.type === 'number') next = input.value === '' ? null : Number(input.value);
          else if (field.widget === 'select') next = input.value;
          else if (['object','object_editor','array','number_or_array[number]','vector3_or_number'].includes(field.widget) || String(field.widget).startsWith('array[')) {
            const raw = input.value.trim();
            if (!raw) next = null;
            else {
              try { next = JSON.parse(raw); }
              catch (error) {
                if (['number_or_array[number]','vector3_or_number'].includes(field.widget)) {
                  next = Number(raw);
                  if (!Number.isFinite(next)) throw new Error('请输入数值或合法 JSON 数组。');
                } else throw new Error('请输入合法 JSON。');
              }
            }
          } else next = input.value;
          onChange(field.path, parameter.array_index, next);
        };
        input.addEventListener('change', commit);
        if (input.tagName === 'TEXTAREA' || input.type === 'text') input.addEventListener('blur', commit);
        const meta = document.createElement('span');
        meta.className = 'p9d-parameter-meta';
        const defaultValue = parameter.default ?? field.default;
        const range = [parameter.minimum ?? field.minimum, parameter.maximum ?? field.maximum];
        const parts = [];
        if (defaultValue !== undefined) parts.push('默认 ' + (Array.isArray(defaultValue) ? JSON.stringify(defaultValue) : String(defaultValue)));
        if (range.some(item => item !== undefined)) parts.push('范围 ' + (range[0] ?? '不限') + ' – ' + (range[1] ?? '不限'));
        meta.textContent = parts.join(' · ');
        const help = document.createElement('span');
        help.className = 'p9d-parameter-help';
        help.textContent = parameter.description_zh || field.description || '参数说明由当前能力 Schema 提供。';
        label.append(title, input, meta, help);
        section.appendChild(label);
      });
      host.appendChild(section);
    });
  }

  function render(options) {
    const treeHost = byId('p9dObjectTree');
    const parameterHost = byId('p9dParameterHost');
    if (!treeHost || !parameterHost) return;
    const tree = options.tree;
    const roots = tree?.roots || [];
    renderTree(treeHost, roots, options.selectedId, options.onSelect);
    const node = findNode(roots, options.selectedId);
    const title = byId('p9dSelectedTitle');
    const summary = byId('p9dSelectedSummary');
    const status = byId('p9dValidationStatus');
    if (title) title.textContent = node?.name_zh || '未选择对象';
    if (summary) summary.textContent = node?.summary_zh || (node?.kind === 'equipment'
      ? '该节点表示模型类别，不代表整星实装数量。'
      : '参数仅显示当前对象真实 Schema 中已登记的字段。');
    if (status) {
      status.textContent = options.validated ? '已通过正式校验' : '配置尚未校验';
      status.dataset.validated = options.validated ? 'true' : 'false';
    }
    renderParameters(parameterHost, {node, schema: options.schema, formData: options.formData, onChange: options.onChange, onSelect: options.onSelect});
  }

  root.P9DObjectConfigPanel = Object.freeze({render});
})(window);
