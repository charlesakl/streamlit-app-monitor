# Configurable Streamlit portfolio monitor

Checks enabled apps every six hours using a real browser, attempts to click Streamlit's wake-up button when present, writes `report.json`, and emails a summary through Brevo.

## Setup
1. Create a GitHub repository (e.g., `streamlit-app-monitor`) and upload **all files including the hidden `.github/workflows/monitor.yml` path** to its default branch.
2. In GitHub Settings → Secrets and variables → Actions, create `BREVO_API_KEY`, `BREVO_FROM_EMAIL` (verified sender), and `REPORT_TO_EMAIL`.
3. Open Actions → Monitor Streamlit portfolio → Run workflow. Check logs and the uploaded JSON report.
4. GitHub Actions runs at 00:17, 06:17, 12:17 and 18:17 UTC, subject to scheduling delays. This is not a guaranteed uptime service.

## Add another project
Edit `apps.json` on GitHub, append a comma-separated object to the `apps` array:

```json
{"name":"My New App","url":"https://my-new-app.streamlit.app/","github":"https://github.com/username/repo","enabled":true}
```

Set `enabled` to `false` to skip an app. Commit the change; no Python or workflow edits are necessary. Validate JSON syntax (especially commas).

## Limitations
This is a best-effort check and wake-up attempt, not a way to guarantee Streamlit Community Cloud stays awake. `LOADED` means the Streamlit UI was detected; it does not verify app business logic, authentication, or APIs. Streamlit can change its sleep screen and break the button selector. GitHub Actions may be delayed. Brevo requires a verified sender and sufficient sending allowance. For guaranteed availability, consider paid hosting.
