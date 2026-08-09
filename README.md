# Board Command / Board Search

Private board-search operating system for Jeff Barnes.

## Architecture

- Flask app with browser UI
- Google Drive / Docs / Sheets / Calendar as canonical storage
- Google OAuth for identity
- Optional OpenAI-powered AI copilot
- Local-first by default, but portable to hosted environments

## Security

Do **not** commit any of the following:

- `.env`
- `credentials.json`
- `token.json`
- `.boardcommand-secret`

The repository contains only code and configuration IDs. Secrets must be provided through local files or hosting-platform secret management.

## Local run

1. Install Python 3.11+
2. Copy `.env.example` to `.env`
3. Add your Google OAuth Desktop credentials as `credentials.json`
4. Run `setup_windows.ps1` once
5. Double-click `start_board_command.bat`

The app binds to `127.0.0.1:8787`.

## Hosted deployment

The app is structured so it can be hosted on services that support Python web apps. For a private production deployment, prefer Google Cloud Run or Render over exposing a localhost-only build.

Google remains the authoritative backend even when hosted.
