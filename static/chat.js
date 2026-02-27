/* agentchattr — WebSocket client */

// Session token injected by the server into the HTML page.
// Sent with every API call and WebSocket connection to authenticate.
const SESSION_TOKEN = window.__SESSION_TOKEN__ || "";

let ws = null;
let pendingAttachments = [];
let autoScroll = true;
let reconnectTimer = null;
let username = 'user';
let agentConfig = {};  // { name: { color, label } } — populated from server
let todos = {};  // { msg_id: "todo" | "done" }
let activeMentions = new Set();  // agent names with pre-@ toggled on
let replyingTo = null;  // { id, sender, text } or null
let unreadCount = 0;    // messages received while scrolled up
let lastMessageDate = null;  // track date for dividers
let soundEnabled = false;  // suppress sounds during initial history load

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
        label.textContent = name === 'default' ? 'Default' : (agentConfig[name]?.label || name);
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

function saveSoundPrefs() {
    localStorage.setItem('agentchattr-sounds', JSON.stringify(soundPrefs));
}

// Real brand logo SVGs from Bootstrap Icons (MIT licensed)
const BRAND_AVATARS = {
    claude: `<svg viewBox="0 0 16 16" fill="white"><path d="m3.127 10.604 3.135-1.76.053-.153-.053-.085H6.11l-.525-.032-1.791-.048-1.554-.065-1.505-.08-.38-.081L0 7.832l.036-.234.32-.214.455.04 1.009.069 1.513.105 1.097.064 1.626.17h.259l.036-.105-.089-.065-.068-.064-1.566-1.062-1.695-1.121-.887-.646-.48-.327-.243-.306-.104-.67.435-.48.585.04.15.04.593.456 1.267.981 1.654 1.218.242.202.097-.068.012-.049-.109-.181-.9-1.626-.96-1.655-.428-.686-.113-.411a2 2 0 0 1-.068-.484l.496-.674L4.446 0l.662.089.279.242.411.94.666 1.48 1.033 2.014.302.597.162.553.06.17h.105v-.097l.085-1.134.157-1.392.154-1.792.052-.504.25-.605.497-.327.387.186.319.456-.045.294-.19 1.23-.37 1.93-.243 1.29h.142l.161-.16.654-.868 1.097-1.372.484-.545.565-.601.363-.287h.686l.505.751-.226.775-.707.895-.585.759-.839 1.13-.524.904.048.072.125-.012 1.897-.403 1.024-.186 1.223-.21.553.258.06.263-.218.536-1.307.323-1.533.307-2.284.54-.028.02.032.04 1.029.098.44.024h1.077l2.005.15.525.346.315.424-.053.323-.807.411-3.631-.863-.872-.218h-.12v.073l.726.71 1.331 1.202 1.667 1.55.084.383-.214.302-.226-.032-1.464-1.101-.565-.497-1.28-1.077h-.084v.113l.295.432 1.557 2.34.08.718-.112.234-.404.141-.444-.08-.911-1.28-.94-1.44-.759-1.291-.093.053-.448 4.821-.21.246-.484.186-.403-.307-.214-.496.214-.98.258-1.28.21-1.016.19-1.263.112-.42-.008-.028-.092.012-.953 1.307-1.448 1.957-1.146 1.227-.274.109-.477-.247.045-.44.266-.39 1.586-2.018.956-1.25.617-.723-.004-.105h-.036l-4.212 2.736-.75.096-.324-.302.04-.496.154-.162 1.267-.871z"/></svg>`,
    codex: `<svg viewBox="0 0 16 16" fill="white"><path d="M14.949 6.547a3.94 3.94 0 0 0-.348-3.273 4.11 4.11 0 0 0-4.4-1.934A4.1 4.1 0 0 0 8.423.2 4.15 4.15 0 0 0 6.305.086a4.1 4.1 0 0 0-1.891.948 4.04 4.04 0 0 0-1.158 1.753 4.1 4.1 0 0 0-1.563.679A4 4 0 0 0 .554 4.72a3.99 3.99 0 0 0 .502 4.731 3.94 3.94 0 0 0 .346 3.274 4.11 4.11 0 0 0 4.402 1.933c.382.425.852.764 1.377.995.526.231 1.095.35 1.67.346 1.78.002 3.358-1.132 3.901-2.804a4.1 4.1 0 0 0 1.563-.68 4 4 0 0 0 1.14-1.253 3.99 3.99 0 0 0-.506-4.716m-6.097 8.406a3.05 3.05 0 0 1-1.945-.694l.096-.054 3.23-1.838a.53.53 0 0 0 .265-.455v-4.49l1.366.778q.02.011.025.035v3.722c-.003 1.653-1.361 2.992-3.037 2.996m-6.53-2.75a2.95 2.95 0 0 1-.36-2.01l.095.057L5.29 12.09a.53.53 0 0 0 .527 0l3.949-2.246v1.555a.05.05 0 0 1-.022.041L6.473 13.3c-1.454.826-3.311.335-4.15-1.098m-.85-6.94A3.02 3.02 0 0 1 3.07 3.949v3.785a.51.51 0 0 0 .262.451l3.93 2.237-1.366.779a.05.05 0 0 1-.048 0L2.585 9.342a2.98 2.98 0 0 1-1.113-4.094zm11.216 2.571L8.747 5.576l1.362-.776a.05.05 0 0 1 .048 0l3.265 1.86a3 3 0 0 1 1.173 1.207 2.96 2.96 0 0 1-.27 3.2 3.05 3.05 0 0 1-1.36.997V8.279a.52.52 0 0 0-.276-.445m1.36-2.015-.097-.057-3.226-1.855a.53.53 0 0 0-.53 0L6.249 6.153V4.598a.04.04 0 0 1 .019-.04L9.533 2.7a3.07 3.07 0 0 1 3.257.139c.474.325.843.778 1.066 1.303.223.526.289 1.103.191 1.664zM5.503 8.575 4.139 7.8a.05.05 0 0 1-.026-.037V4.049c0-.57.166-1.127.476-1.607s.752-.864 1.275-1.105a3.08 3.08 0 0 1 3.234.41l-.096.054-3.23 1.838a.53.53 0 0 0-.265.455zm.742-1.577 1.758-1 1.762 1v2l-1.755 1-1.762-1z"/></svg>`,
    gemini: `<svg viewBox="0 0 65 65" fill="white"><path d="M32.447 0c.68 0 1.273.465 1.439 1.125a38.904 38.904 0 001.999 5.905c2.152 5 5.105 9.376 8.854 13.125 3.751 3.75 8.126 6.703 13.125 8.855a38.98 38.98 0 005.906 1.999c.66.166 1.124.758 1.124 1.438 0 .68-.464 1.273-1.125 1.439a38.902 38.902 0 00-5.905 1.999c-5 2.152-9.375 5.105-13.125 8.854-3.749 3.751-6.702 8.126-8.854 13.125a38.973 38.973 0 00-2 5.906 1.485 1.485 0 01-1.438 1.124c-.68 0-1.272-.464-1.438-1.125a38.913 38.913 0 00-2-5.905c-2.151-5-5.103-9.375-8.854-13.125-3.75-3.749-8.125-6.702-13.125-8.854a38.973 38.973 0 00-5.905-2A1.485 1.485 0 010 32.448c0-.68.465-1.272 1.125-1.438a38.903 38.903 0 005.905-2c5-2.151 9.376-5.104 13.125-8.854 3.75-3.749 6.703-8.125 8.855-13.125a38.972 38.972 0 001.999-5.905A1.485 1.485 0 0132.447 0z"/></svg>`,
};
const USER_AVATAR = `<svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="12" r="5" fill="white" opacity="0.85"/><path d="M7 27C7 21.5 11 18 16 18C21 18 25 21.5 25 27" fill="white" opacity="0.85"/></svg>`;

