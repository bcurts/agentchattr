// Behavior checks for static/channel-attention.js with stubbed browser globals. Run: node tests/channel_attention_smoke.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'channel-attention.js'), 'utf8');
let failed = false;
const check = (name, condition) => {
  console.log((condition ? 'PASS ' : 'FAIL ') + name);
  if (!condition) failed = true;
};

// One "page load": fresh module state, shared localStorage (so reloads keep read markers).
function load(store) {
  const sandbox = {
    window: { username: 'derek', activeChannel: 'general' },
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    document: { documentElement: { dataset: {} }, getElementById: () => null },
    renderChannelTabs: () => {},
    mentionsUser: t => /@derek\b/i.test(t),
    saveUiPrefs: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { CA: sandbox.window.ChannelAttention, win: sandbox.window, document: sandbox.document };
}

const store = {};
let { CA, document } = load(store);
check('off by default shows nothing', CA.reason('ibkr') === '');
CA.noteMessage({ id: 1, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'hey @derek' });
check('mention ignored in reason while off', CA.reason('ibkr') === '');

store['agentchattr-channel-attention'] = 'highlight';
CA.apply();
check('apply sets data attribute', document.documentElement.dataset.channelAttention === 'highlight');

// First ever load: old history is treated as already seen.
({ CA, document } = load(store));
CA.noteMessage({ id: 10, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'old @derek' }, { history: true });
check('first-run history mention is not flagged', CA.reason('ibkr') === '');
CA.noteMessage({ id: 11, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'hey @derek' });
check('live mention flags channel', CA.reason('ibkr').includes('unread mention'));

// Reload: the unread mention must come back from history, using the stored marker.
({ CA } = load(store));
CA.noteMessage({ id: 10, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'old @derek' }, { history: true });
CA.noteMessage({ id: 11, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'hey @derek' }, { history: true });
check('unread mention survives reload', CA.reason('ibkr').includes('unread mention'));
CA.clear('ibkr');
check('opening the channel clears it', CA.reason('ibkr') === '');
({ CA } = load(store));
CA.noteMessage({ id: 11, type: 'chat', sender: 'c2', channel: 'ibkr', text: 'hey @derek' }, { history: true });
check('read mention stays cleared after reload', CA.reason('ibkr') === '');

// Excluded cases.
CA.noteMessage({ id: 20, type: 'join', sender: 'c1', channel: 'consolez', text: '@derek' });
CA.noteMessage({ id: 21, type: 'chat', sender: 'system', channel: 'consolez', text: '@derek' });
CA.noteMessage({ id: 22, type: 'chat', sender: 'derek', channel: 'consolez', text: '@derek' });
check('join, system and own messages do not flag', CA.reason('consolez') === '');
CA.noteMessage({ id: 23, type: 'chat', sender: 'c1', channel: 'general', text: '@derek' });
check('active channel does not flag', CA.reason('general') === '');

// Waiting agent: channel comes from history after a reload.
({ CA } = load(store));
CA.noteMessage({ id: 30, type: 'chat', sender: 'c1', channel: 'consolez', text: 'working' }, { history: true });
CA.noteStatus({ c1: { needs_input: true, available: true }, c2: { needs_input: false, available: true } });
check('already-waiting agent flags its last channel after reload', CA.reason('consolez').includes('agent is waiting'));
CA.noteStatus({ c1: { needs_input: true, available: false } });
check('offline agent does not flag', CA.reason('consolez') === '');
CA.noteStatus({ c1: { needs_input: true, available: true } });
CA.noteMessage({ id: 31, type: 'chat', sender: 'c1', channel: 'consolez', text: '@derek ping' });
check('both reasons combine', CA.reason('consolez') === 'Needs your input: an agent is waiting on you; unread mention of you');
CA.clear('consolez');
check('opening clears the mention only', CA.reason('consolez') === 'Needs your input: an agent is waiting on you');

store['agentchattr-channel-attention'] = 'bogus';
check('invalid stored mode acts as off', CA.reason('consolez') === '');

process.exit(failed ? 1 : 0);
