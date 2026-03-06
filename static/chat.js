/* agentchattr — WebSocket client */

// Session token injected by the server into the HTML page.
// Sent with every API call and WebSocket connection to authenticate.
const SESSION_TOKEN = window.__SESSION_TOKEN__ || "";

let ws = null;
let pendingAttachments = [];
let autoScroll = true;
let reconnectTimer = null;
let username = 'user';
let agentConfig = {};  // { name: { color, label } } — registered instances (used for pills)
let baseColors = {};   // { name: { color, label } } — base agent colors (for message coloring)
let todos = {};  // { msg_id: "todo" | "done" }
let rules = [];  // array of rule objects from server
let activeMentions = new Set();  // agent names with pre-@ toggled on
let replyingTo = null;  // { id, sender, text } or null
let unreadCount = 0;    // messages received while scrolled up
let lastMessageDate = null;  // track date for dividers (general channel)
let lastMessageDates = {};  // { channel: dateString } for per-channel dividers
let soundEnabled = false;  // suppress sounds during initial history load
let activeChannel = localStorage.getItem('agentchattr-channel') || 'general';
let channelList = ['general'];
let channelUnread = {};  // { channelName: count }
let agentHats = {};  // { agent_name: svg_string }
let jobsData = []; // all jobs from server
let activeJobId = null; // currently viewing job in conversation view
let jobUnread = {}; // { job_id: unread_message_count }
let jobReplyTargets = {}; // { job_id: default agent recipient }
let pendingDeleteJobId = null;
let archiveDeleteBatchIds = null; // Set<number> while client-side archive delete animation is active
let jobReorderMute = null; // { ids:Set<number>, channel, status, until:number, suppressed:boolean }
let jobReorderMuteTimer = null;
let _jobViewSwitching = false;

// --- Drag-scroll for overflow containers ---
function enableDragScroll(el) {
    let isDown = false, startX, scrollLeft;
    el.addEventListener('mousedown', e => {
        if (e.button !== 0) return;  // left-click only
        isDown = true; startX = e.pageX - el.offsetLeft; scrollLeft = el.scrollLeft;
        el.style.cursor = 'grabbing';
    });
    el.addEventListener('mouseleave', () => { isDown = false; el.style.cursor = ''; });
    el.addEventListener('mouseup', () => { isDown = false; el.style.cursor = ''; });
    el.addEventListener('mousemove', e => {
        if (!isDown) return;
        e.preventDefault();
        el.scrollLeft = scrollLeft - (e.pageX - el.offsetLeft - startX);
    });
}

// --- Notification sounds ---
const SOUND_OPTIONS = [
    { value: 'soft-chime', label: 'Soft Chime' },
    { value: 'bright-ping', label: 'Bright Ping' },
    { value: 'gentle-pop', label: 'Gentle Pop' },
    { value: 'alert-tone', label: 'Alert Tone' },
    { value: 'pluck', label: 'Pluck' },
    { value: 'click', label: 'Click' },
    { value: 'warm-bell', label: 'Warm Bell' },
    { value: 'none', label: 'None' },
];
const DEFAULT_SOUND = 'soft-chime';
let soundPrefs = JSON.parse(localStorage.getItem('agentchattr-sounds') || '{}');
const soundCache = {};

function playNotificationSound(sender) {
    const key = sender.toLowerCase();
    const soundName = soundPrefs[key] || soundPrefs['default'] || DEFAULT_SOUND;
    if (soundName === 'none') return;
    if (!soundCache[soundName]) {
        soundCache[soundName] = new Audio(`/static/sounds/${soundName}.mp3`);
    }
    const audio = soundCache[soundName];
    audio.currentTime = 0;
    audio.play().catch(() => {});  // ignore autoplay policy errors
}

function buildSoundSettings() {
    const container = document.getElementById('sound-settings');
    if (!container) return;
    container.innerHTML = '';

    // Default sound
    const agents = ['default', ...Object.keys(agentConfig)];
    for (const name of agents) {
        const row = document.createElement('div');
        row.className = 'sound-row';
        const label = document.createElement('span');
        label.className = 'sound-label';
        label.textContent = name === 'default' ? 'Default Sound' : (agentConfig[name]?.label || name);
        const select = document.createElement('select');
        select.className = 'sound-select';
        select.dataset.agent = name;
        for (const opt of SOUND_OPTIONS) {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if ((soundPrefs[name] || (name === 'default' ? DEFAULT_SOUND : '')) === opt.value) {
                o.selected = true;
            }
            select.appendChild(o);
        }
        // Add "Use default" option for non-default agents
        if (name !== 'default') {
            const o = document.createElement('option');
            o.value = '';
            o.textContent = 'Use default';
            if (!soundPrefs[name]) o.selected = true;
            select.insertBefore(o, select.firstChild);
        }
        // Preview on change
        select.addEventListener('change', () => {
            const val = select.value;
            soundPrefs[name] = val;
            localStorage.setItem('agentchattr-sounds', JSON.stringify(soundPrefs));
            if (val && val !== 'none') {
                if (!soundCache[val]) soundCache[val] = new Audio(`/static/sounds/${val}.mp3`);
                soundCache[val].currentTime = 0;
                soundCache[val].play().catch(() => {});
            }
        });
        row.appendChild(label);
        row.appendChild(select);
        container.appendChild(row);
    }
}

// Real brand logo SVGs from Bootstrap Icons (MIT licensed)
const BRAND_AVATARS = {
    claude: `<svg viewBox="0 0 16 16" fill="white"><path d="m3.127 10.604 3.135-1.76.053-.153-.053-.085H6.11l-.525-.032-1.791-.048-1.554-.065-1.505-.08-.38-.081L0 7.832l.036-.234.32-.214.455.04 1.009.069 1.513.105 1.097.064 1.626.17h.259l.036-.105-.089-.065-.068-.064-1.566-1.062-1.695-1.121-.887-.646-.48-.327-.243-.306-.104-.67.435-.48.585.04.15.04.593.456 1.267.981 1.654 1.218.242.202.097-.068.012-.049-.109-.181-.9-1.626-.96-1.655-.428-.686-.113-.411a2 2 0 0 1-.068-.484l.496-.674L4.446 0l.662.089.279.242.411.94.666 1.48 1.033 2.014.302.597.162.553.06.17h.105v-.097l.085-1.134.157-1.392.154-1.792.052-.504.25-.605.497-.327.387.186.319.456-.045.294-.19 1.23-.37 1.93-.243 1.29h.142l.161-.16.654-.868 1.097-1.372.484-.545.565-.601.363-.287h.686l.505.751-.226.775-.707.895-.585.759-.839 1.13-.524.904.048.072.125-.012 1.897-.403 1.024-.186 1.223-.21.553.258.06.263-.218.536-1.307.323-1.533.307-2.284.54-.028.02.032.04 1.029.098.44.024h1.077l2.005.15.525.346.315.424-.053.323-.807.411-3.631-.863-.872-.218h-.12v.073l.726.71 1.331 1.202 1.667 1.55.084.383-.214.302-.226-.032-1.464-1.101-.565-.497-1.28-1.077h-.084v.113l.295.432 1.557 2.34.08.718-.112.234-.404.141-.444-.08-.911-1.28-.94-1.44-.759-1.291-.093.053-.448 4.821-.21.246-.484.186-.403-.307-.214-.496.214-.98.258-1.28.21-1.016.19-1.263.112-.42-.008-.028-.092.012-.953 1.307-1.448 1.957-1.146 1.227-.274.109-.477-.247.045-.44.266-.39 1.586-2.018.956-1.25.617-.723-.004-.105h-.036l-4.212 2.736-.75.096-.324-.302.04-.496.154-.162 1.267-.871z"/></svg>`,
    codex: `<svg viewBox="0 0 16 16" fill="white"><path d="M14.949 6.547a3.94 3.94 0 0 0-.348-3.273 4.11 4.11 0 0 0-4.4-1.934A4.1 4.1 0 0 0 8.423.2 4.15 4.15 0 0 0 6.305.086a4.1 4.1 0 0 0-1.891.948 4.04 4.04 0 0 0-1.158 1.753 4.1 4.1 0 0 0-1.563.679A4 4 0 0 0 .554 4.72a3.99 3.99 0 0 0 .502 4.731 3.94 3.94 0 0 0 .346 3.274 4.11 4.11 0 0 0 4.402 1.933c.382.425.852.764 1.377.995.526.231 1.095.35 1.67.346 1.78.002 3.358-1.132 3.901-2.804a4.1 4.1 0 0 0 1.563-.68 4 4 0 0 0 1.14-1.253 3.99 3.99 0 0 0-.506-4.716m-6.097 8.406a3.05 3.05 0 0 1-1.945-.694l.096-.054 3.23-1.838a.53.53 0 0 0 .265-.455v-4.49l1.366.778q.02.011.025.035v3.722c-.003 1.653-1.361 2.992-3.037 2.996m-6.53-2.75a2.95 2.95 0 0 1-.36-2.01l.095.057L5.29 12.09a.53.53 0 0 0 .527 0l3.949-2.246v1.555a.05.05 0 0 1-.022.041L6.473 13.3c-1.454.826-3.311.335-4.15-1.098m-.85-6.94A3.02 3.02 0 0 1 3.07 3.949v3.785a.51.51 0 0 0 .262.451l3.93 2.237-1.366.779a.05.05 0 0 1-.048 0L2.585 9.342a2.98 2.98 0 0 1-1.113-4.094zm11.216 2.571L8.747 5.576l1.362-.776a.05.05 0 0 1 .048 0l3.265 1.86a3 3 0 0 1 1.173 1.207 2.96 2.96 0 0 1-.27 3.2 3.05 3.05 0 0 1-1.36.997V8.279a.52.52 0 0 0-.276-.445m1.36-2.015-.097-.057-3.226-1.855a.53.53 0 0 0-.53 0L6.249 6.153V4.598a.04.04 0 0 1 .019-.04L9.533 2.7a3.07 3.07 0 0 1 3.257.139c.474.325.843.778 1.066 1.303.223.526.289 1.103.191 1.664zM5.503 8.575 4.139 7.8a.05.05 0 0 1-.026-.037V4.049c0-.57.166-1.127.476-1.607s.752-.864 1.275-1.105a3.08 3.08 0 0 1 3.234.41l-.096.054-3.23 1.838a.53.53 0 0 0-.265.455zm.742-1.577 1.758-1 1.762 1v2l-1.755 1-1.762-1z"/></svg>`,
    gemini: `<svg viewBox="0 0 65 65" fill="white"><path d="M32.447 0c.68 0 1.273.465 1.439 1.125a38.904 38.904 0 001.999 5.905c2.152 5 5.105 9.376 8.854 13.125 3.751 3.75 8.126 6.703 13.125 8.855a38.98 38.98 0 005.906 1.999c.66.166 1.124.758 1.124 1.438 0 .68-.464 1.273-1.125 1.439a38.902 38.902 0 00-5.905 1.999c-5 2.152-9.375 5.105-13.125 8.854-3.749 3.751-6.702 8.126-8.854 13.125a38.973 38.973 0 00-2 5.906 1.485 1.485 0 01-1.438 1.124c-.68 0-1.272-.464-1.438-1.125a38.913 38.913 0 00-2-5.905c-2.151-5-5.103-9.375-8.854-13.125-3.75-3.749-8.125-6.702-13.125-8.854a38.973 38.973 0 00-5.905-2A1.485 1.485 0 010 32.448c0-.68.465-1.272 1.125-1.438a38.903 38.903 0 005.905-2c5-2.151 9.376-5.104 13.125-8.854 3.75-3.749 6.703-8.125 8.855-13.125a38.972 38.972 0 001.999-5.905A1.485 1.485 0 0132.447 0z"/></svg>`,
    qwen: `<svg viewBox="0 0 331 328" fill="none"><defs><linearGradient x1="0" y1="1" x2="1" y2="0.7" id="q_g0"><stop offset="0%" stop-color="#615CED" stop-opacity="0.5"/><stop offset="15%" stop-color="#FDFDFE"/><stop offset="25%" stop-color="#FFFFFF"/></linearGradient><linearGradient x1="1" y1="0.8" x2="0" y2="0" id="q_g1"><stop offset="0%" stop-color="#615CED" stop-opacity="0.5"/><stop offset="15%" stop-color="#FDFDFE"/><stop offset="25%" stop-color="#FFFFFF"/></linearGradient><linearGradient x1="0.5" y1="0" x2="0.5" y2="1" id="q_g2"><stop offset="0%" stop-color="#615CED" stop-opacity="0.5"/><stop offset="15%" stop-color="#FDFDFE"/><stop offset="25%" stop-color="#FFFFFF"/></linearGradient></defs><g><path d="M120 8l23 39-23 39h180l-23 39H102L77 82l43-74z" fill="white"/><path d="M30 86h45l88 152-25 43H53l22-39h45L30 86z" fill="white"/><path d="M143 280l22 39 90-156 22 39h45l-43-74-49 0-87 152z" fill="white"/><path d="M120 8l23 39-23 39h180l-23 39H102L77 82l43-74z" fill="url(#q_g0)"/><path d="M53 280l22-39h45L30 86h45l88 152-25 43H53z" fill="url(#q_g1)"/><path d="M322 202h-45l-22-39-90 156-22-39 87-152 50 0 42 74z" fill="url(#q_g2)"/></g></svg>`,
};
const USER_AVATAR = `<svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="12" r="5" fill="white" opacity="0.85"/><path d="M7 27C7 21.5 11 18 16 18C21 18 25 21.5 25 27" fill="white" opacity="0.85"/></svg>`;

function getAvatarSvg(sender) {
    const s = sender.toLowerCase();
    const resolved = resolveAgent(s);
    if (resolved) {
        if (BRAND_AVATARS[resolved]) return BRAND_AVATARS[resolved];
        // Use base field from agent config (handles custom names like "claudeypops" → claude)
        const cfg = agentConfig[resolved];
        if (cfg && cfg.base && BRAND_AVATARS[cfg.base]) return BRAND_AVATARS[cfg.base];
        // Fallback: parse base-N pattern (claude-2 → claude)
        const base = resolved.replace(/-\d+$/, '');
        if (base !== resolved && BRAND_AVATARS[base]) return BRAND_AVATARS[base];
    }
    // Fall back for offline agents: check config base, then parse pattern
    const cfg = agentConfig[s];
    if (cfg && cfg.base && BRAND_AVATARS[cfg.base]) return BRAND_AVATARS[cfg.base];
    const base = s.replace(/-\d+$/, '');
    if (BRAND_AVATARS[base]) return BRAND_AVATARS[base];
    return USER_AVATAR;
}

// --- Init ---

function init() {
    // Configure marked for chat-style rendering
    marked.setOptions({
        breaks: true,      // single newline → <br>
        gfm: true,         // GitHub-flavored markdown
    });

    detectPlatform();
    fetchRoles();
    connectWebSocket();
    setupInput();
    setupDragDrop();
    setupPaste();
    setupScroll();
    setupSettingsKeys();
    setupKeyboardShortcuts();
    setupRuleForm();
    setupRulesGrip();
    setupJobsGrip();
    setupJobsInput();
    setupJobMentions();

    // Dismiss channel edit controls when clicking outside channel bar
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#channel-bar')) {
            document.querySelectorAll('.channel-tab.editing').forEach(t => t.classList.remove('editing'));
        }
    });
}

function renderMarkdown(text) {
    // Protect Windows paths from escape replacement (e.g. \tests → tab, \new → newline)
    const pathSlots = [];
    text = text.replace(/[A-Z]:[\\\/][\w\-.\\ \/]+/g, (m) => {
        pathSlots.push(m);
        return `\x00P${pathSlots.length - 1}\x00`;
    });
    // Unescape literal \n and \t that agents sometimes send as escaped text
    text = text.replace(/\\\\n/g, '\n').replace(/\\n/g, '\n').replace(/\\t/g, '\t');
    // Treat raw HTML as plain text so message bodies cannot break chat layout.
    text = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Restore paths
    text = text.replace(/\x00P(\d+)\x00/g, (_, i) => pathSlots[parseInt(i)]);
    // Parse markdown, then color @mentions, URLs, and file paths in the output
    let html = marked.parse(text);
    // Remove wrapping <p> tags for single-line messages to keep them inline
    const trimmed = html.trim();
    if (trimmed.startsWith('<p>') && trimmed.endsWith('</p>') && trimmed.indexOf('<p>', 1) === -1) {
        html = trimmed.slice(3, -4);
    }
    html = colorMentions(html);
    html = linkifyUrls(html);
    html = linkifyPaths(html);
    return html;
}