function getAvatarSvg(sender) {
    const resolved = resolveAgent(sender.toLowerCase());
    if (resolved && BRAND_AVATARS[resolved]) return BRAND_AVATARS[resolved];
    return USER_AVATAR;
}

// --- Init ---

function init() {
    // Configure marked for chat-style rendering
    marked.setOptions({
        breaks: true,      // single newline → <br>
        gfm: true,         // GitHub-flavored markdown
    });

    connectWebSocket();
    setupInput();
    setupDragDrop();
    setupPaste();
    setupScroll();
    setupSettingsKeys();
    setupKeyboardShortcuts();
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
    // Match http/https URLs not already inside an href or tag
    return html.replace(/(?<!["=])(https?:\/\/[^\s<>"')\]]+)/g, (match) => {
        // Don't double-wrap if already inside an <a> tag
        return `<a href="${match}" target="_blank" rel="noopener">${match}</a>`;
    });
}

function linkifyPaths(html) {
    // Match Windows paths like E:\foo\bar or E:/foo/bar (not inside existing tags)
    return html.replace(/(?<!["=\/])([A-Z]):[\\\/][\w\-.\\ \/]+/g, (match) => {
        const escaped = match.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        return `<a class="file-link" href="#" onclick="openInExplorer('${escaped}'); return false;" title="Open in Explorer">${match}</a>`;
    });
}

async function openInExplorer(path) {
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
            if (soundEnabled && !document.hasFocus() && event.data.type !== 'join' && event.data.sender && event.data.sender.toLowerCase() !== username.toLowerCase()) {
                playNotificationSound(event.data.sender);
            }
            appendMessage(event.data);
        } else if (event.type === 'agents') {
            applyAgentConfig(event.data);
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
            if (!soundEnabled) soundEnabled = true;
        } else if (event.type === 'typing') {
            updateTyping(event.agent, event.active);
        } else if (event.type === 'settings') {
            applySettings(event.data);
        } else if (event.type === 'delete') {
            handleDeleteBroadcast(event.ids);
        } else if (event.type === 'clear') {
            document.getElementById('messages').innerHTML = '';
            lastMessageDate = null;
        }
    };

    ws.onclose = () => {
        console.log('Disconnected, reconnecting in 2s...');
        soundEnabled = false;  // suppress sounds during reconnect history replay
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
    if (msgDate !== lastMessageDate) {
        lastMessageDate = msgDate;
        const divider = document.createElement('div');
        divider.className = 'date-divider';
        divider.innerHTML = `<span>${formatDateDivider(msgDate)}</span>`;
        container.appendChild(divider);
    }
}

// --- Messages ---

function appendMessage(msg) {
    const container = document.getElementById('messages');

    // Insert date divider if needed
    maybeInsertDateDivider(container, msg);

    const el = document.createElement('div');
    el.className = 'message';
    el.dataset.id = msg.id;

    if (msg.type === 'join' || msg.type === 'leave') {
        el.classList.add('join-msg');
        const color = getColor(msg.sender);
        el.innerHTML = `<span class="join-dot" style="background: ${color}"></span><span class="join-text"><strong style="color: ${color}">${escapeHtml(msg.sender)}</strong> ${msg.type === 'join' ? 'joined' : 'left'}</span>`;
    } else if (msg.type === 'system' || msg.sender === 'system') {
        el.classList.add('system-msg');
        el.innerHTML = `<span class="msg-text">${escapeHtml(msg.text)}</span>`;
    } else {
        const isError = msg.text.startsWith('[') && msg.text.includes('error');
        if (isError) el.classList.add('error-msg');

        let textHtml = renderMarkdown(msg.text);

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

        const avatarHtml = `<div class="avatar" style="background-color: ${senderColor}">${getAvatarSvg(msg.sender)}</div>`;

        const statusLabel = todoStatusLabel(todoStatus);
        el.dataset.rawText = msg.text;
        el.innerHTML = `<div class="todo-strip"></div>${isSelf ? '' : avatarHtml}<div class="chat-bubble" style="--bubble-color: ${senderColor}">${replyHtml}<div class="bubble-header"><span class="msg-sender" style="color: ${senderColor}">${escapeHtml(msg.sender)}</span><span class="msg-time">${msg.time || ''}</span></div><div class="msg-text">${textHtml}</div>${attachmentsHtml}</div><div class="msg-actions"><button class="reply-btn" onclick="startReply(${msg.id}, event)">reply</button><button class="todo-hint" onclick="todoCycle(${msg.id}); event.stopPropagation();">${statusLabel}</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">del</button></div>`;
        if (todoStatus) el.classList.add('msg-todo', `msg-todo-${todoStatus}`);

        // Add copy buttons to code blocks
        addCodeCopyButtons(el);
    }

    container.appendChild(el);

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
        // Re-render markdown with updated mention colors
        const textEl = el.querySelector('.msg-text');
        if (textEl && el.dataset.rawText) {
            textEl.innerHTML = renderMarkdown(el.dataset.rawText);
            addCodeCopyButtons(el);
        }
    }
}

