# Gmail OAuth Connect Design

Date: 2026-03-25
Project: `email_hoover`
Scope: Gmail connection flow for the operator console

## Goal

Add a real Gmail connect flow to the existing FastAPI operator console so the app can obtain and refresh Gmail credentials without requiring a manually pasted bearer token.

This work upgrades the MVP from a developer-only token file setup to a usable local-first OAuth connection flow.

## Problem Statement

The current app only treats Gmail as "connected" when a token file exists on disk, and it uses the raw file contents as a bearer token. That is not a real user-facing integration because:

- there is no `Connect Gmail` flow in the UI,
- access tokens expire,
- there is no refresh-token lifecycle,
- reconnect behavior is only simulated by file presence.

The app needs an OAuth flow that fits the current server-rendered operator console and preserves the existing local-first architecture.

## Out of Scope

This addition does not attempt to:

- add multi-user account management,
- support Outlook or non-Gmail providers,
- store Gmail credentials in the application database,
- introduce a frontend SPA auth layer,
- add granular account settings beyond connect/reconnect status,
- redesign the broader stale-newsletter cleanup workflow.

## Product Shape

The current operator console remains the home screen. When Gmail is disconnected, the dashboard shows a clear `Connect Gmail` action. Clicking it redirects the operator to Google, then back into the app. On successful callback, the app stores refreshable Gmail credentials at the local state path and returns the operator to `/`.

The connected state should feel native to the existing console rather than like a separate setup tool.

## Primary User Outcome

The operator should be able to launch the app locally, click `Connect Gmail`, authorize Gmail access once, and then use the cleanup tool without manually managing expiring access tokens.

Success for this slice means:

- Gmail can be connected from the dashboard,
- stored credentials survive app restarts,
- expired access tokens refresh automatically,
- auth failures degrade to a visible reconnect state,
- scheduled cleanup resumes after a successful reconnect.

## Primary Workflow

### 1. Dashboard load

The app evaluates Gmail auth status from stored credentials:

- if no valid credentials exist, show `Connect Gmail`,
- if credentials exist and are usable, show connected status,
- if stored credentials exist but refresh fails, show `Reconnect Gmail`.

### 2. OAuth start

`GET /auth/google/start` builds a Google OAuth authorization URL using:

- configured client credentials,
- the configured redirect URI,
- Gmail modify scope,
- offline access,
- a CSRF state token.

The route persists the pending auth state in a signed session cookie and redirects to Google.

### 3. OAuth callback

`GET /auth/google/callback` verifies the returned state, exchanges the authorization code for tokens, writes the credential payload to the Gmail token file, clears the pending auth state, resumes scheduled cleanup, and redirects to `/`.

### 4. Runtime token use

When the app talks to Gmail, it loads stored OAuth credentials, refreshes them as needed, and uses the resulting access token for Gmail API calls.

## Configuration Model

The first version should support both client-credential sources:

1. Environment variables
2. A credentials JSON file path provided via environment variable

Recommended config inputs:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_CREDENTIALS_PATH`
- `GOOGLE_REDIRECT_URI`
- existing `GMAIL_TOKEN_PATH`

Rules:

- if `GOOGLE_CREDENTIALS_PATH` is set, parse the installed-app or web client JSON from that file,
- otherwise fall back to explicit env vars,
- fail clearly on missing or malformed config,
- keep the redirect URI explicit rather than inferred from request headers.

## Core Components

### 1. OAuth config loader

Responsibilities:

- load Google client credentials from env vars or JSON file,
- expose the redirect URI and Gmail scopes,
- validate the minimum fields required to start a flow.

### 2. Gmail credential store

Responsibilities:

- persist the OAuth credential payload at the existing local Gmail token path,
- create parent directories as needed,
- load credentials from disk for runtime use,
- avoid partial writes on failed auth attempts.

The token file should store refreshable credential JSON, not a raw bearer token string.

### 3. Auth routes

Responsibilities:

- start the Google OAuth flow,
- validate callback state,
- exchange the auth code for tokens,
- persist credentials,
- redirect the operator back to the dashboard with a visible success or failure state.

Routes:

- `GET /auth/google/start`
- `GET /auth/google/callback`

### 4. Credential-aware Gmail client builder

Responsibilities:

- replace the current raw token reader,
- load stored Google credentials,
- refresh expired access tokens,
- return a valid bearer token string to the existing Gmail HTTP client.

This should preserve the current `GmailClient` surface where possible so discovery, rules, and executor code remain stable.

### 5. Dashboard auth status UI

Responsibilities:

- render a `Connect Gmail` button when disconnected,
- render a compact connected state when credentials are present,
- render reconnect or operator-error banners when auth is broken or misconfigured.

This should be a small extension of the current console, not a separate setup application.

## Data Model

The application database does not need a new auth table for this slice. Gmail credentials stay on disk at the configured token path.

The stored credential payload should include the fields needed by Google credential refresh logic, typically:

- access token,
- refresh token,
- token URI,
- client ID,
- client secret,
- granted scopes,
- expiry metadata when present.

The auth-state check should treat missing, unreadable, or unrefreshable credentials as disconnected.

## Safety Constraints

- No secrets or tokens in rendered HTML, query params, or logs.
- No persistence of partial credentials on callback failure.
- State mismatch must abort the callback.
- Existing stored credentials must remain untouched if a new connect attempt fails before persistence completes.
- Invalid credentials should pause scheduled cleanup and surface reconnect guidance rather than crash the app.

## Error Handling

### Missing OAuth config

If client credentials or redirect configuration are missing:

- the dashboard should show an operator-facing configuration error,
- the `Connect Gmail` action should not silently fail.

### OAuth provider errors

If Google returns an error or omits the auth code:

- redirect back to `/`,
- show an operator-facing error banner,
- keep existing credentials untouched.

### State mismatch

If callback state does not match the signed cookie:

- reject the callback,
- do not write credentials,
- show an operator-facing error.

### Credential refresh failures

If stored credentials cannot be refreshed:

- treat Gmail as disconnected,
- pause the scheduled cleanup job,
- show `Reconnect Gmail` on the dashboard.

### Persistence failures

If the app cannot write the credential file:

- redirect back to `/` with an operator-facing error,
- do not mark the app as connected.

## Testing Strategy

### Unit tests

- config loading from env vars,
- config loading from Google credentials JSON,
- token-store write and read behavior,
- auth-state evaluation for missing, valid, and invalid stored credentials.

### Route tests

- `/auth/google/start` redirects to Google and stores state,
- `/auth/google/callback` success persists credentials and redirects home,
- callback rejects state mismatch,
- callback handles provider-returned OAuth errors,
- callback handles persistence failures without corrupting existing credentials.

### Gmail client/auth tests

- runtime token acquisition from stored credential JSON,
- refresh path produces a valid bearer token,
- invalid refresh state degrades cleanly to reconnect behavior.

### Dashboard tests

- disconnected dashboard renders `Connect Gmail`,
- configured but disconnected dashboard shows actionable auth messaging,
- connected dashboard hides reconnect prompts.

### Scheduler tests

- successful callback clears auth-failed state,
- successful callback resumes the paused cleanup job,
- refresh failure pauses scheduled cleanup and preserves reconnect messaging.

## Implementation Notes

Keep the change local-first and incremental:

- prefer signed cookie state over inventing a database-backed auth session,
- keep the `GmailClient` API stable,
- confine OAuth-specific behavior to config, auth, and token-loading boundaries,
- preserve the existing operator console structure.

This is a connection-flow upgrade, not an authorization subsystem rewrite.
