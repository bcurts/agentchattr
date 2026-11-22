// Run with: node --test tests/browser/mention_selection.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/chat.js'), 'utf8');
const code = source.slice(source.indexOf('// --- Mention toggles ---'), source.indexOf('// --- Voice typing ---'));

class Element {
    constructor() { this.children = []; this.dataset = {}; this.className = ''; this.style = { setProperty() {} }; this.attrs = {}; }
    set innerHTML(value) { if (!value) this.children = []; }
    appendChild(child) { this.children.push(child); }
    setAttribute(key, value) { this.attrs[key] = value; }
    querySelectorAll(selector) { return this.children.filter(c => c.classList.contains(selector.slice(1))); }
    get classList() {
        return {
            contains: name => this.className.split(' ').includes(name),
            add: name => { if (!this.classList.contains(name)) this.className += ' ' + name; },
            remove: name => { this.className = this.className.split(' ').filter(c => c !== name).join(' '); },
            toggle: (name, on) => { if (on) this.classList.add(name); else this.classList.remove(name); },
        };
    }
}

test('double-click selects only the target, persists per channel, and preserves single-click toggling', () => {
    const container = new Element();
    const context = vm.createContext({
        window: {}, activeChannel: 'one', activeMentions: new Set(), _channelMentions: {}, _agentConfigReady: true,
        agentConfig: { agy: {color: 'purple'}, claude: {color: 'orange'}, codex: {color: 'green'} }, colorOverrides: {},
        document: {
            getElementById: id => id === 'mention-toggles' ? container : container.children.find(c => c.id === id),
            createElement: () => new Element(), querySelectorAll: selector => container.querySelectorAll(selector),
        },
        persistMentions(channel) { if (channel) context._channelMentions[channel] = [...context.activeMentions]; },
        updateSchedulePopoverState() {}, enableDragScroll() {},
    });
    vm.runInContext(code, context);
    context.buildMentionToggles();
    const button = name => container.children.find(c => c.dataset.agent === name);
    const all = container.children.find(c => c.id === 'mention-all-toggle');
    all.onclick();
    const target = button('codex');
    // Browsers dispatch two clicks before dblclick.
    target.onclick(); target.onclick(); target.ondblclick();
    assert.deepEqual([...context.activeMentions], ['codex']);
    assert.deepEqual([...context._channelMentions.one], ['codex']);
    assert.equal(all.attrs['aria-pressed'], 'false');
    assert.equal(button('agy').classList.contains('active'), false);
    assert.equal(target.classList.contains('active'), true);
    context.window._onChannelSwitchMentions('one', 'two');
    assert.equal(context.activeMentions.size, 0);
    context.window._onChannelSwitchMentions('two', 'one');
    assert.deepEqual([...context.activeMentions], ['codex']);
    button('claude').onclick();
    assert.deepEqual([...context.activeMentions].sort(), ['claude', 'codex']);
    target.onclick();
    assert.deepEqual([...context.activeMentions], ['claude']);
});
