// Run with: node --test tests/browser/mention_alerts.cjs (no browser dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/mention-alerts.js'), 'utf8');

function setup(mode = 'browser', permission = 'granted', supported = true) {
    const events = {}, controls = {}, alerts = [], sounds = [], channels = [], storage = { 'agentchattr-mention-alerts': mode };
    let requests = 0;
    for (const id of ['setting-mention-alerts', 'mention-alerts-status']) {
        controls[id] = { addEventListener(type, fn) { this[type] = fn; } };
    }
    function Notification(title, options) {
        this.title = title; this.options = options;
        this.close = () => { this.closed = true; };
        alerts.push(this);
    }
    Notification.permission = permission;
    Notification.requestPermission = async () => { requests++; Notification.permission = 'granted'; };
    const document = { hidden: true, title: 'Team chat', hasFocus: () => false,
        getElementById: id => controls[id] || null,
        addEventListener: (type, fn) => { events[type] = fn; } };
    const window = { addEventListener: (type, fn) => { events[type] = fn; },
        focus: () => { window.focused = true; }, switchChannel: ch => channels.push(ch),
        playNotificationSound: sender => sounds.push(sender) };
    if (supported) window.Notification = Notification;
    const context = vm.createContext({ window, document, Notification,
        localStorage: { getItem: k => storage[k], setItem: (k, v) => { storage[k] = v; } } });
    vm.runInContext(source, context);
    events.DOMContentLoaded();
    return { api: window.MentionAlerts, alerts, sounds, channels, window, document, events, controls,
        Notification, requests: () => requests, context, storage };
}
const msg = (extra = {}) => ({ id: 42, sender: 'Claude', type: 'chat', channel: 'work', text: '@Derek please review', ...extra });

test('case-insensitive complete mentions, punctuation, @all, escaped and Unicode names', () => {
    const { mentions } = setup().api;
    for (const text of ['@DEREK', 'Hello, @Derek!', '(@all)', '@All agents']) assert.ok(mentions(text, 'Derek'), text);
    for (const text of ['@Derek2', '@Derek-1', '@Derek_more', 'mail@Derek', '@@Derek', '@alligator', '@all-agents']) assert.equal(mentions(text, 'Derek'), false, text);
    assert.ok(mentions('@a.b+', 'a.b+'));
    assert.ok(mentions('@José!', 'José'));
    assert.equal(mentions('@José2', 'José'), false);
    assert.equal(mentions('@all', ''), false);
});

test('default off, replay, self, non-chat, focused and unmentioned messages never notify', () => {
    assert.equal(setup('off').api.notify(msg(), true, 'Derek'), false);
    const s = setup();
    for (const [message, live] of [[msg(), false], [msg({ sender: 'DEREK' }), true],
        [msg({ type: 'join' }), true], [msg({ type: 'leave' }), true], [msg({ type: 'summary' }), true],
        [msg({ text: 'hello' }), true]]) assert.equal(s.api.notify(message, live, 'Derek'), false);
    s.document.hidden = false; s.document.hasFocus = () => true;
    assert.equal(s.api.notify(msg(), true, 'Derek'), false);
    assert.equal(s.alerts.length, 0);
});

test('background notification payload, dedupe, plain preview, focus navigation and title reset', () => {
    const s = setup();
    s.document.hidden = false; // Unfocused visible windows also qualify.
    s.api.notify(msg({ text: '@Derek **review** [link](https://example.org) <b>now</b> ' + 'x'.repeat(200) }), true, 'Derek');
    s.api.notify(msg(), true, 'Derek');
    assert.equal(s.alerts.length, 1);
    assert.equal(s.alerts[0].title, '[#work] Claude mentioned you');
    assert.equal(s.alerts[0].options.tag, 'agentchattr-mention-42');
    assert.equal(s.alerts[0].options.body.length, 120);
    assert.ok(s.alerts[0].options.body.startsWith('@Derek review link now'));
    assert.equal(s.sounds.length, 0);
    assert.equal(s.document.title, '(1) Mentioned in #work · agentchattr');
    s.api.notify(msg({ id: 43 }), true, 'Derek');
    assert.ok(s.document.title.startsWith('(2)'));
    s.alerts[0].onclick();
    assert.equal(s.window.focused, true);
    assert.deepEqual(s.channels, ['work']);
    assert.equal(s.alerts[0].closed, true);
    assert.equal(s.document.title, 'Team chat');
});

