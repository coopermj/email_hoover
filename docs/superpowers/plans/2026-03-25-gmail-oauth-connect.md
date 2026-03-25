# Gmail OAuth Connect Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real Gmail OAuth connect flow to the FastAPI operator console so the app can obtain, persist, refresh, and use Gmail credentials without manual bearer-token setup.

**Architecture:** Keep the current server-rendered console and add a narrow OAuth slice around it: configuration loading, credential persistence, start/callback routes, and a credential-aware Gmail client builder. Preserve the existing cleanup services and route structure by swapping the token-loading boundary rather than rewriting the Gmail client surface.

**Tech Stack:** FastAPI, Jinja2 templates, `google-auth`, `google-auth-oauthlib`, `httpx`, APScheduler, `pytest`

---

### Task 1: OAuth Config And Credential Store

**Files:**
- Create: `app/gmail/oauth.py`
- Modify: `app/config.py`
- Modify: `app/gmail/auth.py`
- Test: `tests/test_gmail_oauth.py`

- [x] **Step 1: Write the failing tests**

```python
def test_google_oauth_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8765/auth/google/callback")

    settings = Settings.from_env()

    config = load_google_oauth_config(settings)

    assert config.client_id == "client-id"
    assert config.client_secret == "client-secret"
    assert config.redirect_uri == "http://127.0.0.1:8765/auth/google/callback"


def test_credential_store_round_trips_refreshable_token_payload(tmp_path):
    path = tmp_path / "gmail-token.json"
    payload = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    }

    write_gmail_credentials(path, payload)

    assert read_gmail_credentials(path) == payload


def test_auth_state_reports_reconnect_for_invalid_stored_credentials(tmp_path):
    settings = Settings(gmail_token_path=tmp_path / "gmail-token.json")
    settings.gmail_token_path.write_text("{\"token\": \"missing-refresh\"}", encoding="utf-8")

    state = AuthState.from_disk(settings)

    assert state.connected is False
    assert state.reason == "invalid_token"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_gmail_oauth.py -v`
Expected: FAIL with missing OAuth helpers and invalid auth-state behavior

- [x] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


def load_google_oauth_config(settings: Settings) -> GoogleOAuthConfig:
    ...


def read_gmail_credentials(path: Path) -> dict[str, object]:
    ...


def write_gmail_credentials(path: Path, payload: dict[str, object]) -> None:
    ...
```

Add config fields to `Settings` for `google_client_id`, `google_client_secret`, `google_credentials_path`, `google_redirect_uri`, and a stable Gmail modify scope tuple. Update `AuthState.from_disk()` to treat unreadable or structurally invalid OAuth credential files as disconnected.

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_gmail_oauth.py tests/test_gmail_client.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/config.py app/gmail/auth.py app/gmail/oauth.py tests/test_gmail_oauth.py
git commit -m "feat: add Gmail OAuth config and credential store"
```

### Task 2: Auth Routes And Dashboard Connect UX

