
import json
import os
import time
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APPS = json.loads(
    (ROOT / "apps.json").read_text(encoding="utf-8")
)["apps"]

DIAGNOSTICS = ROOT / "diagnostics"
DIAGNOSTICS.mkdir(exist_ok=True)

SLEEP_PHRASES = (
    "app has gone to sleep",
    "this app is asleep",
    "get this app back up",
)

STARTING_PHRASES = (
    "baking your app",
    "please wait while your app is starting",
)

BLOCK_PHRASES = (
    "this site is blocked",
    "cisco umbrella",
    "security threat",
    "access denied",
)

SELECTORS = (
    '[data-testid="stAppViewContainer"]',
    '[data-testid="stMain"]',
    '[data-testid="stApp"]',
    ".stApp",
)


APP_MARKERS = {
    "agentic-ai-sre-transformation": "Reliability at a glance",
    "ai-stock-finder": "Find opportunities",
    "ai-job-hunter": "Daily job recommendations",
    "nfl-fantasy-agent": "NFL Fantasy Intelligence",
    "ai-email-drafting-assistant": "Paste the email you received",
}



def valid_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and host.endswith(".streamlit.app")
        and not parsed.username
        and not parsed.password
    )


def check(app):
    start = time.monotonic()
    name = app["name"]
    url = app["url"]

    result = {
        "name": name,
        "url": url,
        "github": app.get("github", ""),
        "status": "FAILED",
        "detail": "",
        "seconds": None,
    }

    safe_name = "".join(
        c if c.isalnum() else "_" for c in name
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            deadline = time.monotonic() + 45
            woken = False

            while time.monotonic() < deadline:
                body = page.locator("body").inner_text(
                    timeout=10000
                ).lower()

                if any(x in body for x in BLOCK_PHRASES):
                    result["detail"] = (
                        "Security block detected: "
                        + body[:250]
                    )
                    break

                if any(x in body for x in SLEEP_PHRASES):
                    button = page.get_by_role(
                        "button",
                        name="Yes, get this app back up!",
                        exact=False,
                    )

                    if button.count() and button.first.is_visible():
                        button.first.click(timeout=10000)
                        woken = True

                    page.wait_for_timeout(4000)
                    continue

                if any(x in body for x in STARTING_PHRASES):
                    page.wait_for_timeout(4000)
                    continue


                # Recognize known application content.
                hostname = urlparse(url).hostname or ""
                app_key = hostname.removesuffix(".streamlit.app")
                marker = APP_MARKERS.get(app_key)

                if marker and marker.lower() in body:
                result["status"] = "WOKEN" if woken else "LOADED"
                result["detail"] = (
                f"Application content detected: {marker}"
                )
                break

                for selector in SELECTORS:
                    locator = page.locator(selector).first
                    if locator.count() and locator.is_visible():
                        result["status"] = (
                            "WOKEN" if woken else "LOADED"
                        )
                        result["detail"] = (
                            f"Visible Streamlit UI: {selector}"
                        )
                        break

                if result["status"] != "FAILED":
                    break

                page.wait_for_timeout(4000)

            if result["status"] == "FAILED":
                if not result["detail"]:
                    result["detail"] = (
                        "No Streamlit UI detected within 90 seconds"
                    )

                result["detail"] += (
                    f"; title={page.title()[:100]}"
                    f"; final_url={page.url}"
                )

                page.screenshot(
                    path=str(
                        DIAGNOSTICS / f"{safe_name}.png"
                    ),
                    full_page=True,
                    timeout=10000,
                )

        except Exception as exc:
            result["detail"] = (
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )

            try:
                page.screenshot(
                    path=str(
                        DIAGNOSTICS / f"{safe_name}.png"
                    ),
                    full_page=True,
                    timeout=10000,
                )
            except Exception:
                pass

        finally:
            browser.close()

    result["seconds"] = round(
        time.monotonic() - start, 1
    )

    return result


def email_report(results, timestamp):
    key = os.getenv("BREVO_API_KEY", "")
    sender = os.getenv("BREVO_FROM_EMAIL", "")
    recipient = os.getenv("REPORT_TO_EMAIL", "")

    if not all((key, sender, recipient)):
        print("Email skipped: missing Brevo configuration")
        return False

    ok = sum(
        r["status"] in ("LOADED", "WOKEN")
        for r in results
    )

    rows = "".join(
        "<tr>"
        f"<td>{html.escape(r['name'])}</td>"
        f"<td>{html.escape(r['status'])}</td>"
        f"<td>{html.escape(r['detail'])}</td>"
        f"<td>{r['seconds']}s</td>"
        "</tr>"
        for r in results
    )

    body = (
        "<h2>Streamlit Portfolio Monitor</h2>"
        f"<p>{html.escape(timestamp)} UTC</p>"
        f"<p>{ok}/{len(results)} apps loaded</p>"
        "<table border='1' cellpadding='8'>"
        "<tr><th>App</th><th>Status</th>"
        "<th>Details</th><th>Duration</th></tr>"
        f"{rows}</table>"
        "<p>LOADED/WOKEN confirms a visible Streamlit "
        "interface, not full application functionality.</p>"
    )

    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": key,
            "accept": "application/json",
            "content-type": "application/json",
        },
        json={
            "sender": {"email": sender},
            "to": [{"email": recipient}],
            "subject": (
                f"Streamlit Monitor: {ok}/{len(results)} loaded"
            ),
            "htmlContent": body,
        },
        timeout=25,
    )

    response.raise_for_status()
    print("Brevo accepted email report")
    return True


def main():
    enabled = [
        app for app in APPS
        if app.get("enabled", True)
    ]

    for app in enabled:
        if not valid_url(app["url"]):
            raise ValueError(
                f"Invalid Streamlit URL: {app['name']}"
            )

    results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(check, app): app
            for app in enabled
        }

        for future in as_completed(futures):
            app = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "name": app["name"],
                    "url": app["url"],
                    "github": app.get("github", ""),
                    "status": "FAILED",
                    "detail": f"Worker error: {exc}",
                    "seconds": None,
                }

            results.append(result)
            print(
                f"{result['name']}: "
                f"{result['status']} — {result['detail']}",
                flush=True,
            )

    order = {
        app["name"]: index
        for index, app in enumerate(enabled)
    }
    results.sort(key=lambda r: order[r["name"]])

    timestamp = datetime.now(timezone.utc).isoformat()

    (ROOT / "report.json").write_text(
        json.dumps(
            {
                "checked_at": timestamp,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    email_report(results, timestamp)

    if any(r["status"] == "FAILED" for r in results):
        raise SystemExit(
            "One or more applications failed; see report.json"
        )


if __name__ == "__main__":
    main()
