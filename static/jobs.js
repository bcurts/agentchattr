/**
 * jobs.js -- Jobs panel UI module
 *
 * Extracted from chat.js (PR 3 of monolith breakup).
 * Depends on core.js (Hub) and store.js (Store) loaded first.
 *
 * Owns all job state, rendering, and interaction logic.
 * Subscribes to Hub for WS events.
 *
 * Reads from window: activeChannel, username, SESSION_TOKEN, ws,
 *                    agentConfig, escapeHtml, getColor, renderMarkdown,
 *                    getMentionCandidates, openImageModal,
 *                    _lastMentionedAgent, _preserveScroll, startReply,
 *                    soundEnabled, playNotificationSound
 */

// ---------------------------------------------------------------------------
// Job state (moved from chat.js globals)
// ---------------------------------------------------------------------------

let jobsData = []; // all jobs from server
let activeJobId = null; // currently viewing job in conversation view
let jobUnread = {}; // { job_id: unread_message_count }
let jobReplyTargets = {}; // { job_id: default agent recipient }
let pendingDeleteJobId = null;
let archiveDeleteBatchIds = null; // Set<number> while client-side archive delete animation is active
let jobReorderMute = null; // { ids:Set<number>, channel, status, until:number, suppressed:boolean }
let jobReorderMuteTimer = null;
let _jobViewSwitching = false;
let jobPendingAttachments = [];
let jobMentionVisible = false;
let jobMentionIndex = 0;
let jobMentionStart = -1;
let _draggedJobId = null;
let _draggedJobStatus = null;
let _pendingJobReflowTops = null;
let _pendingJobReflowTimer = null;
const _expandedGroups = new Set(['open']); // track which collapsible groups are open across re-renders

// ---------------------------------------------------------------------------
// Message Renderers
// ---------------------------------------------------------------------------
// Register job message renderers so appendMessage in chat.js can
// delegate to us via window._messageRenderers[msg.type](el, msg).

if (!window._messageRenderers) window._messageRenderers = {};

window._messageRenderers['job_created'] = function (el, msg) {
    el.classList.add('system-msg', 'job-breadcrumb');
    const actId = msg.metadata?.job_id;
    if (actId && !jobsData.some(a => a.id === actId)) {
        el.style.display = 'none';
        el.dataset.hiddenReason = 'no-job-data';
    }
    if (actId) {
        el.innerHTML = `<span class="job-breadcrumb-link" onclick="openJobFromBreadcrumb(${actId})" title="Open job">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" style="vertical-align:-2px;margin-right:4px;opacity:0.6"><rect x="2" y="1" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="9" y="8" width="5" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
            New job: <em>${window.escapeHtml(msg.text.replace('Job created: ', ''))}</em></span>`;
    } else {
        el.innerHTML = `<span class="msg-text">${window.escapeHtml(msg.text)}</span>`;
    }
};

// ---------------------------------------------------------------------------
// Job breadcrumb helpers
// ---------------------------------------------------------------------------

function _unhideResolvedBreadcrumbs() {
    // After jobsData is populated (e.g. from WS 'jobs' event), unhide any
    // breadcrumbs that were hidden during history replay because jobsData
    // was still empty when the job_created message was rendered.
    // Only targets elements with data-hidden-reason="no-job-data" to avoid
    // unhiding off-channel breadcrumbs hidden by the channel filter.
    const validIds = new Set(jobsData.map(a => a.id));
    document.querySelectorAll('.job-breadcrumb[data-hidden-reason="no-job-data"]').forEach(el => {
        const link = el.querySelector('.job-breadcrumb-link');
        if (!link) return;
        const onclick = link.getAttribute('onclick') || '';
        const m = onclick.match(/openJobFromBreadcrumb\((\d+)\)/);
        if (m && validIds.has(Number(m[1]))) {
            el.style.display = '';
            delete el.dataset.hiddenReason;
        }
    });
}

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

function _repairJobGroup(group) {
    if (!group || !group.classList.contains('job-group')) return;
    const listEl = group.querySelector('.job-group-list');
    const remaining = listEl ? listEl.querySelectorAll('.job-breadcrumb') : [];
    if (remaining.length === 0) {
        group.remove();
    } else if (remaining.length === 1) {
        // Unwrap single breadcrumb out of the group
        group.replaceWith(remaining[0]);
    } else {
        // Update the count text
        const summary = group.querySelector('.job-group-summary');
        if (summary) {
            summary.innerHTML = summary.innerHTML.replace(/\d+ jobs were started/, `${remaining.length} jobs were started`);
        }
    }
}

