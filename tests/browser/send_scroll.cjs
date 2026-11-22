// Run with: node --test tests/browser/send_scroll.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/chat.js'), 'utf8');
const start = source.indexOf('function sendMessage() {');
const end = source.indexOf('// --- File and image paste/drop ---', start);
assert.ok(start >= 0 && end > start);
const sendMessage = source.slice(start, end);

function setup({ text = 'hello', open = true, uploads = 0, throws = false } = {}) {
    const input = { value: text, style: {}, focus() {}, setSelectionRange() {}, dispatchEvent() {} };
    const status = { textContent: '' }, sent = [];
    let jumps = 0;
    const ctx = vm.createContext({
        document: { getElementById(id) { return id === 'input' ? input : status; } },
        channelUploads: uploads, pendingAttachments: [], activeMentions: new Set(),
        username: 'Derek', activeChannel: 'general', replyingTo: null,
        ws: { readyState: open ? 1 : 0, send(value) { if (throws) throw new Error('send failed'); sent.push(JSON.parse(value)); } },
        WebSocket: { OPEN: 1 }, SLASH_COMMANDS: [], autoScroll: false,
        scrollToBottom() { jumps++; }, restartVoiceAfterEdit() {}, clearAttachments() {}, cancelReply() {}, updateSendButton() {},
    });
    vm.runInContext(sendMessage, ctx);
    return { ctx, input, status, sent, jumps: () => jumps };
}

test('sending a message resumes auto-scroll and jumps to the latest message', () => {
    const s = setup();
    s.ctx.sendMessage();
    assert.equal(s.sent[0].text, 'hello');
    assert.equal(s.ctx.autoScroll, true);
    assert.equal(s.jumps(), 1);
    assert.equal(s.input.value, '');
});

test('empty, uploading, disconnected, and failed sends do not change the reading position', () => {
    for (const options of [{ text: ' ' }, { uploads: 1 }, { open: false }, { throws: true }]) {
        const s = setup(options);
        if (options.throws) assert.throws(() => s.ctx.sendMessage(), /send failed/);
        else s.ctx.sendMessage();
        assert.equal(s.ctx.autoScroll, false);
        assert.equal(s.jumps(), 0);
        if (options.text !== ' ') assert.equal(s.input.value, 'hello');
    }
});

test('summary without a tag shows the mention hint and sends nothing', () => {
    const commands = source.slice(source.indexOf('const SLASH_COMMANDS = ['), source.indexOf('\n];', source.indexOf('const SLASH_COMMANDS = [')) + 3);
    const parsed = vm.runInNewContext(commands + '\nSLASH_COMMANDS');
    for (const text of ['/summary', '/summarise']) {
        const s = setup({ text });
        s.ctx.SLASH_COMMANDS = parsed;
        s.ctx.Event = class Event {};
        let hint = '';
        s.ctx.showSlashHint = value => { hint = value; };
        s.ctx.sendMessage();
        assert.equal(s.sent.length, 0);
        assert.equal(s.input.value, text + ' @');
        assert.match(hint, /Tag an agent/);
    }
});
