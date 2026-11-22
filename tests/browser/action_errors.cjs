const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('HTTP and network errors show literal details and paths until dismissed', async () => {
  const nodes = Object.fromEntries(['action-error', 'action-error-title', 'action-error-detail', 'action-error-path'].map(id => [id, {textContent: '', hidden: true}]));
  const context = {window: {}, document: {getElementById: id => nodes[id]}};
  vm.runInNewContext(fs.readFileSync('static/action-errors.js', 'utf8'), context);
  let error;
  try { await context.window.requireOk({ok: false, status: 500, json: async () => ({error: '<denied>', path: 'C:\\data\\jobs.json'})}); } catch (e) { error = e; }
  context.window.showActionError('Could not create job', error);
  assert.equal(nodes['action-error'].hidden, false);
  assert.equal(nodes['action-error-detail'].textContent, 'Details: <denied>');
  assert.equal(nodes['action-error-path'].textContent, 'File: C:\\data\\jobs.json');
  context.window.showActionError('Could not send', new Error('Network failed'));
  assert.equal(nodes['action-error-path'].textContent, '');
  assert.match(nodes['action-error-detail'].textContent, /Network failed/);
});