// ---------------------------------------------------------------------------
// Jobs panel grip (resize)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Jobs input setup
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Job reply target helpers
// ---------------------------------------------------------------------------

function getJobRecipientOptions() {
    const opts = [];
    for (const [name, cfg] of Object.entries(window.agentConfig)) {
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
        if (!sender || sender.toLowerCase() === window.username.toLowerCase()) continue;
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

// ---------------------------------------------------------------------------
// Jobs panel toggle and view switching
// ---------------------------------------------------------------------------

function toggleJobsPanel() {
    window._preserveScroll(() => {
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

// ---------------------------------------------------------------------------
// Job sorting and ordering helpers
// ---------------------------------------------------------------------------

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

function _beginJobReorderMute({ ids = [], channel = window.activeChannel, status = null, durationMs = 650 } = {}) {
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
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
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
                headers: { 'X-Session-Token': window.SESSION_TOKEN },
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

// ---------------------------------------------------------------------------
// FLIP animation helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Render jobs list
// ---------------------------------------------------------------------------

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
                    headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
                    body: JSON.stringify({ status: group.key }),
                });
                const act = jobsData.find(a => String(a.id) === String(draggedId));
                _beginJobReorderMute({ ids: [draggedId], channel: window.activeChannel, status: group.key });
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
                    <span class="job-title">${window.escapeHtml(a.title)}</span>
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

// ---------------------------------------------------------------------------
// Job conversation view
// ---------------------------------------------------------------------------

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
        briefEl.innerHTML = `<div class="job-brief-text">${window.renderMarkdown(job.body)}</div>`;
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
            headers: { 'X-Session-Token': window.SESSION_TOKEN }
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

// ---------------------------------------------------------------------------
// Job message delete
// ---------------------------------------------------------------------------

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
            headers: { 'X-Session-Token': window.SESSION_TOKEN },
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

// ---------------------------------------------------------------------------
// Append job message to conversation
// ---------------------------------------------------------------------------

function appendJobMessage(msg) {
    const container = document.getElementById('jobs-conv-messages');
    if (!container || msg?.deleted) return;

    const div = document.createElement('div');
    div.className = 'job-msg' + (msg.type === 'suggestion' ? ' job-suggestion' : '');
    div.dataset.msgId = String(msg.id ?? '');
    const senderColor = window.getColor(msg.sender);
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
                <span class="job-msg-sender" style="color: ${senderColor}">${window.escapeHtml(msg.sender)}</span>
                <span class="job-msg-time">${msg.time || ''}</span>
                ${deleteActionHtml}
            </div>
            <div class="job-msg-text">${window.renderMarkdown(msg.text)}</div>
            <div class="suggestion-actions">${resolved
                ? `<span class="suggestion-resolved">${window.escapeHtml(resolved)}</span>`
                : `<button class="suggestion-accept" onclick="acceptSuggestion(${activeJobId}, ${msg.id})">Accept</button><button class="suggestion-dismiss" onclick="dismissSuggestion(${activeJobId}, ${msg.id})">Dismiss</button>`
            }</div>
        `;
    } else {
        let attHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
            attHtml = '<div class="job-msg-attachments">';
            for (const att of msg.attachments) {
                attHtml += `<img src="${window.escapeHtml(att.url)}" alt="${window.escapeHtml(att.name || '')}" onclick="openImageModal('${window.escapeHtml(att.url)}')">`;
            }
            attHtml += '</div>';
        }
        div.innerHTML = `
            <div class="job-msg-header">
                <span class="job-msg-sender" style="color: ${senderColor}">${window.escapeHtml(msg.sender)}</span>
                <span class="job-msg-time">${msg.time || ''}</span>
                ${deleteActionHtml}
            </div>
            ${msg.text ? `<div class="job-msg-text">${window.renderMarkdown(msg.text)}</div>` : ''}
            ${attHtml}
        `;
    }
    container.appendChild(div);
}

// ---------------------------------------------------------------------------
// Job attachments
// ---------------------------------------------------------------------------

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
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
            body: JSON.stringify({
                text: outboundText,
                sender: window.username,
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
        const resp = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': window.SESSION_TOKEN }, body: form });
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
        wrap.innerHTML = `<img src="${att.url}" alt="${window.escapeHtml(att.name)}"><button class="remove-btn" onclick="removeJobAttachment(${i})">x</button>`;
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

// ---------------------------------------------------------------------------
// Job title editing and status toggles
// ---------------------------------------------------------------------------

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
                    headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
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
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
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

// ---------------------------------------------------------------------------
// Create job
// ---------------------------------------------------------------------------

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
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
            body: JSON.stringify({
                title,
                body: jobBody,
                type: 'job',
                channel: window.activeChannel,
                created_by: window.username,
                assignee: window._lastMentionedAgent || '',
            }),
        });
        form.remove();
    } catch (e) {
        console.error('Failed to create job:', e);
    }
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function jobsBack() {
    showJobsListView(() => {
        renderJobsList();
    });
}

// ---------------------------------------------------------------------------
// Job WS event handler
// ---------------------------------------------------------------------------

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
        const isSelfMessage = sender.toLowerCase() === window.username.toLowerCase();
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
        if (window.soundEnabled && !document.hasFocus() && msgType === 'chat' && !isSelfMessage && sender) {
            window.playNotificationSound(sender);
        }

        // If we're viewing this job, append the message. Otherwise count unread.
        if (isViewingThis) {
            appendJobMessage(data.message);
            const container = document.getElementById('jobs-conv-messages');
            if (container) container.scrollTop = container.scrollHeight;
            markJobRead(data.job_id);
        } else if (!isSelfMessage) {
            jobUnread[data.job_id] = (jobUnread[data.job_id] || 0) + 1;
            // Play soft pluck for chat messages in other job threads
            if (window.soundEnabled && document.hasFocus() && msgType === 'chat' && window.playCrossChannelSound) {
                window.playCrossChannelSound();
            }
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
        // Remove breadcrumb from timeline and fix stale group wrappers
        document.querySelectorAll('.job-breadcrumb').forEach(el => {
            const link = el.querySelector('.job-breadcrumb-link');
            if (link) {
                const onclick = link.getAttribute('onclick') || '';
                if (onclick.includes(`openJobFromBreadcrumb(${data.id})`)) {
                    const group = el.closest('.job-group');
                    el.remove();
                    if (group) _repairJobGroup(group);
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

// ---------------------------------------------------------------------------
// Convert-to-Job Lightbox
// ---------------------------------------------------------------------------

function showConvertToJobModal(msgId) {
    const msgEl = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!msgEl) return;
    const rawText = msgEl.dataset.rawText || '';
    const msgSender = msgEl.querySelector('.msg-sender')?.textContent || window.username;

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
    previewEl.innerHTML = window.renderMarkdown(rawText.substring(0, 500));

    // Populate agent picker — only agents, not humans
    const selectEl = modal.querySelector('.convert-job-agent');
    selectEl.innerHTML = '';
    const agents = Object.keys(window.agentConfig);
    const defaultAgent = agents.includes(msgSender) ? msgSender : agents[0];
    for (const name of agents) {
        const opt = document.createElement('option');
        opt.value = name;
        const cfg = window.agentConfig[name];
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

    // Show status message while agent drafts the job card
    const statusMsg = {
        id: Date.now(),
        sender: 'system',
        type: 'system',
        text: `Asking @${agent} to create a job card\u2026`,
        channel: window.activeChannel,
        time: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    };
    if (window.appendMessage) window.appendMessage(statusMsg);

    const instruction = `${window.username}: Please read the following message and use chat_propose_job to propose it as a job. Write a concise title (max 80 chars) and a clear body (max 500 chars) summarizing the task:\n\n---\n${rawText.substring(0, 800)}\n---`;

    try {
        await fetch('/api/trigger-agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
            body: JSON.stringify({
                agent,
                message: instruction,
                channel: window.activeChannel,
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

// ---------------------------------------------------------------------------
// Delete-Job Lightbox
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Proposal accept/dismiss/request-changes
// ---------------------------------------------------------------------------

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
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
            body: JSON.stringify({
                title,
                body: body || '',
                type: 'job',
                channel: window.activeChannel,
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
            headers: { 'X-Session-Token': window.SESSION_TOKEN },
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
            headers: { 'X-Session-Token': window.SESSION_TOKEN },
        });
        // Wait briefly for WS edit event to re-render the message as chat
        setTimeout(() => window.startReply(msgId), 200);
    } catch (e) {
        console.error('Failed to request changes on proposal:', e);
    }
}

// ---------------------------------------------------------------------------
// Breadcrumb navigation
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Suggestion accept/dismiss in jobs
// ---------------------------------------------------------------------------

async function acceptSuggestion(jobId, msgIndex) {
    try {
        await fetch(`/api/jobs/${jobId}/messages/${msgIndex}/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
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
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': window.SESSION_TOKEN },
            body: JSON.stringify({ resolution: 'dismissed' }),
        });
        await loadJobMessages(jobId);
    } catch (e) {
        console.error('Failed to dismiss suggestion:', e);
    }
}

// ---------------------------------------------------------------------------
// Permanent delete for archived jobs
// ---------------------------------------------------------------------------

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
            headers: { 'X-Session-Token': window.SESSION_TOKEN },
        });
        jobsData = jobsData.filter(a => a.id !== jobId);
        renderJobsList();
    } catch (e) {
        console.error('Failed to delete job:', e);
    }
}

