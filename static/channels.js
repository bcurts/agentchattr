'use strict';

// Channel names are the stable routing IDs; labels are only presentation.
const _channelScrollMsg = {};
const SIDEBAR_WIDTH_KEY = 'agentchattr-channel-sidebar-w';
let _channelForm = null;

function _channelLabel(name) { return name === 'general' ? t('conversation.default') : name; }
function _getTopVisibleMsgId() {
    const scroll = document.getElementById('timeline');
    const container = document.getElementById('messages');
    if (!scroll || !container) return null;
    const rect = scroll.getBoundingClientRect();
    for (const el of container.children) {
        if (el.style.display !== 'none' && el.dataset.id && el.getBoundingClientRect().bottom > rect.top) return el.dataset.id;
    }
    return null;
}
function _channelSend(message) {
    try {
        if (!window.ws || window.ws.readyState !== 1) return false;
        window.ws.send(JSON.stringify(message));
        return true;
    } catch (_) { return false; }
}
function _channelError(text) {
    if (!_channelForm) return;
    _channelForm.error.textContent = text;
    _channelForm.input.setAttribute('aria-invalid', text ? 'true' : 'false');
}
function _validateChannelName(value, oldName) {
    const name = value.trim().toLowerCase();
    if (!name) return { error: t('conversation.nameRequired') };
    if (Array.from(name).length > 60) return { error: t('conversation.nameTooLong') };
    if (!/^[\p{L}\p{N}][\p{L}\p{N} _-]*$/u.test(name)) return { error: t('conversation.nameInvalid') };
    if (name !== oldName && window.channelList.some(item => item.trim().toLowerCase() === name)) return { error: t('conversation.nameDuplicate') };
    return { name };
}
function _closeChannelForm() {
    if (!_channelForm) return;
    _channelForm.wrapper.remove();
    _channelForm = null;
    renderChannelSidebar();
}
function _openChannelForm(oldName) {
    if (oldName === 'general') return;
    _closeChannelForm();
    const list = document.getElementById('channel-sidebar-list');
    if (!list) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'channel-inline-create';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = oldName || '';
    input.placeholder = t('conversation.namePlaceholder');
    input.setAttribute('aria-label', oldName ? t('conversation.rename') : t('conversation.new'));
    wrapper.appendChild(input);
    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = 'confirm-btn';
    confirm.textContent = '✓';
    confirm.title = oldName ? t('common.confirm') : t('common.create');
    confirm.setAttribute('aria-label', confirm.title);
    wrapper.appendChild(confirm);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'cancel-btn';
    cancel.textContent = '✕';
    cancel.title = t('common.cancel');
    cancel.setAttribute('aria-label', cancel.title);
    cancel.onclick = _closeChannelForm;
    wrapper.appendChild(cancel);
    const error = document.createElement('div');
    error.className = 'channel-inline-error';
    error.setAttribute('aria-live', 'polite');
    wrapper.appendChild(error);
    _channelForm = { wrapper, input, error, oldName };
    confirm.onclick = () => {
        const result = _validateChannelName(input.value, oldName);
        if (result.error) { _channelError(result.error); input.focus(); return; }
        if (!oldName && window.channelList.length >= 100) { _channelError(t('conversation.limit')); return; }
        if (oldName && result.name === oldName) { _closeChannelForm(); return; }
        const message = oldName ? { type: 'channel_rename', old_name: oldName, new_name: result.name } : { type: 'channel_create', name: result.name };
        if (!_channelSend(message)) { _channelError(t('conversation.connectionLost')); input.focus(); return; }
        if (!oldName) window._setPendingChannelSwitch(result.name);
        _closeChannelForm();
    };
    input.addEventListener('keydown', e => {
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === 'Enter') { e.preventDefault(); confirm.click(); }
        if (e.key === 'Escape') { e.preventDefault(); _closeChannelForm(); }
    });
    input.addEventListener('input', () => _channelError(''));
    renderChannelSidebar();
    if (oldName) input.select(); else input.focus();
}
function showChannelCreateDialog() { _openChannelForm(null); }
function showChannelRenameDialog(name) { _openChannelForm(name); }