function linkifyUrls(html) {
    // Match http/https URLs not already inside an <a> tag.
    // We match tags first to skip them, then capture URLs in the same pass.
    return html.replace(/<a\b[^>]*>.*?<\/a>|(?<!["=])(https?:\/\/[^\s<>"')\]]+)/gs, (match, url) => {
        if (url) {
            return `<a href="${url}" target="_blank" rel="noopener">${url}</a>`;
        }
        return match;
    });
}

let serverPlatform = 'win32';  // default, updated on connect
async function detectPlatform() {
    try {
        const r = await fetch('/api/platform', { headers: { 'X-Session-Token': SESSION_TOKEN } });
        const data = await r.json();
        serverPlatform = data.platform || 'win32';
    } catch (e) { /* fallback to win32 */ }
}

function linkifyPaths(html) {
    // Windows paths: E:\foo\bar or E:/foo/bar
    html = html.replace(/(?<!["=\/])([A-Z]):[\\\/][\w\-.\\ \/]+/g, (match) => {
        const escaped = match.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        return `<a class="file-link" href="#" onclick="openPath('${escaped}'); return false;" title="Open in file manager">${match}</a>`;
    });
    // Unix paths: /Users/..., /home/..., /tmp/..., /opt/..., /var/..., /etc/...
    if (serverPlatform !== 'win32') {
        html = html.replace(/(?<!["=\w])(\/(?:Users|home|tmp|opt|var|etc|usr)\/[\w\-.\/ ]+)/g, (match) => {
            const escaped = match.replace(/'/g, "\\'");
            return `<a class="file-link" href="#" onclick="openPath('${escaped}'); return false;" title="Open in file manager">${match}</a>`;
        });
    }
    return html;
}

async function openPath(path) {
    try {
        await fetch('/api/open-path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ path: path }),
        });
    } catch (err) {
        console.error('Failed to open path:', err);
    }
}

function addCodeCopyButtons(container) {
    const blocks = container.querySelectorAll('pre');
    for (const pre of blocks) {
        if (pre.querySelector('.code-copy-btn')) continue;
        const btn = document.createElement('button');
        btn.className = 'code-copy-btn';
        btn.textContent = 'copy';
        btn.onclick = async (e) => {
            e.stopPropagation();
            const code = pre.querySelector('code')?.textContent || pre.textContent;
            try {
                await navigator.clipboard.writeText(code);
                btn.textContent = 'copied!';
                setTimeout(() => { btn.textContent = 'copy'; }, 1500);
            } catch (err) {
                btn.textContent = 'failed';
                setTimeout(() => { btn.textContent = 'copy'; }, 1500);
            }
        };
        pre.style.position = 'relative';
        pre.appendChild(btn);
    }
}

// --- WebSocket ---

function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(SESSION_TOKEN)}`);

    ws.onopen = () => {
        console.log('WebSocket connected');
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    ws.onmessage = (e) => {
        const event = JSON.parse(e.data);
        if (event.type === 'message') {
            // Play notification sound for new messages from others (not joins, not when focused)
            if (soundEnabled && !document.hasFocus() && event.data.type !== 'join' && event.data.type !== 'leave' && event.data.type !== 'summary' && event.data.sender && event.data.sender.toLowerCase() !== username.toLowerCase()) {
                playNotificationSound(event.data.sender);
            }
            appendMessage(event.data);
        } else if (event.type === 'agent_renamed') {
            // Migrate active mentions before the agents config rebuild
            if (activeMentions.has(event.old_name)) {
                activeMentions.delete(event.old_name);
                activeMentions.add(event.new_name);
            }
            // Update sender name, color, and avatar on all existing messages in the DOM
            const newColor = getColor(event.new_name);
            const newAvatar = getAvatarSvg(event.new_name);
            const newAgentKey = (resolveAgent(event.new_name.toLowerCase()) || event.new_name).toLowerCase();
            const newHat = agentHats[newAgentKey] || '';
            document.querySelectorAll('#messages .message').forEach(el => {
                // Regular chat messages
                const senderEl = el.querySelector('.msg-sender');
                if (senderEl && senderEl.textContent === event.old_name) {

                    senderEl.textContent = event.new_name;
                    senderEl.style.color = newColor;
                    // Update bubble accent color
                    const bubble = el.querySelector('.chat-bubble');
                    if (bubble) bubble.style.setProperty('--bubble-color', newColor);
                    // Update avatar
                    const avatarWrap = el.querySelector('.avatar-wrap');
                    if (avatarWrap) {
                        avatarWrap.dataset.agent = newAgentKey;
                        const avatar = avatarWrap.querySelector('.avatar');
                        if (avatar) {
                            avatar.style.backgroundColor = newColor;
                            avatar.innerHTML = newAvatar;
                        }
                        // Update hat
                        let hatEl = avatarWrap.querySelector('.hat-overlay');
                        if (newHat) {
                            if (!hatEl) {
                                hatEl = document.createElement('div');
                                hatEl.className = 'hat-overlay';
                                avatarWrap.appendChild(hatEl);
                            }
                            hatEl.dataset.agent = newAgentKey;
                            hatEl.innerHTML = newHat;
                        } else if (hatEl) {
                            hatEl.remove();
                        }
                    }
                }
                // Join/leave messages (separate structure, no .msg-sender)
                const joinText = el.querySelector('.join-text strong');
                if (joinText && joinText.textContent === event.old_name) {

                    joinText.textContent = event.new_name;
                    joinText.style.color = newColor;
                    const joinDot = el.querySelector('.join-dot');
                    if (joinDot) joinDot.style.background = newColor;
                }
            });
        } else if (event.type === 'agents') {
            applyAgentConfig(event.data);
        } else if (event.type === 'base_colors') {
            baseColors = event.data || {};
        } else if (event.type === 'todos') {
            todos = {};
            for (const [id, status] of Object.entries(event.data)) {
                todos[parseInt(id)] = status;
            }
        } else if (event.type === 'todo_update') {
            const d = event.data;
            if (d.status === null) {
                delete todos[d.id];
            } else {
                todos[d.id] = d.status;
            }
            updateTodoState(d.id, d.status);
        } else if (event.type === 'status') {
            updateStatus(event.data);
            // Status is the last event sent on connect — enable sounds after history
            if (!soundEnabled) {
                soundEnabled = true;
                const loader = document.getElementById('loading-indicator');
                if (loader) loader.classList.add('hidden');
                filterMessagesByChannel();
                renderChannelTabs();
                // Ensure refresh/reconnect lands on the latest visible message.
                requestAnimationFrame(() => {
                    autoScroll = true;
                    scrollToBottom();
                });
            }
        } else if (event.type === 'typing') {
            updateTyping(event.agent, event.active);
        } else if (event.type === 'settings') {
            applySettings(event.data);
        } else if (event.type === 'delete') {
            handleDeleteBroadcast(event.ids);
        } else if (event.type === 'rules' || event.type === 'decisions') {
            rules = event.data || [];
            renderRulesPanel();
            updateRulesBadge();
        } else if (event.type === 'rule' || event.type === 'decision') {
            handleRuleEvent(event.action, event.data);
        } else if (event.type === 'hats') {
            agentHats = event.data || {};
            updateAllHats();
        } else if (event.type === 'pending_instance') {
            // A new 2nd+ instance registered — queue naming lightbox
            _pendingNameQueue.push({
                name: event.name,
                label: event.label || event.name,
                color: event.color || '#888',
                base: event.base || '',
            });
            _showNextPendingName();
        } else if (event.type === 'channel_renamed') {
            // Migrate data-channel on existing DOM elements
            const container = document.getElementById('messages');
            for (const el of container.children) {
                if ((el.dataset.channel || 'general') === event.old_name) {
                    el.dataset.channel = event.new_name;
                }
            }
            // Update per-channel date tracking
            if (lastMessageDates[event.old_name]) {
                lastMessageDates[event.new_name] = lastMessageDates[event.old_name];
                delete lastMessageDates[event.old_name];
            }
            // Update active channel if we were on the renamed one
            if (activeChannel === event.old_name) {
                activeChannel = event.new_name;
                localStorage.setItem('agentchattr-channel', event.new_name);
            }
        } else if (event.type === 'jobs') {
            jobsData = event.data || [];
            syncJobUnreadCache();
            updateJobsBadge();
            renderJobsList();
        } else if (event.type === 'job') {
            handleJobEvent(event.action, event.data);
        } else if (event.type === 'edit') {
            // A message was edited/demoted — re-render it in place
            const updatedMsg = event.message;
            if (updatedMsg && updatedMsg.id != null) {
                const el = document.querySelector(`.message[data-id="${updatedMsg.id}"]`);
                if (el) {
                    // Insert a fresh message after the old one, then remove the old
                    const placeholder = document.createElement('div');
                    el.after(placeholder);
                    el.remove();
                    // Temporarily hijack container to insert at the right spot
                    const container = document.getElementById('messages');
                    appendMessage(updatedMsg);
                    // Move the newly appended message to where the old one was
                    const newEl = container.lastElementChild;
                    if (newEl && newEl.dataset.id == updatedMsg.id) {
                        placeholder.replaceWith(newEl);
                    } else {
                        placeholder.remove();
                    }
                }
            }
        } else if (event.type === 'clear') {
            const _clearDbgList = document.getElementById('jobs-list');
            const _clearDbgBefore = _clearDbgList ? _clearDbgList.children.length : -1;
            console.log('CLEAR_DEBUG clear event received, channel=' + (event.channel || 'ALL'), 'jobs-panel-children-before=' + _clearDbgBefore);
            const clearChannel = event.channel || null;
            if (clearChannel) {
                // Per-channel clear: remove only messages from that channel
                const container = document.getElementById('messages');
                const toRemove = [];
                for (const el of container.children) {
                    if (el.dataset.id && (el.dataset.channel || 'general') === clearChannel) {
                        toRemove.push(el);
                    }
                }
                toRemove.forEach(el => el.remove());
                // Clean up orphaned date dividers and reset tracking
                delete lastMessageDates[clearChannel];
                filterMessagesByChannel();
            } else {
                // Full clear (all channels)
                document.getElementById('messages').innerHTML = '';
                lastMessageDate = null;
                lastMessageDates = {};
            }
            requestAnimationFrame(() => {
                const _clearDbgAfter = _clearDbgList ? _clearDbgList.children.length : -1;
                console.log('CLEAR_DEBUG after clear (next frame), jobs-panel-children=' + _clearDbgAfter);
            });
        }
    };

    ws.onclose = (e) => {
        // Server sends 4003 when session token is invalid (server restarted).
        // Auto-reload to pick up the fresh token from the new HTML page.
        if (e.code === 4003) {
            console.warn('Session token rejected (server restarted?) — reloading page...');
            location.reload();
            return;
        }
        console.log('Disconnected, reconnecting in 2s...');
        soundEnabled = false;  // suppress sounds during reconnect history replay
        const loader = document.getElementById('loading-indicator');
        if (loader) loader.classList.remove('hidden');
        reconnectTimer = setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        ws.close();
    };
}

// --- Date dividers ---

function getMessageDate(msg) {
    // msg.time is "HH:MM:SS" — we also need the date
    // Use msg.timestamp (epoch) if available, otherwise try to infer from today
    if (msg.timestamp) {
        return new Date(msg.timestamp * 1000).toDateString();
    }
    // Fallback: assume today (messages from history might not have timestamps)
    return new Date().toDateString();
}

function formatDateDivider(dateStr) {
    const date = new Date(dateStr);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    if (date.toDateString() === today.toDateString()) return 'Today';
    if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';

    return date.toLocaleDateString('en-GB', {
        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
    });
}

function maybeInsertDateDivider(container, msg) {
    const msgDate = getMessageDate(msg);
    const channel = msg.channel || 'general';
    const lastDate = lastMessageDates[channel];
    
    if (msgDate !== lastDate) {
        lastMessageDates[channel] = msgDate;
        const divider = document.createElement('div');
        divider.className = 'date-divider';
        divider.dataset.channel = channel;
        divider.innerHTML = `<span>${formatDateDivider(msgDate)}</span>`;
        if (channel !== activeChannel) {
            divider.style.display = 'none';
        }
        container.appendChild(divider);
    }
}

// --- Messages ---

function _collapseJobBreadcrumbs(container, newEl) {
    // Collect consecutive job-breadcrumb elements ending with newEl
    const crumbs = [newEl];
    let prev = newEl.previousElementSibling;
    let existingGroup = null;
    while (prev) {
        if (prev.classList.contains('job-breadcrumb')) {
            crumbs.unshift(prev);
            prev = prev.previousElementSibling;
        } else if (prev.classList.contains('job-group')) {
            // Already a collapsed group — absorb its children
            existingGroup = prev;
            const inner = [...prev.querySelectorAll('.job-breadcrumb')];
            inner.forEach(c => crumbs.unshift(c));
            prev = prev.previousElementSibling;
            existingGroup.remove();
            break;
        } else {
            break;
        }
    }

    if (crumbs.length < 2) return; // nothing to collapse

    // Remember insertion point (the element after the last crumb = newEl)
    const insertBefore = newEl.nextSibling;

    // Build the group wrapper
    const group = document.createElement('div');
    group.className = 'job-group';
    const summary = document.createElement('div');
    summary.className = 'job-group-summary';
    summary.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" style="vertical-align:-2px;margin-right:4px;opacity:0.6"><rect x="2" y="1" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="9" y="8" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>${crumbs.length} jobs were started`;
    summary.onclick = () => {
        group.classList.toggle('expanded');
    };
    group.appendChild(summary);

    const list = document.createElement('div');
    list.className = 'job-group-list';
    for (const c of crumbs) {
        list.appendChild(c); // moves from container into list
    }
    group.appendChild(list);

    // Insert group at the position where the crumbs were
    container.insertBefore(group, insertBefore);
}

function appendMessage(msg) {
    const container = document.getElementById('messages');

    // Insert date divider if needed
    maybeInsertDateDivider(container, msg);

    const el = document.createElement('div');
    el.className = 'message';
    el.dataset.id = msg.id;
    const msgChannel = msg.channel || 'general';
    el.dataset.channel = msgChannel;

    if (msg.type === 'join' || msg.type === 'leave') {
        el.classList.add('join-msg');
        const color = getColor(msg.sender);
        el.innerHTML = `<span class="join-dot" style="background: ${color}"></span><span class="join-text"><strong style="color: ${color}">${escapeHtml(msg.sender)}</strong> ${msg.type === 'join' ? 'joined' : 'left'}</span>`;
    } else if (msg.type === 'summary') {
        el.classList.add('summary-msg');
        const color = getColor(msg.sender);
        el.innerHTML = `<div class="summary-card"><span class="summary-pill">Summary</span><span class="summary-author" style="color: ${color}">${escapeHtml(msg.sender)}</span><div class="summary-text">${escapeHtml(msg.text)}</div></div>`;
    } else if (msg.type === 'job_proposal') {
        el.classList.add('proposal-msg');
        const meta = msg.metadata || {};
        const title = escapeHtml(meta.title || '');
        const body = meta.body ? renderMarkdown(meta.body) : '';
        const color = getColor(msg.sender);
        const status = meta.status || 'pending';
        const isPending = status === 'pending';
        el.dataset.proposalTitle = meta.title || '';
        el.dataset.proposalBody = meta.body || '';
        el.dataset.proposalSender = msg.sender || '';
        el.innerHTML = `
            <div class="proposal-card ${isPending ? '' : 'proposal-resolved'}">
                <div class="proposal-header">
                    <span class="proposal-pill">Job Proposal</span>
                    <span class="proposal-author" style="color: ${color}">${escapeHtml(msg.sender)}</span>
                </div>
                <div class="proposal-title">${title}</div>
                ${body ? `<div class="proposal-body">${body}</div>` : ''}
                ${isPending ? `
                    <div class="proposal-actions">
                        <button class="proposal-accept" onclick="acceptProposal(${msg.id})">Accept</button>
                        <button class="proposal-request-changes" onclick="requestChangesProposal(${msg.id})">Request Changes</button>
                        <button class="proposal-dismiss" onclick="dismissProposal(${msg.id})">Dismiss</button>
                    </div>
                ` : `
                    <div class="proposal-status-resolved">${status === 'accepted' ? 'Accepted' : 'Dismissed'}</div>
                `}
            </div>
            ${!isPending ? `<div class="msg-actions"><button class="reply-btn" onclick="startReply(${msg.id}, event)">reply</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">del</button></div>` : ''}`;
    } else if (msg.type === 'rule_proposal') {
        el.classList.add('proposal-msg');
        const meta = msg.metadata || {};
        const ruleText = escapeHtml(meta.text || msg.text || '');
        const color = getColor(msg.sender);
        const status = meta.status || 'pending';
        const isPending = status === 'pending';
        el.innerHTML = `
            <div class="proposal-card rule-proposal-card ${isPending ? '' : 'proposal-resolved'}">
                <div class="proposal-header">
                    <span class="proposal-pill rule-proposal-pill">Rule Proposal</span>
                    <span class="proposal-author" style="color: ${color}">${escapeHtml(msg.sender)}</span>
                </div>
                <div class="rule-proposal-text">${ruleText}</div>
                ${isPending ? `
                    <div class="proposal-actions">
                        <button class="proposal-accept" onclick="resolveRuleProposal(${msg.id}, 'activate')">Activate</button>
                        <button class="proposal-request-changes" onclick="resolveRuleProposal(${msg.id}, 'draft')">Add to drafts</button>
                        <button class="proposal-dismiss" onclick="dismissRuleProposal(${msg.id})">Dismiss</button>
                    </div>
                ` : `
                    <div class="proposal-status-resolved">${status === 'activated' ? 'Activated' : status === 'drafted' ? 'Added to drafts' : 'Dismissed'}</div>
                `}
            </div>
            ${!isPending ? `<div class="msg-actions"><button class="reply-btn" onclick="startReply(${msg.id}, event)">reply</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">del</button></div>` : ''}`;
    } else if (msg.type === 'job_created') {
        el.classList.add('system-msg', 'job-breadcrumb');
        const actId = msg.metadata?.job_id;
        const color = getColor(msg.sender);
        // Hide breadcrumb if the job no longer exists (was deleted)
        if (actId && !jobsData.some(a => a.id === actId)) {
            el.style.display = 'none';
        }
        if (actId) {
            el.innerHTML = `<span class="job-breadcrumb-link" onclick="openJobFromBreadcrumb(${actId})" title="Open job">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" style="vertical-align:-2px;margin-right:4px;opacity:0.6"><rect x="2" y="1" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="9" y="8" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
                New job: <em>${escapeHtml(msg.text.replace('Job created: ', ''))}</em></span>`;
        } else {
            el.innerHTML = `<span class="msg-text">${escapeHtml(msg.text)}</span>`;
        }
    } else if (msg.type === 'system' || msg.sender === 'system') {
        el.classList.add('system-msg');
        el.innerHTML = `<span class="msg-text">${escapeHtml(msg.text)}</span>`;
    } else {
        const isError = msg.text.startsWith('[') && msg.text.includes('error');
        if (isError) el.classList.add('error-msg');

        // Update last mentioned agent if message is from user (Ben)
        if (msg.sender.toLowerCase() === username.toLowerCase()) {
            const mentions = msg.text.match(/@(\w+)/g);
            if (mentions) {
                const lastMention = mentions[mentions.length - 1].slice(1).toLowerCase();
                // Check against registered agents (agentConfig keys are name labels)
                if (agentConfig[lastMention]) {
                    _lastMentionedAgent = lastMention;
                }
            }
        }

        let textHtml = styleHashtags(renderMarkdown(msg.text));

        const senderColor = getColor(msg.sender);
        const isSelf = msg.sender.toLowerCase() === username.toLowerCase();
        el.classList.add(isSelf ? 'self' : 'other');

        let attachmentsHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
            attachmentsHtml = '<div class="msg-attachments">';
            for (const att of msg.attachments) {
                attachmentsHtml += `<img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name)}" onclick="openImageModal('${escapeHtml(att.url)}')">`;
            }
            attachmentsHtml += '</div>';
        }

        const todoStatus = todos[msg.id] || null;

        // Reply quote (if this message is a reply)
        let replyHtml = '';
        if (msg.reply_to !== undefined && msg.reply_to !== null) {
            const parentEl = document.querySelector(`.message[data-id="${msg.reply_to}"]`);
            if (parentEl) {
                const parentSender = parentEl.querySelector('.msg-sender')?.textContent || '?';
                const parentText = parentEl.dataset.rawText || parentEl.querySelector('.msg-text')?.textContent || '';
                const truncated = parentText.length > 80 ? parentText.slice(0, 80) + '...' : parentText;
                const parentColor = parentEl.querySelector('.msg-sender')?.style.color || 'var(--text-dim)';
                replyHtml = `<div class="reply-quote" onclick="scrollToMessage(${msg.reply_to})"><span class="reply-sender" style="color: ${parentColor}">${escapeHtml(parentSender)}</span> ${escapeHtml(truncated)}</div>`;
            }
        }

        const agentKey = (resolveAgent(msg.sender.toLowerCase()) || msg.sender).toLowerCase();
        const hatSvg = agentHats[agentKey] || '';
        const hatHtml = hatSvg ? `<div class="hat-overlay" data-agent="${escapeHtml(agentKey)}">${hatSvg}</div>` : '';
        const avatarHtml = `<div class="avatar-wrap" data-agent="${escapeHtml(agentKey)}"><div class="avatar" style="background-color: ${senderColor}">${getAvatarSvg(msg.sender)}</div>${hatHtml}</div>`;

        const statusLabel = todoStatusLabel(todoStatus);
        el.dataset.rawText = msg.text;
        const senderRole = _agentRoles[msg.sender] || '';
        const roleClass = senderRole ? 'bubble-role has-role' : 'bubble-role';
        const rolePillHtml = !isSelf ? `<button class="${roleClass}" onclick="showBubbleRolePicker(this, '${escapeHtml(msg.sender)}')" title="${senderRole ? escapeHtml(senderRole) : 'Set role'}">${senderRole || 'choose a role'}</button>` : '';
        el.innerHTML = `<div class="todo-strip"></div>${isSelf ? '' : avatarHtml}<div class="chat-bubble" style="--bubble-color: ${senderColor}">${replyHtml}<div class="bubble-header"><span class="msg-sender" style="color: ${senderColor}">${escapeHtml(msg.sender)}</span>${rolePillHtml}<span class="msg-time">${msg.time || ''}</span></div><div class="msg-text">${textHtml}</div>${attachmentsHtml}<button class="convert-job-pill" onclick="startJobFromMessage(${msg.id}); event.stopPropagation();" title="Convert to job">convert to job</button><button class="bubble-copy" onclick="copyMessage(${msg.id}, event)" title="Copy message"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></div><div class="msg-actions"><button class="reply-btn" onclick="startReply(${msg.id}, event)">reply</button><button class="todo-hint" onclick="todoCycle(${msg.id}); event.stopPropagation();">${statusLabel}</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">del</button></div>`;
        if (todoStatus) el.classList.add('msg-todo', `msg-todo-${todoStatus}`);

        // Add copy buttons to code blocks
        addCodeCopyButtons(el);
    }

    // Hide messages from other channels
    if (msgChannel !== activeChannel) {
        el.style.display = 'none';
        // Track unread for background channels (skip joins/leaves and initial history load)
        if (soundEnabled && msg.type !== 'join' && msg.type !== 'leave') {
            channelUnread[msgChannel] = (channelUnread[msgChannel] || 0) + 1;
            renderChannelTabs();
        }
    }

    container.appendChild(el);

    // Collapse consecutive job_created messages into a group
    if (msg.type === 'job_created') {
        _collapseJobBreadcrumbs(container, el);
    }

    if (msgChannel !== activeChannel) return;  // don't scroll for hidden messages

    if (autoScroll) {
        scrollToBottom();
    } else {
        unreadCount++;
        updateScrollAnchor();
    }
}

function getSenderClass(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'system';
    if (resolveAgent(s)) return 'agent';
    // Check base colors for offline agents
    const base = s.replace(/-\d+$/, '');
    if (base in baseColors) return 'agent';
    return 'user';
}

function resolveAgent(name) {
    const s = name.toLowerCase();
    if (s in agentConfig) return s;
    // Try prefix match: "gemini-cli" → "gemini"
    for (const key of Object.keys(agentConfig)) {
        if (s.startsWith(key)) return key;
    }
    return null;
}

function getColor(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'var(--system-color)';
    const resolved = resolveAgent(s);
    if (resolved) return agentConfig[resolved].color;
    // Fall back to base agent colors (for historical messages from offline agents)
    const base = s.replace(/-\d+$/, '');
    if (base in baseColors) return baseColors[base].color;
    return 'var(--user-color)';
}

function colorMentions(textHtml) {
    // Match any @word — we'll resolve color per match
    return textHtml.replace(/@(\w[\w-]*)/gi, (match, name) => {
        const lower = name.toLowerCase();
        if (lower === 'both' || lower === 'all') {
            return `<span class="mention" style="color: var(--accent)">@${name}</span>`;
        }
        const resolved = resolveAgent(lower);
        if (resolved) {
            const color = agentConfig[resolved].color;
            return `<span class="mention" style="color: ${color}">@${name}</span>`;
        }
        // Non-agent mention (e.g. @ben, @user) — use user color
        return `<span class="mention" style="color: var(--user-color)">@${name}</span>`;
    });
}

function scrollToBottom() {
    const timeline = document.getElementById('timeline');
    timeline.scrollTop = timeline.scrollHeight;
    unreadCount = 0;
    updateScrollAnchor();
}

function updateScrollAnchor() {
    const anchor = document.getElementById('scroll-anchor');
    if (autoScroll) {
        anchor.classList.add('hidden');
    } else {
        anchor.classList.remove('hidden');
        const badge = anchor.querySelector('.unread-badge');
        if (badge) {
            badge.textContent = unreadCount;
            badge.style.display = unreadCount > 0 ? 'flex' : 'none';
        }
    }
}

// --- Agents ---

function applyAgentConfig(data) {
    agentConfig = {};
    for (const [name, cfg] of Object.entries(data)) {
        agentConfig[name.toLowerCase()] = cfg;
    }
    buildStatusPills();
    buildMentionToggles();
    buildSoundSettings();
    // Re-color any messages already rendered (e.g. from a reconnect)
    recolorMessages();
    updateJobReplyTargetUI();
}

function recolorMessages() {
    const msgs = document.querySelectorAll('.message[data-id]');
    for (const el of msgs) {
        const sender = el.querySelector('.msg-sender');
        if (!sender) continue;
        const name = sender.textContent.trim();
        const color = getColor(name);
        sender.style.color = color;
        // Update bubble color
        const bubble = el.querySelector('.chat-bubble');
        if (bubble) bubble.style.setProperty('--bubble-color', color);
        // Update avatar color
        const avatar = el.querySelector('.avatar');
        if (avatar) avatar.style.backgroundColor = color;
        // Re-render markdown with updated mention colors and hashtags
        const textEl = el.querySelector('.msg-text');
        if (textEl && el.dataset.rawText) {
            textEl.innerHTML = styleHashtags(renderMarkdown(el.dataset.rawText));
            addCodeCopyButtons(el);
        }
    }
}

// --- Hats ---

function updateAllHats() {
    // Update hat overlays on all message avatars
    document.querySelectorAll('.avatar-wrap[data-agent]').forEach(wrap => {
        const agent = wrap.dataset.agent;
        const svg = agentHats[agent] || '';
        let overlay = wrap.querySelector('.hat-overlay');

        if (svg) {
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'hat-overlay';
                overlay.dataset.agent = agent;
                wrap.appendChild(overlay);
            }
            overlay.innerHTML = svg;
        } else {
            if (overlay) overlay.remove();
        }
    });
}

// --- Hat drag-to-trash ---

const TRASH_SVG = `<svg viewBox="0 0 20 20" fill="none" width="20" height="20"><rect x="4" y="6" width="12" height="12" rx="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M3 6h14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M8 3h4v3H8z" stroke="currentColor" stroke-width="1.2"/><rect class="trash-lid" x="3" y="4.5" width="14" height="2" rx="0.5" fill="currentColor" style="transform-origin: 10px 5.5px"/></svg>`;

let hatDragState = null;  // { agent, ghostEl, originRect, trashEl, wrapEl }

