const test = require('node:test');
const assert = require('node:assert/strict');
const ChannelMentions = require('../static/channel-mentions.js');

const values = (state, channel) => [...state.get(channel)].sort();

test('each channel keeps its own selections while switching and replying', () => {
    const state = new ChannelMentions();
    state.reconcile(['general', 'a', 'b']);
    state.get('a').add('alice');
    state.get('b').add('bob');
    state.get('a').clear(); // reply changes only this channel's selection
    state.get('a').add('bob');
    assert.deepEqual(values(state, 'a'), ['bob']);
    assert.deepEqual(values(state, 'b'), ['bob']);
    assert.deepEqual(values(state, 'general'), []);
    state.get('a').add('alice');
    assert.deepEqual(values(state, 'b'), ['bob']);
});

test('agent rename and configuration pruning cover every channel', () => {
    const state = new ChannelMentions();
    state.reconcile(['a', 'b']);
    state.get('a').add('alice');
    state.get('a').add('gone');
    state.get('b').add('alice');
    state.renameAgent('ALICE', 'Carol');
    state.pruneAgents(['CAROL']);
    assert.deepEqual(values(state, 'a'), ['carol']);
    assert.deepEqual(values(state, 'b'), ['carol']);
});

test('rename survives settings before event and event before settings', () => {
    for (const settingsFirst of [true, false]) {
        const state = new ChannelMentions();
        state.reconcile(['general', 'old']);
        state.get('old').add('alice');
        if (settingsFirst) state.reconcile(['general', 'new']);
        state.renameChannel('old', 'new');
        if (!settingsFirst) state.reconcile(['general', 'new']);
        assert.deepEqual(values(state, 'new'), ['alice']);
        assert.deepEqual(values(state, 'general'), []);
    }
});

test('deleted names are fresh when recreated, including after a rename', () => {
    const state = new ChannelMentions();
    state.reconcile(['general', 'old']);
    state.get('old').add('alice');
    state.reconcile(['general']);
    state.reconcile(['general', 'old']);
    assert.deepEqual(values(state, 'old'), []);
    state.get('old').add('bob');
    state.reconcile(['general', 'new']);
    state.renameChannel('old', 'new');
    assert.deepEqual(values(state, 'new'), ['bob']);
    state.reconcile(['general', 'new', 'old']);
    assert.deepEqual(values(state, 'old'), []);
});

test('renaming and pruning agents while a channel is awaiting rename', () => {
    const state = new ChannelMentions();
    state.reconcile(['old']);
    state.get('old').add('alice');
    state.get('old').add('gone');
    state.reconcile(['new']);
    state.renameAgent('alice', 'bob');
    state.pruneAgents(['bob']);
    state.renameChannel('old', 'new');
    assert.deepEqual(values(state, 'new'), ['bob']);
});

test('agent rename survives either event order and keeps the active Set reference', () => {
    for (const configFirst of [true, false]) {
        const state = new ChannelMentions();
        state.reconcile(['a', 'b', 'c']);
        state.pruneAgents(['claude-2', 'other']);
        const active = state.get('a');
        active.add('claude-2');
        state.get('b').add('claude-2');
        state.get('c').add('other');
        if (configFirst) {
            state.pruneAgents(['planner', 'other']);
            assert.deepEqual([...active], []); // old name cannot be sent
            assert.deepEqual(values(state, 'b'), []);
            state.pruneAgents(['planner', 'other']); // repeated config is safe
        }
        state.renameAgent('claude-2', 'planner');
        if (!configFirst) state.pruneAgents(['planner', 'other']);
        assert.strictEqual(state.get('a'), active);
        assert.deepEqual([...active], ['planner']);
        assert.deepEqual(values(state, 'b'), ['planner']);
        assert.deepEqual(values(state, 'c'), ['other']);
    }
});

test('agent tombstones cannot leak into a deleted and recreated channel', () => {
    const state = new ChannelMentions();
    state.reconcile(['a', 'b']);
    state.get('a').add('claude-2');
    state.get('b').add('claude-2');
    state.pruneAgents(['planner']);
    state.reconcile(['b']);
    state.reconcile(['a', 'b']);
    state.renameAgent('claude-2', 'planner');
    assert.deepEqual(values(state, 'a'), []);
    assert.deepEqual(values(state, 'b'), ['planner']);
});

test('an agent reappearing under the old name does not revive its removed selection', () => {
    const state = new ChannelMentions();
    state.reconcile(['a']);
    state.get('a').add('old');
    state.pruneAgents(['new']);
    state.pruneAgents(['old', 'new']);
    state.renameAgent('old', 'new');
    assert.deepEqual(values(state, 'a'), []);
});