function renderChannelSidebar() {
    const list = document.getElementById('channel-sidebar-list');
    if (!list) return;
    // Do not detach an active editor during broadcasts: doing so cancels IME composition.
    const form = _channelForm;
    if (form && form.wrapper.isConnected) {
        const title = document.getElementById('conversation-title');
        if (title) { title.textContent = _channelLabel(window.activeChannel); title.title = _channelLabel(window.activeChannel); }
        return;
    }
    list.replaceChildren();
    const search = document.getElementById('conversation-search');
    const query = search ? search.value.trim().toLowerCase() : '';
    const names = window.channelList.filter(name => !query || name.toLowerCase().includes(query) || _channelLabel(name).toLowerCase().includes(query));
    const count = document.getElementById('conversation-count');
    if (count) count.textContent = t('conversation.count', { count: names.length });
    const title = document.getElementById('conversation-title');
    if (title) { title.textContent = _channelLabel(window.activeChannel); title.title = _channelLabel(window.activeChannel); }
    for (const name of names) {
        const row = document.createElement('div');
        row.className = 'channel-sidebar-row' + (name === window.activeChannel ? ' active' : '');
        row.dataset.channel = name;
        const select = document.createElement('button');
        select.type = 'button';
        select.className = 'channel-sidebar-select';
        select.title = _channelLabel(name);
        if (name === window.activeChannel) select.setAttribute('aria-current', 'page');
        const label = document.createElement('span');
        label.className = 'channel-sidebar-row-label';
        label.textContent = _channelLabel(name);
        select.appendChild(label);
        const unread = window.channelUnread[name] || 0;
        if (unread > 0 && name !== window.activeChannel) {
            const dot = document.createElement('span');
            dot.className = 'channel-sidebar-row-unread';
            dot.textContent = unread > 99 ? '99+' : unread;
            select.appendChild(dot);
        }
        select.onclick = () => switchChannel(name);
        row.appendChild(select);
        if (name !== 'general') {
            const actions = document.createElement('span');
            actions.className = 'channel-sidebar-row-actions';
            const edit = document.createElement('button');
            edit.type = 'button';
            edit.title = t('channel.rename.title');
            edit.setAttribute('aria-label', t('conversation.renameNamed', { name }));
            edit.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M11.5 2.5l2 2L5 13H3v-2L11.5 2.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
            edit.onclick = () => _openChannelForm(name);
            const del = document.createElement('button');
            del.type = 'button';
            del.title = t('channel.delete.title');
            del.setAttribute('aria-label', t('conversation.deleteNamed', { name }));
            del.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4v8.5h6V4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            del.onclick = () => _sidebarConfirmDelete(name, row, label);
            actions.append(edit, del);
            row.appendChild(actions);
        }
        list.appendChild(row);
        if (form && form.oldName === name) { row.hidden = true; list.appendChild(form.wrapper); }
    }
    if (!names.length) {
        const empty = document.createElement('div');
        empty.className = 'conversation-empty';
        empty.textContent = t('conversation.empty');
        list.appendChild(empty);
    }
    if (form && !form.wrapper.isConnected) list.appendChild(form.wrapper);
    const add = document.getElementById('channel-sidebar-add');
    if (add) add.classList.toggle('disabled', window.channelList.length >= 100);
}
function renderChannelTabs() {
    const container = document.getElementById('channel-tabs');
    if (container) {
        container.replaceChildren();
        for (const name of window.channelList) {
            const tab = document.createElement('button');
            tab.type = 'button';
            tab.className = 'channel-tab' + (name === window.activeChannel ? ' active' : '');
            tab.dataset.channel = name;
            tab.textContent = _channelLabel(name);
            tab.onclick = () => switchChannel(name);
            container.appendChild(tab);
        }
        const add = document.getElementById('channel-add-btn');
        if (add) add.classList.toggle('disabled', window.channelList.length >= 100);
    }
    renderChannelSidebar();
}
function _sidebarConfirmDelete(name, row, label) {
    if (name === 'general' || row.classList.contains('confirm-delete')) return;
    row.classList.add('confirm-delete');
    const actions = row.querySelector('.channel-sidebar-row-actions');
    const select = row.querySelector('.channel-sidebar-select');
    const original = label.textContent;
    label.textContent = t('channel.deleteConfirm', { name });
    select.disabled = true;
    actions.hidden = true;
    const bar = document.createElement('span');
    bar.className = 'channel-sidebar-row-actions';
    bar.style.display = 'flex';
    const yes = document.createElement('button');
    yes.type = 'button'; yes.textContent = '✓'; yes.title = t('common.confirm'); yes.setAttribute('aria-label', yes.title);
    const no = document.createElement('button');
    no.type = 'button'; no.textContent = '✕'; no.title = t('common.cancel'); no.setAttribute('aria-label', no.title);
    const error = document.createElement('span');
    error.className = 'channel-inline-error'; error.setAttribute('aria-live', 'polite');
    no.onclick = () => { row.classList.remove('confirm-delete'); label.textContent = original; select.disabled = false; actions.hidden = false; bar.remove(); error.remove(); };
    yes.onclick = () => {
        if (!_channelSend({ type: 'channel_delete', name })) { error.textContent = t('conversation.connectionLost'); return; }
        if (window.activeChannel === name) switchChannel('general');
        else no.click();
    };
    bar.append(yes, no); row.append(bar, error); yes.focus();
}
function deleteChannel(name) {
    const list = document.getElementById('channel-sidebar-list');
    const row = list && Array.from(list.querySelectorAll('.channel-sidebar-row')).find(el => el.dataset.channel === name);
    if (row) _sidebarConfirmDelete(name, row, row.querySelector('.channel-sidebar-row-label'));
}
function switchChannel(name) {
    const oldName = window.activeChannel;
    if (name !== oldName) {
        const topId = _getTopVisibleMsgId();
        if (topId) _channelScrollMsg[oldName] = topId;
        window._setActiveChannel(name);
        window.channelUnread[name] = 0;
        localStorage.setItem('agentchattr-channel', name);
        filterMessagesByChannel();
        renderChannelTabs();
        Store.set('activeChannel', name);
        const savedId = _channelScrollMsg[name];
        const el = savedId && Array.from(document.querySelectorAll('.message[data-id]')).find(item => item.dataset.id === savedId);
        if (el) el.scrollIntoView({ block: 'start' }); else window.scrollToBottom();
        if (typeof window.onConversationSwitch === 'function') window.onConversationSwitch(oldName, name);
    }
    if (typeof window.closeConversationSidebar === 'function') window.closeConversationSidebar();
    document.getElementById('input')?.focus();
}
function filterMessagesByChannel() {
    const container = document.getElementById('messages');
    if (!container) return;
    for (const el of container.children) el.style.display = (el.dataset.channel || 'general') === window.activeChannel ? '' : 'none';
    if (typeof window.dayFloatRefresh === 'function') window.dayFloatRefresh();
}
function setChannelSidebarMode(_mode, persist = true) {
    const sidebar = document.getElementById('channel-sidebar');
    if (!sidebar) return;
    document.body.classList.add('channels-in-sidebar');
    sidebar.classList.remove('hidden');
    const top = document.getElementById('channel-sidebar-top');
    const support = document.querySelector('.channel-support');
    const update = document.getElementById('update-pill');
    if (top) { if (update && update.parentElement !== top) top.appendChild(update); if (support && support.parentElement !== top) top.appendChild(support); }
    if (persist) localStorage.setItem('agentchattr-channel-sidebar-mode', 'sidebar');
    const setting = document.getElementById('setting-channel-sidebar');
    if (setting) setting.value = 'sidebar';
    renderChannelSidebar();
}
function setupChannelSidebarGrip() {
    const grip = document.getElementById('channel-sidebar-grip');
    const panel = document.getElementById('channel-sidebar');
    if (!grip || !panel) return;
    let dragging = false, startX = 0, startWidth = 0;
    grip.addEventListener('mousedown', e => { e.preventDefault(); dragging = true; startX = e.clientX; startWidth = panel.offsetWidth; grip.classList.add('dragging'); panel.style.transition = 'none'; document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'; });
    document.addEventListener('mousemove', e => { if (!dragging) return; const width = Math.min(Math.max(startWidth + e.clientX - startX, 200), 360); panel.style.setProperty('--channel-sidebar-w', width + 'px'); panel.style.width = width + 'px'; });
    document.addEventListener('mouseup', () => { if (!dragging) return; dragging = false; grip.classList.remove('dragging'); panel.style.transition = ''; document.body.style.cursor = ''; document.body.style.userSelect = ''; localStorage.setItem(SIDEBAR_WIDTH_KEY, panel.offsetWidth); });
}
function _syncClearChatWidth() {
    const clear = document.getElementById('clear-chat-btn');
    const group = document.querySelector('#input-row .send-group');
    if (clear && group && group.offsetWidth > 0) clear.style.width = group.offsetWidth + 'px';
}
function _channelsInit() {
    setupChannelSidebarGrip();
    const panel = document.getElementById('channel-sidebar');
    const saved = parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY) || '', 10);
    if (panel) { const width = Number.isFinite(saved) ? Math.min(Math.max(saved, 200), 360) : 264; panel.style.setProperty('--channel-sidebar-w', width + 'px'); panel.style.width = width + 'px'; }
    setChannelSidebarMode('sidebar', false);
    const search = document.getElementById('conversation-search');
    if (search) search.addEventListener('input', renderChannelSidebar);
    const setting = document.getElementById('setting-channel-sidebar');
    if (setting) setting.addEventListener('change', () => setChannelSidebarMode('sidebar'));
    requestAnimationFrame(_syncClearChatWidth);
    window.addEventListener('resize', _syncClearChatWidth);
}
window.showChannelCreateDialog = showChannelCreateDialog;
window.switchChannel = switchChannel;
window.filterMessagesByChannel = filterMessagesByChannel;
window.renderChannelTabs = renderChannelTabs;
window.deleteChannel = deleteChannel;
window.showChannelRenameDialog = showChannelRenameDialog;
window.renderChannelSidebar = renderChannelSidebar;
window.setChannelSidebarMode = setChannelSidebarMode;
window.Channels = { init: _channelsInit };
