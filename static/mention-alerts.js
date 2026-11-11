/* Opt-in alerts for live mentions. The page must remain open. */
(function () {
    'use strict';
    const key = 'agentchattr-mention-alerts';
    const modes = ['off', 'browser', 'sound'];
    let mode = 'off';
    try { mode = localStorage.getItem(key) || 'off'; } catch (_) { /* private storage */ }
    if (!modes.includes(mode)) mode = 'off';
    let pending = false;
    let error = '';
    let count = 0;
    let originalTitle = null;
    const seen = new Set();

    function mentions(text, name, includeAll = true) {
        if (!name || typeof text !== 'string') return false;
        const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const who = includeAll ? escaped + '|all' : escaped;
        return new RegExp('(^|[^\\p{L}\\p{N}_@-])@(?:' + who + ')(?![\\p{L}\\p{N}_-])', 'iu').test(text);
    }

    function preview(text) {
        return String(text || '').replace(/<[^>]*>/g, '')
            .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
            .replace(/[`*_~#>]/g, '').replace(/\s+/g, ' ').trim().slice(0, 120);
    }

    function render() {
        const control = document.getElementById('setting-mention-alerts');
        const status = document.getElementById('mention-alerts-status');
        if (!control || !status) return;
        control.value = mode;
        control.disabled = pending || !('Notification' in window);
        status.textContent = !('Notification' in window) ? '(Not supported by browser)'
            : Notification.permission === 'denied' ? '(Permission blocked in browser settings)'
            : error || (pending ? '(Waiting for browser permission)'
                : mode !== 'off' && Notification.permission !== 'granted' ? '(Permission not granted; select an alert option to retry)' : '');
    }

    async function changeMode(event) {
        mode = modes.includes(event.target.value) ? event.target.value : 'off';
        error = '';
        try { localStorage.setItem(key, mode); } catch (_) { error = '(Preference could not be saved)'; }
        if (mode !== 'off' && 'Notification' in window && Notification.permission === 'default') {
            pending = true;
            render();
            try {
                // Called directly in the user's change handler, never on page load.
                await Notification.requestPermission();
            } catch (_) { error = '(Browser could not enable notifications)'; }
            pending = false;
        }
        if (mode === 'off') resetTitle();
        render();
    }

    function resetTitle() {
        if (originalTitle !== null) document.title = originalTitle;
        originalTitle = null;
        count = 0;
        render();
    }

    // Returns true when this feature owns the mention's sound behavior.
    function notify(msg, live, name) {
        if (mode === 'off' || !live || !['chat', 'decision'].includes(msg.type) || msg.id == null || !msg.sender
            || msg.sender.toLowerCase() === String(name).toLowerCase()
            || !(document.hidden || !document.hasFocus()) || !mentions(msg.text, name)) return false;
        if (seen.has(msg.id)) return true;
        seen.add(msg.id);
        if (seen.size > 1000) seen.delete(seen.values().next().value);
        const channel = msg.channel || 'general';
        if (originalTitle === null) originalTitle = document.title;
        document.title = `(${++count}) Mentioned in #${channel} · agentchattr`;
        if ('Notification' in window && Notification.permission === 'granted') {
            try {
                const alert = new Notification(`[#${channel}] ${msg.sender} mentioned you`, {
                    body: preview(msg.text), tag: `agentchattr-mention-${msg.id}`, silent: true,
                });
                alert.onclick = () => {
                    window.focus();
                    window.switchChannel(channel);
                    alert.close();
                    resetTitle();
                };
                // Sound only for a direct @you mention; @all keeps the visual notification only.
                if (mode === 'sound' && mentions(msg.text, name, false)) window.playNotificationSound(msg.sender);
                return true;
            } catch (_) {
                error = '(Browser could not show notifications)';
                render();
            }
        } else render();
        // Let the existing sound path handle blocked/failed notifications once.
        return false;
    }

    window.MentionAlerts = { notify, mentions, preview, render };
    window.addEventListener('focus', resetTitle);
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('setting-mention-alerts').addEventListener('change', changeMode);
        render();
    });
})();
