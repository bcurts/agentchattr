// User-visible errors remain until dismissed and are inserted as text, never HTML.
window.showActionError = function(action, error) {
    const banner = document.getElementById('action-error');
    document.getElementById('action-error-title').textContent = '⚠ ' + action;
    document.getElementById('action-error-detail').textContent = 'Details: ' + (error.message || String(error));
    document.getElementById('action-error-path').textContent = error.path ? 'File: ' + error.path : '';
    banner.hidden = false;
};
window.requireOk = async function(response) {
    if (response.ok) return response;
    let data = {};
    try { data = await response.json(); } catch (_) { /* status remains useful */ }
    const error = new Error(data.error || data.detail || ('HTTP ' + response.status + ' ' + response.statusText));
    error.path = data.path;
    throw error;
};