**Files:**
- Modify: `app/main.py`
- Modify: `app/web/routes.py`
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_dashboard_routes.py`

- [x] **Step 1: Write the failing tests**

```python
def test_dashboard_shows_connect_button_when_gmail_disconnected(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Connect Gmail" in response.text


def test_oauth_start_redirects_to_google_and_sets_state_cookie(client, monkeypatch):
    monkeypatch.setattr("app.web.routes.build_google_authorization_redirect", fake_redirect)

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert "email_hoover_oauth_state=" in response.headers["set-cookie"]


def test_oauth_callback_persists_credentials_and_redirects_home(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.web.routes.exchange_google_code", fake_exchange)

    response = client.get("/auth/google/callback?state=known&code=abc", follow_redirects=False, cookies={...})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_routes.py -v`
Expected: FAIL because the dashboard has no connect button and the auth routes do not exist

- [x] **Step 3: Write minimal implementation**

```python
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)


@router.get("/auth/google/start")
def start_google_oauth(request: Request) -> RedirectResponse:
    ...


@router.get("/auth/google/callback")
def complete_google_oauth(request: Request) -> RedirectResponse:
    ...
```

Render a `Connect Gmail` button in the dashboard when auth is disconnected. Store the OAuth state token in the session cookie, redirect to Google, handle callback success and failure, persist the credential payload, and redirect back to `/` with operator-visible error messaging when needed.

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_routes.py tests/test_app_health.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/main.py app/web/routes.py app/templates/dashboard.html app/templates/base.html tests/test_dashboard_routes.py
git commit -m "feat: add Gmail OAuth connect routes"
```

### Task 3: Refreshable Gmail Client Builder And Scheduler Resume

**Files:**
- Modify: `app/gmail/oauth.py`
- Modify: `app/services/scheduler.py`
- Modify: `app/gmail/client.py`
- Modify: `app/gmail/auth.py`
- Test: `tests/test_gmail_oauth.py`
- Test: `tests/test_app_health.py`
- Test: `tests/test_dashboard_routes.py`

- [x] **Step 1: Write the failing tests**

```python
def test_build_gmail_client_reads_refreshable_credentials(tmp_path):
    write_gmail_credentials(tmp_path / "gmail-token.json", payload)

    client = build_gmail_client(Settings(...), credentials_loader=fake_loader)

    assert client._token_getter() == "fresh-access-token"


def test_callback_resumes_scheduler_after_successful_connect(client, monkeypatch):
    response = client.get("/auth/google/callback?state=known&code=abc", follow_redirects=False, cookies={...})

    assert response.status_code == 303
    assert client.app.state.cleanup_job_auth_failed is False
    assert client.app.state.scheduler.get_job("cleanup").next_run_time is not None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_gmail_oauth.py tests/test_dashboard_routes.py tests/test_app_health.py -v`
Expected: FAIL because the scheduler still reads raw token strings and callback success does not resume cleanup

- [x] **Step 3: Write minimal implementation**

```python
def load_google_credentials(path: Path, refresh_request: GoogleAuthRequest | None = None) -> Credentials:
    ...


def read_gmail_access_token(path: Path) -> str:
    credentials = load_google_credentials(path)
    if not credentials.valid:
        credentials.refresh(refresh_request or GoogleAuthRequest())
        write_gmail_credentials(path, json.loads(credentials.to_json()))
    return credentials.token
```

Replace the raw token-file reader in the scheduler with a credential loader that refreshes and persists updated credentials. On successful OAuth callback, clear `cleanup_job_auth_failed` and resume the paused cleanup job.

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_gmail_oauth.py tests/test_dashboard_routes.py tests/test_app_health.py tests/test_gmail_client.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/gmail/oauth.py app/services/scheduler.py app/gmail/client.py app/gmail/auth.py tests/test_gmail_oauth.py tests/test_dashboard_routes.py tests/test_app_health.py
git commit -m "feat: refresh Gmail credentials automatically"
```

### Task 4: Full Verification And Local Smoke Test

**Files:**
- Modify: `docs/superpowers/plans/2026-03-25-gmail-oauth-connect.md`

- [x] **Step 1: Run the full automated suite**

Run: `python3 -m pytest -v`
Expected: PASS

- [x] **Step 2: Run a local app smoke test**

Run: `python3 -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765`
Expected: app starts successfully with the dashboard reachable at `/` and auth routes registered

- [x] **Step 3: Verify the smoke test endpoints**

Run: `curl -i http://127.0.0.1:8765/health`
Expected: `HTTP/1.1 200 OK`

Run: `curl -i http://127.0.0.1:8765/`
Expected: HTML contains `Connect Gmail` when no stored credentials exist

- [x] **Step 4: Mark the plan complete**

Update this file so each finished step is checked off.

- [x] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-03-25-gmail-oauth-connect.md
git commit -m "docs: complete Gmail OAuth connect plan"
```