function buildStatusPills() {
    const container = document.getElementById('agent-status');
    container.innerHTML = '';
    for (const [name, cfg] of Object.entries(agentConfig)) {
        const pill = document.createElement('div');
        pill.className = 'status-pill';
        pill.id = `status-${name}`;
        pill.innerHTML = `<span class="status-dot"></span><span class="status-label">${escapeHtml(cfg.label || name)}</span>`;
        container.appendChild(pill);
    }
}

// --- Status ---

function updateStatus(data) {
    for (const [name, info] of Object.entries(data)) {
        if (name === 'paused') continue;
        const pill = document.getElementById(`status-${name}`);
        if (!pill) continue;

        pill.classList.remove('available', 'busy', 'offline');
        if (info.busy) {
            pill.classList.add('busy');
        } else if (info.available) {
            pill.classList.add('available');
        } else {
            pill.classList.add('offline');
        }

        // Click to open session in terminal
        if (info.session_id) {
            pill.title = `Click to open ${name} session in terminal`;
            pill.style.cursor = 'pointer';
            pill.onclick = async () => {
                const label = pill.querySelector('.status-label');
                label.textContent = 'Opening...';
                try {
                    const resp = await fetch(`/api/open-session/${name}`, { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN } });
                    const result = await resp.json();
                    if (resp.ok) {
                        label.textContent = 'Opened!';
                    } else {
                        label.textContent = result.error || 'Error';
                    }
                } catch (err) {
                    label.textContent = 'Error';
                }
                setTimeout(() => {
                    label.textContent = info.label || name;
                }, 2000);
            };
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

function applySettings(data) {
    if (data.title) {
        document.getElementById('room-title').textContent = data.title;
        document.title = data.title;
        document.getElementById('setting-title').value = data.title;
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
}

function toggleSettings() {
    const bar = document.getElementById('settings-bar');
    bar.classList.toggle('hidden');
    if (!bar.classList.contains('hidden')) {
        document.getElementById('setting-username').focus();
    }
}

function clearChat() {
    if (!confirm('Clear all chat messages? This cannot be undone.')) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username }));
    }
    document.getElementById('settings-bar').classList.add('hidden');
}