document.addEventListener('mousedown', (e) => {
    const overlay = e.target.closest('.hat-overlay');
    if (!overlay || hatDragState) return;
    e.preventDefault();

    const agent = overlay.dataset.agent;
    const wrap = overlay.closest('.avatar-wrap');
    if (!wrap) return;

    const rect = overlay.getBoundingClientRect();

    // Create drag ghost (fixed position, follows cursor)
    const ghost = document.createElement('div');
    ghost.className = 'hat-drag-ghost';
    ghost.innerHTML = overlay.innerHTML;
    ghost.style.width = rect.width + 'px';
    ghost.style.height = rect.height + 'px';
    ghost.style.left = rect.left + 'px';
    ghost.style.top = rect.top + 'px';
    document.body.appendChild(ghost);

    // Hide original overlay
    overlay.style.visibility = 'hidden';

    // Create trash can to the left of the avatar-wrap
    const trash = document.createElement('div');
    trash.className = 'hat-trash';
    trash.innerHTML = TRASH_SVG;
    wrap.appendChild(trash);
    // Force reflow then show
    trash.offsetHeight;
    trash.classList.add('visible');

    hatDragState = { agent, ghostEl: ghost, originRect: rect, trashEl: trash, wrapEl: wrap, overlayEl: overlay };
});

document.addEventListener('mousemove', (e) => {
    if (!hatDragState) return;
    const { ghostEl, trashEl } = hatDragState;

    // Move ghost to follow cursor (centered on cursor)
    ghostEl.style.left = (e.clientX - ghostEl.offsetWidth / 2) + 'px';
    ghostEl.style.top = (e.clientY - ghostEl.offsetHeight / 2) + 'px';

    // Check proximity to trash for highlight
    const trashRect = trashEl.getBoundingClientRect();
    const ghostCX = e.clientX;
    const ghostCY = e.clientY;
    const overTrash = ghostCX >= trashRect.left - 12 && ghostCX <= trashRect.right + 12 &&
                      ghostCY >= trashRect.top - 12 && ghostCY <= trashRect.bottom + 12;
    trashEl.classList.toggle('hover', overTrash);
});

document.addEventListener('mouseup', (e) => {
    if (!hatDragState) return;
    const { agent, ghostEl, originRect, trashEl, wrapEl, overlayEl } = hatDragState;

    // Check if dropped on trash
    const trashRect = trashEl.getBoundingClientRect();
    const overTrash = e.clientX >= trashRect.left - 12 && e.clientX <= trashRect.right + 12 &&
                      e.clientY >= trashRect.top - 12 && e.clientY <= trashRect.bottom + 12;

    if (overTrash) {
        // Snap ghost to trash center, shrink, fade out
        ghostEl.style.transition = 'all 0.25s ease-in';
        ghostEl.style.left = (trashRect.left + trashRect.width / 2 - ghostEl.offsetWidth / 2) + 'px';
        ghostEl.style.top = (trashRect.top + trashRect.height / 2 - ghostEl.offsetHeight / 2) + 'px';
        ghostEl.style.transform = 'scale(0.2)';
        ghostEl.style.opacity = '0';

        // Chomp animation on trash
        trashEl.classList.remove('hover');
        trashEl.classList.add('chomping');

        // Send DELETE to server
        fetch(`/api/hat/${agent}`, {
            method: 'DELETE',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        }).catch(err => console.error('Hat delete failed:', err));

        // Cleanup after animation
        setTimeout(() => {
            ghostEl.remove();
            trashEl.remove();
            if (overlayEl) overlayEl.remove();
        }, 600);
    } else {
        // Return ghost to original position
        ghostEl.style.transition = 'all 0.3s ease';
        ghostEl.style.left = originRect.left + 'px';
        ghostEl.style.top = originRect.top + 'px';

        // Fade out trash
        trashEl.classList.remove('hover', 'visible');

        setTimeout(() => {
            ghostEl.remove();
            trashEl.remove();
            overlayEl.style.visibility = '';
        }, 300);
    }

    hatDragState = null;
});

// Cancel hat drag on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && hatDragState) {
        const { ghostEl, originRect, trashEl, overlayEl } = hatDragState;
        ghostEl.style.transition = 'all 0.3s ease';
        ghostEl.style.left = originRect.left + 'px';
        ghostEl.style.top = originRect.top + 'px';
        trashEl.classList.remove('hover', 'visible');
        setTimeout(() => {
            ghostEl.remove();
            trashEl.remove();
            overlayEl.style.visibility = '';
        }, 300);
        hatDragState = null;
    }
});

function buildStatusPills() {
    const container = document.getElementById('agent-status');
    container.innerHTML = '';
    for (const [name, cfg] of Object.entries(agentConfig)) {
        const pill = document.createElement('div');
        pill.className = 'status-pill';
        if (cfg.state === 'pending') pill.classList.add('pending');
        pill.id = `status-${name}`;
        pill.title = `@${name}`;  // Tooltip: canonical name for manual @-typing
        pill.style.setProperty('--agent-color', cfg.color || '#4ade80');
        pill.innerHTML = `<span class="status-dot"></span><span class="status-label">${escapeHtml(cfg.label || name)}</span>`;
        // Left-click to rename or name pending instance
        pill.addEventListener('click', () => {
            const mode = cfg.state === 'pending' ? 'pending' : 'rename';
            showAgentNameModal({
                name, label: cfg.label || name, color: cfg.color || '#888',
                base: cfg.base || '', mode,
            });
        });
        container.appendChild(pill);
    }
    enableDragScroll(container);
}

// --- Agent naming lightbox ---

const _pendingNameQueue = [];
let _nameModalActive = false;

function _showNextPendingName() {
    if (_nameModalActive || _pendingNameQueue.length === 0) return;
    const next = _pendingNameQueue.shift();
    // Only show if still pending in agentConfig
    const cfg = agentConfig[next.name];
    if (cfg && cfg.state === 'pending') {
        showAgentNameModal({ ...next, mode: 'pending' });
    } else {
        _showNextPendingName(); // skip stale entries
    }
}

function showAgentNameModal(opts) {
    // opts: { name, label, color, base, mode: 'pending' | 'rename' }
    _nameModalActive = true;
    let modal = document.getElementById('agent-name-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'agent-name-modal';
        modal.className = 'agent-name-modal hidden';
        modal.innerHTML = `
            <div class="agent-name-dialog">
                <div class="agent-name-header">
                    <div class="agent-name-avatar"></div>
                    <h3 class="agent-name-title"></h3>
                </div>
                <p class="agent-name-subtitle"></p>
                <input type="text" class="agent-name-input" maxlength="24" spellcheck="false" autocomplete="off" />
                <div class="agent-name-actions">
                    <button class="agent-name-cancel">Cancel</button>
                    <button class="agent-name-confirm">Confirm</button>
                </div>
            </div>`;
        // Close on backdrop click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) _closeAgentNameModal();
        });
        document.body.appendChild(modal);
    }

    const avatarEl = modal.querySelector('.agent-name-avatar');
    const titleEl = modal.querySelector('.agent-name-title');
    const subtitleEl = modal.querySelector('.agent-name-subtitle');
    const inputEl = modal.querySelector('.agent-name-input');
    const cancelBtn = modal.querySelector('.agent-name-cancel');
    const confirmBtn = modal.querySelector('.agent-name-confirm');

    // Set agent color accent
    modal.style.setProperty('--agent-color', opts.color);

    // Avatar from brand
    const brandKey = opts.base || opts.name.replace(/-\d+$/, '');
    avatarEl.innerHTML = BRAND_AVATARS[brandKey] || USER_AVATAR;
    avatarEl.style.background = opts.color;

    if (opts.mode === 'pending') {
        const familyLabel = (baseColors[opts.base] || {}).label || opts.base || 'agent';
        titleEl.textContent = 'Name this agent';
        subtitleEl.textContent = `A new ${familyLabel} instance connected`;
    } else {
        titleEl.textContent = 'Rename agent';
        subtitleEl.textContent = `Current ID: @${opts.name}`;
    }

    inputEl.value = opts.label;
    inputEl.placeholder = opts.label;

    // Remove old listeners by cloning
    const newCancel = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancel, cancelBtn);
    const newConfirm = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirm, confirmBtn);

    newCancel.addEventListener('click', () => _closeAgentNameModal());
    newConfirm.addEventListener('click', () => {
        const label = inputEl.value.trim();
        if (!label) return;
        if (ws && ws.readyState === WebSocket.OPEN) {
            if (opts.mode === 'pending') {
                ws.send(JSON.stringify({ type: 'name_pending', name: opts.name, label }));
            } else {
                ws.send(JSON.stringify({ type: 'rename_agent', name: opts.name, label }));
            }
        }
        _closeAgentNameModal();
    });

    // Enter key confirms
    inputEl.onkeydown = (e) => {
        if (e.key === 'Enter') { newConfirm.click(); e.preventDefault(); }
        if (e.key === 'Escape') { _closeAgentNameModal(); e.preventDefault(); }
    };

    modal.classList.remove('hidden');
    // Focus and select input text after animation frame
    requestAnimationFrame(() => { inputEl.focus(); inputEl.select(); });
}

function _closeAgentNameModal() {
    const modal = document.getElementById('agent-name-modal');
    if (modal) modal.classList.add('hidden');
    _nameModalActive = false;
    // Show next pending if queued
    setTimeout(_showNextPendingName, 200);
}

// --- Bubble role picker ---

function showBubbleRolePicker(btn, agentName) {
    // Close any existing picker and reset z-index on its parent message
    document.querySelectorAll('.bubble-role-picker').forEach(p => {
        const msg = p.closest('.message');
        if (msg) msg.style.zIndex = '';
        p.remove();
    });

    const ROLE_PRESETS = [
        { label: 'Planner', emoji: '📋' },
        { label: 'Designer', emoji: '✨' },
        { label: 'Architect', emoji: '🏛️' },
        { label: 'Builder', emoji: '🔨' },
        { label: 'Reviewer', emoji: '🔍' },
        { label: 'Researcher', emoji: '🔬' },
        { label: 'Red Team', emoji: '🛡️' },
        { label: 'Wry', emoji: '🍸' },
        { label: 'Unhinged', emoji: '🤪' },
        { label: 'Hype', emoji: '🎉' },
    ];

    const currentRole = (_agentRoles[agentName] || '').toLowerCase();
    const picker = document.createElement('div');
    picker.className = 'bubble-role-picker';
    const closePicker = () => { if (msgEl) msgEl.style.zIndex = ''; picker.remove(); };

    // None chip
    const noneChip = document.createElement('button');
    noneChip.className = 'role-preset-chip' + (!currentRole ? ' active' : '');
    noneChip.textContent = 'None';
    noneChip.addEventListener('click', () => { _setRole(agentName, ''); closePicker(); });
    picker.appendChild(noneChip);

    for (const preset of ROLE_PRESETS) {
        const chip = document.createElement('button');
        chip.className = 'role-preset-chip' + (currentRole === preset.label.toLowerCase() ? ' active' : '');
        chip.textContent = `${preset.emoji} ${preset.label}`;
        chip.addEventListener('click', () => { _setRole(agentName, preset.label); closePicker(); });
        picker.appendChild(chip);
    }

    // Custom text input
    const customRow = document.createElement('div');
    customRow.className = 'bubble-role-custom';
    const customInput = document.createElement('input');
    customInput.type = 'text';
    customInput.className = 'bubble-role-input';
    customInput.placeholder = 'Custom...';
    customInput.maxLength = 30;
    customInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const val = customInput.value.trim();
            if (val) { _setRole(agentName, val); closePicker(); }
            e.preventDefault();
        }
        if (e.key === 'Escape') { closePicker(); }
    });
    customRow.appendChild(customInput);
    picker.appendChild(customRow);

    // Place inside the chat-bubble, positioned below the clicked button
    const bubble = btn.closest('.chat-bubble');
    const msgEl = btn.closest('.message');
    if (msgEl) msgEl.style.zIndex = '50';
    bubble.appendChild(picker);

    // Position picker below the button that was clicked
    requestAnimationFrame(() => {
        const btnRect = btn.getBoundingClientRect();
        const bubbleRect = bubble.getBoundingClientRect();
        picker.style.top = (btnRect.bottom - bubbleRect.top + 4) + 'px';
        picker.style.left = (btnRect.left - bubbleRect.left) + 'px';
        picker.style.right = 'auto';

        // Flip upward if picker would overflow below the footer/viewport
        const pickerRect = picker.getBoundingClientRect();
        const footerEl = document.querySelector('footer');
        const maxBottom = footerEl ? footerEl.getBoundingClientRect().top : window.innerHeight - 20;
        if (pickerRect.bottom > maxBottom) {
            picker.style.top = 'auto';
            picker.style.bottom = (bubbleRect.bottom - btnRect.top + 4) + 'px';
        }
        // Nudge left if overflowing right edge
        if (pickerRect.right > window.innerWidth - 10) {
            picker.style.left = 'auto';
            picker.style.right = '0';
        }
    });

    // Close on outside click (next tick to avoid catching the current click)
    setTimeout(() => {
        const closeHandler = (e) => {
            if (!picker.contains(e.target)) {
                closePicker();
                document.removeEventListener('click', closeHandler, true);
            }
        };
        document.addEventListener('click', closeHandler, true);
    }, 0);
}

function _syncBubbleRolePills(agentName) {
    const role = String(_agentRoles[agentName] || '').trim();
    const pillText = role || 'choose a role';
    document.querySelectorAll('.message').forEach(msg => {
        const senderEl = msg.querySelector('.msg-sender');
        const btn = msg.querySelector('.bubble-role');
        if (!btn || !senderEl || senderEl.textContent !== agentName) return;
        btn.textContent = pillText;
        btn.title = role || 'Set role';
        btn.classList.toggle('has-role', !!role);
    });
}

function _setRole(agentName, role) {
    fetch(`/api/roles/${agentName}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
        body: JSON.stringify({ role }),
    });
    // Optimistic update
    _agentRoles[agentName] = role;
    _syncBubbleRolePills(agentName);
}

// --- Status ---

const _agentRoles = {};  // name → role string

function fetchRoles() {
    fetch('/api/roles').then(r => r.json()).then(roles => {
        Object.assign(_agentRoles, roles);
        for (const name of Object.keys(roles || {})) {
            _syncBubbleRolePills(name);
        }
    }).catch(() => {});
}

const _ROLE_EMOJI = {
    'planner': '📋', 'builder': '🔨', 'reviewer': '🔍', 'researcher': '🔬',
    'chaos gremlin': '😈', 'red team': '🛡️', 'roast': '🔥', 'hype': '🎉',
};

function updateStatus(data) {
    for (const [name, info] of Object.entries(data)) {
        if (name === 'paused') continue;
        const pill = document.getElementById(`status-${name}`);
        if (!pill) continue;

        pill.classList.remove('available', 'working', 'offline');
        // Pending pills keep their pending animation (set in buildStatusPills)
        if (!pill.classList.contains('pending')) {
            if (info.busy && info.available) {
                pill.classList.add('working');
            } else if (info.available) {
                pill.classList.add('available');
            } else {
                pill.classList.add('offline');
            }
        }

        // Keep agent color in sync
        if (info.color) pill.style.setProperty('--agent-color', info.color);

        // Track role (displayed on bubbles, not on pill)
        if (info.role !== undefined) {
            _agentRoles[name] = info.role;
            _syncBubbleRolePills(name);
        }
    }
}

function updateTyping(agent, active) {
    const indicator = document.getElementById('typing-indicator');
    if (active) {
        indicator.querySelector('.typing-name').textContent = agent;
        indicator.classList.remove('hidden');
        if (autoScroll) scrollToBottom();
    } else {
        indicator.classList.add('hidden');
    }
}

// --- Settings ---

let pendingChannelSwitch = null;

function applySettings(data) {
    if (data.title) {
        document.getElementById('room-title').textContent = data.title;
        document.title = data.title;
    }
    if (data.username) {
        username = data.username;
        document.getElementById('sender-label').textContent = username;
        document.getElementById('setting-username').value = username;
    }
    if (data.font) {
        document.body.classList.remove('font-mono', 'font-serif', 'font-sans');
        document.body.classList.add('font-' + data.font);
        document.getElementById('setting-font').value = data.font;
    }
    if (data.max_agent_hops !== undefined) {
        document.getElementById('setting-hops').value = data.max_agent_hops;
    }
    if (data.history_limit !== undefined) {
        document.getElementById('setting-history').value = String(data.history_limit);
    }
    if (data.contrast) {
        document.body.classList.toggle('high-contrast', data.contrast === 'high');
        document.getElementById('setting-contrast').value = data.contrast;
    }
    if (data.channels && Array.isArray(data.channels)) {
        channelList = data.channels;
        // If active channel was deleted, switch to general
        if (!channelList.includes(activeChannel)) {
            activeChannel = 'general';
            localStorage.setItem('agentchattr-channel', 'general');
            filterMessagesByChannel();
        }
        renderChannelTabs();

        if (pendingChannelSwitch && channelList.includes(pendingChannelSwitch)) {
            const name = pendingChannelSwitch;
            pendingChannelSwitch = null;
            switchChannel(name);
        }
    }
}

function toggleSettings() {
    const bar = document.getElementById('settings-bar');
    bar.classList.toggle('hidden');
    document.getElementById('settings-toggle').classList.toggle('active', !bar.classList.contains('hidden'));
    if (!bar.classList.contains('hidden')) {
        document.getElementById('setting-username').focus();
    }
}

function clearChat() {
    if (!confirm(`Clear all messages in #${activeChannel}? This cannot be undone.`)) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username, channel: activeChannel }));
    }
    document.getElementById('settings-bar').classList.add('hidden');
}

function saveSettings() {
    const newUsername = document.getElementById('setting-username').value.trim();
    const newFont = document.getElementById('setting-font').value;
    const newHops = document.getElementById('setting-hops').value;
    const histVal = document.getElementById('setting-history').value;
    const newHistory = histVal === 'all' ? 'all' : (parseInt(histVal) || 50);
    const newContrast = document.getElementById('setting-contrast').value;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'update_settings',
            data: {
                username: newUsername || 'user',
                font: newFont,
                max_agent_hops: parseInt(newHops) || 4,
                history_limit: newHistory,
                contrast: newContrast,
            }
        }));
    }
}

function setupSettingsKeys() {
    // Auto-save on blur/Enter for text/number fields
    for (const id of ['setting-username', 'setting-hops']) {
        const el = document.getElementById(id);
        el.addEventListener('blur', () => saveSettings());
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                el.blur();
            }
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
    }

    // Auto-save on change for selects, escape to close
    for (const id of ['setting-font', 'setting-history', 'setting-contrast']) {
        const el = document.getElementById(id);
        el.addEventListener('change', () => {
            // Apply contrast immediately (don't wait for server round-trip)
            if (id === 'setting-contrast') {
                document.body.classList.toggle('high-contrast', el.value === 'high');
            }
            saveSettings();
        });
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
    }
}

// --- Keyboard shortcuts ---

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('image-modal');
        const modalOpen = modal && !modal.classList.contains('hidden');

        if (e.key === 'Escape') {
            const nameModal = document.getElementById('agent-name-modal');
            if (nameModal && !nameModal.classList.contains('hidden')) { _closeAgentNameModal(); return; }
            const convertModal = document.getElementById('convert-job-modal');
            if (convertModal && !convertModal.classList.contains('hidden')) { closeConvertJobModal(); return; }
            const deleteJobModal = document.getElementById('delete-job-modal');
            if (deleteJobModal && !deleteJobModal.classList.contains('hidden')) { closeDeleteJobModal(); return; }
            if (modalOpen) { closeImageModal(); return; }
            if (replyingTo) { cancelReply(); }
        }
        if (modalOpen && e.key === 'ArrowLeft') { e.preventDefault(); modalPrev(e); }
        if (modalOpen && e.key === 'ArrowRight') { e.preventDefault(); modalNext(e); }

    });
}

// --- Slash command menu ---

function showSlashHint(text) {
    const input = document.getElementById('input');
    if (!input) return;
    const original = input.placeholder;
    input.placeholder = text;
    input.classList.add('slash-hint-active');
    setTimeout(() => {
        input.placeholder = original;
        input.classList.remove('slash-hint-active');
    }, 3000);
}

const SLASH_COMMANDS = [
    { cmd: '/artchallenge', desc: 'SVG art challenge — all agents create artwork (optional theme)', broadcast: true },
    { cmd: '/hatmaking', desc: 'All agents design a hat to wear on their avatar', broadcast: true },
    { cmd: '/roastreview', desc: 'Get all agents to review and roast each other\'s work', broadcast: true },
    { cmd: '/poetry haiku', desc: 'Agents write a haiku about the codebase', broadcast: true },
    { cmd: '/poetry limerick', desc: 'Agents write a limerick about the codebase', broadcast: true },
    { cmd: '/poetry sonnet', desc: 'Agents write a sonnet about the codebase', broadcast: true },
    { cmd: '/summary', desc: 'Summarize recent messages — tag an agent (e.g. /summary @claude)', broadcast: false, needsMention: true },
    { cmd: '/summarise', desc: 'Summarize recent messages — tag an agent (e.g. /summarise @claude)', broadcast: false, needsMention: true, hidden: true },
    { cmd: '/continue', desc: 'Resume after loop guard pauses', broadcast: false },
    { cmd: '/clear', desc: 'Clear messages in current channel', broadcast: false },
];

let slashMenuIndex = 0;
let slashMenuVisible = false;
let mentionMenuIndex = 0;
let mentionMenuVisible = false;
let mentionMenuStart = -1;  // cursor position of the '@'

function updateSlashMenu(text) {
    const menu = document.getElementById('slash-menu');
    if (!text.startsWith('/') || text.includes(' ') && !text.startsWith('/poetry')) {
        menu.classList.add('hidden');
        slashMenuVisible = false;
        return;
    }

    const query = text.toLowerCase();
    const matches = SLASH_COMMANDS.filter(c => !c.hidden && c.cmd.startsWith(query));

    if (matches.length === 0 || (matches.length === 1 && matches[0].cmd === query)) {
        menu.classList.add('hidden');
        slashMenuVisible = false;
        return;
    }

    menu.innerHTML = '';
    slashMenuIndex = Math.min(slashMenuIndex, matches.length - 1);

    matches.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'slash-item' + (i === slashMenuIndex ? ' active' : '');
        row.innerHTML = `<span class="slash-cmd">${escapeHtml(item.cmd)}</span><span class="slash-desc">${escapeHtml(item.desc)}</span>`;
        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectSlashCommand(item.cmd);
        });
        row.addEventListener('mouseenter', () => {
            slashMenuIndex = i;
            menu.querySelectorAll('.slash-item').forEach((el, j) => el.classList.toggle('active', j === i));
        });
        menu.appendChild(row);
    });

    menu.classList.remove('hidden');
    slashMenuVisible = true;
}

function selectSlashCommand(cmd) {
    const input = document.getElementById('input');
    input.value = cmd;
    input.focus();
    document.getElementById('slash-menu').classList.add('hidden');
    slashMenuVisible = false;
}

// --- Mention autocomplete ---

