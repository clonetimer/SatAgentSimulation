'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {setProperty() {}};
    this.attributes = {}; this.listeners = {}; this.className = ''; this.textContent = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = value; }
  click() { this.listeners.click?.(); }
}
test('P9-D renders real object hierarchy and binds parameter edits to schema paths', () => {
  const ids = ['p9dObjectTree','p9dSelectedTitle','p9dSelectedSummary','p9dValidationStatus','p9dParameterHost'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element('div')]));
  const document = {
    getElementById: id => elements[id] || null,
    createElement: tag => new Element(tag),
  };
  const window = {};
  const source = fs.readFileSync('src/sat_sim/web/object-config-panel.js', 'utf8');
  vm.runInNewContext(source, {window, document, JSON, Boolean, String, Array});
  const changes = [];
  const tree = {schema_version:'workbench-object-tree.v2', roots:[{
    object_id:'whole_spacecraft', name_zh:'整星', kind:'whole_spacecraft', children:[{
      object_id:'subsystem.adcs', name_zh:'姿态控制分系统', kind:'subsystem', children:[{
        node_id:'component.reaction_wheel:physical-parameters', kind:'parameter_group',
        name_zh:'物理配置参数', children:[{
          node_id:'component.reaction_wheel:parameter:wheel_inertia_kg_m2:0',
          kind:'parameter', source_field:'wheel_inertia_kg_m2', display_name_zh:'反作用轮 1 · 转动惯量',
          unit:'kg·m²', default:0.1, minimum:0.001, maximum:1, description_zh:'轮体转动惯量',
          array_index:0, owner_object_id:'component.reaction_wheel'
        }]
      }]
    }]
  }]};
  const schema = {capability:{capability_id:'component.reaction_wheel.v1'}, fields:[{
    path:'parameters.values.wheel_inertia_kg_m2', type:'array[number]', widget:'array[number]',
    default:[0.1], group_id:'mechanical', group_title:'机械参数', description:'惯量参数'
  }]};
  window.P9DObjectConfigPanel.render({
    tree, selectedId:'subsystem.adcs', schema, formData:{parameters:{values:{wheel_inertia_kg_m2:[0.1]}}},
    validated:false, onSelect:id=>changes.push(['select',id]),
    onChange:(...args)=>changes.push(['change',...args])
  });
  const buttons = elements.p9dObjectTree.children.filter(item=>item.tagName==='button');
  assert.equal(buttons.length, 2);
  buttons[1].click();
  assert.deepEqual(changes[0], ['select','subsystem.adcs']);
  assert.equal(elements.p9dSelectedTitle.textContent,'姿态控制分系统');
  assert.equal(elements.p9dValidationStatus.textContent,'配置尚未校验');
  const group = elements.p9dParameterHost.children[0];
  assert.equal(group.children[0].textContent,'机械参数');
  const field = group.children[1];
  assert.equal(field.children[0].textContent,'反作用轮 1 · 转动惯量kg·m²');
  const input = field.children[1];
  assert.equal(input.type,'number');
  assert.equal(input.min,'0.001');
  assert.equal(input.max,'1');
  input.value='0.12';
  input.listeners.change();
  assert.deepEqual(changes[1], ['change','parameters.values.wheel_inertia_kg_m2',0,0.12]);
});