function saveSettings() {
    const newUsername = document.getElementById('setting-username').value.trim();
    const newTitle = document.getElementById('setting-title').value.trim();
    const newFont = document.getElementById('setting-font').value;
    const newHops = document.getElementById('setting-hops').value;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'update_settings',
            data: {
                username: newUsername || 'user',
                title: newTitle || 'agentchattr',
                font: newFont,
                max_agent_hops: parseInt(newHops) || 4,
            }
        }));
    }

    document.getElementById('settings-bar').classList.add('hidden');
}

function setupSettingsKeys() {
    // Save on Enter in settings fields
    for (const id of ['setting-username', 'setting-title', 'setting-hops']) {
        document.getElementById(id).addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                saveSettings();
            }
            if (e.key === 'Escape') {
                document.getElementById('settings-bar').classList.add('hidden');
            }
        });
    }
    // Also handle the select
    document.getElementById('setting-font').addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.getElementById('settings-bar').classList.add('hidden');
        }
    });
}

// --- Keyboard shortcuts ---

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('image-modal');
        const modalOpen = modal && !modal.classList.contains('hidden');

        if (e.key === 'Escape') {
            if (modalOpen) { closeImageModal(); return; }
            if (replyingTo) { cancelReply(); }
        }
        if (modalOpen && e.key === 'ArrowLeft') { e.preventDefault(); modalPrev(e); }
        if (modalOpen && e.key === 'ArrowRight') { e.preventDefault(); modalNext(e); }
    });
}

// --- Slash command menu ---

const SLASH_COMMANDS = [
    { cmd: '/roastreview', desc: 'Get all agents to review and roast each other\'s work' },
    { cmd: '/poetry haiku', desc: 'Agents write a haiku about the codebase' },
    { cmd: '/poetry limerick', desc: 'Agents write a limerick about the codebase' },
    { cmd: '/poetry sonnet', desc: 'Agents write a sonnet about the codebase' },
    { cmd: '/continue', desc: 'Resume after loop guard pauses' },
    { cmd: '/clear', desc: 'Clear all chat messages' },
];