function getMentionCandidates() {
    // Build list: registered agents + "all agents" + username (self) + known humans
    const candidates = [];
    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;
        candidates.push({ name, label: cfg.label || name, color: cfg.color });
    }
    candidates.push({ name: 'all agents', label: 'all agents', color: 'var(--accent)' });
    return candidates;
}

function updateMentionMenu() {
    const menu = document.getElementById('mention-menu');
    const input = document.getElementById('input');
    const text = input.value;
    const cursor = input.selectionStart;

    // Don't show if slash menu is active
    if (slashMenuVisible) {
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    // Find the '@' before cursor that starts this mention
    let atPos = -1;
    for (let i = cursor - 1; i >= 0; i--) {
        if (text[i] === '@') { atPos = i; break; }
        // Allow spaces if we are still matching a multi-word label like "all agents"
        if (!/[\w\-\s]/.test(text[i])) break;
        // Optimization: don't look back more than 30 chars
        if (cursor - i > 30) break;
    }

    if (atPos < 0 || (atPos > 0 && /\w/.test(text[atPos - 1]))) {
        // No @ found, or @ is mid-word (e.g. email)
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    const query = text.slice(atPos + 1, cursor).toLowerCase();
    mentionMenuStart = atPos;

    const candidates = getMentionCandidates();
    const matches = candidates.filter(c =>
        c.name.toLowerCase().includes(query) || c.label.toLowerCase().includes(query)
    );

    if (matches.length === 0) {
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    menu.innerHTML = '';
    mentionMenuIndex = Math.min(mentionMenuIndex, matches.length - 1);

    matches.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'mention-item' + (i === mentionMenuIndex ? ' active' : '');
        row.dataset.name = item.name;
        row.innerHTML = `<span class="mention-dot" style="background: ${item.color}"></span><span class="mention-name">${escapeHtml(item.label)}</span>`;
        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectMention(item.name);
        });
        row.addEventListener('mouseenter', () => {
            mentionMenuIndex = i;
            menu.querySelectorAll('.mention-item').forEach((el, j) => el.classList.toggle('active', j === i));
        });
        menu.appendChild(row);
    });

    menu.classList.remove('hidden');
    mentionMenuVisible = true;
}

let _lastMentionedAgent = ''; // track most recent mention for auto-assignment

function selectMention(name) {
    const input = document.getElementById('input');
    _lastMentionedAgent = name; // remember for auto-assigning jobs
    const text = input.value;
    const cursor = input.selectionStart;
    // Replace from @ to cursor with @name + space
    const before = text.slice(0, mentionMenuStart);
    const after = text.slice(cursor);
    const mention = `@${name} `;
    input.value = before + mention + after;
    const newPos = mentionMenuStart + mention.length;
    input.setSelectionRange(newPos, newPos);
    input.focus();
    document.getElementById('mention-menu').classList.add('hidden');
    mentionMenuVisible = false;
}

// --- Input ---

function setupInput() {
    const input = document.getElementById('input');

    input.addEventListener('keydown', (e) => {
        if (mentionMenuVisible) {
            const menu = document.getElementById('mention-menu');
            const items = menu.querySelectorAll('.mention-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[mentionMenuIndex];
                if (active) {
                    selectMention(active.dataset.name);
                }
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                mentionMenuVisible = false;
                return;
            }
        }
        if (slashMenuVisible) {
            const menu = document.getElementById('slash-menu');
            const items = menu.querySelectorAll('.slash-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[slashMenuIndex];
                if (active) selectSlashCommand(active.querySelector('.slash-cmd').textContent);
                if (e.key === 'Enter') sendMessage();
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                slashMenuVisible = false;
                return;
            }
        }
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize + slash menu + mention menu
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        updateSlashMenu(input.value);
        updateMentionMenu();
    });
}

function sendMessage() {
    const input = document.getElementById('input');
    let text = input.value.trim();

    if (!text && pendingAttachments.length === 0) return;

    // Prepend active mention toggles if the message doesn't already mention them
    // Skip for non-broadcast slash commands (e.g. /clear, /continue)
    let skipMentions = false;
    if (text.startsWith('/')) {
        const cmdWord = text.split(/\s/)[0].toLowerCase();
        const matchedCmd = SLASH_COMMANDS.find(c => c.cmd.startsWith(cmdWord) || cmdWord.startsWith(c.cmd.split(/\s/)[0]));
        if (matchedCmd && !matchedCmd.broadcast) {
            skipMentions = true;
        }
        // Commands that need an @mention — show hint and keep command in input
        if (matchedCmd && matchedCmd.needsMention && !/@\w/.test(text)) {
            const canonical = matchedCmd.cmd.split(/\s/)[0];  // e.g. '/summary'
            input.value = canonical + ' @';
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
            showSlashHint(`Tag an agent: ${canonical} @claude`);
            // Trigger mention autocomplete for the '@'
            input.dispatchEvent(new Event('input', { bubbles: true }));
            return;
        }
    }
    if (activeMentions.size > 0 && text && !skipMentions) {
        const prefix = [...activeMentions].map(n => `@${n}`).join(' ');
        // Only prepend if user didn't already @mention these agents
        const lower = text.toLowerCase();
        const missing = [...activeMentions].filter(n => !lower.includes(`@${n}`));
        if (missing.length > 0) {
            text = missing.map(n => `@${n}`).join(' ') + ' ' + text;
        }
    }

    const payload = {
        type: 'message',
        text: text,
        sender: username,
        channel: activeChannel,
        attachments: pendingAttachments.map(a => ({
            path: a.path,
            name: a.name,
            url: a.url,
        })),
    };
    if (replyingTo) {
        payload.reply_to = replyingTo.id;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
    }

    input.value = '';
    input.style.height = 'auto';
    clearAttachments();
    cancelReply();
    input.focus();
}

// --- Image paste/drop ---

function setupPaste() {
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        // Route to job upload if job input is focused
        const jobInput = document.getElementById('jobs-conv-input-text');
        const isJobFocused = jobInput && document.activeElement === jobInput;

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (isJobFocused) {
                    await uploadJobImage(file);
                } else {
                    await uploadImage(file);
                }
            }
        }
    });
}

function setupDragDrop() {
    const dropzone = document.getElementById('dropzone');
    let dragCount = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCount++;
        if (e.dataTransfer?.types?.includes('Files')) {
            dropzone.classList.remove('hidden');
        }
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCount--;
        if (dragCount <= 0) {
            dragCount = 0;
            dropzone.classList.add('hidden');
        }
    });

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    document.addEventListener('drop', async (e) => {
        e.preventDefault();
        dragCount = 0;
        dropzone.classList.add('hidden');

        const files = e.dataTransfer?.files;
        if (!files) return;

        for (const file of files) {
            if (file.type.startsWith('image/')) {
                await uploadImage(file);
            }
        }
    });
}

async function uploadImage(file) {
    const form = new FormData();
    form.append('file', file);

    try {
        const resp = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN }, body: form });
        const data = await resp.json();

        pendingAttachments.push({
            path: data.path,
            name: data.name,
            url: data.url,
        });

        renderAttachments();
    } catch (err) {
        console.error('Upload failed:', err);
    }
}

function renderAttachments() {
    const container = document.getElementById('attachments');
    container.innerHTML = '';

    pendingAttachments.forEach((att, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'attachment-preview';
        wrap.innerHTML = `
            <img src="${att.url}" alt="${escapeHtml(att.name)}">
            <button class="remove-btn" onclick="removeAttachment(${i})">x</button>
        `;
        container.appendChild(wrap);
    });
}

function removeAttachment(index) {
    pendingAttachments.splice(index, 1);
    renderAttachments();
}

function clearAttachments() {
    pendingAttachments = [];
    document.getElementById('attachments').innerHTML = '';
}

// --- Scroll tracking ---

function setupScroll() {
    const timeline = document.getElementById('timeline');
    const messages = document.getElementById('messages');

    timeline.addEventListener('scroll', () => {
        const distFromBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        autoScroll = distFromBottom < 60;

        if (autoScroll) {
            unreadCount = 0;
        }
        updateScrollAnchor();
    });

    // Keep pinned to bottom when content changes (e.g. images load)
    const resizeObserver = new ResizeObserver(() => {
        if (autoScroll) {
            scrollToBottom();
        }
    });
    resizeObserver.observe(messages);
}

// --- Reply ---

function copyMessage(msgId, event) {
    if (event) event.stopPropagation();
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    const msgText = el.querySelector('.msg-text');
    const html = msgText?.innerHTML || '';
    const markdown = el.dataset.rawText || msgText?.innerText || '';
    const done = () => {
        const btn = el.querySelector('.bubble-copy');
        if (btn) {
            btn.classList.add('copied');
            setTimeout(() => btn.classList.remove('copied'), 1500);
        }
    };
    // Rich HTML + raw markdown — rich editors get HTML, code/markdown editors get source
    if (navigator.clipboard.write) {
        navigator.clipboard.write([new ClipboardItem({
            'text/html': new Blob([html], {type: 'text/html'}),
            'text/plain': new Blob([markdown], {type: 'text/plain'}),
        })]).then(done);
    } else {
        navigator.clipboard.writeText(markdown).then(done);
    }
}

function startReply(msgId, event) {
    if (event) event.stopPropagation();
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    const sender = el.querySelector('.msg-sender')?.textContent?.trim() || '?';
    const text = el.dataset.rawText || el.querySelector('.msg-text')?.textContent || '';
    replyingTo = { id: msgId, sender, text };
    renderReplyPreview();

    // Auto-activate mention chip for the replied-to sender, deactivate others
    const resolved = resolveAgent(sender.toLowerCase());
    if (resolved) {
        for (const btn of document.querySelectorAll('.mention-toggle')) {
            const agent = btn.dataset.agent;
            if (agent === resolved) {
                activeMentions.add(agent);
                btn.classList.add('active');
            } else {
                activeMentions.delete(agent);
                btn.classList.remove('active');
            }
        }
    }

    document.getElementById('input').focus();
}

function renderReplyPreview() {
    let container = document.getElementById('reply-preview');
    if (!replyingTo) {
        if (container) container.remove();
        return;
    }
    if (!container) {
        container = document.createElement('div');
        container.id = 'reply-preview';
        const inputRow = document.getElementById('input-row');
        inputRow.parentNode.insertBefore(container, inputRow);
    }
    const truncated = replyingTo.text.length > 100 ? replyingTo.text.slice(0, 100) + '...' : replyingTo.text;
    const color = getColor(replyingTo.sender);
    container.innerHTML = `<span class="reply-preview-label">replying to</span> <span style="color: ${color}; font-weight: 600">${escapeHtml(replyingTo.sender)}</span>: ${escapeHtml(truncated)} <button class="dismiss-btn reply-cancel" onclick="cancelReply()">&times;</button>`;
}

function cancelReply() {
    replyingTo = null;
    const el = document.getElementById('reply-preview');
    if (el) el.remove();
}

function scrollToMessage(msgId) {
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('highlight');
    setTimeout(() => el.classList.remove('highlight'), 1500);
}

// --- Todos ---

function todoStatusLabel(status) {
    if (!status) return 'pin';
    if (status === 'todo') return 'done?';
    return 'unpin';
}

function todoCycle(msgId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const status = todos[msgId] || null;
    if (!status) {
        ws.send(JSON.stringify({ type: 'todo_add', id: msgId }));
    } else if (status === 'todo') {
        ws.send(JSON.stringify({ type: 'todo_toggle', id: msgId }));
    } else {
        // done → remove
        ws.send(JSON.stringify({ type: 'todo_remove', id: msgId }));
    }
}

function todoAdd(msgId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'todo_add', id: msgId }));
}

function todoToggle(msgId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'todo_toggle', id: msgId }));
}

function todoRemove(msgId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'todo_remove', id: msgId }));
}

function updateTodoState(msgId, status) {
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;

    el.classList.remove('msg-todo', 'msg-todo-todo', 'msg-todo-done');

    if (status === 'todo') {
        el.classList.add('msg-todo', 'msg-todo-todo');
    } else if (status === 'done') {
        el.classList.add('msg-todo', 'msg-todo-done');
    }

    const hint = el.querySelector('.todo-hint');
    if (hint) hint.textContent = todoStatusLabel(status);

    // Update panel if open
    const panel = document.getElementById('pins-panel');
    if (!panel.classList.contains('hidden')) renderTodosPanel();
}

// --- Delete mode ---

let deleteMode = false;
let deleteSelected = new Set();
let deleteDragging = false;

function deleteClick(msgId, event) {
    event.stopPropagation();
    enterDeleteMode(msgId);
}

function enterDeleteMode(initialId) {
    if (deleteMode) return;
    deleteMode = true;
    deleteSelected.clear();
    if (initialId != null) deleteSelected.add(initialId);

    // Add delete-mode class — children transform right (no layout reflow)
    document.getElementById('messages').classList.add('delete-mode');

    // Add radio circles to all messages (not joins)
    document.querySelectorAll('.message[data-id]').forEach(el => {
        if (el.classList.contains('join-msg') || el.classList.contains('system-msg') || el.classList.contains('summary-msg')) return;
        const id = parseInt(el.dataset.id);
        const circle = document.createElement('div');
        circle.className = 'delete-radio' + (deleteSelected.has(id) ? ' selected' : '');
        circle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            toggleDeleteSelect(id);
            deleteDragging = true;
        });
        circle.addEventListener('mouseenter', () => {
            if (deleteDragging) toggleDeleteSelect(id, true);
        });
        el.prepend(circle);
    });

    // Show floating delete bar
    showDeleteBar();
    updateDeleteBar();
    document.getElementById('scroll-anchor').style.bottom = '180px';
}

function toggleDeleteSelect(id, dragForceSelect) {
    const el = document.querySelector(`.message[data-id="${id}"]`);
    if (!el) return;
    const circle = el.querySelector('.delete-radio');

    if (dragForceSelect) {
        deleteSelected.add(id);
        if (circle) circle.classList.add('selected');
    } else {
        if (deleteSelected.has(id)) {
            deleteSelected.delete(id);
            if (circle) circle.classList.remove('selected');
        } else {
            deleteSelected.add(id);
            if (circle) circle.classList.add('selected');
        }
    }
    updateDeleteBar();
}

function showDeleteBar() {
    let bar = document.getElementById('delete-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'delete-bar';
        bar.innerHTML = `<button class="delete-bar-cancel" onclick="exitDeleteMode()">Cancel</button><span class="delete-bar-count"></span><button class="delete-bar-confirm" onclick="confirmDelete()">Delete</button>`;
        const footer = document.querySelector('footer');
        footer.parentNode.insertBefore(bar, footer);
    }
    bar.classList.remove('hidden');
}

function updateDeleteBar() {
    const count = deleteSelected.size;
    const span = document.querySelector('.delete-bar-count');
    if (span) span.textContent = count > 0 ? `${count} selected` : 'Select messages';
    const btn = document.querySelector('.delete-bar-confirm');
    if (btn) {
        btn.textContent = count > 0 ? `Delete (${count})` : 'Delete';
        btn.disabled = count === 0;
    }
}

function confirmDelete() {
    if (!ws || deleteSelected.size === 0) return;
    ws.send(JSON.stringify({ type: 'delete', ids: [...deleteSelected] }));
    exitDeleteMode();
}

function exitDeleteMode() {
    deleteMode = false;
    deleteSelected.clear();
    deleteDragging = false;

    // Remove delete-mode — children transform back (no layout reflow)
    document.getElementById('messages').classList.remove('delete-mode');

    // Collapse bar
    const bar = document.getElementById('delete-bar');
    if (bar) {
        bar.classList.add('hidden');
    }

    // Fade out radios then remove
    document.querySelectorAll('.delete-radio').forEach(el => {
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.2s';
        setTimeout(() => el.remove(), 200);
    });

    document.getElementById('scroll-anchor').style.bottom = '';
}

// Auto-scroll while dragging near edges
let deleteScrollInterval = null;
document.addEventListener('mousemove', (e) => {
    if (!deleteDragging) return;
    const timeline = document.getElementById('timeline');
    const rect = timeline.getBoundingClientRect();
    const edgeZone = 60;

    if (e.clientY < rect.top + edgeZone) {
        // Near top — scroll up
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop -= 8, 16);
        }
    } else if (e.clientY > rect.bottom - edgeZone) {
        // Near bottom — scroll down
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop += 8, 16);
        }
    } else if (deleteScrollInterval) {
        clearInterval(deleteScrollInterval);
        deleteScrollInterval = null;
    }
});

// Stop drag on mouseup
document.addEventListener('mouseup', () => {
    deleteDragging = false;
    if (deleteScrollInterval) {
        clearInterval(deleteScrollInterval);
        deleteScrollInterval = null;
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && deleteMode) exitDeleteMode();
});

function handleDeleteBroadcast(ids) {
    for (const id of ids) {
        const el = document.querySelector(`.message[data-id="${id}"]`);
        if (el) el.remove();
        // Clean from todos
        delete todos[id];
    }
    // Refresh todos panel if open
    const panel = document.getElementById('pins-panel');
    if (panel && !panel.classList.contains('hidden')) renderTodosPanel();
}

function togglePinsPanel() {
    _preserveScroll(() => {
        const panel = document.getElementById('pins-panel');
        panel.classList.toggle('hidden');
        document.getElementById('pins-toggle').classList.toggle('active', !panel.classList.contains('hidden'));
        if (!panel.classList.contains('hidden')) {
            renderTodosPanel();
        }
    });
}

function renderTodosPanel() {
    const list = document.getElementById('pins-list');
    list.innerHTML = '';

    const todoIds = Object.keys(todos);
    if (todoIds.length === 0) {
        list.innerHTML = '<div class="pins-empty">No pinned messages</div>';
        return;
    }

    // Chronological order (by message ID)
    const sorted = todoIds.map(Number).sort((a, b) => a - b);

    for (const id of sorted) {
        const el = document.querySelector(`.message[data-id="${id}"]`);
        if (!el) continue;

        const status = todos[id];
        const item = document.createElement('div');
        item.className = `todo-item ${status === 'done' ? 'todo-done' : ''}`;

        const time = el.querySelector('.msg-time')?.textContent || '';
        const sender = (el.querySelector('.msg-sender')?.textContent || '').trim();
        const text = el.querySelector('.msg-text')?.textContent || '';
        const senderColor = el.querySelector('.msg-sender')?.style.color || 'var(--text)';

        const check = status === 'done' ? '&#10003;' : '&#9675;';
        const checkClass = status === 'done' ? 'todo-check done' : 'todo-check';
        const msgChannel = el.dataset.channel || 'general';

        item.innerHTML = `<button class="${checkClass}" onclick="todoToggle(${id})">${check}</button><span class="msg-time" style="color:var(--accent);font-weight:600;margin-right:4px">#${msgChannel}</span> <span class="msg-time">${escapeHtml(time)}</span> <span class="msg-sender" style="color: ${senderColor}">${escapeHtml(sender)}</span> <span class="msg-text">${escapeHtml(text)}</span><button class="dismiss-btn danger" onclick="todoRemove(${id})" title="Remove from todos">&times;</button>`;
        item.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            // Cross-channel pin: switch channel if needed
            const msgChannel = el.dataset.channel || 'general';
            if (msgChannel !== activeChannel) {
                switchChannel(msgChannel);
            }
            scrollToMessage(id);
            togglePinsPanel();
        });
        list.appendChild(item);
    }
}

// --- Channels ---

function renderChannelTabs() {
    const container = document.getElementById('channel-tabs');
    if (!container) return;

    // Preserve inline create input if it exists
    const existingCreate = container.querySelector('.channel-inline-create');
    container.innerHTML = '';

    for (const name of channelList) {
        const tab = document.createElement('button');
        tab.className = 'channel-tab' + (name === activeChannel ? ' active' : '');
        tab.dataset.channel = name;

        const label = document.createElement('span');
        label.className = 'channel-tab-label';
        label.textContent = '# ' + name;
        tab.appendChild(label);

        const unread = channelUnread[name] || 0;
        if (unread > 0 && name !== activeChannel) {
            const dot = document.createElement('span');
            dot.className = 'channel-unread-dot';
            dot.textContent = unread > 99 ? '99+' : unread;
            tab.appendChild(dot);
        }

        // Edit + delete icons for non-general tabs (visible on hover via CSS)
        if (name !== 'general') {
            const actions = document.createElement('span');
            actions.className = 'channel-tab-actions';

            const editBtn = document.createElement('button');
            editBtn.className = 'ch-edit-btn';
            editBtn.title = 'Rename';
            editBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M11.5 2.5l2 2L5 13H3v-2L11.5 2.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
            editBtn.onclick = (e) => { e.stopPropagation(); showChannelRenameDialog(name); };
            actions.appendChild(editBtn);

            const delBtn = document.createElement('button');
            delBtn.className = 'ch-delete-btn';
            delBtn.title = 'Delete';
            delBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4v8.5h6V4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            delBtn.onclick = (e) => { e.stopPropagation(); deleteChannel(name); };
            actions.appendChild(delBtn);

            tab.appendChild(actions);
        }

        tab.onclick = (e) => {
            if (e.target.closest('.channel-tab-actions')) return;
            if (name === activeChannel) {
                // Second click on active tab — toggle edit controls
                tab.classList.toggle('editing');
            } else {
                // Clear any editing state, switch channel
                document.querySelectorAll('.channel-tab.editing').forEach(t => t.classList.remove('editing'));
                switchChannel(name);
            }
        };

        container.appendChild(tab);
    }

    // Re-append inline create if it was open
    if (existingCreate) {
        container.appendChild(existingCreate);
    }

    // Update add button disabled state
    const addBtn = document.getElementById('channel-add-btn');
    if (addBtn) {
        addBtn.classList.toggle('disabled', channelList.length >= 8);
    }
}

const _channelScrollMsg = {};  // channel name → message ID at top of viewport

function _getTopVisibleMsgId() {
    const scroll = document.getElementById('timeline');
    const container = document.getElementById('messages');
    if (!scroll || !container) return null;
    const rect = scroll.getBoundingClientRect();
    for (const el of container.children) {
        if (el.style.display === 'none' || !el.dataset.id) continue;
        const elRect = el.getBoundingClientRect();
        if (elRect.bottom > rect.top) return el.dataset.id;
    }
    return null;
}

function switchChannel(name) {
    if (name === activeChannel) return;
    // Save top-visible message ID for current channel
    const topId = _getTopVisibleMsgId();
    if (topId) _channelScrollMsg[activeChannel] = topId;
    activeChannel = name;
    channelUnread[name] = 0;
    localStorage.setItem('agentchattr-channel', name);
    filterMessagesByChannel();
    renderChannelTabs();
    updateTopicBar();
    // Restore: scroll to saved message, or bottom if none saved
    const savedId = _channelScrollMsg[name];
    if (savedId) {
        const el = document.querySelector(`.message[data-id="${savedId}"]`);
        if (el) { el.scrollIntoView({ block: 'start' }); return; }
    }
    scrollToBottom();
}

