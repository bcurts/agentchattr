"""Opt-in headless test for unknown-status rendering; no server or agent processes."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
source = (root / 'static' / 'chat.js').read_text(encoding='utf-8')
update = source[source.index('function updateStatus(data) {'):source.index('function updateTyping(')]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
        executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        args=['--disable-gpu'])
    page = browser.new_page()
    page.set_content('<div id="status-c2" class="status-pill"><span class="status-unknown" hidden>ⓘ</span></div>')
    page.evaluate('window.soundEnabled = false; window.colorOverrides = {};')
    page.add_script_tag(content=update)
    page.evaluate("updateStatus({c2: {available: true, busy: true, activity_known: false}})")
    assert page.locator('.status-unknown').is_visible()
    assert not page.locator('#status-c2').evaluate("e => e.classList.contains('working')")
    page.evaluate("updateStatus({c2: {available: true, busy: true, activity_known: true}})")
    assert page.locator('.status-unknown').is_hidden()
    assert page.locator('#status-c2').evaluate("e => e.classList.contains('working')")
    page.evaluate("updateStatus({c2: {available: false, busy: false, activity_known: false}})")
    assert page.locator('.status-unknown').is_hidden()
    browser.close()
print('PASS: unknown, known working, and offline status rendering')
