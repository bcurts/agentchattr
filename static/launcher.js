/**
 * agentchattr Launcher Control Panel - Frontend
 * 
 * Design baseline: launcher-control-panel-v3.1.2.html mockup
 * Constraints:
 *   - Frontend does NOT generate instance names
 *   - Frontend does NOT construct yolo command arguments
 *   - All state comes from backend API
 */

// ============================================================
// CONFIG
// ============================================================
const USE_MOCK = false;
const API_BASE = '';
const POLL_INTERVAL_MS = 3000;
const SESSION_TOKEN = window.__SESSION_TOKEN__ || "";

// ============================================================
// STATE
// ============================================================
const state = {
    server: null,        // { status, port, mcp_sse, mcp_http, data_dir, uptime }
    templates: [],       // AgentTemplate[] from config
    processes: [],       // ManagedProcess[]
    logs: {},            // { process_key: LogLine[] }
    events: [],          // Recent events
    currentPage: 'dashboard',
    currentAgentFilter: 'all',
    currentTerminalTab: null,
    drawer: {
        open: false,
        advancedOpen: false,
        mode: 'normal',
        autoStart: true,
        selectedBase: null
    }
};

// ============================================================
// API CLIENT
// ============================================================

const realApi = {
    async _fetch(method, path, body) {
        const opts = { method, headers: { 'X-Session-Token': SESSION_TOKEN } };
        if (body) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(API_BASE + path, opts);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: 'Unknown error' }));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        return res.json();
    },
    getStatus()    { return this._fetch('GET', '/api/launcher/status'); },
    getAgents()    { return this._fetch('GET', '/api/launcher/agents'); },
    startServer()  { return this._fetch('POST', '/api/launcher/server/start'); },
    stopServer()   { return this._fetch('POST', '/api/launcher/server/stop'); },
    restartServer(){ return this._fetch('POST', '/api/launcher/server/restart'); },
    startAgent(base, body) { return this._fetch('POST', `/api/launcher/agents/${base}/start`, body); },
    stopProcess(key)       { return this._fetch('POST', `/api/launcher/processes/${key}/stop`); },
    restartProcess(key)    { return this._fetch('POST', `/api/launcher/processes/${key}/restart`); },
    getLogs(key)           { return this._fetch('GET', `/api/launcher/logs/${key}`); }
};

