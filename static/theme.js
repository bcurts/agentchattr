/**
 * theme.js -- lightweight day/night theme switching.
 */

(function () {
    'use strict';

    const STORAGE_KEY = 'agentchattr-theme';
    const DEFAULT_THEME = 'light';

    function normalizeTheme(value) {
        return value === 'dark' ? 'dark' : DEFAULT_THEME;
    }

    function getTheme() {
        return normalizeTheme(localStorage.getItem(STORAGE_KEY));
    }

    function applyTheme(theme) {
        const nextTheme = normalizeTheme(theme);
        document.documentElement.dataset.theme = nextTheme;
        document.documentElement.style.colorScheme = nextTheme;
        const selector = document.getElementById('setting-theme');
        if (selector && selector.value !== nextTheme) selector.value = nextTheme;
    }

    function setTheme(theme) {
        const nextTheme = normalizeTheme(theme);
        localStorage.setItem(STORAGE_KEY, nextTheme);
        applyTheme(nextTheme);
    }

    function bindThemeSelector() {
        const selector = document.getElementById('setting-theme');
        if (!selector) return;
        selector.value = getTheme();
        selector.addEventListener('change', () => setTheme(selector.value));
    }

    window.getTheme = getTheme;
    window.setTheme = setTheme;
    window.applyTheme = applyTheme;

    applyTheme(getTheme());
    document.addEventListener('DOMContentLoaded', bindThemeSelector);
})();