function filterMessagesByChannel() {
    const container = document.getElementById('messages');
    if (!container) return;

    for (const el of container.children) {
        const ch = el.dataset.channel || 'general';
        el.style.display = ch === activeChannel ? '' : 'none';
    }
}

function showChannelCreateDialog() {
    if (channelList.length >= 8) return;
    const tabs = document.getElementById('channel-tabs');
    // Remove existing inline create if any
    tabs.querySelector('.channel-inline-create')?.remove();

    // Hide the + button while creating
    const addBtn = document.getElementById('channel-add-btn');
    if (addBtn) addBtn.style.display = 'none';

    const wrapper = document.createElement('div');
    wrapper.className = 'channel-inline-create';

    const prefix = document.createElement('span');
    prefix.className = 'channel-input-prefix';
    prefix.textContent = '#';
    wrapper.appendChild(prefix);

    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 20;
    input.placeholder = 'channel-name';
    wrapper.appendChild(input);

    const cleanup = () => { wrapper.remove(); if (addBtn) addBtn.style.display = ''; };

    const confirm = document.createElement('button');
    confirm.className = 'confirm-btn';
    confirm.innerHTML = '&#10003;';
    confirm.title = 'Create';
    confirm.onclick = () => { submitInlineCreate(input, wrapper); if (addBtn) addBtn.style.display = ''; };
    wrapper.appendChild(confirm);

    const cancel = document.createElement('button');
    cancel.className = 'cancel-btn';
    cancel.innerHTML = '&#10005;';
    cancel.title = 'Cancel';
    cancel.onclick = cleanup;
    wrapper.appendChild(cancel);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); submitInlineCreate(input, wrapper); if (addBtn) addBtn.style.display = ''; }
        if (e.key === 'Escape') cleanup();
    });
    input.addEventListener('input', () => {
        input.value = input.value.toLowerCase().replace(/[^a-z0-9\-]/g, '');
    });

    tabs.appendChild(wrapper);
    input.focus();
}

function submitInlineCreate(input, wrapper) {
    const name = input.value.trim().toLowerCase();
    if (!name || !/^[a-z0-9][a-z0-9\-]{0,19}$/.test(name)) return;
    if (channelList.includes(name)) { input.focus(); return; }
    pendingChannelSwitch = name;
    ws.send(JSON.stringify({ type: 'channel_create', name }));
    wrapper.remove();
}

function showChannelRenameDialog(oldName) {
    // Reuse inline create pattern but for rename
    const tabs = document.getElementById('channel-tabs');
    tabs.querySelector('.channel-inline-create')?.remove();

    const wrapper = document.createElement('div');
    wrapper.className = 'channel-inline-create';

    const prefix = document.createElement('span');
    prefix.className = 'channel-input-prefix';
    prefix.textContent = '#';
    wrapper.appendChild(prefix);

    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 20;
    input.value = oldName;
    wrapper.appendChild(input);

    const confirm = document.createElement('button');
    confirm.className = 'confirm-btn';
    confirm.innerHTML = '&#10003;';
    confirm.title = 'Rename';
    confirm.onclick = () => {
        const newName = input.value.trim().toLowerCase();
        if (!newName || !/^[a-z0-9][a-z0-9\-]{0,19}$/.test(newName)) return;
        if (newName !== oldName) {
            ws.send(JSON.stringify({ type: 'channel_rename', old_name: oldName, new_name: newName }));
            if (activeChannel === oldName) {
                activeChannel = newName;
                localStorage.setItem('agentchattr-channel', newName);
            }
        }
        wrapper.remove();
    };
    wrapper.appendChild(confirm);

    const cancel = document.createElement('button');
    cancel.className = 'cancel-btn';
    cancel.innerHTML = '&#10005;';
    cancel.title = 'Cancel';
    cancel.onclick = () => wrapper.remove();
    wrapper.appendChild(cancel);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); confirm.click(); }
        if (e.key === 'Escape') wrapper.remove();
    });
    input.addEventListener('input', () => {
        input.value = input.value.toLowerCase().replace(/[^a-z0-9\-]/g, '');
    });

    tabs.appendChild(wrapper);
    input.select();
}

function deleteChannel(name) {
    if (name === 'general') return;
    const tab = document.querySelector(`.channel-tab[data-channel="${name}"]`);
    if (!tab || tab.classList.contains('confirm-delete')) return;

    const label = tab.querySelector('.channel-tab-label');
    const actions = tab.querySelector('.channel-tab-actions');
    const originalText = label.textContent;
    const originalOnclick = tab.onclick;

    tab.classList.add('confirm-delete');
    tab.classList.remove('editing');
    label.textContent = `delete #${name}?`;
    if (actions) actions.style.display = 'none';

    // Add confirm/cancel buttons
    const confirmBar = document.createElement('span');
    confirmBar.className = 'channel-delete-confirm';

    const tickBtn = document.createElement('button');
    tickBtn.className = 'ch-confirm-yes';
    tickBtn.title = 'Confirm delete';
    tickBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5 6.5-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    const crossBtn = document.createElement('button');
    crossBtn.className = 'ch-confirm-no';
    crossBtn.title = 'Cancel';
    crossBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

    confirmBar.appendChild(tickBtn);
    confirmBar.appendChild(crossBtn);
    tab.appendChild(confirmBar);

    const revert = () => {
        tab.classList.remove('confirm-delete');
        label.textContent = originalText;
        if (actions) actions.style.display = '';
        confirmBar.remove();
        tab.onclick = originalOnclick;
        document.removeEventListener('click', outsideClick);
    };

    tickBtn.onclick = (e) => {
        e.stopPropagation();
        revert();
        ws.send(JSON.stringify({ type: 'channel_delete', name }));
        if (activeChannel === name) switchChannel('general');
    };

    crossBtn.onclick = (e) => {
        e.stopPropagation();
        revert();
    };

    // Clicking the tab itself during confirm does nothing
    tab.onclick = (e) => { e.stopPropagation(); };

    const outsideClick = (e) => {
        if (!tab.contains(e.target)) revert();
    };
    setTimeout(() => document.addEventListener('click', outsideClick), 0);
}

// --- Mention toggles ---

function buildMentionToggles() {
    const container = document.getElementById('mention-toggles');
    container.innerHTML = '';

    // Prune stale mentions for agents no longer in config
    for (const name of activeMentions) {
        if (!(name in agentConfig)) activeMentions.delete(name);
    }

    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;  // skip pending instances
        const btn = document.createElement('button');
        btn.className = 'mention-toggle';
        btn.dataset.agent = name;
        btn.textContent = `@${cfg.label || name}`;
        btn.title = `@${name}`;  // Tooltip: canonical name
        btn.style.setProperty('--agent-color', cfg.color);
        // Restore active state for mentions that survived the rebuild
        if (activeMentions.has(name)) {
            btn.classList.add('active');
        }
        btn.onclick = () => {
            if (activeMentions.has(name)) {
                activeMentions.delete(name);
                btn.classList.remove('active');
            } else {
                activeMentions.add(name);
                btn.classList.add('active');
            }
        };
        container.appendChild(btn);
    }
    enableDragScroll(container);
}

// --- Voice typing ---

let recognition = null;
let isListening = false;

function toggleVoice() {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        alert('Speech recognition not supported — use Chrome or Edge.');
        return;
    }

    if (isListening) {
        stopVoice();
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'en-GB';
    recognition.continuous = true;
    recognition.interimResults = true;

    const input = document.getElementById('input');
    const baseText = input.value;
    let finalTranscript = '';

    recognition.onstart = () => {
        isListening = true;
        document.getElementById('mic').classList.add('recording');
    };

    recognition.onresult = (e) => {
        let interim = '';
        finalTranscript = '';
        for (let i = 0; i < e.results.length; i++) {
            const t = e.results[i][0].transcript;
            if (e.results[i].isFinal) {
                finalTranscript += t;
            } else {
                interim += t;
            }
        }
        input.value = baseText + (baseText ? ' ' : '') + finalTranscript + interim;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    };

    recognition.onerror = (e) => {
        console.error('Speech error:', e.error);
        stopVoice();
    };

    recognition.onend = () => {
        stopVoice();
    };

    recognition.start();
}

function stopVoice() {
    isListening = false;
    document.getElementById('mic').classList.remove('recording');
    if (recognition) {
        try { recognition.stop(); } catch (_) {}
        recognition = null;
    }
}

// --- Image modal ---

let modalImages = [];  // all image URLs in chat
let modalIndex = 0;    // current image index

function getAllChatImages() {
    const imgs = document.querySelectorAll('.msg-attachments img, .job-msg-attachments img');
    return [...imgs].map(img => img.src);
}

function openImageModal(url) {
    modalImages = getAllChatImages();
    // Match by endsWith since onclick passes relative URL but img.src is absolute
    modalIndex = modalImages.findIndex(src => src.endsWith(url) || src === url);
    if (modalIndex === -1) modalIndex = 0;

    let modal = document.getElementById('image-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'image-modal';
        modal.className = 'hidden';
        modal.innerHTML = `<button class="modal-nav modal-prev" onclick="modalPrev(event)">&lsaquo;</button><img onclick="event.stopPropagation()"><button class="modal-nav modal-next" onclick="modalNext(event)">&rsaquo;</button><span class="modal-counter"></span>`;
        modal.addEventListener('click', closeImageModal);
        document.body.appendChild(modal);
    }
    updateModalImage(modal);
    modal.classList.remove('hidden');
}

function updateModalImage(modal) {
    if (!modal) modal = document.getElementById('image-modal');
    if (!modal || modalImages.length === 0) return;
    modal.querySelector('img').src = modalImages[modalIndex];
    const counter = modal.querySelector('.modal-counter');
    if (counter) {
        counter.textContent = `${modalIndex + 1} / ${modalImages.length}`;
    }
    // Hide arrows at beginning/end, or if only one image
    const prev = modal.querySelector('.modal-prev');
    const next = modal.querySelector('.modal-next');
    if (prev) prev.style.display = modalIndex > 0 ? 'flex' : 'none';
    if (next) next.style.display = modalIndex < modalImages.length - 1 ? 'flex' : 'none';
}

function modalPrev(event) {
    event.stopPropagation();
    if (modalIndex <= 0) return;
    modalIndex--;
    updateModalImage();
}

function modalNext(event) {
    event.stopPropagation();
    if (modalIndex >= modalImages.length - 1) return;
    modalIndex++;
    updateModalImage();
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    if (modal) modal.classList.add('hidden');
}

// --- Auto-grow textarea helper ---

const RULE_MAX_CHARS = 160;
const RULE_REASON_MAX_CHARS = 240;

function autoGrowTextarea(el) {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
}

function setupCharCounter(textareaId, counterId) {
    const ta = document.getElementById(textareaId);
    const counter = document.getElementById(counterId);
    if (!ta || !counter) return;

    function update() {
        autoGrowTextarea(ta);
        const len = ta.value.length;
        counter.textContent = `${len}/${RULE_MAX_CHARS}`;
        counter.classList.toggle('over', len >= RULE_MAX_CHARS);
    }
    ta.addEventListener('input', update);
    update();
}

function setupRulesGrip() {
    const grip = document.getElementById('rules-grip');
    const panel = document.getElementById('rules-panel');
    if (!grip || !panel) return;

    let dragging = false;
    let startX = 0;
    let startWidth = 0;

    grip.addEventListener('mousedown', (e) => {
        e.preventDefault();
        dragging = true;
        startX = e.clientX;
        startWidth = panel.offsetWidth;
        grip.classList.add('dragging');
        panel.style.transition = 'none';
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const delta = startX - e.clientX;
        const newWidth = Math.min(Math.max(startWidth + delta, 220), window.innerWidth * 0.5);
        panel.style.setProperty('--panel-w', newWidth + 'px');
        panel.style.width = newWidth + 'px';
        panel.style.minWidth = newWidth + 'px';
    });

    document.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false;
        grip.classList.remove('dragging');
        panel.style.transition = '';
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    });
}

function setupRuleForm() {
    // Form is now inline via showCreateRule(), no persistent elements to set up
}

// --- Rules ---

function handleRuleEvent(action, rule) {
    if (action === 'propose') {
        rules.push(rule);
    } else if (['activate', 'deactivate', 'edit', 'approve'].includes(action)) {
        const idx = rules.findIndex(r => r.id === rule.id);
        if (idx >= 0) rules[idx] = rule;
    } else if (action === 'delete') {
        rules = rules.filter(r => r.id !== rule.id);
        _seenRuleIds.delete(rule.id);
    }
    renderRulesPanel();
    updateRulesBadge();
}

async function _preserveScroll(fn) {
    const timeline = document.getElementById('timeline');
    if (!timeline) { await fn(); return; }

    // Check if we are at the bottom (with a small buffer)
    const wasAtBottom = autoScroll || (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 60);
    const topId = _getTopVisibleMsgId();

    // Save the exact pixel offset of the top visible message
    let savedOffset = 0;
    if (topId) {
        const el = document.querySelector(`.message[data-id="${topId}"]`);
        if (el) {
            savedOffset = el.getBoundingClientRect().top - timeline.getBoundingClientRect().top;
        }
    }

    // Disable smooth scrolling for instant correction
    const oldSmooth = timeline.style.scrollBehavior;
    timeline.style.scrollBehavior = 'auto';

    await fn();

    // Continuously correct scroll during sidebar CSS transition
    function correctScroll() {
        if (wasAtBottom) {
            timeline.scrollTop = timeline.scrollHeight;
        } else if (topId) {
            const el = document.querySelector(`.message[data-id="${topId}"]`);
            if (el) {
                const newRect = el.getBoundingClientRect();
                const timelineRect = timeline.getBoundingClientRect();
                timeline.scrollTop += (newRect.top - timelineRect.top) - savedOffset;
            }
        }
    }

    // Correct immediately
    void timeline.scrollHeight;
    correctScroll();

    // Keep correcting each frame during the transition (~300ms)
    let frames = 0;
    function tick() {
        correctScroll();
        if (++frames < 20) requestAnimationFrame(tick); // ~333ms at 60fps
        else timeline.style.scrollBehavior = oldSmooth;
    }
    requestAnimationFrame(tick);
}

function toggleRulesPanel() {
    _preserveScroll(() => {
        const panel = document.getElementById('rules-panel');
        panel.classList.toggle('hidden');
        document.getElementById('rules-toggle').classList.toggle('active', !panel.classList.contains('hidden'));
        if (!panel.classList.contains('hidden')) {
            // Mark all current drafts as seen
            for (const r of rules) {
                if (r.status === 'proposed' || r.status === 'draft') _seenRuleIds.add(r.id);
            }
            updateRulesBadge();
            renderRulesPanel();
        }
    });
}

function remindAgents() {
    const btn = document.querySelector('.rules-remind-btn');
    if (btn) btn.disabled = true;
    fetch('/api/rules/remind', { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN } })
        .then(r => r.json())
        .then(() => {
            if (btn) {
                const orig = btn.textContent;
                btn.textContent = 'Queued — next trigger';
                setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
            }
        })
        .catch(err => {
            console.error('remind failed', err);
            if (btn) btn.disabled = false;
        });
}

let _rulesArchivedExpanded = false;
let _draggedRuleId = null;
let _seenRuleIds = new Set();

