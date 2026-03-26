# email_hoover

Gmail-focused tooling for finding stale newsletter senders, approving cleanup rules, and running those rules manually or on a schedule.

## Current Scope

The app currently includes:

- a server-rendered operator console
- newsletter candidate discovery and rule approval
- manual and scheduled cleanup execution
- Gmail OAuth connect flow
- audit logging for cleanup activity

## Requirements

- Python 3.12+
- a Google Cloud project with Gmail API enabled
- an OAuth client configured for local development

## Install

```bash
python3 -m pip install -e ".[dev]"
```

## Configure Gmail OAuth

The app supports two ways to supply Google OAuth client credentials.

### Option 1: Credentials JSON file

Set:

```bash
export GOOGLE_CREDENTIALS_PATH=/absolute/path/to/google-oauth-client.json
```

The JSON should be a Google OAuth client file containing either an `installed` or `web` client definition.

### Option 2: Explicit environment variables

Set:

```bash
export GOOGLE_CLIENT_ID=your-client-id
export GOOGLE_CLIENT_SECRET=your-client-secret
```

### Required redirect URI

By default the app expects:

```bash
export GOOGLE_REDIRECT_URI=http://127.0.0.1:8765/auth/google/callback
```

Your Google OAuth client must allow that exact redirect URI unless you override it with a different `GOOGLE_REDIRECT_URI`.

## Optional Environment Variables

```bash
export GMAIL_TOKEN_PATH="$HOME/.local/state/email-hoover/gmail-token.json"
export SESSION_SECRET="change-me-for-non-dev-use"
```

`GMAIL_TOKEN_PATH` stores the refreshable Gmail credential payload after a successful connect.

## Run

If you use the default redirect URI, run the app on port `8765`:

```bash
python3 -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

If Gmail is not connected yet, the dashboard will show `Connect Gmail`.

## Connect Gmail

1. Start the app with the same host/port used in `GOOGLE_REDIRECT_URI`.
2. Open the dashboard.
3. Click `Connect Gmail`.
4. Complete the Google consent flow.
5. Return to `/` and confirm the reconnect banner is gone.

If OAuth client config is missing, `/auth/google/start` now redirects back to the dashboard with an operator-visible error instead of crashing.

## Test

Use:

```bash
python3 -m pytest -v
```

Use `python3 -m pytest` rather than the `pytest` console script in this environment.