test('permission is requested only on selection; preference persists and sound is opt-in', async () => {
    const s = setup('off', 'default');
    assert.equal(s.requests(), 0);
    await s.controls['setting-mention-alerts'].change({ target: { value: 'sound' } });
    assert.equal(s.requests(), 1);
    assert.equal(s.storage['agentchattr-mention-alerts'], 'sound');
    s.api.notify(msg(), true, 'Derek');
    assert.deepEqual(s.sounds, ['Claude']);
    s.events.focus();
    assert.equal(s.document.title, 'Team chat');
});

test('blocked, unsupported, dismissed, and failed notifications show status', async () => {
    const blocked = setup('browser', 'denied');
    assert.match(blocked.controls['mention-alerts-status'].textContent, /Permission blocked/);
    blocked.api.notify(msg(), true, 'Derek');
    assert.equal(blocked.alerts.length, 0);
    assert.equal(blocked.requests(), 0);
    const unsupported = setup('browser', 'default', false);
    assert.equal(unsupported.controls['setting-mention-alerts'].disabled, true);
    assert.match(unsupported.controls['mention-alerts-status'].textContent, /Not supported/);
    const dismissed = setup('browser', 'default');
    dismissed.Notification.requestPermission = async () => 'default';
    await dismissed.controls['setting-mention-alerts'].change({ target: { value: 'browser' } });
    assert.match(dismissed.controls['mention-alerts-status'].textContent, /Permission not granted/);
    dismissed.Notification.requestPermission = async () => { throw Error('blocked'); };
    await dismissed.controls['setting-mention-alerts'].change({ target: { value: 'browser' } });
    assert.match(dismissed.controls['mention-alerts-status'].textContent, /could not enable/);
});

test('actual message dispatcher keeps history silent and avoids duplicate alert audio', () => {
    const s = setup('sound');
    const chat = fs.readFileSync(path.join(root, 'static/chat.js'), 'utf8');
    const start = chat.indexOf('function handleServerEvent(event) {');
    const end = chat.indexOf('\nfunction ', start + 1);
    Object.assign(s.context, { historyLoading: false, pendingLiveEvents: [], pendingHistory: [],
        scheduleHistoryRender() {}, Hub: { emit() {} }, soundEnabled: true, username: 'Derek',
        appendMessage() {}, playNotificationSound: s.window.playNotificationSound });
    vm.runInContext(chat.slice(start, end), s.context);
    vm.runInContext('handleServerEvent({type:"history", messages:[{id:42,text:"@Derek"}]})', s.context);
    assert.equal(s.alerts.length, 0);
    s.context.soundEnabled = false;
    s.context.event = { type: 'message', data: msg() };
    vm.runInContext('handleServerEvent(event)', s.context);
    assert.equal(s.alerts.length, 0);
    s.context.soundEnabled = true;
    vm.runInContext('handleServerEvent(event)', s.context);
    assert.equal(s.alerts.length, 1);
    assert.deepEqual(s.sounds, ['Claude']);
});

test('decision prompts alert; blocked and failed notifications preserve one legacy sound', () => {
    for (const mode of ['browser', 'sound']) {
        for (const failure of ['denied', 'default', 'throw']) {
            const s = setup(mode, failure === 'throw' ? 'granted' : failure);
            if (failure === 'throw') {
                const Broken = function () { throw Error('desktop unsupported'); };
                Broken.permission = 'granted';
                s.window.Notification = s.context.Notification = Broken;
            }
            const chat = fs.readFileSync(path.join(root, 'static/chat.js'), 'utf8');
            const start = chat.indexOf('function handleServerEvent(event) {');
            Object.assign(s.context, { historyLoading: false, Hub: { emit() {} }, soundEnabled: true,
                username: 'Derek', appendMessage() {}, playNotificationSound: s.window.playNotificationSound,
                event: { type: 'message', data: msg({ type: 'decision' }) } });
            vm.runInContext(chat.slice(start, chat.indexOf('\nfunction ', start + 1)), s.context);
            vm.runInContext('handleServerEvent(event); handleServerEvent(event)', s.context);
            assert.equal(s.alerts.length, 0);
            assert.deepEqual(s.sounds, ['Claude'], mode + ' ' + failure);
        }
    }
    const s = setup('browser');
    assert.equal(s.api.notify(msg({ type: 'decision' }), true, 'Derek'), true);
    assert.equal(s.alerts.length, 1);
});
