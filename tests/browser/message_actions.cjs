const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('static/chat.js', 'utf8');
const section = src.slice(src.indexOf('const MESSAGE_ACTION_ICONS'), src.indexOf('function todoCycle(msgId)'));
const update = src.slice(src.indexOf('function updateTodoState(msgId, status)'), src.indexOf('// --- Delete mode ---'));

test('pin states update the icon, hover tooltip and accessible label', () => {
  const attrs = {};
  const button = {title: '', innerHTML: '', setAttribute: (name, value) => { attrs[name] = value; }};
  const message = {classList: {add() {}, remove() {}}, querySelector: () => button};
  const document = {querySelector: () => message, getElementById: () => null};
  const ctx = {document};
  vm.runInNewContext(section + update, ctx);
  for (const [status, label] of [[null, 'Pin message'], ['todo', 'Mark pinned message done'], ['done', 'Unpin message']]) {
    ctx.updateTodoState(1, status);
    assert.equal(button.title, label);
    assert.equal(attrs['aria-label'], label);
    assert.match(button.innerHTML, /<svg/);
    assert.doesNotMatch(button.innerHTML, />\s*(pin|done|unpin)\s*</i);
  }
});

test('message action markup uses labeled icons for reply and delete', () => {
  assert.equal((src.match(/aria-label="Reply">\$\{messageActionIcon\('reply'\)\}/g) || []).length, 3);
  assert.equal((src.match(/aria-label="Delete">\$\{messageActionIcon\('delete'\)\}/g) || []).length, 3);
});
