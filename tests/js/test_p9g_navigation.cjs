const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/index.html'), 'utf8');

function primaryNavButtons() {
  const nav = html.match(/<nav class="sidebar-nav"[\s\S]*?<\/nav>/)?.[0] || '';
  const primary = nav.split('<details class="sidebar-nav-group">')[0];
  return [...primary.matchAll(/<button[^>]*class="nav-button[^"]*"[^>]*data-view="([^"]+)"[^>]*>[\s\S]*?<b>([^<]+)<\/b>/g)]
    .map(match => ({view: match[1], label: match[2]}));
}

test('P9-G keeps exactly four primary workbench journeys', () => {
  const buttons = primaryNavButtons();
  assert.deepEqual(buttons.map(item => item.label), ['快速仿真', '任务中心', '卫星数字场景', '专业建模']);
  assert.deepEqual(buttons.map(item => item.view), ['wizard', 'wizard', 'scene', 'creator']);
});

test('professional V15 creator remains a distinct primary route', () => {
  const nav = html.match(/<nav class="sidebar-nav"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(nav, /data-view="creator"[^>]*>[\s\S]*?<b>专业建模<\/b>/);
  assert.match(html, /id="graphAssemblyComposer"/);
  assert.match(html, /id="assemblyModuleLibrary"/);
  assert.match(html, /id="assemblyValidateBtn"/);
  assert.match(html, /id="assemblyRunBtn"/);
  assert.match(html, /id="assemblyExportProgramBtn"/);
});

test('legacy analysis and operations remain reachable in progressive groups', () => {
  const nav = html.match(/<nav class="sidebar-nav"[\s\S]*?<\/nav>/)?.[0] || '';
  for (const view of ['experiments', 'goals', 'runs', 'evaluations', 'diagnostics', 'logs']) {
    assert.match(nav, new RegExp('data-view="' + view + '"'));
  }
  assert.match(nav, /<summary>实验与分析<\/summary>/);
  assert.match(nav, /<summary>高级工具<\/summary>/);
  assert.match(nav, /id="interactiveNavButton"/);
  assert.match(nav, /id="continuousNavButton"/);
});

test('first-load default remains the guided simulation wizard', () => {
  const nav = html.match(/<nav class="sidebar-nav"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(nav, /class="nav-button active" data-view="wizard"/);
  assert.match(html, /aria-label="快速仿真"/);
});
