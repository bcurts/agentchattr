const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('log warning shows literal error and full path, then clears on recovery', () => {
    const source = fs.readFileSync('static/chat.js', 'utf8');
    const fn = source.match(/function renderLogStatus\(data\) \{[\s\S]*?\n\}/)[0];
    const nodes = {'log-warning': {}, 'log-warning-path': {}, 'log-warning-error': {}};
    const ctx = vm.createContext({document: {getElementById: id => nodes[id]}});
    vm.runInContext(fn, ctx);
    ctx.renderLogStatus({path: 'C:\\data\\log.jsonl', error: '<Permission denied>'});
    assert.equal(nodes['log-warning'].hidden, false);
    assert.equal(nodes['log-warning-path'].textContent, 'C:\\data\\log.jsonl');
    assert.equal(nodes['log-warning-error'].textContent, '<Permission denied>');
    ctx.renderLogStatus({path: 'C:\\data\\log.jsonl', error: null});
    assert.equal(nodes['log-warning'].hidden, true);
});
