/* Channel attention: make a channel entry glow or flash when your input is needed there.
 * Two signals: an agent waiting on you (permission/question prompt), and an unread @mention
 * from a person or agent. Off by default; synced across browsers through ui_prefs. */
(function () {
    'use strict';
    const STORAGE_KEY = 'agentchattr-channel-attention';
    const MODES = ['off', 'highlight', 'flash'];
    const mentionedIn = new Set();        // channels with an unread mention of you
    const lastChannelOf = new Map();      // agent name -> channel it last posted in
    let waitingChannels = new Set();      // channels holding an agent that waits on you

    function mode() {
        let value = 'off';
        try { value = localStorage.getItem(STORAGE_KEY) || 'off'; } catch (_) { /* private storage */ }
        return MODES.includes(value) ? value : 'off';
    }

    function rerender() {
        if (mode() !== 'off' && typeof renderChannelTabs === 'function') renderChannelTabs();
    }

    function apply() {
        const value = mode();
        document.documentElement.dataset.channelAttention = value;
        const select = document.getElementById('setting-channel-attention');
        if (select) select.value = value;
        if (typeof renderChannelTabs === 'function') renderChannelTabs();
    }

    function setup() {
        const select = document.getElementById('setting-channel-attention');
        if (select) {
            select.addEventListener('change', () => {
                try { localStorage.setItem(STORAGE_KEY, select.value); } catch (_) { /* optional */ }
                apply();
                if (typeof saveUiPrefs === 'function') saveUiPrefs();
            });
        }
        apply();
    }

    function isAutomatic(msg) {
        return !msg.sender || msg.sender === 'system' || msg.type === 'system'
            || msg.type === 'join' || msg.type === 'leave' || msg.type === 'summary';
    }

    // Called for every live (not history) message before it is rendered.
    function noteMessage(msg) {
        if (!msg || isAutomatic(msg)) return;
        const channel = msg.channel || 'general';
        const me = String(window.username || '').toLowerCase();
        if (String(msg.sender).toLowerCase() === me) return;
        lastChannelOf.set(msg.sender, channel);
        const isChat = msg.type === 'chat' || msg.type === 'decision';
        if (isChat && channel !== window.activeChannel
            && typeof mentionsUser === 'function' && mentionsUser(msg.text)) {
            mentionedIn.add(channel);
            rerender();
        }
    }

    // Called with the agent status map; recomputes which channels hold an agent waiting on you.
    function noteStatus(data) {
        const next = new Set();
        for (const [name, info] of Object.entries(data || {})) {
            if (name === 'paused' || !info || !(info.needs_input && info.available)) continue;
            const channel = lastChannelOf.get(name);
            if (channel) next.add(channel);
        }
        const changed = next.size !== waitingChannels.size || [...next].some(c => !waitingChannels.has(c));
        waitingChannels = next;
        if (changed) rerender();
    }

    function clear(channel) {
        if (mentionedIn.delete(channel)) rerender();
    }

    // Empty string = no attention needed; otherwise the tooltip explaining why.
    function reason(channel) {
        if (mode() === 'off' || channel === window.activeChannel) return '';
        const parts = [];
        if (waitingChannels.has(channel)) parts.push('an agent is waiting on you');
        if (mentionedIn.has(channel)) parts.push('unread mention of you');
        return parts.length ? 'Needs your input: ' + parts.join('; ') : '';
    }

    window.ChannelAttention = { setup, apply, noteMessage, noteStatus, clear, reason, MODES };
})();
