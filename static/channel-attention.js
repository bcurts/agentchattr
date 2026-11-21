/* Channel attention: make a channel entry glow or flash when your input is needed there.
 * Two signals: an agent waiting on you (permission/question prompt), and an unread @mention
 * from a person or agent. Off by default; synced across browsers through ui_prefs. */
(function () {
    'use strict';
    const STORAGE_KEY = 'agentchattr-channel-attention';
    const MODES = ['off', 'highlight', 'flash'];
    const mentionedIn = new Map();        // channel -> id of its newest unread mention of you
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

    // Read markers: the newest message id you have seen per channel, kept in this browser.
    // They let an unread mention survive a reload; they are never sent to the server.
    const SEEN_KEY = 'agentchattr-channel-seen';
    let seen = {};
    let firstRun = true;
    const newestId = new Map();           // channel -> newest message id loaded this session
    try {
        const stored = localStorage.getItem(SEEN_KEY);
        if (stored !== null) { seen = JSON.parse(stored) || {}; firstRun = false; }
    } catch (_) { /* private storage or bad JSON: treat as first run */ }

    function markSeen(channel, id) {
        if (!Number.isInteger(id) || (seen[channel] || 0) >= id) return;
        seen[channel] = id;
        try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch (_) { /* optional */ }
        // A mention at or before the new marker has now been read (for example, you replied after it).
        const mention = mentionedIn.get(channel);
        if (mention !== undefined && mention <= id) {
            mentionedIn.delete(channel);
            rerender();
        }
    }

    // Called for every message (history and live) before it is rendered.
    function noteMessage(msg, options = {}) {
        if (!msg) return;
        const channel = msg.channel || 'general';
        const me = String(window.username || '').toLowerCase();
        const own = String(msg.sender || '').toLowerCase() === me;
        if (Number.isInteger(msg.id) && msg.id > (newestId.get(channel) || 0)) newestId.set(channel, msg.id);
        // Reading a channel, writing in it, or the very first load (nothing to compare against) all count as seen.
        if (channel === window.activeChannel || own || (options.history && firstRun)) markSeen(channel, msg.id);
        if (isAutomatic(msg) || own) return;
        lastChannelOf.set(msg.sender, channel);  // history arrives oldest first, so the latest post wins
        const isChat = msg.type === 'chat' || msg.type === 'decision';
        if (isChat && channel !== window.activeChannel && !(options.history && firstRun)
            && Number.isInteger(msg.id) && msg.id > (seen[channel] || 0)
            && typeof mentionsUser === 'function' && mentionsUser(msg.text)) {
            mentionedIn.set(channel, msg.id);
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
        markSeen(channel, newestId.get(channel));
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