let slashMenuIndex = 0;
let slashMenuVisible = false;

function updateSlashMenu(text) {
    const menu = document.getElementById('slash-menu');
    if (!text.startsWith('/') || text.includes(' ') && !text.startsWith('/poetry')) {
        menu.classList.add('hidden');
        slashMenuVisible = false;
        return;
    }

    const query = text.toLowerCase();
    const matches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(query));

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

// --- Input ---

function setupInput() {
    const input = document.getElementById('input');

    input.addEventListener('keydown', (e) => {
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

    // Auto-resize + slash menu
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        updateSlashMenu(input.value);
    });
}

function sendMessage() {
    const input = document.getElementById('input');
    let text = input.value.trim();

    if (!text && pendingAttachments.length === 0) return;

    // Prepend active mention toggles if the message doesn't already mention them
    if (activeMentions.size > 0 && text) {
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

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                await uploadImage(file);
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

    timeline.addEventListener('scroll', () => {
        const distFromBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        autoScroll = distFromBottom < 60;

        if (autoScroll) {
            unreadCount = 0;
        }
        updateScrollAnchor();
    });
}

// --- Reply ---

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
    container.innerHTML = `<span class="reply-preview-label">replying to</span> <span style="color: ${color}; font-weight: 600">${escapeHtml(replyingTo.sender)}</span>: ${escapeHtml(truncated)} <button class="reply-cancel" onclick="cancelReply()">&times;</button>`;
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

    // Add delete-mode class to timeline for CSS shift
    document.getElementById('messages').classList.add('delete-mode');

    // Add radio circles to all messages (not joins)
    document.querySelectorAll('.message[data-id]').forEach(el => {
        if (el.classList.contains('join-msg') || el.classList.contains('system-msg')) return;
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
    document.getElementById('scroll-anchor').style.bottom = '170px';
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
        footer.insertBefore(bar, footer.firstChild);
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

    // Animate messages back
    document.getElementById('messages').classList.remove('delete-mode');

    // Animate bar out
    const bar = document.getElementById('delete-bar');
    if (bar) {
        bar.style.animation = 'delete-bar-out 0.2s ease-in forwards';
        setTimeout(() => {
            bar.classList.add('hidden');
            bar.style.animation = '';
        }, 200);
    }

    // Fade out radios then remove
    document.querySelectorAll('.delete-radio').forEach(el => {
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.2s';
        setTimeout(() => el.remove(), 200);
    });

    document.getElementById('scroll-anchor').style.bottom = '120px';
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
    const panel = document.getElementById('pins-panel');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        renderTodosPanel();
    }
}

function renderTodosPanel() {
    const list = document.getElementById('pins-list');
    list.innerHTML = '';

    const todoIds = Object.keys(todos);
    if (todoIds.length === 0) {
        list.innerHTML = '<div class="pins-empty">No todos</div>';
        return;
    }

    // Sort: open first, then done
    const sorted = todoIds.map(Number).sort((a, b) => {
        const sa = todos[a], sb = todos[b];
        if (sa === sb) return a - b;
        return sa === 'todo' ? -1 : 1;
    });

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

        item.innerHTML = `<button class="${checkClass}" onclick="todoToggle(${id})">${check}</button><span class="msg-time">${escapeHtml(time)}</span> <span class="msg-sender" style="color: ${senderColor}">${escapeHtml(sender)}</span> <span class="msg-text">${escapeHtml(text)}</span><button class="todo-remove-btn" onclick="todoRemove(${id})" title="Remove from todos">&times;</button>`;
        list.appendChild(item);
    }
}

// --- Mention toggles ---

function buildMentionToggles() {
    const container = document.getElementById('mention-toggles');
    container.innerHTML = '';

    for (const [name, cfg] of Object.entries(agentConfig)) {
        const btn = document.createElement('button');
        btn.className = 'mention-toggle';
        btn.dataset.agent = name;
        btn.textContent = `@${cfg.label || name}`;
        btn.style.setProperty('--agent-color', cfg.color);
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
    const imgs = document.querySelectorAll('.msg-attachments img');
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

// --- Helpers ---

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// --- Start ---

document.addEventListener('DOMContentLoaded', init);

