'use strict';

// The sidebar and composer share one workspace; mobile uses a dismissible drawer.
function toggleConversationSidebar() {
    const mobile = window.matchMedia('(max-width: 760px)').matches;
    if (mobile) document.body.classList.toggle('sidebar-open');
    else {
        document.body.classList.toggle('sidebar-collapsed');
        localStorage.setItem('agentchattr-sidebar-collapsed', document.body.classList.contains('sidebar-collapsed') ? '1' : '0');
    }
    updateSidebarAccessibility();
    if (typeof repositionScrollAnchor === 'function') requestAnimationFrame(repositionScrollAnchor);
}
function closeConversationSidebar() {
    if (!window.matchMedia('(max-width: 760px)').matches) return;
    document.body.classList.remove('sidebar-open');
    updateSidebarAccessibility();
}
function updateSidebarAccessibility() {
    const mobile = window.matchMedia('(max-width: 760px)').matches;
    const open = mobile ? document.body.classList.contains('sidebar-open') : !document.body.classList.contains('sidebar-collapsed');
    document.getElementById('sidebar-toggle')?.setAttribute('aria-expanded', String(open));
    const sidebar = document.getElementById('channel-sidebar');
    if (sidebar) sidebar.inert = !open;
}
function refreshConversationEmptyState() {
    const empty = document.getElementById('conversation-empty');
    const messages = document.getElementById('messages');
    const loading = document.getElementById('loading-indicator');
    if (!empty || !messages) return;
    const hasMessages = Array.from(messages.children).some(el => el.classList.contains('message') && el.style.display !== 'none');
    empty.classList.toggle('hidden', hasMessages || !loading?.classList.contains('hidden'));
}
document.addEventListener('DOMContentLoaded', () => {
    if (localStorage.getItem('agentchattr-sidebar-collapsed') === '1') document.body.classList.add('sidebar-collapsed');
    updateSidebarAccessibility();
    window.matchMedia('(max-width: 760px)').addEventListener('change', () => {
        document.body.classList.remove('sidebar-open');
        updateSidebarAccessibility();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
            closeConversationSidebar();
            document.getElementById('sidebar-toggle')?.focus();
        }
    });
    const observer = new MutationObserver(refreshConversationEmptyState);
    observer.observe(document.getElementById('messages'), { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'data-channel'] });
    observer.observe(document.getElementById('loading-indicator'), { attributes: true, attributeFilter: ['class'] });
    refreshConversationEmptyState();
});
