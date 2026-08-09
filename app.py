from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_urlsafe(48)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
)

ALLOWED_EMAIL = os.getenv("ALLOWED_EMAIL", "cmajeff@gmail.com").lower().strip()
TOKEN_FILE = ROOT / "token.json"


def hosted_token_store_enabled() -> bool:
    return bool(os.getenv("GCP_SECRET_PROJECT_ID") and os.getenv("GCP_SERVICE_ACCOUNT_JSON"))


def _secret_client():
    from google.cloud import secretmanager

    info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(info)
    return secretmanager.SecretManagerServiceClient(credentials=creds)


def _secret_name() -> str:
    return os.getenv("GCP_TOKEN_SECRET_NAME", "board-command-google-oauth")


def _secret_parent() -> str:
    return f"projects/{os.environ['GCP_SECRET_PROJECT_ID']}"


def _secret_resource() -> str:
    return f"{_secret_parent()}/secrets/{_secret_name()}"


def _load_token_json() -> str | None:
    if hosted_token_store_enabled():
        try:
            client = _secret_client()
            response = client.access_secret_version(
                request={"name": f"{_secret_resource()}/versions/latest"}
            )
            return response.payload.data.decode("utf-8")
        except Exception:
            return None
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8")
    return None


def _save_token_json(token_json: str) -> None:
    if hosted_token_store_enabled():
        from google.api_core.exceptions import AlreadyExists, NotFound
        from google.cloud import secretmanager

        client = _secret_client()
        try:
            client.get_secret(request={"name": _secret_resource()})
        except NotFound:
            try:
                client.create_secret(
                    request={
                        "parent": _secret_parent(),
                        "secret_id": _secret_name(),
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except AlreadyExists:
                pass
        client.add_secret_version(
            request={
                "parent": _secret_resource(),
                "payload": {"data": token_json.encode("utf-8")},
            }
        )
        return
    TOKEN_FILE.write_text(token_json, encoding="utf-8")


def client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri] if redirect_uri else [],
            }
        }
    credentials_file = ROOT / "credentials.json"
    if credentials_file.exists():
        return json.loads(credentials_file.read_text(encoding="utf-8"))
    return None


def save_creds(creds: Credentials) -> None:
    _save_token_json(creds.to_json())


def load_creds() -> Credentials | None:
    raw = _load_token_json()
    if not raw:
        return None
    data = json.loads(raw)
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_creds(creds)
    return creds if creds.valid else None


def service(name: str, version: str):
    creds = load_creds()
    if not creds:
        raise RuntimeError("Google is not connected. Sign in again from Board Command.")
    return build(name, version, credentials=creds, cache_discovery=False)


def owner_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("email", "").lower() != ALLOWED_EMAIL:
            return jsonify(error="Unauthorized"), 401
        return fn(*args, **kwargs)

    return wrapper


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, token_store="secret-manager" if hosted_token_store_enabled() else "local-file")


@app.route("/")
def index():
    return render_template(
        "index.html",
        authorized=session.get("email", "").lower() == ALLOWED_EMAIL,
        email=session.get("email"),
    )


@app.route("/auth/google")
def auth_google():
    config = client_config()
    if not config:
        return "Google OAuth credentials are not configured.", 500
    flow = Flow.from_client_config(config, scopes=SCOPES)
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or url_for("oauth_callback", _external=True)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    config = client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, state=session.get("oauth_state"))
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or url_for("oauth_callback", _external=True)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    profile = build("oauth2", "v2", credentials=creds, cache_discovery=False).userinfo().get().execute()
    email = profile.get("email", "").lower()
    if email != ALLOWED_EMAIL:
        session.clear()
        return "Access denied", 403
    save_creds(creds)
    session["email"] = email
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/status")
def status():
    return jsonify(
        google_connected=bool(load_creds()),
        authorized=session.get("email", "").lower() == ALLOWED_EMAIL,
        email=session.get("email"),
        openai_configured=bool(os.getenv("OPENAI_API_KEY")),
        token_store="Google Secret Manager" if hosted_token_store_enabled() else "local token.json",
    )


@app.route("/api/dashboard")
@owner_required
def dashboard():
    output = {"companies": [], "calendar": [], "raw_dashboard": []}
    sheets = service("sheets", "v4")
    sheet_id = CONFIG["control_center_sheet_id"]
    value_ranges = sheets.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id,
        ranges=["Dashboard!A1:H30", "'Dossier Index'!A1:L100"],
    ).execute().get("valueRanges", [])
    output["raw_dashboard"] = value_ranges[0].get("values", []) if value_ranges else []
    dossier_rows = value_ranges[1].get("values", []) if len(value_ranges) > 1 else []
    if dossier_rows:
        headers = dossier_rows[0]
        for row in dossier_rows[1:]:
            item = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
            if item.get("Organization"):
                output["companies"].append(item)

    calendar = service("calendar", "v3")
    now = datetime.now(timezone.utc)
    events = calendar.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=21)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute().get("items", [])
    output["calendar"] = [
        {
            "summary": event.get("summary"),
            "start": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
            "htmlLink": event.get("htmlLink"),
        }
        for event in events
        if "Board" in ((event.get("summary") or "") + " " + (event.get("description") or ""))
    ]
    return jsonify(output)


@app.route("/api/companies")
@owner_required
def companies():
    drive = service("drive", "v3")
    parent = CONFIG["company_dossiers_folder_id"]
    files = drive.files().list(
        q=f"'{parent}' in parents and trashed=false",
        fields="files(id,name,mimeType,modifiedTime,webViewLink)",
        orderBy="name",
        pageSize=200,
    ).execute().get("files", [])
    return jsonify(files)


@app.route("/api/sheet/<name>")
@owner_required
def sheet(name):
    allowed = {
        "Pipeline", "Relationships", "Targets", "Outreach Log",
        "Content & Visibility", "Weekly Review", "Dossier Index", "Distribution Queue",
    }
    if name not in allowed:
        return jsonify(error="Not allowed"), 400
    sheets = service("sheets", "v4")
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=CONFIG["control_center_sheet_id"],
        range=f"'{name}'!A1:Z500",
    ).execute().get("values", [])
    return jsonify(rows)


@app.route("/api/ai", methods=["POST"])
@owner_required
def ai():
    if not os.getenv("OPENAI_API_KEY"):
        return jsonify(error="OPENAI_API_KEY not configured"), 400
    from openai import OpenAI

    payload = request.get_json(force=True)
    prompt = payload.get("prompt", "")
    context = payload.get("context", "")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        instructions=(
            "You are Jeff Barnes' private Board Command copilot. Be a board-search strategist, "
            "governance researcher, editor and critical-thinking partner. Distinguish facts from "
            "inference. Never leak private CRM data into public drafts unless explicitly asked."
        ),
        input=f"CONTEXT:\n{context[:18000]}\n\nREQUEST:\n{prompt}",
    )
    return jsonify(answer=response.output_text)


if __name__ == "__main__":
    app.run("127.0.0.1", 8787, debug=False)
