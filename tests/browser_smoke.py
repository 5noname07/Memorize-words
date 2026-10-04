"""Real mobile/desktop browser workflow against Waitress and an isolated temporary DB.

Run: python tests/browser_smoke.py (Edge by default; BROWSER_CHANNEL=chromium for installed Chromium).
No real users or passwords are included, and the production database is never opened.
"""
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from playwright.sync_api import sync_playwright, expect
from waitress import create_server


def main():
    with tempfile.TemporaryDirectory() as temporary:
        config = {'TESTING': True, 'DATABASE': str(Path(temporary) / 'ui.sqlite3'), 'SECRET_KEY': secrets.token_hex(32)}
        app = create_app(config)
        password = secrets.token_urlsafe(24)
        for username in ('mobile-test', 'other-user'):
            result = app.test_cli_runner().invoke(args=['create-user', username], input=f'{password}\n{password}\n')
            assert result.exit_code == 0, result.output
        server = create_server(app, host='127.0.0.1', port=0)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                channel = os.environ.get('BROWSER_CHANNEL', 'msedge')
                browser = playwright.chromium.launch(channel=None if channel == 'chromium' else channel, headless=True)
                context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.effective_port}')
                expect(page.locator('#login-page')).to_be_visible()
                page.locator('#login-form input[name=username]').fill('mobile-test')
                page.locator('#login-form input[name=password]').fill(password)
                page.locator('#login-form button').click()
                expect(page.locator('#app-page')).to_be_visible()
                page.locator('[data-view=batches]').click()
                page.locator('#new-batch').click()
                page.locator('#editor-form input[name=name]').fill('手机测试批次')
                page.locator('#editor-form button[type=submit]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                page.get_by_role('button', name='查看单词').click()
                page.locator('#import-words').click()
                page.locator('textarea[name=csv]').fill('english,meaning,phonetic\napple,苹果,/ˈæpəl/')
                page.locator('#editor-form button[type=submit]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                expect(page.locator('#word-list')).to_contain_text('apple')
                page.locator('[data-view=study]').click()
                page.locator('#start-study').click()
                expect(page.locator('#study-card .meaning')).to_have_text('苹果')
                expect(page.locator('#study-card .phonetic')).to_have_text('/ˈæpəl/')
                page.locator('#study-card input').fill('aple')
                page.locator('#study-card button[type=submit]').click()
                expect(page.locator('#study-card .feedback')).to_contain_text('已记录到错题集')
                page.locator('[data-view=mistakes]').click()
                expect(page.locator('#mistake-list')).to_contain_text('apple')
                page.locator('#start-mistakes').click()
                page.locator('#mistakes-card input').fill('apple')
                page.locator('#mistakes-card button[type=submit]').click()
                expect(page.locator('#mistakes-card .feedback')).to_contain_text('答对了')
                expect(page.locator('#mistake-list')).to_contain_text('错题练习答对 1 次')
                page.locator('[data-view=history]').click()
                expect(page.locator('#history-list .word-row')).to_have_count(2)
                for width in (320, 390, 768, 1280):
                    page.set_viewport_size({'width': width, 'height': 900})
                    for view in ('study','batches','mistakes','history'):
                        page.locator(f'[data-view={view}]').click()
                        expect(page.locator(f'#view-{view}')).to_be_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (width, view)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('[data-view=study]').click()
                expect(page.locator('#notice')).not_to_be_visible(timeout=10000)
                page.screenshot(path='artifacts/mobile-study.png', full_page=True)
                page.reload()
                expect(page.locator('#app-page')).to_be_visible()
                page.locator('[data-view=history]').click()
                expect(page.locator('#history-list .word-row')).to_have_count(2)
                page.locator('#logout').click()
                expect(page.locator('#login-page')).to_be_visible()
                page.locator('#login-form input[name=username]').fill('other-user')
                page.locator('#login-form input[name=password]').fill(password)
                page.locator('#login-form button').click()
                expect(page.locator('#app-page')).to_be_visible()
                page.locator('[data-view=batches]').click()
                expect(page.locator('#batch-list')).to_contain_text('还没有批次')
                page.locator('[data-view=mistakes]').click()
                expect(page.locator('#mistake-list')).to_contain_text('暂无待复习')
                page.locator('[data-view=history]').click()
                expect(page.locator('#history-list')).to_contain_text('开始练习后')
                assert not errors, errors
                context.close()
                browser.close()
            print('PASS: mobile login, batch/import, study/mistakes, history, refresh, account isolation; 320/390/768/1280px; no browser errors.')
        finally:
            server.close()
            server.task_dispatcher.shutdown()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