function renderRulesPanel() {
    const list = document.getElementById('rules-list');
    if (!list) return;

    // Preserve in-progress create form across re-renders
    const activeForm = list.querySelector('.job-create-form');
    let savedForm = null, savedFocusSelector = null, savedSelStart = 0, savedSelEnd = 0;
    if (activeForm) {
        const focused = document.activeElement;
        if (focused && activeForm.contains(focused)) {
            savedFocusSelector = focused.tagName.toLowerCase() + (focused.className ? '.' + focused.className.split(' ')[0] : '');
            savedSelStart = focused.selectionStart || 0;
            savedSelEnd = focused.selectionEnd || 0;
        }
        savedForm = activeForm.parentNode.removeChild(activeForm);
    }
    list.innerHTML = '';

    const normalize = (s) => s === 'proposed' ? 'draft' : (s === 'approved' ? 'active' : (s || 'draft'));
    // Filter out pending rules — they only exist as proposal cards in the timeline
    const panelRules = rules.filter(r => r.status !== 'pending');
    const activeCount = panelRules.filter(r => normalize(r.status) === 'active').length;

    // Update header counter
    const counter = document.getElementById('rules-counter');
    if (counter) counter.textContent = `${activeCount}/10`;

    if (panelRules.length === 0) {
        const ghost = document.createElement('div');
        ghost.className = 'sb-ghost-card';
        ghost.innerHTML = `
            <div class="sb-ghost-title">No rules yet</div>
            <div class="sb-ghost-meta">Tell your agents how to work</div>
        `;
        ghost.onclick = () => showCreateRule();
        list.appendChild(ghost);
        // Centered helper text
        const helper = document.createElement('div');
        helper.className = 'rules-centered-hint';
        helper.textContent = 'New rules are sent on the next agent trigger';
        list.appendChild(helper);
        if (savedForm) {
            list.prepend(savedForm);
            if (savedFocusSelector) {
                const el = savedForm.querySelector(savedFocusSelector);
                if (el) { el.focus(); try { el.setSelectionRange(savedSelStart, savedSelEnd); } catch {} }
            }
        }
        return;
    }

    // Group order: active first, drafts second, inactive collapsed
    const groups = [
        { key: 'active', label: 'ACTIVE', items: [] },
        { key: 'draft', label: 'DRAFTS', items: [] },
        { key: 'archived', label: 'ARCHIVE', items: [] },
    ];
    for (const r of panelRules) {
        const status = normalize(r.status);
        const g = groups.find(g => g.key === status);
        if (g) g.items.push(r);
        else groups[2].items.push(r);
    }

    // Soft warning at 7+ active
    if (activeCount >= 7) {
        const warning = document.createElement('div');
        warning.className = 'rules-soft-warning';
        warning.textContent = 'Less than seven active rules tends to work better';
        list.appendChild(warning);
    }

    for (const group of groups) {
        group.items.sort((a, b) => b.id - a.id);

        const isCollapsible = group.key === 'archived';
        const isCollapsed = isCollapsible && !_rulesArchivedExpanded;

        const header = document.createElement('div');
        header.className = 'rules-group-header ' + group.key + (isCollapsed ? ' collapsed' : '');
        const isEmpty = group.items.length === 0;
        header.textContent = isEmpty ? group.label : `${group.label} (${group.items.length})`;
        if (isEmpty) header.classList.add('empty-group');
        if (isCollapsible) {
            header.onclick = () => {
                _rulesArchivedExpanded = !_rulesArchivedExpanded;
                header.classList.toggle('collapsed');
                const container = header.nextElementSibling;
                if (container) container.classList.toggle('collapsed');
            };
        }

        // Drop target: drag a rule card onto this header to change its status
        header.addEventListener('dragover', (e) => {
            if (!_draggedRuleId) return;
            e.preventDefault();
            header.classList.add('drop-target');
        });
        header.addEventListener('dragleave', () => {
            header.classList.remove('drop-target');
        });
        header.addEventListener('drop', (e) => {
            e.preventDefault();
            header.classList.remove('drop-target');
            if (!_draggedRuleId) return;
            const id = _draggedRuleId;
            const d = rules.find(r => r.id === id);
            if (!d) return;
            const currentStatus = normalize(d.status);
            if (currentStatus === group.key) return;
            if (group.key === 'active') {
                ws.send(JSON.stringify({ type: 'rule_activate', id }));
            } else {
                ws.send(JSON.stringify({ type: 'rule_deactivate', id }));
            }
        });

        list.appendChild(header);

        const itemsContainer = document.createElement('div');
        itemsContainer.className = 'rules-group-items' + (isCollapsed ? ' collapsed' : '');
        const itemsInner = document.createElement('div');
        itemsInner.className = 'rules-group-items-inner';

        for (const d of group.items) {
            const card = document.createElement('div');
            card.className = 'rule-card';
            card.dataset.id = d.id;
            card.draggable = true;
            card.onclick = () => editRule(d.id);

            card.addEventListener('dragstart', (e) => {
                _draggedRuleId = d.id;
                card.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            card.addEventListener('dragend', () => {
                _draggedRuleId = null;
                card.classList.remove('dragging');
            });

            const displayStatus = normalize(d.status);

            card.innerHTML = `
                <div class="rule-card-header">
                    <span class="rule-status-dot ${displayStatus}"></span>
                    <div class="rule-text">${escapeHtml(d.text || d.decision || '')}</div>
                </div>
            `;
            itemsInner.appendChild(card);
        }

        // Trash zone for archived rules — same style as Jobs
        if (group.key === 'archived' && group.items.length > 0) {
            const trashZone = document.createElement('div');
            trashZone.className = 'archive-trash-zone visible';
            trashZone.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4v8.5h6V4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="archive-trash-hint">Drag here to delete</span>`;

            trashZone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; trashZone.classList.add('hover'); });
            trashZone.addEventListener('dragleave', () => { trashZone.classList.remove('hover'); });
            trashZone.addEventListener('drop', async (e) => {
                e.preventDefault();
                trashZone.classList.remove('hover');
                if (!_draggedRuleId) return;
                const id = _draggedRuleId;
                const d = rules.find(r => r.id === id);
                if (!d) return;
                trashZone.classList.add('chomping');
                ws.send(JSON.stringify({ type: 'rule_delete', id }));
                setTimeout(() => trashZone.classList.remove('chomping'), 500);
            });

            itemsInner.appendChild(trashZone);
        }

        itemsContainer.appendChild(itemsInner);
        list.appendChild(itemsContainer);
    }

    // Centered helper text at bottom
    const helper = document.createElement('div');
    helper.className = 'rules-centered-hint';
    helper.textContent = 'New rules are sent on the next agent trigger';
    list.appendChild(helper);

    // Re-insert preserved create form at top
    if (savedForm) {
        list.prepend(savedForm);
        if (savedFocusSelector) {
            const el = savedForm.querySelector(savedFocusSelector);
            if (el) { el.focus(); try { el.setSelectionRange(savedSelStart, savedSelEnd); } catch {} }
        }
    }
}

function updateRulesBadge() {
    const badge = document.getElementById('rules-badge');
    if (!badge) return;
    // Only count unseen proposals — not all drafts
    const panel = document.getElementById('rules-panel');
    const panelOpen = panel && !panel.classList.contains('hidden');
    if (panelOpen) {
        // Panel is open — everything is seen
        for (const r of rules) {
            if (r.status === 'proposed' || r.status === 'draft') _seenRuleIds.add(r.id);
        }
    }
    const count = rules.filter(r => (r.status === 'proposed' || r.status === 'draft') && !_seenRuleIds.has(r.id)).length;
    badge.textContent = count;
    badge.classList.toggle('hidden', count === 0);
}

function syncJobUnreadCache() {
    const validIds = new Set((jobsData || []).map(a => Number(a.id)));
    for (const id of validIds) {
        if (!Object.prototype.hasOwnProperty.call(jobUnread, id)) {
            jobUnread[id] = 0;
        }
    }
    for (const key of Object.keys(jobUnread)) {
        const id = Number(key);
        if (!validIds.has(id)) {
            delete jobUnread[key];
        }
    }
}

function updateJobsBadge() {
    const badge = document.getElementById('jobs-badge');
    if (!badge) return;
    let total = 0;
    for (const count of Object.values(jobUnread)) {
        total += Number(count || 0);
    }
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.classList.toggle('hidden', total === 0);
}

function markJobRead(jobId) {
    if (jobId == null) return;
    jobUnread[jobId] = 0;
    updateJobsBadge();
}

function showCreateRule() {
    const list = document.getElementById('rules-list');
    if (!list) return;
    const existing = list.querySelector('.job-create-form');
    if (existing) { existing.remove(); return; }

    const form = document.createElement('div');
    form.className = 'job-create-form';
    form.innerHTML = `
        <input type="text" placeholder="Write a short rule agents should follow" class="rule-create-text" maxlength="160" autofocus>
        <div class="job-create-actions">
            <button class="cancel-btn" onclick="this.closest('.job-create-form').remove()">Cancel</button>
            <button class="create-btn" onclick="submitCreateRule(this)">Create</button>
        </div>
    `;
    list.prepend(form);
    const textInput = form.querySelector('.rule-create-text');
    textInput.focus();
    textInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            submitCreateRule(form.querySelector('.create-btn'));
        } else if (e.key === 'Escape') {
            form.remove();
        }
    });
}

function submitCreateRule(btn) {
    const form = btn.closest('.job-create-form');
    const textInput = form.querySelector('.rule-create-text');
    const text = (textInput.value || '').trim();
    if (!text) { textInput.focus(); return; }

    ws.send(JSON.stringify({
        type: 'rule_propose',
        text: text,
        author: username,
        channel: activeChannel,
    }));
    form.remove();
}

function toggleRuleStatus(id) {
    const d = rules.find(r => r.id === id);
    if (!d) return;

    if (d.status === 'active' || d.status === 'approved') {
        ws.send(JSON.stringify({ type: 'rule_deactivate', id }));
    } else {
        ws.send(JSON.stringify({ type: 'rule_activate', id }));
    }
}

function editRule(id) {
    const d = rules.find(r => r.id === id);
    if (!d) return;

    const card = document.querySelector(`.rule-card[data-id="${id}"]`);
    if (!card || card.classList.contains('editing')) return;
    card.classList.add('editing');

    const editArea = document.createElement('div');
    editArea.className = 'rule-edit-area';
    editArea.onclick = (e) => e.stopPropagation();
    editArea.innerHTML = `
        <textarea class="rule-edit-field" maxlength="${RULE_MAX_CHARS}" rows="1" data-limit="${RULE_MAX_CHARS}">${escapeHtml(d.text || '')}</textarea>
        <div class="char-counter">${(d.text || '').length}/${RULE_MAX_CHARS}</div>
        <div class="rule-edit-actions">
            <button class="save-btn" onclick="event.stopPropagation();saveRuleEdit(${id})">Save</button>
            <button class="cancel-btn" onclick="event.stopPropagation();cancelRuleEdit(${id})">Cancel</button>
            <span style="flex:1"></span>
            <button class="delete-inline-btn" onclick="event.stopPropagation();deleteRule(${id})">Delete</button>
        </div>
    `;
    card.appendChild(editArea);

    // Wire auto-grow + counters on edit fields
    editArea.querySelectorAll('.rule-edit-field').forEach(ta => {
        const counter = ta.nextElementSibling;
        const limit = parseInt(ta.dataset.limit) || RULE_MAX_CHARS;
        autoGrowTextarea(ta);
        ta.addEventListener('input', () => {
            autoGrowTextarea(ta);
            if (counter && counter.classList.contains('char-counter')) {
                counter.textContent = `${ta.value.length}/${limit}`;
                counter.classList.toggle('over', ta.value.length >= limit);
            }
        });
    });

    // Focus the textarea, cursor at end
    const firstField = editArea.querySelector('textarea');
    firstField.focus();
    firstField.selectionStart = firstField.selectionEnd = firstField.value.length;
}

function saveRuleEdit(id) {
    const card = document.querySelector(`.rule-card[data-id="${id}"]`);
    if (!card) return;

    const field = card.querySelector('.rule-edit-field');
    const newText = field?.value.trim();

    if (!newText) return;

    ws.send(JSON.stringify({
        type: 'rule_edit',
        id,
        text: newText,
    }));
}

function cancelRuleEdit(id) {
    const card = document.querySelector(`.rule-card[data-id="${id}"]`);
    if (!card) return;
    card.classList.remove('editing');
    const editArea = card.querySelector('.rule-edit-area');
    if (editArea) editArea.remove();
}

function startDeleteRule(id) {
    const card = document.querySelector(`.rule-card[data-id="${id}"]`);
    if (!card) return;
    const actions = card.querySelector('.rule-actions');
    if (!actions || actions.dataset.confirming) return;
    actions.dataset.confirming = '1';
    actions.style.opacity = '1';
    actions.innerHTML = `
        <span style="font-size:11px;color:var(--error-color);white-space:nowrap;margin-right:4px">Delete?</span>
        <button class="confirm-yes" style="background:var(--error-color);color:#fff;border:none;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit" onclick="deleteRule(${id})">Yes</button>
        <button class="confirm-no" style="background:transparent;color:var(--text-dim);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit" onclick="cancelDeleteRule(${id})">No</button>
    `;
}

function deleteRule(id) {
    const d = rules.find(r => r.id === id);
    ws.send(JSON.stringify({ type: 'rule_delete', id }));

    // Prefill a rejection message to the proposer
    const author = d?.author || d?.owner;
    if (d && author && author.toLowerCase() !== username.toLowerCase()) {
        const input = document.getElementById('input');
        const reasonBit = d.reason ? ` (reason: ${d.reason})` : '';
        input.value = `@${author} Rule rejected: "${d.text || d.decision || ''}"${reasonBit} — `;
        input.focus();
        input.selectionStart = input.selectionEnd = input.value.length;
        input.dispatchEvent(new Event('input'));
    }
}

function cancelDeleteRule(id) {
    renderRulesPanel();
}

// Style #hashtags in rendered message text
function styleHashtags(html) {
    // "Match and skip" pattern: consume HTML tags first (to skip hex colors in
    // style attributes like color: #da7756), then match real hashtags in text.
    return html.replace(/<[^>]*>|((?:^|\s))(#([a-zA-Z][a-zA-Z0-9_-]{0,39}))\b/g,
        (match, prefix, fullHash, tag) => {
            if (tag === undefined) return match; // HTML tag — skip
            const lower = tag.toLowerCase();
            if (['clear', 'off', 'none', 'end'].includes(lower)) {
                return `${prefix}<span class="msg-hashtag" style="opacity:0.5">#${tag}</span>`;
            }
            return `${prefix}<span class="msg-hashtag">#${tag}</span>`;
        });
}

// --- Jobs ---

function setupJobsGrip() {
    const grip = document.getElementById('jobs-grip');
    const panel = document.getElementById('jobs-panel');
    if (!grip || !panel) return;

    let dragging = false;
    let startX = 0;
    let startWidth = 0;

    grip.addEventListener('mousedown', (e) => {
        e.preventDefault();
        dragging = true;
        startX = e.clientX;
        startWidth = panel.offsetWidth;
        grip.classList.add('dragging');
        panel.style.transition = 'none';
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const delta = startX - e.clientX;
        const newWidth = Math.min(Math.max(startWidth + delta, 260), window.innerWidth * 0.5);
        panel.style.setProperty('--panel-w', newWidth + 'px');
        panel.style.width = newWidth + 'px';
        panel.style.minWidth = newWidth + 'px';
    });

    document.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false;
        grip.classList.remove('dragging');
        panel.style.transition = '';
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    });
}

function setupJobsInput() {
    const input = document.getElementById('jobs-conv-input-text');
    if (!input) return;
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !jobMentionVisible) {
            e.preventDefault();
            sendJobMessage();
            return;
        }
        if (e.key === 'Tab' && !jobMentionVisible && activeJobId) {
            e.preventDefault();
            cycleJobReplyTarget(e.shiftKey ? -1 : 1);
        }
    });
    // Auto-grow
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });
}

function getJobRecipientOptions() {
    const opts = [];
    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;
        opts.push({
            name,
            label: cfg.label || name,
            color: cfg.color || 'var(--accent)',
        });
    }
    return opts;
}

function _normalizeJobRecipient(name, options = null) {
    if (!name) return '';
    const opts = options || getJobRecipientOptions();
    const wanted = String(name).toLowerCase();
    const hit = opts.find(o => o.name.toLowerCase() === wanted);
    return hit ? hit.name : '';
}

function _extractJobMentionTargets(text) {
    if (!text) return [];
    const opts = getJobRecipientOptions();
    if (opts.length === 0) return [];
    const byLower = {};
    for (const o of opts) byLower[o.name.toLowerCase()] = o.name;
    const hits = [];
    const re = /@([a-zA-Z][\w-]*)/g;
    let m;
    while ((m = re.exec(text)) !== null) {
        const key = m[1].toLowerCase();
        const canonical = byLower[key];
        if (canonical && !hits.includes(canonical)) hits.push(canonical);
    }
    return hits;
}

function resolveJobDefaultRecipient(job, messages = []) {
    const opts = getJobRecipientOptions();
    if (opts.length === 0) return '';
    const hasStored = job && Object.prototype.hasOwnProperty.call(jobReplyTargets, job.id);
    if (hasStored) {
        const stored = jobReplyTargets[job.id];
        if (stored === null) return '';
        const normalized = _normalizeJobRecipient(stored, opts);
        if (normalized) return normalized;
    }

    // New/empty jobs should start un-targeted.
    if (!messages || messages.length === 0) return '';

    // For active threads with history, infer from last non-self agent sender.
    for (let i = messages.length - 1; i >= 0; i--) {
        const sender = String(messages[i]?.sender || '');
        if (!sender || sender.toLowerCase() === username.toLowerCase()) continue;
        const normalized = _normalizeJobRecipient(sender, opts);
        if (normalized) return normalized;
    }

    // Fallback to assignee if present and valid.
    const assignee = _normalizeJobRecipient(job?.assignee || '', opts);
    if (assignee) return assignee;
    return '';
}

function updateJobReplyTargetUI() {
    const row = document.getElementById('job-reply-target-row');
    const btn = document.getElementById('job-reply-target-btn');
    const dot = document.getElementById('job-reply-target-dot');
    const nameEl = document.getElementById('job-reply-target-name');
    const clearBtn = document.getElementById('job-reply-target-clear');
    if (!row || !btn || !dot || !nameEl || !clearBtn) return;
    if (!activeJobId) {
        row.classList.add('hidden');
        return;
    }
    const opts = getJobRecipientOptions();
    if (opts.length === 0) {
        row.classList.add('hidden');
        return;
    }
    const hasStored = Object.prototype.hasOwnProperty.call(jobReplyTargets, activeJobId);
    let selected = null;
    if (hasStored) {
        const stored = jobReplyTargets[activeJobId];
        if (stored !== null) {
            const normalized = _normalizeJobRecipient(stored, opts);
            selected = opts.find(o => o.name === normalized) || null;
        }
    }
    if (!selected && hasStored && jobReplyTargets[activeJobId] !== null) {
        jobReplyTargets[activeJobId] = null;
    }
    if (selected) {
        dot.style.background = selected.color || 'var(--accent)';
        nameEl.textContent = selected.label || selected.name;
        btn.title = `Reply target: ${selected.label || selected.name} (Tab to cycle)`;
    } else {
        nameEl.textContent = 'none';
        btn.title = 'Reply target: none (Tab to choose)';
    }
    btn.classList.toggle('no-target', !selected);
    clearBtn.classList.toggle('hidden', !selected);
    row.classList.remove('hidden');
}

function cycleJobReplyTarget(step = 1) {
    if (!activeJobId) return;
    const opts = getJobRecipientOptions();
    if (opts.length === 0) return;
    const current = _normalizeJobRecipient(jobReplyTargets[activeJobId], opts);
    let idx = opts.findIndex(o => o.name === current);
    if (idx < 0) {
        idx = step < 0 ? opts.length - 1 : 0;
    } else {
        idx = (idx + step + opts.length) % opts.length;
    }
    jobReplyTargets[activeJobId] = opts[idx].name;
    updateJobReplyTargetUI();
    document.getElementById('jobs-conv-input-text')?.focus();
}

function clearJobReplyTarget(event) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    if (!activeJobId) return;
    jobReplyTargets[activeJobId] = null;
    updateJobReplyTargetUI();
    document.getElementById('jobs-conv-input-text')?.focus();
}

function toggleJobsPanel() {
    _preserveScroll(() => {
        const panel = document.getElementById('jobs-panel');
        panel.classList.toggle('hidden');
        document.getElementById('jobs-toggle').classList.toggle('active', !panel.classList.contains('hidden'));
        if (!panel.classList.contains('hidden')) {
            // Return to list view if we were in conversation view
            showJobsListView();
            renderJobsList();
        }
    });
}

function _animateJobViewSwitch(exitView, enterView, direction, onDone) {
    // No animation — instant swap
    if (exitView) exitView.classList.add('hidden');
    if (enterView) enterView.classList.remove('hidden');
    if (onDone) onDone();
}

function showJobsListView(onDone) {
    const listView = document.getElementById('jobs-list-view');
    const convView = document.getElementById('jobs-conversation-view');
    activeJobId = null;
    updateJobReplyTargetUI();
    if (!convView.classList.contains('hidden')) {
        _animateJobViewSwitch(convView, listView, 'back', onDone);
    } else {
        listView.classList.remove('hidden');
        convView.classList.add('hidden');
        _jobViewSwitching = false;
        if (onDone) onDone();
    }
}

const _expandedGroups = new Set(['open']); // track which collapsible groups are open across re-renders

function _jobSortValue(a) {
    const raw = Number(a?.sort_order);
    return Number.isFinite(raw) ? raw : 0;
}

function _compareJobsForList(a, b) {
    const byOrder = _jobSortValue(b) - _jobSortValue(a);
    if (byOrder !== 0) return byOrder;
    return (b.updated_at || 0) - (a.updated_at || 0);
}

function _clearJobReorderTargets(container) {
    if (!container) return;
    container.querySelectorAll('.reorder-target-before, .reorder-target-after').forEach((el) => {
        el.classList.remove('reorder-target-before', 'reorder-target-after');
    });
}

function _orderedIdsForJobGroup(status) {
    return jobsData
        .filter(a => a.status === status)
        .sort(_compareJobsForList)
        .map(a => Number(a.id));
}

function _applyLocalJobOrder(status, orderedIds) {
    const n = orderedIds.length;
    const byId = new Map();
    orderedIds.forEach((id, idx) => byId.set(Number(id), n - idx));
    for (const a of jobsData) {
        if (a.status !== status) continue;
        const nextOrder = byId.get(Number(a.id));
        if (nextOrder != null) a.sort_order = nextOrder;
    }
}

function _isJobsListVisible() {
    const panel = document.getElementById('jobs-panel');
    return Boolean(panel && !panel.classList.contains('hidden') && !activeJobId);
}

function _beginJobReorderMute({ ids = [], channel = activeChannel, status = null, durationMs = 650 } = {}) {
    const muteIds = new Set(
        (ids || []).map(id => Number(id)).filter(id => Number.isFinite(id))
    );
    if (muteIds.size === 0) return;
    const ttl = Math.max(180, Number(durationMs) || 0);
    if (jobReorderMuteTimer) clearTimeout(jobReorderMuteTimer);
    jobReorderMute = {
        ids: muteIds,
        channel,
        status,
        until: Date.now() + ttl,
        suppressed: false,
    };
    jobReorderMuteTimer = setTimeout(() => {
        const muted = jobReorderMute;
        jobReorderMute = null;
        jobReorderMuteTimer = null;
        if (muted && muted.suppressed && _isJobsListVisible()) {
            renderJobsList();
        }
    }, ttl);
}

function _shouldSuppressJobUpdateRender(data) {
    if (!jobReorderMute || !data) return false;
    if (Date.now() > jobReorderMute.until) {
        if (jobReorderMuteTimer) {
            clearTimeout(jobReorderMuteTimer);
            jobReorderMuteTimer = null;
        }
        jobReorderMute = null;
        return false;
    }
    const id = Number(data.id);
    if (!Number.isFinite(id) || !jobReorderMute.ids.has(id)) return false;
    if (jobReorderMute.status && data.status !== jobReorderMute.status) return false;
    jobReorderMute.suppressed = true;
    return true;
}

