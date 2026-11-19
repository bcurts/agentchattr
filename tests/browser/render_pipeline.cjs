// Run with: node --test tests/browser/render_pipeline.cjs
// Runs the real renderMarkdown pipeline from static/chat.js with a real `marked` parser
// (tests/fixtures/marked-12.0.2.min.js, the page itself loads marked from a CDN). There is no DOM:
// a minimal `document` stub covers the entity decoding in isSafeLinkHref, so this checks the HTML
// string the pipeline produces, not how a browser renders it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/chat.js'), 'utf8').replace(/\r\n/g, '\n');
const FUNCTIONS = ['isSafeLinkHref', 'sanitizeLinkHrefs', 'renderMarkdown', 'mapTextOutsideSkipped', 'linkifyUrls',
    'pathSpaceSource', 'windowsPathSource', 'unixPathSource', 'uncPathSource', 'protectedPathSource',
    'linkifyPaths', 'colorMentions', 'styleHashtags'];
const pick = (re, label) => { const m = source.match(re); assert.ok(m, `${label} not found in chat.js`); return m[0]; };
const code = [pick(/const SAFE_LINK_SCHEMES[^\n]*/, 'SAFE_LINK_SCHEMES'), pick(/const TEXT_SKIP_TAGS[^\n]*/, 'TEXT_SKIP_TAGS'),
    ...FUNCTIONS.map((n) => pick(new RegExp(`function ${n}\\([\\s\\S]*?\\n}\\n`), n))].join('\n');

function makeRenderer(platform) {
    const ctx = vm.createContext({
        console, URL,
        document: { createElement: () => { let raw = ''; return { set innerHTML(v) { raw = v; }, get value() { return raw; } }; } },
        resolveAgent: (name) => (name === 'codex' ? 'codex' : null),
        getColor: () => '#abc',
        serverPlatform: platform,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../fixtures/marked-12.0.2.min.js'), 'utf8'), ctx);
    vm.runInContext('marked.setOptions({ breaks: true, gfm: true });', ctx);
    vm.runInContext(code, ctx);
    // The same post-processing order as the message renderer.
    return (text) => ctx.styleHashtags(ctx.renderMarkdown(text));
}
const bare = (html) => html.replace(/ onclick="[^"]*"| title="Open in file manager"| class="file-link"| href="#"/g, '');
const render = { win32: makeRenderer('win32'), linux: makeRenderer('linux') };

test('image alt text is never rewritten', () => {
    for (const [platform, alt] of [['win32', 'inspect C:/Users/DL/file.txt'], ['linux', 'see /tmp/file.txt #ballast @codex https://x.example']]) {
        const out = render[platform](`![${alt}](https://example.com/i.png)`);
        assert.equal(out, `<img src="https://example.com/i.png" alt="${alt}">`);
    }
});

test('inline and fenced code stay literal', () => {
    for (const platform of ['win32', 'linux']) {
        const inline = render[platform]('`@codex #ballast https://example.com C:\\Users\\DL\\file.txt /tmp/file.txt`');
        assert.equal(inline, '<code>@codex #ballast https://example.com C:\\Users\\DL\\file.txt /tmp/file.txt</code>');
        const fenced = render[platform]('```\n@codex #ballast https://example.com\nC:\\Users\\DL\\f.txt\n```');
        assert.doesNotMatch(fenced, /<(?:a|span)\b/);
    }
});

test('text next to code is still styled', () => {
    const out = render.win32('`x` then @codex #ballast https://a.com C:\\a\\b');
    assert.match(out, /class="mention"/);
    assert.match(out, /class="msg-hashtag">#ballast</);
    assert.match(out, /<a href="https:\/\/a\.com"/);
    assert.match(out, /<a [^>]*>C:\\a\\b<\/a>/);
});

test('UNC paths, with or without spaces, keep backslash sequences like \\new and \\tests', () => {
    for (const raw of ['\\\\server\\new\\tests', '\\\\server\\my docs\\new\\tests']) {
        for (const platform of ['win32', 'linux']) assert.equal(render[platform](raw), raw, raw);
    }
});

test('literal NUL placeholders in a message cannot collide with internal ones', () => {
    for (const raw of ['\x00P0\x00', '\x00C0\x00', 'a \x00C0\x00 `code` C:\\x\\y']) {
        const out = render.win32(raw);
        assert.doesNotMatch(out, /undefined/);
        assert.doesNotMatch(out, /\x00/);
    }
});

test('path boundaries through the full pipeline', () => {
    assert.equal(bare(render.win32('in C:\\Users\\DL\\Github 2. start the localhost dashboard')),
        'in <a>C:\\Users\\DL\\Github</a> 2. start the localhost dashboard');
    assert.equal(bare(render.win32('open C:\\Program Files and C:\\x\\y.')),
        'open <a>C:\\Program Files</a> and <a>C:\\x\\y</a>.');
    assert.equal(bare(render.linux('/home/u/f then read /tmp/o')), '<a>/home/u/f</a> then read <a>/tmp/o</a>');
});

test('markdown links keep their text and open in a new tab; emails are not mentions', () => {
    const out = render.win32('[C:\\q\\r](https://example.com) mail a@b.com');
    assert.match(out, /<a href="https:\/\/example\.com" target="_blank" rel="noopener noreferrer">C:\\q\\r<\/a>/);
    assert.doesNotMatch(out, /class="mention"/);
});
