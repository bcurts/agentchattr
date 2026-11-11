// Run with: node --test tests/browser/scroll_anchor.cjs (no browser dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/chat.js'), 'utf8');
function section(start, end) {
    const offset = source.indexOf(start);
    const limit = source.indexOf(end, offset);
    assert.ok(offset >= 0 && limit > offset, `Missing source section: ${start}`);
    return source.slice(offset, limit);
}
const functions = section('function scrollToBottom(', '// --- Agents ---')
    + section('function setupScroll()', '// --- Reply ---');

function setup() {
    const classes = new Set(['hidden']);
    const badge = { style: {} };
    const anchor = {
        classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
        style: {}, querySelector: () => badge,
    };
    const events = {}, observers = [], messages = {}, contentArea = {};
    let jumps = 0;
    const timeline = {
        clientHeight: 500, scrollHeight: 2000, scrollTop: 900,
        addEventListener: (name, fn) => { events[name] = fn; },
        getBoundingClientRect: () => ({ left: 0, width: 900 }),
        scrollTo({ top }) {
            jumps++;
            this.scrollTop = Math.min(top, this.scrollHeight - this.clientHeight);
        },
    };
    const context = vm.createContext({
        autoScroll: false, unreadCount: 3, historyLoading: false,
        appendMessage() {}, dayFloatOnScroll() {},
        window: { addEventListener() {} },
        document: {
            getElementById: id => ({ timeline, messages, 'scroll-anchor': anchor }[id]),
            querySelector: selector => selector === 'footer' ? { offsetHeight: 100 } : contentArea,
        },
        ResizeObserver: class {
            constructor(fn) { this.fn = fn; }
            observe(element) { observers.push({ element, fn: this.fn }); }
        },
    });
    vm.runInContext(functions + ';setupScroll();', context);
    const run = code => vm.runInContext(code, context);
    function resize(element) {
        const observed = observers.filter(entry => entry.element === element);
        assert.ok(observed.length, 'Element must be observed for resize');
        for (const entry of observed) entry.fn();
    }
    return { timeline, messages, context, badge, events, run, resize,
        hidden: () => classes.has('hidden'), jumps: () => jumps };
}

test('arrow requires strictly more than one viewport below the current position', () => {
    const s = setup();
    for (const [distance, hidden] of [[0, true], [60, true], [499, true], [500, true], [501, false], [1000, false]]) {
        s.timeline.scrollTop = s.timeline.scrollHeight - s.timeline.clientHeight - distance;
        s.run('updateScrollAnchor()');
        assert.equal(s.hidden(), hidden, `distance ${distance}`);
    }
    assert.equal(s.badge.textContent, 3);
    assert.equal(s.badge.style.display, 'flex');
});

test('viewport growth hides the arrow and shrinking restores it without a scroll event', () => {
    const s = setup();
    s.run('updateScrollAnchor()');
    assert.equal(s.hidden(), false); // 600px below a 500px viewport
    s.timeline.clientHeight = 700;
    s.resize(s.timeline);
    assert.equal(s.hidden(), true); // 400px below a 700px viewport
    s.timeline.clientHeight = 500;
    s.resize(s.timeline);
    assert.equal(s.hidden(), false);
    assert.equal(s.timeline.scrollTop, 900);
    assert.equal(s.jumps(), 0);
});

test('content growth and shrinkage refresh visibility while scrolled up', () => {
    const s = setup();
    s.timeline.scrollTop = 1100;
    s.run('updateScrollAnchor()');
    assert.equal(s.hidden(), true); // 400px below
    s.timeline.scrollHeight = 2200;
    s.resize(s.messages);
    assert.equal(s.hidden(), false); // image load adds 200px
    s.timeline.scrollHeight = 2000;
    s.resize(s.messages);
    assert.equal(s.hidden(), true);
    assert.equal(s.timeline.scrollTop, 1100);
    assert.equal(s.jumps(), 0);
});

test('resize keeps bottom pinning and clears unread count when auto-scroll is active', () => {
    const s = setup();
    s.context.autoScroll = true;
    s.timeline.scrollHeight = 2400;
    s.resize(s.messages);
    assert.equal(s.timeline.scrollTop, 1900);
    s.timeline.clientHeight = 700;
    s.resize(s.timeline);
    assert.equal(s.timeline.scrollTop, 1700);
    assert.equal(s.context.unreadCount, 0);
    assert.equal(s.hidden(), true);
    assert.equal(s.jumps(), 2);
});

test('loading history suppresses bottom pinning during resize', () => {
    const s = setup();
    s.context.autoScroll = true;
    s.context.historyLoading = true;
    s.resize(s.messages);
    s.resize(s.timeline);
    assert.equal(s.timeline.scrollTop, 900);
    assert.equal(s.context.unreadCount, 3);
    assert.equal(s.hidden(), true);
    assert.equal(s.jumps(), 0);
});

test('arrow click jumps to bottom, clears unread count and resumes scroll tracking', () => {
    const s = setup();
    const html = fs.readFileSync(path.join(__dirname, '../../static/index.html'), 'utf8');
    const click = html.match(/id="scroll-anchor"[^>]*onclick="([^"]+)"/);
    assert.ok(click, 'Arrow must have a click handler');
    s.run('updateScrollAnchor()');
    assert.equal(s.hidden(), false);
    s.run(click[1]);
    assert.equal(s.timeline.scrollTop, 1500);
    assert.equal(s.hidden(), true);
    assert.equal(s.context.unreadCount, 0);
    s.events.scroll();
    assert.equal(s.context.autoScroll, true);
});