async function _persistJobOrder(status, orderedIds) {
    const resp = await fetch('/api/jobs/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
        body: JSON.stringify({ status, ordered_ids: orderedIds }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to persist order: ${resp.status}`);
    }
}

async function reorderJobWithinGroup(status, draggedId, targetId, insertAfter) {
    const ordered = _orderedIdsForJobGroup(status);
    const from = ordered.indexOf(Number(draggedId));
    const to = ordered.indexOf(Number(targetId));
    if (from < 0 || to < 0 || from === to) return;

    const [moved] = ordered.splice(from, 1);
    let insertAt = to + (insertAfter ? 1 : 0);
    if (from < insertAt) insertAt -= 1;
    ordered.splice(insertAt, 0, moved);

    _beginJobReorderMute({ ids: ordered, status });
    _applyLocalJobOrder(status, ordered);
    _flipRenderJobs();
    try {
        await _persistJobOrder(status, ordered);
    } catch (err) {
        console.error(err);
        // Reload canonical state from server on failure.
        try {
            const resp = await fetch('/api/jobs', {
                headers: { 'X-Session-Token': SESSION_TOKEN },
            });
            if (resp.ok) {
                jobsData = await resp.json();
                syncJobUnreadCache();
                renderJobsList();
            }
        } catch (reloadErr) {
            console.error('Failed to reload jobs after reorder failure:', reloadErr);
        }
    }
}

let _draggedJobId = null;
let _draggedJobStatus = null;
let _pendingJobReflowTops = null;
let _pendingJobReflowTimer = null;

function _flushPendingJobReflow() {
    if (!_pendingJobReflowTops) return;
    const tops = _pendingJobReflowTops;
    _pendingJobReflowTops = null;
    animateJobListReflow(tops);
}

function _flipRenderJobs() {
    const prevTops = captureJobCardTops();
    const scrollContainer = document.getElementById('jobs-list-view');
    const scrollY = scrollContainer ? scrollContainer.scrollTop : 0;
    
    renderJobsList();
    
    if (scrollContainer) scrollContainer.scrollTop = scrollY;

    // During HTML5 drag lifecycle, some browsers suppress paint/transition work.
    // Queue reflow animation until dragend for reliable visual playback.
    if (_draggedJobId) {
        if (!_pendingJobReflowTops) _pendingJobReflowTops = prevTops;
        if (_pendingJobReflowTimer) clearTimeout(_pendingJobReflowTimer);
        // Fallback in case dragend does not fire (e.g. source node replaced).
        _pendingJobReflowTimer = setTimeout(() => {
            _pendingJobReflowTimer = null;
            _flushPendingJobReflow();
        }, 120);
        return;
    }
    animateJobListReflow(prevTops);
}

function renderJobsList() {
    const list = document.getElementById('jobs-list');
    if (!list) return;
    // Preserve in-progress create form across re-renders (save focus state)
    const activeForm = list.querySelector('.job-create-form');
    let savedForm = null, savedFocusSelector = null, savedSelStart = 0, savedSelEnd = 0;
    if (activeForm) {
        const focused = document.activeElement;
        if (focused && activeForm.contains(focused)) {
            savedFocusSelector = focused.tagName.toLowerCase() + (focused.className ? '.' + focused.className.split(' ')[0] : '');
            savedSelStart = focused.selectionStart || 0;
            savedSelEnd = focused.selectionEnd || 0;
        }
        savedForm = activeForm.parentNode.removeChild(activeForm);
    }
    list.innerHTML = '';

    // Jobs are global — show all regardless of channel
    const channelJobs = jobsData;

    // Group by status: open first, then done, then archived
    const groups = [
        { key: 'open', label: 'TO DO', items: [] },
        { key: 'done', label: 'ACTIVE', items: [] },
        { key: 'archived', label: 'CLOSED', items: [] },
    ];
    for (const a of channelJobs) {
        const g = groups.find(g => g.key === a.status);
        if (g) g.items.push(a);
    }

    // Ghost card only when there are zero jobs total
    if (channelJobs.length === 0) {
        const ghost = document.createElement('div');
        ghost.className = 'sb-ghost-card';
        ghost.innerHTML = `
            <div class="sb-ghost-title">Create your first job</div>
            <div class="sb-ghost-meta">Track work items with threaded conversations. Use @mentions to loop in agents.</div>
        `;
        ghost.onclick = () => {
            const btn = document.querySelector('.jobs-create-btn');
            if (btn) btn.click();
        };
        list.appendChild(ghost);
    }

    for (const group of groups) {
        // Sort by explicit manual order first; fallback to recency.
        group.items.sort(_compareJobsForList);

        const isCollapsible = group.key === 'open' || group.key === 'archived';
        const isExpanded = _expandedGroups.has(group.key);
        const isCollapsed = isCollapsible && !isExpanded;
        const header = document.createElement('div');
        header.className = 'jobs-group-header ' + group.key + (isCollapsible ? ' collapsible' : '') + (isCollapsed ? ' collapsed' : '');
        header.dataset.status = group.key;
        const isEmpty = group.items.length === 0;
        header.textContent = isEmpty ? group.label : `${group.label} (${group.items.length})`;
        if (isEmpty) header.classList.add('empty-group');
        if (isCollapsible) {
            header.onclick = () => {
                header.classList.toggle('collapsed');
                const container = header.nextElementSibling;
                if (container) container.classList.toggle('collapsed');
                if (header.classList.contains('collapsed')) {
                    _expandedGroups.delete(group.key);
                } else {
                    _expandedGroups.add(group.key);
                }
            };
        }

        // Drop target: drag a card from another group onto this header to change its status
        header.addEventListener('dragover', (e) => {
            if (!_draggedJobId || _draggedJobStatus === group.key) return;
            e.preventDefault();
            header.classList.add('drop-target');
        });
        header.addEventListener('dragleave', () => {
            header.classList.remove('drop-target');
        });
        header.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            header.classList.remove('drop-target');
            const draggedId = _draggedJobId;
            if (!draggedId || _draggedJobStatus === group.key) return;
            const oldStatus = _draggedJobStatus;
            try {
                await fetch(`/api/jobs/${draggedId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
                    body: JSON.stringify({ status: group.key }),
                });
                const act = jobsData.find(a => String(a.id) === String(draggedId));
                _beginJobReorderMute({ ids: [draggedId], channel: activeChannel, status: group.key });
                if (act) act.status = group.key;
                _flipRenderJobs();

            } catch (err) { console.error('Failed to change status:', err); }
        });

        list.appendChild(header);

        // Wrap group items in a container for collapsing (CSS grid animation)
        const itemsContainer = document.createElement('div');
        itemsContainer.className = 'jobs-group-items' + (isCollapsed ? ' collapsed' : '');
        const itemsInner = document.createElement('div');
        itemsInner.className = 'jobs-group-items-inner';

        for (const a of group.items) {
            const card = document.createElement('div');
            card.className = 'job-card';
            card.dataset.id = a.id;
            card.onclick = () => openJobConversation(a.id);
            card.addEventListener('selectstart', (e) => e.preventDefault());

            const msgCount = (a.messages || []).filter(m => !m?.deleted).length;
            const unread = jobUnread[a.id] || 0;

            const unreadHtml = unread > 0
                ? `<span class="job-unread-dot" title="Unread messages">${unread > 99 ? '99+' : unread}</span>`
                : '';

            card.innerHTML = `
                <div class="job-card-header">
                    <span class="job-status-dot ${a.status}"></span>
                    <span class="job-title">${escapeHtml(a.title)}</span>
                    <span class="job-msg-count">${msgCount > 0 ? msgCount : ''}</span>
                    ${unreadHtml}
                </div>
            `;


            card.draggable = true;
            card.addEventListener('dragstart', (e) => {
                let ids = [card.dataset.id];
                if (group.key === 'archived') {
                    const selected = [...itemsContainer.querySelectorAll('.archive-selected')].map(c => c.dataset.id);
                    // If dragging one of the selected cards, drag the whole selection.
                    if (selected.length > 0 && selected.includes(card.dataset.id)) {
                        ids = selected;
                    }
                }
                
                _draggedJobId = card.dataset.id;
                _draggedJobStatus = group.key;
                
                e.dataTransfer.setData('application/x-job-id', String(card.dataset.id));
                e.dataTransfer.setData('application/x-job-status', group.key);
                e.dataTransfer.setData('application/x-job-multi', ids.length > 1 ? '1' : '0');
                e.dataTransfer.setData('application/x-archive-ids', JSON.stringify(ids));
                e.dataTransfer.effectAllowed = 'move';
                
                card.classList.add('reorder-dragging');
                if (group.key === 'archived') {
                    card.classList.remove('archive-holding');
                    document.body.classList.add('archive-no-select');
                    ids.forEach(id => {
                        const el = itemsContainer.querySelector(`.job-card[data-id="${id}"]`);
                        if (el) el.classList.add('archive-dragging');
                    });
                    itemsContainer.classList.add('archive-drag-active');
                    
                    const trash = itemsContainer.querySelector('.archive-trash-zone');
                    if (trash) {
                        trash.classList.add('drop-ready');
                        const hint = trash.querySelector('.archive-trash-hint');
                        if (hint) hint.textContent = ids.length > 1 ? `Drop to delete ${ids.length} jobs` : 'Drop to delete job';
                    }
                } else {
                    itemsContainer.classList.add('job-reorder-active');
                }
            });
            card.addEventListener('dragover', (e) => {
                if (_draggedJobStatus !== group.key || !_draggedJobId || _draggedJobId === card.dataset.id) return;
                e.preventDefault();
                // Clear all other indicators first, then set this one
                _clearJobReorderTargets(itemsContainer);
                const rect = card.getBoundingClientRect();
                const before = e.clientY < rect.top + rect.height / 2;
                card.classList.toggle('reorder-target-before', before);
                card.classList.toggle('reorder-target-after', !before);
            });
            card.addEventListener('drop', async (e) => {
                const draggedId = _draggedJobId;
                const draggedStatus = _draggedJobStatus;
                _clearJobReorderTargets(itemsContainer);
                if (!draggedStatus || draggedStatus !== group.key || !draggedId) return;
                if (draggedId === card.dataset.id) return;
                e.preventDefault();
                e.stopPropagation();
                const rect = card.getBoundingClientRect();
                const insertAfter = e.clientY >= rect.top + rect.height / 2;
                await reorderJobWithinGroup(group.key, draggedId, card.dataset.id, insertAfter);
            });
            card.addEventListener('dragend', () => {
                _draggedJobId = null;
                _draggedJobStatus = null;
                document.body.classList.remove('archive-no-select');
                card.classList.remove('reorder-dragging');
                itemsContainer.classList.remove('job-reorder-active', 'archive-drag-active');
                itemsContainer.querySelectorAll('.archive-dragging').forEach(c => c.classList.remove('archive-dragging'));
                _clearJobReorderTargets(itemsContainer);
                const trash = itemsContainer.querySelector('.archive-trash-zone');
                if (trash) {
                    trash.classList.remove('drop-ready', 'hover');
                    updateArchiveTrashHint(itemsContainer);
                }
                if (_pendingJobReflowTimer) {
                    clearTimeout(_pendingJobReflowTimer);
                    _pendingJobReflowTimer = null;
                }
                _flushPendingJobReflow();
            });

            // Archived cards: shift+click for multi-select
            if (group.key === 'archived') {
                card.classList.add('archive-selectable');
                const clearHoldState = () => card.classList.remove('archive-holding');
                card.addEventListener('pointerdown', (e) => {
                    if (e.button !== 0) return;
                    card.classList.add('archive-holding');
                });
                card.addEventListener('pointerup', clearHoldState);
                card.addEventListener('pointercancel', clearHoldState);
                card.addEventListener('mouseleave', clearHoldState);
                const origOnclick = card.onclick;
                card.onclick = (e) => {
                    if (e.shiftKey) {
                        e.stopPropagation();
                        card.classList.toggle('archive-selected');
                        updateArchiveTrashHint(itemsContainer);
                        return;
                    }
                    origOnclick(e);
                };
            }

            itemsInner.appendChild(card);
        }

        itemsContainer.addEventListener('dragover', (e) => {
            if (_draggedJobStatus !== group.key || !_draggedJobId) return;
            e.preventDefault();
        });
        itemsContainer.addEventListener('drop', async (e) => {
            if (e.target.closest('.job-card') || e.target.closest('.archive-trash-zone')) return;
            const draggedStatus = _draggedJobStatus;
            const draggedId = _draggedJobId;
            _clearJobReorderTargets(itemsContainer);
            if (!draggedStatus || draggedStatus !== group.key || !draggedId) return;
            e.preventDefault();
            const cards = [...itemsContainer.querySelectorAll('.job-card')];
            const lastCard = cards[cards.length - 1];
            if (!lastCard) return;
            if (String(lastCard.dataset.id) === String(draggedId)) return;
            await reorderJobWithinGroup(group.key, draggedId, lastCard.dataset.id, true);
        });

        // Add trash zone at the bottom of archived group — always visible
        if (group.key === 'archived' && group.items.length > 0) {
            const trashZone = document.createElement('div');
            trashZone.className = 'archive-trash-zone visible';
            trashZone.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4v8.5h6V4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="archive-trash-hint">Drag here to delete</span>`;

            // Click to delete selected items
            trashZone.addEventListener('click', async () => {
                const selected = [...itemsContainer.querySelectorAll('.archive-selected')];
                if (selected.length === 0) return;
                await deleteArchiveIds(selected.map(c => c.dataset.id), trashZone);
            });

            // Drag-and-drop support
            trashZone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; trashZone.classList.add('hover'); _clearJobReorderTargets(itemsInner); });
            trashZone.addEventListener('dragleave', () => { trashZone.classList.remove('hover'); });
            trashZone.addEventListener('drop', async (e) => {
                e.preventDefault();
                trashZone.classList.remove('hover');
                itemsContainer.classList.remove('archive-drag-active');
                document.body.classList.remove('archive-no-select');
                let ids;
                try { ids = JSON.parse(e.dataTransfer.getData('application/x-archive-ids')); } catch { return; }
                if (!ids || ids.length === 0) return;
                await deleteArchiveIds(ids, trashZone);
            });

            itemsInner.appendChild(trashZone);
        }

        itemsContainer.appendChild(itemsInner);
        list.appendChild(itemsContainer);
    }

    // Re-insert preserved create form at top and restore focus
    if (savedForm) {
        list.prepend(savedForm);
        if (savedFocusSelector) {
            const el = savedForm.querySelector(savedFocusSelector);
            if (el) {
                el.focus();
                try { el.setSelectionRange(savedSelStart, savedSelEnd); } catch {}
            }
        }
    }
}

async function openJobConversation(jobId) {
    const job = jobsData.find(a => a.id === jobId);
    if (!job) return;
    activeJobId = jobId;
    markJobRead(jobId);

    const listView = document.getElementById('jobs-list-view');
    const convView = document.getElementById('jobs-conversation-view');

    // Prepare content while conv view is still hidden
    // Set header — click to edit title inline
    const titleEl = document.getElementById('jobs-conv-title');
    titleEl.textContent = job.title;
    titleEl.onclick = () => startEditJobTitle(job, titleEl);
    updateJobToggles(job.status);

    // Render unified brief card header
    let briefEl = convView.querySelector('.job-brief-card');
    if (briefEl) briefEl.remove();
    let legacyBody = convView.querySelector('.job-body-brief');
    if (legacyBody) legacyBody.remove();
    let legacyPinned = convView.querySelector('.job-pinned-msg');
    if (legacyPinned) legacyPinned.remove();

    if (job.body) {
        briefEl = document.createElement('div');
        briefEl.className = 'job-brief-card';
        briefEl.innerHTML = `<div class="job-brief-text">${renderMarkdown(job.body)}</div>`;
        const messagesContainer = document.getElementById('jobs-conv-messages');
        messagesContainer.parentNode.insertBefore(briefEl, messagesContainer);
    }

    // Load messages before showing the view
    const messages = await loadJobMessages(jobId);
    const target = resolveJobDefaultRecipient(job, messages);
    if (target) jobReplyTargets[jobId] = target;
    updateJobReplyTargetUI();

    // Switch views — instant swap (animation deferred to future Motion Guard impl)
    listView.classList.add('hidden');
    convView.classList.remove('hidden');

    // Pre-fill starter text for empty jobs
    const jobInput = document.getElementById('jobs-conv-input-text');
    const msgCount = (job.messages || []).filter(m => !m?.deleted).length;
    if (msgCount === 0 && target) {
        const starterText = `@${target} start this job`;
        jobInput.value = starterText;
        jobInput.placeholder = 'Send to assign · click to edit';
        jobInput.classList.add('job-starter-prefill');
        const clearStarter = () => {
            if (jobInput.classList.contains('job-starter-prefill')) {
                jobInput.value = '';
                jobInput.placeholder = 'Message...';
                jobInput.classList.remove('job-starter-prefill');
            }
            jobInput.removeEventListener('focus', clearStarter);
            jobInput.removeEventListener('click', clearStarter);
        };
        jobInput.addEventListener('focus', clearStarter);
        jobInput.addEventListener('click', clearStarter);
    } else {
        jobInput.placeholder = 'Message...';
        jobInput.classList.remove('job-starter-prefill');
        jobInput.focus();
    }
}

async function loadJobMessages(jobId) {
    const container = document.getElementById('jobs-conv-messages');
    container.innerHTML = '';

    try {
        const resp = await fetch(`/api/jobs/${jobId}/messages`, {
            headers: { 'X-Session-Token': SESSION_TOKEN }
        });
        if (!resp.ok) return [];
        const msgs = await resp.json();
        const visibleMsgs = msgs.filter(msg => !msg?.deleted);

        if (visibleMsgs.length === 0) {
            // Only show empty state if there's no brief card either
            const convView = document.getElementById('jobs-conversation-view');
            const hasBrief = !!convView.querySelector('.job-brief-card');
            if (!hasBrief) {
                container.innerHTML = '<div class="jobs-empty" style="font-size:12px; padding:16px">No messages yet. Start the conversation!</div>';
            }
            return msgs;
        }

        // Render all messages in the scrollable area
        for (const msg of visibleMsgs) {
            appendJobMessage(msg);
        }

        container.scrollTop = container.scrollHeight;
        return msgs;
    } catch (e) {
        container.innerHTML = '<div class="jobs-empty">Failed to load messages.</div>';
        return [];
    }
}

const _jobMsgDeleteTimers = new Map();
function _clearJobMsgDeleteTimer(jobId, msgId) {
    const key = `${jobId}:${msgId}`;
    const timer = _jobMsgDeleteTimers.get(key);
    if (timer) clearTimeout(timer);
    _jobMsgDeleteTimers.delete(key);
}

function _animateRemoveJobMessage(msgEl) {
    if (!msgEl || msgEl.dataset.removing === '1') {
        if (msgEl) msgEl?.remove?.();
        return;
    }
    msgEl.dataset.removing = '1';
    const h = msgEl.offsetHeight || 0;
    msgEl.style.maxHeight = `${h}px`;
    msgEl.style.overflow = 'hidden';
    msgEl.style.willChange = 'max-height, opacity, transform, margin, padding';
    requestAnimationFrame(() => {
        msgEl.classList.add('job-msg-removing');
        msgEl.style.maxHeight = '0px';
    });
    const cleanup = () => {
        msgEl.removeEventListener('transitionend', cleanup);
        msgEl.remove();
    };
    msgEl.addEventListener('transitionend', cleanup);
    setTimeout(cleanup, 240);
}

function _renderJobMsgDeleteDefault(actions, jobId, msgId) {
    if (!actions) return;
    actions.classList.remove('confirming');
    actions.innerHTML = `<button class="job-msg-del-btn" onclick="startDeleteJobMessage(${jobId}, ${msgId})">DEL</button>`;
}

function startDeleteJobMessage(jobId, msgId, event) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const selector = `.job-msg[data-msg-id="${msgId}"]`;
    const msgEl = document.querySelector(selector);
    const actions = msgEl?.querySelector('.job-msg-actions');
    if (!actions) return;

    document.querySelectorAll('.job-msg-actions.confirming').forEach((el) => {
        const parent = el.closest('.job-msg');
        const priorId = Number(parent?.dataset.msgId || NaN);
        if (Number.isFinite(priorId) && priorId !== Number(msgId)) {
            _clearJobMsgDeleteTimer(jobId, priorId);
            _renderJobMsgDeleteDefault(el, jobId, priorId);
        }
    });

    actions.classList.add('confirming');
    actions.innerHTML = `
        <span class="job-msg-delete-label">Delete?</span>
        <button class="job-msg-confirm-yes" onclick="confirmDeleteJobMessage(${jobId}, ${msgId})">Yes</button>
        <button class="job-msg-confirm-no" onclick="cancelDeleteJobMessage(${jobId}, ${msgId})">No</button>
    `;
    _clearJobMsgDeleteTimer(jobId, msgId);
    const key = `${jobId}:${msgId}`;
    _jobMsgDeleteTimers.set(key, setTimeout(() => {
        cancelDeleteJobMessage(jobId, msgId);
    }, 4000));
}

async function confirmDeleteJobMessage(jobId, msgId, event) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    _clearJobMsgDeleteTimer(jobId, msgId);
    try {
        const resp = await fetch(`/api/jobs/${jobId}/messages/${msgId}`, {
            method: 'DELETE',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        });
        if (!resp.ok) {
            cancelDeleteJobMessage(jobId, msgId);
            return;
        }
        const msgEl = document.querySelector(`.job-msg[data-msg-id="${msgId}"]`);
        _animateRemoveJobMessage(msgEl);
    } catch (err) {
        console.error('Failed to delete job message:', err);
        cancelDeleteJobMessage(jobId, msgId);
    }
}

function cancelDeleteJobMessage(jobId, msgId, event) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    _clearJobMsgDeleteTimer(jobId, msgId);
    const msgEl = document.querySelector(`.job-msg[data-msg-id="${msgId}"]`);
    const actions = msgEl?.querySelector('.job-msg-actions');
    _renderJobMsgDeleteDefault(actions, jobId, msgId);
}

function appendJobMessage(msg) {
    const container = document.getElementById('jobs-conv-messages');
    if (!container || msg?.deleted) return;

    const div = document.createElement('div');
    div.className = 'job-msg' + (msg.type === 'suggestion' ? ' job-suggestion' : '');
    div.dataset.msgId = String(msg.id ?? '');
    const senderColor = getColor(msg.sender);
    const msgId = Number(msg.id);
    const canDelete = Number.isFinite(msgId);
    const deleteActionHtml = canDelete
        ? `<div class="job-msg-actions"><button class="job-msg-del-btn" onclick="startDeleteJobMessage(${activeJobId}, ${msgId})">DEL</button></div>`
        : '';

    if (msg.type === 'suggestion') {
        const resolved = msg.resolved;
        div.innerHTML = `
            <div class="job-msg-header">
                <span class="suggestion-pill">Suggestion</span>
                <span class="job-msg-sender" style="color: ${senderColor}">${escapeHtml(msg.sender)}</span>
                <span class="job-msg-time">${msg.time || ''}</span>
                ${deleteActionHtml}
            </div>
            <div class="job-msg-text">${renderMarkdown(msg.text)}</div>
            <div class="suggestion-actions">${resolved
                ? `<span class="suggestion-resolved">${escapeHtml(resolved)}</span>`
                : `<button class="suggestion-accept" onclick="acceptSuggestion(${activeJobId}, ${msg.id})">Accept</button><button class="suggestion-dismiss" onclick="dismissSuggestion(${activeJobId}, ${msg.id})">Dismiss</button>`
            }</div>
        `;
    } else {
        let attHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
            attHtml = '<div class="job-msg-attachments">';
            for (const att of msg.attachments) {
                attHtml += `<img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name || '')}" onclick="openImageModal('${escapeHtml(att.url)}')">`;
            }
            attHtml += '</div>';
        }
        div.innerHTML = `
            <div class="job-msg-header">
                <span class="job-msg-sender" style="color: ${senderColor}">${escapeHtml(msg.sender)}</span>
                <span class="job-msg-time">${msg.time || ''}</span>
                ${deleteActionHtml}
            </div>
            ${msg.text ? `<div class="job-msg-text">${renderMarkdown(msg.text)}</div>` : ''}
            ${attHtml}
        `;
    }
    container.appendChild(div);
}

let jobPendingAttachments = [];

async function sendJobMessage() {
    if (!activeJobId) return;
    const input = document.getElementById('jobs-conv-input-text');
    const text = input.value.trim();
    if (!text && jobPendingAttachments.length === 0) return;
    const explicitTargets = _extractJobMentionTargets(text);
    const hasBroadcastMention = /@(?:all|both)\b/i.test(text);
    let outboundText = text;
    if (explicitTargets.length > 0) {
        jobReplyTargets[activeJobId] = explicitTargets[0];
    } else if (!hasBroadcastMention) {
        const target = _normalizeJobRecipient(jobReplyTargets[activeJobId]);
        if (target) {
            outboundText = text ? `@${target} ${text}` : `@${target}`;
        }
    }

    try {
        const resp = await fetch(`/api/jobs/${activeJobId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({
                text: outboundText,
                sender: username,
                attachments: jobPendingAttachments.map(a => ({
                    path: a.path, name: a.name, url: a.url,
                })),
            }),
        });
        if (resp.ok) {
            input.value = '';
            input.style.height = 'auto';
            clearJobAttachments();
            updateJobReplyTargetUI();
        }
    } catch (e) {
        console.error('Failed to send job message:', e);
    }
}

async function uploadJobImage(file) {
    const form = new FormData();
    form.append('file', file);
    try {
        const resp = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN }, body: form });
        const data = await resp.json();
        jobPendingAttachments.push({ path: data.path, name: data.name, url: data.url });
        renderJobAttachments();
    } catch (err) {
        console.error('Job upload failed:', err);
    }
}

function renderJobAttachments() {
    const container = document.getElementById('job-attachments');
    if (!container) return;
    container.innerHTML = '';
    jobPendingAttachments.forEach((att, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'attachment-preview';
        wrap.innerHTML = `<img src="${att.url}" alt="${escapeHtml(att.name)}"><button class="remove-btn" onclick="removeJobAttachment(${i})">x</button>`;
        container.appendChild(wrap);
    });
}

function removeJobAttachment(index) {
    jobPendingAttachments.splice(index, 1);
    renderJobAttachments();
}

function clearJobAttachments() {
    jobPendingAttachments = [];
    const container = document.getElementById('job-attachments');
    if (container) container.innerHTML = '';
}

function startEditJobTitle(job, titleEl) {
    if (titleEl.querySelector('input')) return; // already editing
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'job-title-input';
    input.value = job.title;
    input.maxLength = 120;
    titleEl.textContent = '';
    titleEl.appendChild(input);
    input.focus();
    input.select();

    const commit = async () => {
        const newTitle = input.value.trim();
        if (newTitle && newTitle !== job.title) {
            try {
                await fetch(`/api/jobs/${job.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
                    body: JSON.stringify({ title: newTitle }),
                });
                job.title = newTitle;
            } catch (e) { console.error('Failed to update title:', e); }
        }
        titleEl.textContent = job.title;
        titleEl.onclick = () => startEditJobTitle(job, titleEl);
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { input.value = job.title; input.blur(); }
    });
    input.addEventListener('blur', commit, { once: true });
}