// Mock API that simulates realistic state
const mockApi = {
    _delay(ms = 300) { return new Promise(r => setTimeout(r, ms)); },

    async getStatus() {
        await this._delay();
        return {
            server: {
                running: true,
                port: 8300,
                host: '127.0.0.1',
                mcp_sse: 'active',
                mcp_http: 'active',
                data_dir: './data',
                uptime: '2h 14m',
                memory: '128MB',
                errors_last_hour: 0
            },
            templates: {
                codex:  { base: 'codex',  label: 'Codex',  color: '#10a37f', command: 'codex',  cwd: '..', supports_yolo: true },
                kimi:   { base: 'kimi',   label: 'Kimi',   color: '#1783ff', command: 'kimi',   cwd: '..', supports_yolo: false },
                claude: { base: 'claude', label: 'Claude', color: '#da7756', command: 'claude', cwd: '..', supports_yolo: true },
                gemini: { base: 'gemini', label: 'Gemini', color: '#4285f4', command: 'gemini', cwd: '..', supports_yolo: true },
                qwen:   { base: 'qwen',   label: 'Qwen',   color: '#8b5cf6', command: 'qwen',   cwd: '..', supports_yolo: true },
                kilo:   { base: 'kilo',   label: 'Kilo',   color: '#f7f677', command: 'kilo',   cwd: '..', supports_yolo: false },
            },
            processes: {
                'agent:codex:1234': { key: 'agent:codex:1234', kind: 'agent', base: 'codex', assigned_name: 'codex', status: 'working', started_by_launcher: true, pid: 1234, role: 'Builder', mode: 'normal', start_time: Date.now() - 3600000 },
                'agent:kimi:2345':  { key: 'agent:kimi:2345',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi',  status: 'running', started_by_launcher: true, pid: 2345, role: 'none', mode: 'normal', start_time: Date.now() - 7200000 },
                'agent:kimi:3456':  { key: 'agent:kimi:3456',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi-2', status: 'running', started_by_launcher: true, pid: 3456, role: 'Planner', mode: 'yolo', start_time: Date.now() - 5400000 },
                'agent:kimi:4567':  { key: 'agent:kimi:4567',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi-3', status: 'stopped', started_by_launcher: true, pid: null, role: 'none', mode: 'normal', start_time: null, last_error: 'WebSocket 连接断开' },
                'agent:claude:5678':{ key: 'agent:claude:5678', kind: 'agent', base: 'claude', assigned_name: 'claude', status: 'stopped', started_by_launcher: false, pid: null, role: 'Reviewer', mode: 'normal', start_time: null },
                'agent:gemini:6789':{ key: 'agent:gemini:6789', kind: 'agent', base: 'gemini', assigned_name: 'gemini', status: 'stopped', started_by_launcher: false, pid: null, role: 'Researcher', mode: 'normal', start_time: null },
            }
        };
    },

    async getAgents() {
        await this._delay(200);
        return {
            templates: {
                codex:  { base: 'codex',  label: 'Codex',  color: '#10a37f', command: 'codex',  cwd: '..', supports_yolo: true },
                kimi:   { base: 'kimi',   label: 'Kimi',   color: '#1783ff', command: 'kimi',   cwd: '..', supports_yolo: false },
                claude: { base: 'claude', label: 'Claude', color: '#da7756', command: 'claude', cwd: '..', supports_yolo: true },
                gemini: { base: 'gemini', label: 'Gemini', color: '#4285f4', command: 'gemini', cwd: '..', supports_yolo: true },
                qwen:   { base: 'qwen',   label: 'Qwen',   color: '#8b5cf6', command: 'qwen',   cwd: '..', supports_yolo: true },
                kilo:   { base: 'kilo',   label: 'Kilo',   color: '#f7f677', command: 'kilo',   cwd: '..', supports_yolo: false },
            },
            processes: {
                'agent:codex:1234': { key: 'agent:codex:1234', kind: 'agent', base: 'codex', assigned_name: 'codex', status: 'working', started_by_launcher: true, pid: 1234, role: 'Builder', mode: 'normal', start_time: Date.now() - 3600000 },
                'agent:kimi:2345':  { key: 'agent:kimi:2345',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi',  status: 'running', started_by_launcher: true, pid: 2345, role: 'none', mode: 'normal', start_time: Date.now() - 7200000 },
                'agent:kimi:3456':  { key: 'agent:kimi:3456',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi-2', status: 'running', started_by_launcher: true, pid: 3456, role: 'Planner', mode: 'yolo', start_time: Date.now() - 5400000 },
                'agent:kimi:4567':  { key: 'agent:kimi:4567',  kind: 'agent', base: 'kimi',  assigned_name: 'kimi-3', status: 'stopped', started_by_launcher: true, pid: null, role: 'none', mode: 'normal', start_time: null, last_error: 'WebSocket 连接断开' },
                'agent:claude:5678':{ key: 'agent:claude:5678', kind: 'agent', base: 'claude', assigned_name: 'claude', status: 'stopped', started_by_launcher: false, pid: null, role: 'Reviewer', mode: 'normal', start_time: null },
                'agent:gemini:6789':{ key: 'agent:gemini:6789', kind: 'agent', base: 'gemini', assigned_name: 'gemini', status: 'stopped', started_by_launcher: false, pid: null, role: 'Researcher', mode: 'normal', start_time: null },
            }
        };
    },

    async startServer() {
        await this._delay(800);
        return { status: 'running', port: 8300 };
    },

    async stopServer() {
        await this._delay(500);
        return { status: 'stopped' };
    },

    async restartServer() {
        await this._delay(1200);
        return { status: 'running', port: 8300 };
    },

    async startAgent(base, body) {
        await this._delay(600);
        const existing = state.processes.filter(p => p.base === base && p.status === 'running');
        const nextNum = existing.length === 0 ? '' : `-${existing.length + 1}`;
        const assignedName = base + nextNum;
        const key = `agent:${base}:${Date.now()}`;
        return {
            process_key: key,
            base: base,
            assigned_name: assignedName,
            status: 'starting',
            started_by_launcher: true
        };
    },

    async stopProcess(key) {
        await this._delay(400);
        return { status: 'stopped' };
    },

    async restartProcess(key) {
        await this._delay(800);
        return { status: 'starting' };
    },

    async getLogs(key) {
        await this._delay(200);
        const sampleLogs = [
            { time: '14:20:01', level: 'INFO', text: 'Wrapper 启动中...' },
            { time: '14:20:03', level: 'OK', text: '技能已加载' },
            { time: '14:20:04', level: 'OK', text: 'WebSocket 连接成功' },
            { time: '14:20:15', level: 'INFO', text: '收到 #agentchat 消息' },
            { time: '14:20:16', level: 'OK', text: '消息已发送' },
        ];
        return { logs: sampleLogs };
    }
};

const api = USE_MOCK ? mockApi : realApi;

// ============================================================
// UTILS
// ============================================================

function el(id) { return document.getElementById(id); }

function formatTime(ts) {
    if (!ts) return '--';
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

function getStatusBadge(status) {
    const map = {
        running:  { cls: 'online',  text: '在线',    dot: 'online' },
        active:   { cls: 'online',  text: '在线',    dot: 'online' },
        working:  { cls: 'working', text: '工作中',  dot: 'working' },
        starting: { cls: 'working', text: '启动中',  dot: 'working' },
        stopping: { cls: 'working', text: '停止中',  dot: 'working' },
        stopped:  { cls: 'offline', text: '离线',    dot: 'offline' },
        error:    { cls: 'error',   text: '异常',    dot: 'error' },
        external: { cls: 'online',  text: '外部运行', dot: 'online' }
    };
    return map[status] || { cls: 'offline', text: status, dot: 'offline' };
}

function getAvatarInitials(name) {
    if (!name) return '??';
    return name.substring(0, 2).toUpperCase();
}

function getRoleLabel(role) {
    if (!role || role === 'none') return '';
    const map = {
        planner: '规划者',
        builder: '构建者',
        reviewer: '审查者',
        researcher: '研究者'
    };
    return map[role] || role;
}

// ============================================================
// TOAST
// ============================================================

function showToast(message, type = 'success') {
    const container = el('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 16A8 8 0 108 0a8 8 0 000 16zm3.78-9.72a.75.75 0 00-1.06-1.06L6.75 9.19 5.28 7.72a.75.75 0 00-1.06 1.06l2 2a.75.75 0 001.06 0l4.5-4.5z"/></svg> ${message}`;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================
// DATA LOADING
// ============================================================

function _dictToArray(obj) {
    if (!obj) return [];
    if (Array.isArray(obj)) return obj;
    return Object.values(obj);
}

async function loadStatus() {
    try {
        const data = await api.getStatus();
        // Backend returns { server: {...}, templates: {...}, processes: {...} }
        // Normalize dicts to arrays, fall back to flat object for mock compat
        state.server = data.server || data;
        if (data.templates) state.templates = _dictToArray(data.templates);
        if (data.processes) state.processes = _dictToArray(data.processes);
    } catch (e) {
        state.server = { running: false, error: e.message };
    }
}

async function loadAgents() {
    try {
        const data = await api.getAgents();
        state.templates = _dictToArray(data.templates);
        let processes = _dictToArray(data.processes);

        // Deduplicate: if both external:* and launcher-managed exist for same assigned_name,
        // prefer launcher-managed (more accurate info: pid, logs, etc.)
        const nameMap = new Map();
        for (const p of processes) {
            const name = p.assigned_name || p.name;
            if (!name) continue;
            const existing = nameMap.get(name);
            if (!existing || (existing.started_by_launcher === false && p.started_by_launcher === true)) {
                nameMap.set(name, p);
            }
        }
        processes = Array.from(nameMap.values());

        // Merge registry_instances (external agents not started by launcher)
        const registryInstances = _dictToArray(data.registry_instances);
        if (registryInstances && registryInstances.length > 0) {
            const existingKeys = new Set(processes.map(p => p.key));
            const existingNames = new Set(processes.map(p => p.assigned_name || p.name).filter(Boolean));
            for (const inst of registryInstances) {
                // Skip if already in processes
                const key = inst.key || `agent:${inst.base}:${inst.name}`;
                const name = inst.name || inst.assigned_name || inst.base;
                if (existingKeys.has(key) || existingNames.has(name)) continue;
                processes.push({
                    key: key,
                    kind: 'agent',
                    base: inst.base || inst.agent_type,
                    assigned_name: name,
                    status: inst.status || 'running',
                    started_by_launcher: false,
                    pid: inst.pid || null,
                    role: inst.role || 'none',
                    mode: 'normal',
                    start_time: null,
                    last_error: null,
                });
            }
        }
        state.processes = processes;
    } catch (e) {
        showToast('加载代理列表失败: ' + e.message, 'error');
    }
}

async function refreshAll() {
    await Promise.all([loadStatus(), loadAgents()]);
    renderAll();
}

// ============================================================
// RENDERING
// ============================================================

function renderAll() {
    renderServerStatus();
    renderFilterTabs();
    renderAgentList('dashboard-agent-list');
    renderAgentList('agents-agent-list');
    renderSummary();
    renderEvents();
    renderTerminalTabs();
    renderTerminalPanes();
    updateNavCount();
}

function renderServerStatus() {
    const srv = state.server || {};
    const badge = el('server-status-badge');
    const dot = el('server-status-dot');
    const text = el('server-status-text');

    const isRunning = srv.running === true || srv.status === 'running';
    const managed = srv.managed_by_launcher === true;
    const st = getStatusBadge(isRunning ? 'running' : (srv.status === 'stopped' ? 'stopped' : 'error'));
    badge.className = `status-badge ${st.cls}`;
    dot.className = `status-dot ${st.dot}`;
    text.textContent = st.text;

    el('server-port').textContent = srv.port ?? '--';
    // Backend returns mcp_http_port / mcp_sse_port (numbers), or legacy mcp_sse/mcp_http strings
    const mcpSseText = srv.mcp_sse_port ? `端口 ${srv.mcp_sse_port}` : (srv.mcp_sse === 'active' ? '活跃' : (srv.mcp_sse ?? '--'));
    const mcpHttpText = srv.mcp_http_port ? `端口 ${srv.mcp_http_port}` : (srv.mcp_http === 'active' ? '活跃' : (srv.mcp_http ?? '--'));
    el('server-mcp-sse').textContent = mcpSseText;
    el('server-mcp-sse').style.color = isRunning ? 'var(--online)' : '';
    el('server-mcp-http').textContent = mcpHttpText;
    el('server-mcp-http').style.color = isRunning ? 'var(--online)' : '';
    el('server-data-dir').textContent = srv.data_dir ?? '--';

    el('metric-memory').textContent = srv.memory ?? '--';
    el('metric-errors').textContent = srv.errors_last_hour ?? 0;

    // Server control buttons: this is a runtime console, not a bootstrap launcher.
    // Start: only if not running. Stop/Restart: only if managed_by_launcher.
    const startBtn = el('btn-start-server');
    if (startBtn) {
        startBtn.disabled = isRunning;
        startBtn.title = isRunning ? '服务已在运行（控制面板本身就是服务的一部分）' : '';
    }
    const stopBtn = el('btn-stop-server');
    if (stopBtn) {
        stopBtn.disabled = !isRunning || !managed;
        stopBtn.title = (!isRunning) ? '服务未运行' : (managed ? '' : '外部启动的服务，请从原终端停止');
    }
    const restartBtn = el('btn-restart-server');
    if (restartBtn) {
        restartBtn.disabled = !isRunning || !managed;
        restartBtn.title = (!isRunning) ? '服务未运行' : (managed ? '' : '外部启动的服务，无法通过控制面板重启');
    }
}

function renderFilterTabs() {
    const bases = [...new Set(state.templates.map(t => t.base))];
    const counts = {};
    bases.forEach(b => counts[b] = state.processes.filter(p => p.base === b).length);

    function build(containerId) {
        const container = el(containerId);
        if (!container) return;
        // Keep "all" tab, rebuild the rest
        const allBtn = container.querySelector('[data-filter="all"]');
        const wasActive = allBtn?.classList.contains('active') ? 'all' : container.querySelector('.active')?.dataset.filter;
        container.innerHTML = '';

        const total = state.processes.length;
        const allTab = document.createElement('button');
        allTab.className = `agent-filter-tab ${wasActive === 'all' || !wasActive ? 'active' : ''}`;
        allTab.dataset.filter = 'all';
        allTab.textContent = `全部 (${total})`;
        allTab.onclick = () => setAgentFilter('all', allTab);
        container.appendChild(allTab);

        bases.forEach(base => {
            const tmpl = state.templates.find(t => t.base === base);
            const label = tmpl ? tmpl.label : base;
            const count = counts[base] || 0;
            const btn = document.createElement('button');
            btn.className = `agent-filter-tab ${wasActive === base ? 'active' : ''}`;
            btn.dataset.filter = base;
            btn.textContent = `${label} (${count})`;
            btn.onclick = () => setAgentFilter(base, btn);
            container.appendChild(btn);
        });
    }

    build('dashboard-filter-tabs');
    build('agents-filter-tabs');
}

function setAgentFilter(filter, btnEl) {
    state.currentAgentFilter = filter;
    const parent = btnEl.parentElement;
    parent.querySelectorAll('.agent-filter-tab').forEach(t => t.classList.remove('active'));
    btnEl.classList.add('active');
    renderAgentList('dashboard-agent-list');
    renderAgentList('agents-agent-list');
}

function renderAgentList(containerId) {
    const container = el(containerId);
    if (!container) return;

    const filter = state.currentAgentFilter;
    const filtered = state.processes.filter(p => filter === 'all' || p.base === filter);

    if (filtered.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无代理实例</div>';
        return;
    }

    container.innerHTML = '';
    filtered.forEach(proc => {
        const tmpl = state.templates.find(t => t.base === proc.base);
        const st = getStatusBadge(proc.status);
        const roleLabel = getRoleLabel(proc.role);
        const initials = getAvatarInitials(proc.assigned_name || proc.base);
        const canStop = proc.started_by_launcher && (proc.status === 'running' || proc.status === 'working');
        const canStart = proc.status === 'stopped' || proc.status === 'error';
        const canRestart = proc.started_by_launcher && (proc.status === 'running' || proc.status === 'working');
        const isExternal = !proc.started_by_launcher && proc.status !== 'stopped';

        const card = document.createElement('div');
        card.className = 'agent-card';
        card.dataset.type = proc.base;
        card.dataset.key = proc.key;

        const modeTag = proc.mode === 'yolo' ? '<span style="color:var(--warning);font-size:10px;margin-left:4px;">[Yolo]</span>' : '';

        card.innerHTML = `
            <div class="agent-avatar ${proc.base}">${initials}</div>
            <div class="agent-info">
                <div class="agent-name-row">
                    <span class="agent-name">${proc.assigned_name || proc.base}</span>
                    <span class="agent-type-tag">${proc.base}</span>
                    <span class="status-badge ${st.cls}"><span class="status-dot ${st.dot}"></span>${isExternal ? '外部运行' : st.text}</span>
                    ${modeTag}
                </div>
                <div class="agent-role">${roleLabel ? roleLabel + ' · ' : ''}${tmpl ? tmpl.label : proc.base}代理</div>
                <div class="agent-meta">
                    <span><svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M8 4a4 4 0 100 8 4 4 0 000-8z"/></svg>${proc.pid ? 'PID: ' + proc.pid : '未运行'}</span>
                    <span><svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 2A3.5 3.5 0 002 5.5v5A3.5 3.5 0 005.5 14h5a3.5 3.5 0 003.5-3.5v-5A3.5 3.5 0 0010.5 2h-5z"/></svg>${proc.started_by_launcher ? '由启动器管理' : '外部进程'}</span>
                </div>
            </div>
            <div class="agent-actions">
                ${canStop
                    ? `<button class="btn btn-sm btn-outline-danger" data-action="stop" data-key="${proc.key}">停止</button>`
                    : canStart
                        ? `<button class="btn btn-sm btn-outline-success" data-action="start" data-base="${proc.base}">启动</button>`
                        : `<button class="btn btn-sm" disabled>--</button>`
                }
                <button class="btn btn-sm btn-ghost" data-action="restart" data-key="${proc.key}" title="重启" ${canRestart ? '' : 'disabled'}>
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M11.534 7.151a.5.5 0 01-.742-.437 4 4 0 00-7.036-2.088.5.5 0 11-.824-.564 5 5 0 018.796 2.61.5.5 0 01-.194.479z"/></svg>
                </button>
            </div>
        `;
        container.appendChild(card);
    });

    // Update summary text
    const summaryEl = el('agent-summary-text');
    if (summaryEl) {
        const online = state.processes.filter(p => p.status === 'running' || p.status === 'active' || p.status === 'working').length;
        const working = state.processes.filter(p => p.status === 'working').length;
        summaryEl.textContent = `${state.processes.length} 个实例 · ${online} 在线 · ${working} 工作中`;
    }
}

function renderSummary() {
    const online = state.processes.filter(p => p.status === 'running' || p.status === 'active').length;
    const working = state.processes.filter(p => p.status === 'working').length;
    const offline = state.processes.filter(p => p.status === 'stopped' || p.status === 'error').length;
    const err = state.processes.filter(p => p.status === 'error').length;

    el('summary-online').textContent = online;
    el('summary-working').textContent = working;
    el('summary-offline').textContent = offline;
    el('summary-error').textContent = err;

    el('metric-online').textContent = online;
    el('metric-working').textContent = working;
}

function renderEvents() {
    const container = el('recent-events');
    if (!container) return;

    const events = state.events.length > 0 ? state.events : [
        { time: '刚刚', type: 'info', text: '控制台已加载' }
    ];

    container.innerHTML = '';
    events.slice(0, 5).forEach(ev => {
        const item = document.createElement('div');
        item.className = 'event-item';
        item.innerHTML = `
            <span class="event-time">${ev.time}</span>
            <span class="event-dot ${ev.type}"></span>
            <span class="event-text">${ev.text}</span>
        `;
        container.appendChild(item);
    });
}

function updateNavCount() {
    const count = state.processes.length;
    const badge = el('nav-agent-count');
    if (badge) badge.textContent = count;
}

function renderTerminalTabs() {
    const container = el('terminal-tabs');
    if (!container) return;

    // Build tabs from processes + server
    const serverStatus = state.server?.status || (state.server?.running ? 'running' : 'stopped');
    const tabs = [{ key: 'server', label: 'server', status: serverStatus }];
    state.processes.forEach(p => {
        tabs.push({ key: p.key, label: p.assigned_name || p.base, status: p.status });
    });

    container.innerHTML = '';
    tabs.forEach((tab, i) => {
        const st = getStatusBadge(tab.status);
        const isActive = state.currentTerminalTab === tab.key || (i === 0 && !state.currentTerminalTab);
        if (isActive) state.currentTerminalTab = tab.key;

        const btn = document.createElement('div');
        btn.className = `terminal-tab ${isActive ? 'active' : ''}`;
        btn.innerHTML = `<span class="tab-status" style="background:var(--${st.dot})"></span>${tab.label}`;
        btn.onclick = () => switchTerminalTab(tab.key, btn);
        container.appendChild(btn);
    });
}

function renderTerminalPanes() {
    const container = el('terminal-content');
    if (!container) return;

    const tabs = [{ key: 'server', label: 'server' }];
    state.processes.forEach(p => tabs.push({ key: p.key, label: p.assigned_name || p.base }));

    container.innerHTML = '';
    tabs.forEach((tab, i) => {
        const isActive = state.currentTerminalTab === tab.key || (i === 0 && !state.currentTerminalTab);
        const pane = document.createElement('div');
        pane.className = `terminal-pane ${isActive ? 'active' : ''}`;
        pane.id = `term-${tab.key}`;

        // Generate some mock log lines
        const logs = state.logs[tab.key] || [
            { time: formatTime(Date.now() - 60000), level: 'INFO', text: `[${tab.label}] 日志加载中...` }
        ];

        pane.innerHTML = logs.map(l => `
            <div class="log-line">
                <span class="log-time">${l.time}</span>
                <span class="log-level log-level-${l.level.toLowerCase()}">${l.level}</span>
                <span class="log-msg ${l.level === 'INFO' ? 'dim' : ''}">${l.text}</span>
            </div>
        `).join('');

        container.appendChild(pane);
    });
}

function switchTerminalTab(key, btnEl) {
    state.currentTerminalTab = key;
    document.querySelectorAll('.terminal-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.terminal-pane').forEach(p => p.classList.remove('active'));
    btnEl.classList.add('active');
    const pane = el(`term-${key}`);
    if (pane) pane.classList.add('active');
}

// ============================================================
// DRAWER
// ============================================================

function openDrawer() {
    state.drawer.open = true;
    el('drawer-overlay').classList.add('active');
    el('agent-drawer').classList.add('active');

    // Populate agent type dropdown from templates
    const select = el('drawer-agent-type');
    select.innerHTML = '';
    state.templates.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.base;
        opt.textContent = `${t.label} — ${t.base}`;
        select.appendChild(opt);
    });
    // Add custom option
    const customOpt = document.createElement('option');
    customOpt.value = 'custom';
    customOpt.textContent = '自定义';
    select.appendChild(customOpt);

    // Reset fields
    el('drawer-custom-role-group').style.display = 'none';
    el('drawer-custom-role-input').value = '';
    el('drawer-workdir').value = 'D:\\kimicode';
    el('drawer-port').value = '';
    el('drawer-env').value = '';
    setDrawerMode('normal');
    setDrawerAutoStart(true);
    updateDrawerHint();
}

function closeDrawer() {
    state.drawer.open = false;
    el('drawer-overlay').classList.remove('active');
    el('agent-drawer').classList.remove('active');
}

function setDrawerMode(mode) {
    state.drawer.mode = mode;
    const btns = el('drawer-mode-control').querySelectorAll('.segment-btn');
    btns.forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    el('drawer-mode-hint').textContent = mode === 'normal'
        ? '普通模式：高风险操作需要确认'
        : 'Yolo 模式：自动执行，适合可信本地任务';
}

function setDrawerAutoStart(val) {
    state.drawer.autoStart = val;
    const toggle = el('drawer-auto-start');
    toggle.classList.toggle('active', val);
}

function toggleDrawerAdvanced() {
    state.drawer.advancedOpen = !state.drawer.advancedOpen;
    const fields = el('drawer-advanced-fields');
    const arrow = el('adv-arrow');
    const toggle = el('drawer-advanced-toggle');
    fields.style.display = state.drawer.advancedOpen ? 'block' : 'none';
    toggle.classList.toggle('open', state.drawer.advancedOpen);
}

function updateDrawerHint() {
    const type = el('drawer-agent-type').value;
    const hint = el('drawer-instance-hint');
    if (type === 'custom') {
        hint.textContent = '自定义代理的实例名和启动命令需手动配置';
        return;
    }
    const existing = state.processes.filter(p => p.base === type);
    if (existing.length === 0) {
        hint.textContent = `当前无该类型实例，启动后将分配为 ${type}`;
    } else {
        const names = existing.map(p => p.assigned_name || p.base).join('、');
        hint.textContent = `当前已有 ${names}，启动后将自动分配新名称`;
    }
}

async function saveAgent(autoStart) {
    const base = el('drawer-agent-type').value;
    const role = el('drawer-agent-role').value;
    const customRole = el('drawer-custom-role-input').value.trim();
    const cwd = el('drawer-workdir').value;
    const mode = state.drawer.mode;

    if (base === 'custom') {
        showToast('自定义代理请在配置文件中手动添加', 'error');
        return;
    }

    const tmpl = state.templates.find(t => t.base === base);
    if (mode === 'yolo' && tmpl && !tmpl.supports_yolo) {
        showToast(`${tmpl.label} 暂不支持 Yolo 模式`, 'error');
        return;
    }

    const body = {
        base: base,
        mode: mode,
        role: role === 'custom' && customRole ? customRole : (role === 'none' ? null : role),
        custom_role: role === 'custom' ? customRole : null,
        cwd: cwd,
        auto_start: autoStart
    };

    if (!autoStart) {
        // MVP: "仅保存" is not yet supported. Hide the button, but keep fallback.
        closeDrawer();
        showToast('「仅保存」功能第一版暂不支持，请使用「保存并启动」', 'error');
        return;
    }

    try {
        const result = await api.startAgent(base, body);
        closeDrawer();

        const tmpl2 = state.templates.find(t => t.base === base);
        const label = tmpl2 ? tmpl2.label : base;
        showToast(`已启动 ${label}，实例名：${result.assigned_name || '分配中'}`, 'success');

        // Refresh state
        await refreshAll();
    } catch (e) {
        showToast('启动失败: ' + e.message, 'error');
    }
}

// ============================================================
// ACTIONS
// ============================================================

async function doStartServer() {
    const srv = state.server || {};
    const isRunning = srv.running === true || srv.status === 'running';
    if (isRunning) {
        showToast('服务已在运行。控制面板是 server 的内置页面，无法冷启动自身。如需重启请使用「重启服务」。', 'error');
        return;
    }
    try {
        await api.startServer();
        showToast('服务已启动');
        await refreshAll();
    } catch (e) {
        showToast('启动服务失败: ' + e.message, 'error');
    }
}

async function doStopServer() {
    try {
        await api.stopServer();
        showToast('服务已停止');
        await refreshAll();
    } catch (e) {
        showToast('停止服务失败: ' + e.message, 'error');
    }
}

async function doRestartServer() {
    try {
        await api.restartServer();
        showToast('服务已重启');
        await refreshAll();
    } catch (e) {
        showToast('重启服务失败: ' + e.message, 'error');
    }
}

async function doStartAgent(base) {
    try {
        const tmpl = state.templates.find(t => t.base === base);
        const cwd = tmpl ? tmpl.cwd : null;
        const body = { base, mode: 'normal', role: null, auto_start: true };
        if (cwd) body.cwd = cwd;
        const result = await api.startAgent(base, body);
        showToast(`已启动 ${base}，实例名：${result.assigned_name || '分配中'}`);
        await refreshAll();
    } catch (e) {
        showToast('启动代理失败: ' + e.message, 'error');
    }
}

async function doStopProcess(key) {
    try {
        await api.stopProcess(key);
        showToast('代理已停止');
        await refreshAll();
    } catch (e) {
        showToast('停止代理失败: ' + e.message, 'error');
    }
}

async function doRestartProcess(key) {
    try {
        await api.restartProcess(key);
        showToast('代理重启中...');
        await refreshAll();
    } catch (e) {
        showToast('重启代理失败: ' + e.message, 'error');
    }
}

async function doStartAll() {
    const stopped = state.processes.filter(p => p.status === 'stopped' && p.started_by_launcher);
    for (const p of stopped) {
        await doStartAgent(p.base);
    }
    if (stopped.length === 0) showToast('没有可启动的代理');
}

async function doStopAll() {
    const running = state.processes.filter(p => (p.status === 'running' || p.status === 'working') && p.started_by_launcher);
    for (const p of running) {
        await doStopProcess(p.key);
    }
    if (running.length === 0) showToast('没有可停止的代理');
}

async function doRestartAll() {
    const running = state.processes.filter(p => (p.status === 'running' || p.status === 'working') && p.started_by_launcher);
    for (const p of running) {
        await doRestartProcess(p.key);
    }
    if (running.length === 0) showToast('没有可重启的代理');
}

function doOpenChat() {
    const port = state.server?.port || 8300;
    window.open(`http://127.0.0.1:${port}`, '_blank');
}

function doClearLogs() {
    const pane = document.querySelector('.terminal-pane.active');
    if (pane) {
        pane.innerHTML = '<div class="log-line"><span class="log-msg dim">--- 日志已清空 ---</span></div>';
    }
}

function doCopyLogs() {
    const pane = document.querySelector('.terminal-pane.active');
    if (!pane) return;
    const text = Array.from(pane.querySelectorAll('.log-msg')).map(el => el.textContent).join('\n');
    navigator.clipboard.writeText(text).then(() => showToast('日志已复制到剪贴板')).catch(() => showToast('复制失败', 'error'));
}

// ============================================================
// EVENT LISTENERS
// ============================================================

function initEventListeners() {
    // Page navigation
    document.querySelectorAll('.page-nav-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const pageId = tab.dataset.page;
            document.querySelectorAll('.page-nav-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            el('page-' + pageId).classList.add('active');
            state.currentPage = pageId;
        });
    });

    // Top bar buttons
    el('btn-open-chat').addEventListener('click', doOpenChat);
    el('btn-start-all').addEventListener('click', doStartAll);
    el('btn-stop-all').addEventListener('click', doStopAll);
    el('btn-restart-all').addEventListener('click', doRestartAll);

    // Server buttons
    el('btn-start-server').addEventListener('click', doStartServer);
    el('btn-stop-server').addEventListener('click', doStopServer);
    el('btn-restart-server').addEventListener('click', doRestartServer);
    el('btn-open-chat-2').addEventListener('click', doOpenChat);

    // Add agent buttons
    el('btn-add-agent-dash').addEventListener('click', openDrawer);
    el('btn-add-agent-page').addEventListener('click', openDrawer);

    // Drawer
    el('drawer-overlay').addEventListener('click', closeDrawer);
    el('drawer-close').addEventListener('click', closeDrawer);
    el('drawer-btn-cancel').addEventListener('click', closeDrawer);
    el('drawer-btn-save').addEventListener('click', () => saveAgent(false));
    el('drawer-btn-save-start').addEventListener('click', () => saveAgent(true));

    // Drawer mode toggle
    el('drawer-mode-control').querySelectorAll('.segment-btn').forEach(btn => {
        btn.addEventListener('click', () => setDrawerMode(btn.dataset.mode));
    });

    // Drawer auto-start toggle
    el('drawer-auto-start').addEventListener('click', () => setDrawerAutoStart(!state.drawer.autoStart));

    // Drawer advanced toggle
    el('drawer-advanced-toggle').addEventListener('click', toggleDrawerAdvanced);

    // Drawer agent type change
    el('drawer-agent-type').addEventListener('change', updateDrawerHint);

    // Drawer role change
    el('drawer-agent-role').addEventListener('change', () => {
        const role = el('drawer-agent-role').value;
        el('drawer-custom-role-group').style.display = role === 'custom' ? 'block' : 'none';
    });

    // Terminal buttons
    el('btn-clear-logs').addEventListener('click', doClearLogs);
    el('btn-copy-logs').addEventListener('click', doCopyLogs);

    // View all events -> switch to terminal
    el('btn-view-all-events').addEventListener('click', () => {
        const terminalTab = document.querySelector('[data-page="terminal"]');
        if (terminalTab) terminalTab.click();
    });

    // Delegate agent card actions
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.dataset.action;
        if (action === 'stop') {
            doStopProcess(btn.dataset.key);
        } else if (action === 'start') {
            doStartAgent(btn.dataset.base);
        } else if (action === 'restart') {
            doRestartProcess(btn.dataset.key);
        }
    });
}

// ============================================================
// INIT
// ============================================================

async function init() {
    initEventListeners();
    await refreshAll();

    // Periodic refresh (fallback if WS not connected)
    setInterval(refreshAll, POLL_INTERVAL_MS);

    // WebSocket for real-time events
    connectLauncherWS();
}

function connectLauncherWS() {
    const wsUrl = `ws://${window.location.host}/ws/launcher/events?token=${encodeURIComponent(SESSION_TOKEN)}`;
    const ws = new WebSocket(wsUrl);
    ws.onopen = () => {
        console.log('[Launcher] WS connected');
    };
    ws.onmessage = (ev) => {
        try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'status' && msg.data) {
                const data = msg.data;
                state.server = data.server || state.server;
                if (data.templates) state.templates = _dictToArray(data.templates);
                if (data.processes) state.processes = _dictToArray(data.processes);
                renderAll();
            }
        } catch (e) {
            console.error('[Launcher] WS message error:', e);
        }
    };
    ws.onerror = (e) => {
        console.error('[Launcher] WS error:', e);
    };
    ws.onclose = () => {
        console.log('[Launcher] WS disconnected, will retry...');
        setTimeout(connectLauncherWS, 5000);
    };
}

init();
