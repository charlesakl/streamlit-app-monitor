import json, os, time, html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APPS = json.loads((ROOT / 'apps.json').read_text(encoding='utf-8'))['apps']
SLEEP_PHRASES = ('app has gone to sleep', 'this app is asleep', 'get this app back up')
STARTING_PHRASES = ('baking your app', 'please wait while your app is starting')

def valid_url(url, domain):
    p = urlparse(url)
    return p.scheme == 'https' and bool(p.hostname) and (p.hostname == domain.lstrip('.') or p.hostname.endswith(domain)) and not p.username

def check(page, app, timeout_seconds=180, retries=2):
    start = time.monotonic()
    result = {'name': app['name'], 'url': app['url'], 'github': app.get('github', ''), 'status': 'FAILED', 'detail': '', 'seconds': None}
    notes = []
    woken = False
    for attempt in range(1, retries + 1):
        try:
            page.goto(app['url'], wait_until='domcontentloaded', timeout=60000)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                body = page.locator('body').inner_text(timeout=15000).lower()
                if any(s in body for s in SLEEP_PHRASES):
                    button = page.get_by_role('button', name='Yes, get this app back up!', exact=False)
                    if button.count() and button.first.is_visible():
                        button.first.click(timeout=10000)
                        woken = True
                        notes.append('Wake button clicked')
                    page.wait_for_timeout(5000)
                    continue
                if any(s in body for s in STARTING_PHRASES):
                    page.wait_for_timeout(5000)
                    continue
                # Check several Streamlit UI elements, not only one version-specific test ID.
                selectors = ('[data-testid="stAppViewContainer"]', '[data-testid="stMain"]', '[data-testid="stApp"]', '.stApp')
                for selector in selectors:
                    locator = page.locator(selector).first
                    if locator.count() and locator.is_visible():
                        result['status'] = 'WOKEN' if woken else 'LOADED'
                        result['detail'] = '; '.join(notes + [f'Visible Streamlit UI ({selector}); not a functional test'])
                        result['seconds'] = round(time.monotonic() - start, 1)
                        return result
                page.wait_for_timeout(4000)
            notes.append(f'Attempt {attempt}: no detectable Streamlit UI within {timeout_seconds}s')
        except Exception as exc:
            notes.append(f'Attempt {attempt}: {type(exc).__name__}: {str(exc)[:160]}')
        if attempt < retries:
            page.wait_for_timeout(5000)
    result['detail'] = '; '.join(notes)
    result['seconds'] = round(time.monotonic() - start, 1)
    return result

def email_report(results, timestamp):
    key, sender, recipient = (os.getenv(k, '') for k in ('BREVO_API_KEY', 'BREVO_FROM_EMAIL', 'REPORT_TO_EMAIL'))
    if not all((key, sender, recipient)):
        print('Email skipped: missing BREVO_API_KEY, BREVO_FROM_EMAIL, or REPORT_TO_EMAIL')
        return False
    ok = sum(r['status'] in ('LOADED', 'WOKEN') for r in results)
    rows = ''.join('<tr><td>'+html.escape(r['name'])+'</td><td>'+html.escape(r['status'])+'</td><td>'+html.escape(r['detail'])+'</td><td>'+str(r['seconds'])+'s</td></tr>' for r in results)
    body = f'<h2>Streamlit Portfolio Monitor</h2><p>{html.escape(timestamp)} UTC — {ok}/{len(results)} apps loaded</p><table border="1" cellpadding="8"><tr><th>App</th><th>Status</th><th>Details</th><th>Duration</th></tr>{rows}</table><p>LOADED/WOKEN indicates a visible Streamlit UI, not full application functionality.</p>'
    response = requests.post('https://api.brevo.com/v3/smtp/email', headers={'api-key':key, 'accept':'application/json','content-type':'application/json'}, json={'sender':{'email':sender},'to':[{'email':recipient}],'subject':f'Streamlit Monitor: {ok}/{len(results)} loaded','htmlContent':body}, timeout=25)
    response.raise_for_status()
    print('Brevo accepted email report')
    return True

def main():
    enabled = [a for a in APPS if a.get('enabled', True)]
    for app in enabled:
        if not valid_url(app['url'], '.streamlit.app'):
            raise ValueError(f'Invalid Streamlit URL for {app["name"]}')
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for app in enabled:
                page = browser.new_page()
                try:
                    result = check(page, app)
                    results.append(result)
                    print(f'{result["name"]}: {result["status"]} — {result["detail"]}', flush=True)
                finally:
                    page.close()
        finally:
            browser.close()
    timestamp = datetime.now(timezone.utc).isoformat()
    (ROOT / 'report.json').write_text(json.dumps({'checked_at':timestamp,'results':results}, indent=2, ensure_ascii=False), encoding='utf-8')
    email_report(results, timestamp)
    if any(r['status'] == 'FAILED' for r in results):
        raise SystemExit('One or more apps did not load; see report.json')

if __name__ == '__main__':
    main()