async function toggleJobStatus(status) {
    if (!activeJobId) return;
    const job = jobsData.find(a => a.id === activeJobId);
    if (!job) return;
    const oldStatus = job.status;
    const statusLabels = { 'open': 'TO DO', 'done': 'ACTIVE', 'archived': 'CLOSED' };

    try {
        await fetch(`/api/jobs/${activeJobId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ status }),
        });
        // Update local data
        job.status = status;
        if (status === 'archived') {
            jobsBack();
        } else {
            updateJobToggles(status);
        }

        renderJobsList();
    } catch (e) {
        console.error('Failed to update job status:', e);
    }
}

function updateJobToggles(activeStatus) {
    const toggles = document.querySelectorAll('#jobs-status-toggles .job-toggle');
    toggles.forEach(t => {
        t.classList.toggle('active', t.dataset.status === activeStatus);
    });
}

function showCreateJob() {
    const list = document.getElementById('jobs-list');
    if (!list) return;
    // Remove existing form if any
    const existing = list.querySelector('.job-create-form');
    if (existing) { existing.remove(); return; }

    const form = document.createElement('div');
    form.className = 'job-create-form';
    form.innerHTML = `
        <input type="text" placeholder="Job title" class="job-create-title" maxlength="120" autofocus>
        <textarea placeholder="Description (optional)" class="job-create-body" maxlength="1000" rows="2"></textarea>
        <div class="job-create-actions">
            <button class="cancel-btn" onclick="this.closest('.job-create-form').remove()">Cancel</button>
            <button class="create-btn" onclick="submitCreateJob(this)">Create</button>
        </div>
    `;
    list.prepend(form);
    const titleInput = form.querySelector('.job-create-title');
    titleInput.focus();
    // Enter on title moves to body, Enter on empty body submits
    titleInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            form.querySelector('.job-create-body').focus();
        } else if (e.key === 'Escape') {
            form.remove();
        }
    });
    const bodyTA = form.querySelector('.job-create-body');
    bodyTA.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') form.remove();
    });
    bodyTA.addEventListener('input', () => {
        bodyTA.style.height = 'auto';
        bodyTA.style.height = bodyTA.scrollHeight + 'px';
    });
}

async function submitCreateJob(btn) {
    const form = btn.closest('.job-create-form');
    const titleInput = form.querySelector('.job-create-title');
    const title = titleInput.value.trim();
    if (!title) { titleInput.focus(); return; }
    const bodyInput = form.querySelector('.job-create-body');
    const jobBody = bodyInput ? bodyInput.value.trim() : '';

    try {
        await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({
                title,
                body: jobBody,
                type: 'job',
                channel: activeChannel,
                created_by: username,
                assignee: _lastMentionedAgent || '',
            }),
        });
        form.remove();
    } catch (e) {
        console.error('Failed to create job:', e);
    }
}

function jobsBack() {
    showJobsListView(() => {
        renderJobsList();
    });
}

function handleJobEvent(action, data) {
    let suppressListRender = false;
    if (action === 'create') {
        if (!jobsData.some(a => a.id === data.id)) jobsData.push(data);
        if (!Object.prototype.hasOwnProperty.call(jobUnread, data.id)) {
            jobUnread[data.id] = 0;
        }
    } else if (action === 'update') {
        const idx = jobsData.findIndex(a => a.id === data.id);
        if (idx >= 0) jobsData[idx] = data;
        if (!Object.prototype.hasOwnProperty.call(jobUnread, data.id)) {
            jobUnread[data.id] = 0;
        }
        suppressListRender = _shouldSuppressJobUpdateRender(data);
    } else if (action === 'message') {
        // data = { job_id, message }
        const job = jobsData.find(a => a.id === data.job_id);
        if (job) {
            if (!job.messages) job.messages = [];
            job.messages.push(data.message);
        }
        const panel = document.getElementById('jobs-panel');
        const convView = document.getElementById('jobs-conversation-view');
        const isViewingThis = Boolean(
            panel &&
            !panel.classList.contains('hidden') &&
            convView &&
            !convView.classList.contains('hidden') &&
            activeJobId === data.job_id
        );
        const sender = (data.message && data.message.sender) ? String(data.message.sender) : '';
        const isSelfMessage = sender.toLowerCase() === username.toLowerCase();
        const msgType = data.message.type || 'chat';
        if (!isSelfMessage) {
            const normalized = _normalizeJobRecipient(sender);
            const hasStoredTarget = Object.prototype.hasOwnProperty.call(jobReplyTargets, data.job_id);
            if (normalized && hasStoredTarget && jobReplyTargets[data.job_id] !== null) {
                jobReplyTargets[data.job_id] = normalized;
                if (isViewingThis) updateJobReplyTargetUI();
            }
        }

        // Play notification sound for new job messages from others (matching channel behavior)
        if (soundEnabled && !document.hasFocus() && msgType === 'chat' && !isSelfMessage && sender) {
            playNotificationSound(sender);
        }

        // If we're viewing this job, append the message. Otherwise count unread.
        if (isViewingThis) {
            appendJobMessage(data.message);
            const container = document.getElementById('jobs-conv-messages');
            if (container) container.scrollTop = container.scrollHeight;
            markJobRead(data.job_id);
        } else if (!isSelfMessage) {
            jobUnread[data.job_id] = (jobUnread[data.job_id] || 0) + 1;
        }
    } else if (action === 'message_delete') {
        // data = { job_id, message_id }
        const job = jobsData.find(a => a.id === data.job_id);
        if (job && Array.isArray(job.messages)) {
            const hit = job.messages.find(m => Number(m.id) === Number(data.message_id));
            if (hit) {
                hit.deleted = true;
                hit.text = '';
                hit.attachments = [];
            }
        }
        const panel = document.getElementById('jobs-panel');
        const convView = document.getElementById('jobs-conversation-view');
        const isViewingThis = Boolean(
            panel &&
            !panel.classList.contains('hidden') &&
            convView &&
            !convView.classList.contains('hidden') &&
            activeJobId === data.job_id
        );
        if (isViewingThis) {
            const msgEl = document.querySelector(`.job-msg[data-msg-id="${data.message_id}"]`);
            _animateRemoveJobMessage(msgEl);
            markJobRead(data.job_id);
        }
    } else if (action === 'delete') {
        jobsData = jobsData.filter(a => a.id !== data.id);
        delete jobUnread[data.id];
        // Remove breadcrumb from timeline
        document.querySelectorAll('.job-breadcrumb').forEach(el => {
            const msgData = el.dataset;
            // Check the onclick handler for matching job ID
            const link = el.querySelector('.job-breadcrumb-link');
            if (link) {
                const onclick = link.getAttribute('onclick') || '';
                if (onclick.includes(`openJobFromBreadcrumb(${data.id})`)) {
                    el.remove();
                }
            }
        });
        if (activeJobId === data.id) {
            showJobsListView();
        }
    }
    updateJobsBadge();
    // Re-render list if visible
    const panel = document.getElementById('jobs-panel');
    if (panel && !panel.classList.contains('hidden') && !activeJobId) {
        if (_jobViewSwitching) return;
        if (action === 'delete' && archiveDeleteBatchIds && archiveDeleteBatchIds.has(Number(data.id))) {
            return;
        }
        if (suppressListRender) return;
        renderJobsList();
    }
}

// --- Convert-to-Job Lightbox ---

function showConvertToJobModal(msgId) {
    const msgEl = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!msgEl) return;
    const rawText = msgEl.dataset.rawText || '';
    const msgSender = msgEl.querySelector('.msg-sender')?.textContent || username;

    let modal = document.getElementById('convert-job-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'convert-job-modal';
        modal.className = 'convert-job-modal hidden';
        modal.innerHTML = `
            <div class="convert-job-dialog">
                <h3 class="convert-job-title">Convert to Job</h3>
                <p class="convert-job-subtitle">An agent will write a job proposal for you to accept</p>
                <div class="convert-job-preview"></div>
                <label class="convert-job-label">Ask agent to write proposal</label>
                <select class="convert-job-agent"></select>
                <div class="convert-job-actions">
                    <button class="convert-job-cancel">Cancel</button>
                    <button class="convert-job-confirm">Convert</button>
                </div>
            </div>`;
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeConvertJobModal();
        });
        document.body.appendChild(modal);
    }

    // Store raw text on modal for the confirm handler
    modal.dataset.rawText = rawText;
    modal.dataset.sourceMsgId = String(msgId);

    // Populate message preview
    const previewEl = modal.querySelector('.convert-job-preview');
    previewEl.innerHTML = renderMarkdown(rawText.substring(0, 500));

    // Populate agent picker — only agents, not humans
    const selectEl = modal.querySelector('.convert-job-agent');
    selectEl.innerHTML = '';
    const agents = Object.keys(agentConfig);
    const defaultAgent = agents.includes(msgSender) ? msgSender : agents[0];
    for (const name of agents) {
        const opt = document.createElement('option');
        opt.value = name;
        const cfg = agentConfig[name];
        opt.textContent = cfg?.label || name;
        if (name === defaultAgent) opt.selected = true;
        selectEl.appendChild(opt);
    }

    // Wire buttons (clone to remove old listeners)
    const cancelBtn = modal.querySelector('.convert-job-cancel');
    const confirmBtn = modal.querySelector('.convert-job-confirm');
    const newCancel = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancel, cancelBtn);
    const newConfirm = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirm, confirmBtn);

    newCancel.addEventListener('click', closeConvertJobModal);
    newConfirm.addEventListener('click', _doConvertToJob);

    modal.classList.remove('hidden');
    requestAnimationFrame(() => selectEl.focus());
}

async function _doConvertToJob() {
    const modal = document.getElementById('convert-job-modal');
    if (!modal) return;
    const agent = modal.querySelector('.convert-job-agent').value;
    const rawText = modal.dataset.rawText || '';
    const sourceMsgId = parseInt(modal.dataset.sourceMsgId || '0', 10) || 0;
    if (!agent) return;

    closeConvertJobModal();

    // Silently trigger the agent to propose a job — no visible chat message
    const instruction = `${username}: Please read the following message and use chat_propose_job to propose it as a job. Write a concise title (max 80 chars) and a clear body (max 500 chars) summarizing the task:\n\n---\n${rawText.substring(0, 800)}\n---`;

    try {
        await fetch('/api/trigger-agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({
                agent,
                message: instruction,
                channel: activeChannel,
                source_msg_id: sourceMsgId,
            }),
        });
    } catch (e) {
        console.error('Failed to trigger agent for job conversion:', e);
    }
}

function closeConvertJobModal() {
    const modal = document.getElementById('convert-job-modal');
    if (modal) modal.classList.add('hidden');
}

// --- Delete-Job Lightbox ---

function showDeleteJobModal(jobId) {
    let modal = document.getElementById('delete-job-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'delete-job-modal';
        modal.className = 'convert-job-modal hidden';
        modal.innerHTML = `
            <div class="convert-job-dialog delete-job-dialog">
                <h3 class="convert-job-title">Delete Job Permanently?</h3>
                <p class="convert-job-subtitle">This removes the job and its messages permanently. This cannot be undone.</p>
                <div class="delete-job-target"></div>
                <div class="convert-job-actions">
                    <button class="convert-job-cancel">Cancel</button>
                    <button class="delete-job-confirm">Delete</button>
                </div>
            </div>`;
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeDeleteJobModal();
        });
        document.body.appendChild(modal);
    }

    pendingDeleteJobId = jobId;
    const job = jobsData.find(a => a.id === jobId);
    const target = modal.querySelector('.delete-job-target');
    if (target) {
        const title = job?.title || `Job #${jobId}`;
        target.textContent = title;
    }

    const cancelBtn = modal.querySelector('.convert-job-cancel');
    const confirmBtn = modal.querySelector('.delete-job-confirm');
    const newCancel = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancel, cancelBtn);
    const newConfirm = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirm, confirmBtn);
    newCancel.addEventListener('click', closeDeleteJobModal);
    newConfirm.addEventListener('click', confirmDeleteJobPermanent);

    modal.classList.remove('hidden');
    requestAnimationFrame(() => newConfirm.focus());
}

function closeDeleteJobModal() {
    const modal = document.getElementById('delete-job-modal');
    if (modal) modal.classList.add('hidden');
    pendingDeleteJobId = null;
}

function startJobFromMessage(msgId) {
    showConvertToJobModal(msgId);
}

async function acceptProposal(msgId) {
    const msgEl = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!msgEl) return;
    const title = msgEl.dataset.proposalTitle;
    const body = msgEl.dataset.proposalBody;
    const proposalSender = msgEl.dataset.proposalSender;
    if (!title) return;

    try {
        const resp = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({
                title,
                body: body || '',
                type: 'job',
                channel: activeChannel,
                created_by: proposalSender,
                anchor_msg_id: msgId,
            }),
        });
        const job = await resp.json();
        if (job && job.id) {
            // Update the proposal card to show "Accepted"
            const card = msgEl.querySelector('.proposal-card');
            if (card) {
                card.classList.add('proposal-resolved');
                const actions = card.querySelector('.proposal-actions');
                if (actions) actions.innerHTML = '<div class="proposal-status-resolved">Accepted</div>';
            }
            // Open the job (don't push to jobsData — WS 'create' event handles that)
            const panel = document.getElementById('jobs-panel');
            if (panel.classList.contains('hidden')) toggleJobsPanel();
            // Small delay to let WS event populate jobsData
            setTimeout(() => {
                openJobConversation(job.id);
                // Set reply target to the proposing agent
                if (proposalSender) {
                    jobReplyTargets[job.id] = proposalSender;
                    updateJobReplyTargetUI();
                }
            }, 200);
        }
    } catch (e) {
        console.error('Failed to accept proposal:', e);
    }
}

async function dismissProposal(msgId) {
    // Demote on server — converts proposal to regular chat message
    try {
        await fetch(`/api/messages/${msgId}/demote`, {
            method: 'POST',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        });
    } catch (e) {
        console.error('Failed to demote proposal:', e);
    }
}

async function requestChangesProposal(msgId) {
    // Demote proposal to chat message, then open a reply to it
    try {
        await fetch(`/api/messages/${msgId}/demote`, {
            method: 'POST',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        });
        // Wait briefly for WS edit event to re-render the message as chat
        setTimeout(() => startReply(msgId), 200);
    } catch (e) {
        console.error('Failed to request changes on proposal:', e);
    }
}

async function resolveRuleProposal(msgId, action) {
    try {
        await fetch(`/api/messages/${msgId}/resolve_rule_proposal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ action }),
        });
    } catch (e) {
        console.error('Failed to resolve rule proposal:', e);
    }
}

async function dismissRuleProposal(msgId) {
    // Demote to regular chat message — same as job proposal dismiss
    try {
        await fetch(`/api/messages/${msgId}/demote_rule_proposal`, {
            method: 'POST',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        });
    } catch (e) {
        console.error('Failed to dismiss rule proposal:', e);
    }
}

async function openJobFromBreadcrumb(jobId) {
    const job = jobsData.find(a => a.id === jobId);
    if (!job) return;
    const panel = document.getElementById('jobs-panel');
    if (panel.classList.contains('hidden')) {
        // Force browser to compute the hidden state BEFORE removing class
        void panel.offsetHeight;
        // Remove hidden class — transition should animate from -360 to 0
        panel.classList.remove('hidden');
        document.getElementById('jobs-toggle').classList.add('active');
        // Force reflow AFTER class change to commit the transition start
        // before openJobConversation modifies child DOM
        void panel.offsetHeight;
    }
    // Switch to conversation view for this job
    await openJobConversation(jobId);
}

// --- Suggestion accept/dismiss in jobs ---

async function acceptSuggestion(jobId, msgIndex) {
    try {
        await fetch(`/api/jobs/${jobId}/messages/${msgIndex}/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ resolution: 'accepted' }),
        });
        // Reload conversation to reflect change
        await loadJobMessages(jobId);
    } catch (e) {
        console.error('Failed to accept suggestion:', e);
    }
}

async function dismissSuggestion(jobId, msgIndex) {
    try {
        await fetch(`/api/jobs/${jobId}/messages/${msgIndex}/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ resolution: 'dismissed' }),
        });
        await loadJobMessages(jobId);
    } catch (e) {
        console.error('Failed to dismiss suggestion:', e);
    }
}

// --- Permanent delete for archived jobs ---

async function deleteJobPermanent(jobId) {
    showDeleteJobModal(jobId);
}

async function confirmDeleteJobPermanent() {
    const jobId = pendingDeleteJobId;
    if (!jobId) return;
    closeDeleteJobModal();
    try {
        await fetch(`/api/jobs/${jobId}?permanent=true`, {
            method: 'DELETE',
            headers: { 'X-Session-Token': SESSION_TOKEN },
        });
        jobsData = jobsData.filter(a => a.id !== jobId);
        renderJobsList();
    } catch (e) {
        console.error('Failed to delete job:', e);
    }
}

// --- Archive trash helpers ---

function captureJobCardTops() {
    const tops = new Map();
    document.querySelectorAll('#jobs-list .job-card').forEach((card) => {
        const id = Number(card.dataset.id);
        if (!Number.isFinite(id)) return;
        tops.set(id, card.getBoundingClientRect().top);
    });
    return tops;
}

function animateJobListReflow(prevTops) {
    if (!prevTops || prevTops.size === 0) return;
    const moved = [];
    document.querySelectorAll('#jobs-list .job-card').forEach((card) => {
        const id = Number(card.dataset.id);
        if (!prevTops.has(id)) return;
        const previous = prevTops.get(id);
        const next = card.getBoundingClientRect().top;
        const dy = previous - next;
        if (Math.abs(dy) < 1) return;
        moved.push({ card, dy });
    });
    if (moved.length === 0) return;

    // FLIP: set initial offset (no transition)
    for (const { card, dy } of moved) {
        card.style.transition = 'none';
        card.style.transform = `translateY(${dy}px)`;
    }
    
    // Force the browser to commit the offset before starting the transition
    void document.body.offsetHeight;
    
    // Use setTimeout to escape any browser D&D paint suppression logic
    setTimeout(() => {
        for (const { card } of moved) {
            card.style.transition = 'transform 260ms cubic-bezier(0.22, 1, 0.36, 1)';
            card.style.transform = 'translateY(0)';
            const cleanup = () => {
                card.style.transition = '';
                card.style.transform = '';
                card.removeEventListener('transitionend', cleanup);
            };
            card.addEventListener('transitionend', cleanup);
            // Fallback cleanup in case transitionend doesn't fire
            setTimeout(cleanup, 300);
        }
    }, 20);
}

async function deleteArchiveIds(ids, trashZone) {
    const normalizedIds = [...new Set(
        (ids || []).map(id => Number(id)).filter(id => Number.isFinite(id))
    )];
    if (normalizedIds.length === 0) return;
    const prevTops = captureJobCardTops();
    archiveDeleteBatchIds = new Set(normalizedIds);
    const itemsContainer = trashZone.closest('.jobs-group-items');
    if (itemsContainer) {
        for (const id of normalizedIds) {
            const el = itemsContainer.querySelector(`.job-card[data-id="${id}"]`);
            if (el) el.classList.add('archive-removing');
        }
    }
    trashZone.classList.add('chomping');
    const deletedIds = [];
    for (const id of normalizedIds) {
        try {
            const resp = await fetch(`/api/jobs/${id}?permanent=true`, {
                method: 'DELETE',
                headers: { 'X-Session-Token': SESSION_TOKEN },
            });
            if (resp.ok) deletedIds.push(id);
        } catch (err) { console.error('Failed to delete job:', err); }
    }
    if (deletedIds.length > 0) {
        const deletedSet = new Set(deletedIds);
        jobsData = jobsData.filter(a => !deletedSet.has(Number(a.id)));
        for (const id of deletedIds) delete jobUnread[id];
        renderJobsList();
        animateJobListReflow(prevTops);
        updateJobsBadge();
    }
    setTimeout(() => { trashZone.classList.remove('chomping'); }, 500);
    setTimeout(() => { archiveDeleteBatchIds = null; }, 1200);
}

function updateArchiveTrashHint(container) {
    const trash = container.querySelector('.archive-trash-zone');
    if (!trash) return;
    const count = container.querySelectorAll('.archive-selected').length;
    const hint = trash.querySelector('.archive-trash-hint');
    if (count > 0) {
        trash.classList.add('has-selection');
        hint.textContent = `Delete ${count} selected`;
    } else {
        trash.classList.remove('has-selection');
        hint.textContent = 'Drag here to delete';
    }
}

// --- Job @mention autocomplete ---

let jobMentionVisible = false;
let jobMentionIndex = 0;
let jobMentionStart = -1;

function setupJobMentions() {
    const input = document.getElementById('jobs-conv-input-text');
    if (!input) return;

    input.addEventListener('input', updateJobMentionMenu);
    input.addEventListener('keydown', (e) => {
        if (jobMentionVisible) {
            const menu = document.getElementById('job-mention-menu');
            const items = menu.querySelectorAll('.mention-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                jobMentionIndex = (jobMentionIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === jobMentionIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                jobMentionIndex = (jobMentionIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === jobMentionIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[jobMentionIndex];
                if (active) selectJobMention(active.dataset.name);
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                jobMentionVisible = false;
                return;
            }
        }
        // Note: Enter-to-send is handled by setupJobsInput() — don't duplicate here
    });
}

function updateJobMentionMenu() {
    const menu = document.getElementById('job-mention-menu');
    const input = document.getElementById('jobs-conv-input-text');
    const text = input.value;
    const cursor = input.selectionStart;

    let atPos = -1;
    for (let i = cursor - 1; i >= 0; i--) {
        if (text[i] === '@') { atPos = i; break; }
        if (!/[\w\-\s]/.test(text[i])) break;
        if (cursor - i > 30) break;
    }

    if (atPos < 0 || (atPos > 0 && /\w/.test(text[atPos - 1]))) {
        menu.classList.add('hidden');
        jobMentionVisible = false;
        return;
    }

    const query = text.slice(atPos + 1, cursor).toLowerCase();
    jobMentionStart = atPos;

    const candidates = getMentionCandidates();
    const matches = candidates.filter(c =>
        c.name.toLowerCase().includes(query) || c.label.toLowerCase().includes(query)
    );

    if (matches.length === 0) {
        menu.classList.add('hidden');
        jobMentionVisible = false;
        return;
    }

    menu.innerHTML = '';
    jobMentionIndex = Math.min(jobMentionIndex, matches.length - 1);

    matches.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'mention-item' + (i === jobMentionIndex ? ' active' : '');
        row.dataset.name = item.name;
        row.innerHTML = `<span class="mention-dot" style="background: ${item.color}"></span><span class="mention-name">${escapeHtml(item.label)}</span>`;
        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectJobMention(item.name);
        });
        row.addEventListener('mouseenter', () => {
            jobMentionIndex = i;
            menu.querySelectorAll('.mention-item').forEach((el, j) => el.classList.toggle('active', j === i));
        });
        menu.appendChild(row);
    });

    menu.classList.remove('hidden');
    jobMentionVisible = true;
}

function selectJobMention(name) {
    const input = document.getElementById('jobs-conv-input-text');
    _lastMentionedAgent = name; // track for future job creation
    const text = input.value;
    const cursor = input.selectionStart;
    const before = text.slice(0, jobMentionStart);
    const after = text.slice(cursor);
    const mention = `@${name} `;
    input.value = before + mention + after;
    const newPos = jobMentionStart + mention.length;
    input.setSelectionRange(newPos, newPos);
    input.focus();
    document.getElementById('job-mention-menu').classList.add('hidden');
    jobMentionVisible = false;
}

// --- Helpers ---


function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// --- Start ---

document.addEventListener('DOMContentLoaded', init);

