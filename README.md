# Board Command / Board Search

Private board-search operating system for Jeff Barnes.

## Architecture

- Flask app with browser UI
- Google Drive / Docs / Sheets / Calendar as canonical storage
- Google OAuth for identity and access control
- Optional OpenAI-powered AI copilot
- Local-first, but deployable to Render without changing the code
- Hosted Google OAuth refresh tokens stored in Google Secret Manager

## Security model

Never commit:

- `.env`
- `credentials.json`
- `token.json`
- `.boardcommand-secret`
- Google service-account JSON
- OpenAI API keys

The repository contains code and Google file/folder IDs only. Those IDs do not bypass Google permissions.

The app hard-allowlists `cmajeff@gmail.com` by default. Protected API routes independently check the authenticated session.

## Local run

1. Install Python 3.11+.
2. Copy `.env.example` to `.env`.
3. Create a Google OAuth client and add local OAuth credentials as `credentials.json`.
4. Run `setup_windows.ps1` once.
5. Double-click `start_board_command.bat`.

The local app binds to `127.0.0.1:8787`. In local mode the Google refresh token is stored in ignored `token.json` on the laptop.

## Hosted deployment on Render

`render.yaml` is included in the repository. Create a Render Blueprint/Web Service from this repository and configure the secret environment variables listed below.

### Google OAuth web client

For hosted mode create a **Web application** OAuth client in Google Cloud rather than using the local Desktop client.

Add the deployed callback URL to the OAuth client's Authorized redirect URIs, for example:

`https://YOUR-RENDER-HOST/oauth/callback`

Configure these Render secrets:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` — full callback URL ending `/oauth/callback`
- `OPENAI_API_KEY` — optional but required for the AI Copilot

Render generates `FLASK_SECRET_KEY` through the blueprint.

## Durable Google OAuth with Google Secret Manager

Render's normal application filesystem is not the canonical place to persist OAuth refresh tokens. Board Command therefore switches automatically to Google Secret Manager when these variables are configured:

- `GCP_SECRET_PROJECT_ID`
- `GCP_SERVICE_ACCOUNT_JSON`
- `GCP_TOKEN_SECRET_NAME` (defaults to `board-command-google-oauth`)

### One-time Google Cloud setup

1. In the Google Cloud project used for Board Command, enable **Secret Manager API**.
2. Create a dedicated service account such as `board-command-secret-store`.
3. Grant it only the permissions necessary to create/read/add versions to the Board Command OAuth secret. For a simple initial setup, `Secret Manager Admin` works; tighten this to a custom least-privilege role later.
4. Create a JSON key for that service account.
5. Put the **entire JSON object** into the Render secret environment variable `GCP_SERVICE_ACCOUNT_JSON`.
6. Put the Google Cloud project ID into `GCP_SECRET_PROJECT_ID`.
7. Do not commit the service-account JSON anywhere.

On the first successful Google sign-in, Board Command writes the authorized-user credential JSON as a new Secret Manager version. On later restarts or redeploys it reads the latest version. Refreshed OAuth tokens are written back as new secret versions automatically.

In hosted mode, `token.json` is not used.

## Render configuration included

The included blueprint:

- installs `requirements.txt`
- starts Gunicorn
- exposes `/healthz` for health checks
- generates a stable Flask session secret
- declares the Google/OpenAI/GCP values as non-committed secrets
- automatically uses Google Secret Manager for OAuth token persistence when configured

## Google remains the backend

Board Command does not move the Board Seat 2027 system into Render. Google remains authoritative for:

- Drive company dossier folders
- Google Docs research and content
- Control Center / CRM Sheets
- Board-related Calendar events

The hosted app is the interface and AI layer, not a replacement storage silo.

## Operational recommendation

Keep the repository private if possible. Even though no credentials are committed, this is a personal operating application and there is little benefit to exposing its internal architecture publicly.