// ---------------------------------------------------------------------------
// Archive trash helpers
// ---------------------------------------------------------------------------

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
                headers: { 'X-Session-Token': window.SESSION_TOKEN },
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

// ---------------------------------------------------------------------------
// Unread badge helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Job @mention autocomplete
// ---------------------------------------------------------------------------

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

    const candidates = window.getMentionCandidates();
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
        row.innerHTML = `<span class="mention-dot" style="background: ${item.color}"></span><span class="mention-name">${window.escapeHtml(item.label)}</span>`;
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
    window._lastMentionedAgent = name; // track for future job creation
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

// ---------------------------------------------------------------------------
// Hub subscriptions -- handle job WebSocket events
// ---------------------------------------------------------------------------

Hub.on('jobs', function (event) {
    jobsData = event.data || [];
    syncJobUnreadCache();
    updateJobsBadge();
    renderJobsList();
    // Unhide breadcrumbs that were hidden because jobsData was empty during replay
    _unhideResolvedBreadcrumbs();
});

Hub.on('job', function (event) {
    handleJobEvent(event.action, event.data);
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function _jobsInit() {
    setupJobsGrip();
    setupJobsInput();
    setupJobMentions();
}

// ---------------------------------------------------------------------------
// Window exports (for inline onclick handlers in generated HTML)
// ---------------------------------------------------------------------------

window.toggleJobsPanel = toggleJobsPanel;
window.showCreateJob = showCreateJob;
window.jobsBack = jobsBack;
window.toggleJobStatus = toggleJobStatus;
window.openJobConversation = openJobConversation;
window.openJobFromBreadcrumb = openJobFromBreadcrumb;
window.sendJobMessage = sendJobMessage;
window.cycleJobReplyTarget = cycleJobReplyTarget;
window.clearJobReplyTarget = clearJobReplyTarget;
window.startDeleteJobMessage = startDeleteJobMessage;
window.confirmDeleteJobMessage = confirmDeleteJobMessage;
window.cancelDeleteJobMessage = cancelDeleteJobMessage;
window.startJobFromMessage = startJobFromMessage;
window.acceptProposal = acceptProposal;
window.dismissProposal = dismissProposal;
window.requestChangesProposal = requestChangesProposal;
window.acceptSuggestion = acceptSuggestion;
window.dismissSuggestion = dismissSuggestion;
window.deleteJobPermanent = deleteJobPermanent;
window.showDeleteJobModal = showDeleteJobModal;
window.closeDeleteJobModal = closeDeleteJobModal;
window.submitCreateJob = submitCreateJob;
window.uploadJobImage = uploadJobImage;
window.removeJobAttachment = removeJobAttachment;
window.reorderJobWithinGroup = reorderJobWithinGroup;
window._collapseJobBreadcrumbs = _collapseJobBreadcrumbs;
window.syncJobUnreadCache = syncJobUnreadCache;
window.updateJobsBadge = updateJobsBadge;
window.renderJobsList = renderJobsList;
window.markJobRead = markJobRead;
window.handleJobEvent = handleJobEvent;

window.Jobs = { init: _jobsInit };
