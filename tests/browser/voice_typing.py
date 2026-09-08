"""Voice editing/lifecycle regression checks in the real browser UI.

Run an isolated Agentchattr server, install Playwright/Chromium in a test env,
then run: python tests/browser/voice_typing.py http://127.0.0.1:PORT
This clicks/types in the app and sends one uniquely named test message.
SpeechRecognition is replaced at the browser API boundary: these checks do
not test microphone hardware, recognition accuracy, or the speech service.
"""
import argparse
import uuid

from playwright.sync_api import expect, sync_playwright

SPEECH_API = r"""
localStorage.setItem('help_seen', '1');
window.speech = {runs: [], autoStart: true, autoEnd: true, failStart: false};
class FakeSpeechRecognition {
    constructor() {
        this.running = false;
        this.starts = 0;
        this.aborts = 0;
        speech.runs.push(this);
    }
    emit(type, data = {}) { this['on' + type]?.call(this, {type, ...data}); }
    start() {
        if (speech.failStart || this.running) throw new Error('start failed');
        this.running = true;
        this.starts++;
        if (speech.autoStart) queueMicrotask(() => this.emit('start'));
    }
    end() { this.running = false; this.emit('end'); }
    abort() {
        this.aborts++;
        this.running = false;
        if (speech.autoEnd) queueMicrotask(() => {
            this.emit('error', {error: 'aborted'});
            this.emit('end');
        });
    }
    stop() {
        this.running = false;
        if (speech.autoEnd) queueMicrotask(() => this.emit('end'));
    }
    result(text, final = true) {
        const result = [{transcript: text}];
        result.isFinal = final;
        this.emit('result', {resultIndex: 0, results: [result]});
    }
}
window.SpeechRecognition = FakeSpeechRecognition;
"""


def speech(page, text, index=-1, final=True):
    page.evaluate('([text, index, final]) => speech.runs.at(index).result(text, final)',
                  [text, index, final])


def manual_edit(page):
    page.locator('#input').fill('Original')
    page.locator('#mic').click()
    speech(page, 'first')
    page.locator('#input').fill('Edited')
    speech(page, 'first and more', index=0)
    expect(page.locator('#input')).to_have_value('Edited')
    page.wait_for_function('speech.runs.length === 2')
    speech(page, 'fresh words')
    expect(page.locator('#input')).to_have_value('Edited fresh words')


def automatic_restart(page):
    page.locator('#input').fill('Original')
    page.locator('#mic').click()
    speech(page, 'first')
    page.evaluate("speech.runs.at(-1).emit('error', {error: 'no-speech'}); speech.runs.at(-1).end()")
    speech(page, 'second')
    expect(page.locator('#input')).to_have_value('Original first second')


def interim_results(page):
    page.locator('#input').fill('Notes:\n')
    page.locator('#mic').click()
    speech(page, 'brown', final=False)
    speech(page, 'brown fox', final=False)
    speech(page, 'brown fox')
    expect(page.locator('#input')).to_have_value('Notes:\nbrown fox')
    page.evaluate("""() => {
        const first = [{transcript: 'brown fox'}]; first.isFinal = true;
        const second = [{transcript: ' jumps'}]; second.isFinal = false;
        speech.runs.at(-1).emit('result', {resultIndex: 1, results: [first, second]});
    }""")
    expect(page.locator('#input')).to_have_value('Notes:\nbrown fox jumps')
    expect(page.locator('.send-group')).not_to_have_class('send-group inactive')


def stopped_result(page):
    page.locator('#mic').click()
    speech(page, 'before stop')
    page.locator('#mic').click()
    page.locator('#input').fill('Edited after stop')
    speech(page, 'late unwanted words', index=0)
    expect(page.locator('#input')).to_have_value('Edited after stop')


def stop_during_startup(page):
    page.evaluate('speech.autoStart = false; speech.autoEnd = false')
    page.locator('#mic').click()
    page.locator('#mic').click()
    assert page.evaluate('speech.runs.length') == 1
    page.evaluate("speech.runs[0].emit('start'); speech.runs[0].result('late startup'); speech.runs[0].end()")
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#input')).to_have_value('')


def send_while_listening(page):
    marker = 'Voice test ' + uuid.uuid4().hex[:8]
    page.locator('#input').fill(marker)
    page.locator('#mic').click()
    speech(page, 'spoken')
    page.locator('#send').click()
    speech(page, 'spoken and late', index=0)
    expect(page.locator('#input')).to_have_value('')
    page.wait_for_function('speech.runs.length === 2')
    speech(page, 'next message')
    expect(page.locator('#input')).to_have_value('next message')
    expect(page.locator('.msg-text').filter(has_text=marker + ' spoken')).to_have_count(1)


def stale_callbacks(page):
    page.locator('#mic').click()
    page.evaluate('speech.autoEnd = false')
    page.locator('#mic').click()
    page.locator('#mic').click()
    page.evaluate("""speech.runs[0].emit('error', {error: 'not-allowed'});
        speech.runs[0].end(); speech.runs[0].result('old');""")
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'true')
    speech(page, 'new recording')
    expect(page.locator('#input')).to_have_value('new recording')


def denied_permission(page):
    dialogs = []
    page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    page.locator('#input').fill('Keep this')
    page.locator('#mic').click()
    page.evaluate("speech.runs.at(-1).emit('error', {error: 'not-allowed'})")
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#input')).to_have_value('Keep this')
    assert len(dialogs) == 1


def start_failure(page):
    page.evaluate('speech.failStart = true')
    page.locator('#mic').click()
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'false')
    page.evaluate('speech.failStart = false')
    page.locator('#mic').click()
    speech(page, 'retry works')
    expect(page.locator('#input')).to_have_value('retry works')


def rapid_typing(page):
    page.locator('#mic').click()
    speech(page, 'old words')
    page.locator('#input').fill('')
    page.locator('#input').press_sequentially('Edited quickly', delay=20)
    page.wait_for_function('speech.runs.length === 2')
    speech(page, 'new words')
    expect(page.locator('#input')).to_have_value('Edited quickly new words')
    assert page.evaluate('speech.runs.length') == 2


def stop_during_edit_restart(page):
    page.locator('#mic').click()
    speech(page, 'old words')
    page.locator('#input').fill('Keep edit')
    page.locator('#mic').click()
    page.wait_for_timeout(400)
    assert page.evaluate('speech.runs.length') == 1
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#input')).to_have_value('Keep edit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='URL of the isolated test app')
    args = parser.parse_args()
    checks = [manual_edit, automatic_restart, interim_results, stopped_result,
              stop_during_startup, send_while_listening, stale_callbacks,
              denied_permission, start_failure, rapid_typing, stop_during_edit_restart]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for check in checks:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script(SPEECH_API)
                try:
                    page.goto(args.url)
                    page.locator('#loading-indicator.hidden').wait_for(state='attached')
                    check(page)
                    assert not errors, errors
                    print(f'PASS {check.__name__}', flush=True)
                finally:
                    page.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
