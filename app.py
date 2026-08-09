from __future__ import annotations
import os, json, secrets
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
CONFIG = json.loads((ROOT / "config.json").read_text())
SCOPES=["openid","https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/drive","https://www.googleapis.com/auth/documents","https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/calendar"]
app=Flask(__name__)
app.secret_key=os.getenv("FLASK_SECRET_KEY") or secrets.token_urlsafe(48)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV")=="production")
ALLOWED_EMAIL=os.getenv("ALLOWED_EMAIL","cmajeff@gmail.com").lower().strip()
TOKEN_FILE=ROOT/"token.json"

def client_config():
    cid=os.getenv("GOOGLE_CLIENT_ID")
    sec=os.getenv("GOOGLE_CLIENT_SECRET")
    red=os.getenv("GOOGLE_REDIRECT_URI")
    if cid and sec:
        return {"web":{"client_id":cid,"client_secret":sec,"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[red] if red else []}}
    p=ROOT/"credentials.json"
    if p.exists():
        return json.loads(p.read_text())
    return None

def save_creds(c): TOKEN_FILE.write_text(c.to_json())
def load_creds():
    if not TOKEN_FILE.exists(): return None
    c=Credentials.from_authorized_user_file(str(TOKEN_FILE),SCOPES)
    if c.expired and c.refresh_token:
        c.refresh(GoogleRequest()); save_creds(c)
    return c if c.valid else None

def svc(n,v):
    c=load_creds()
    if not c: raise RuntimeError("Google not connected")
    return build(n,v,credentials=c,cache_discovery=False)

def owner_required(fn):
    @wraps(fn)
    def w(*a,**k):
        if session.get("email","").lower()!=ALLOWED_EMAIL:return jsonify(error="Unauthorized"),401
        return fn(*a,**k)
    return w

@app.after_request
def sec(r):
    r.headers["X-Content-Type-Options"]="nosniff"; r.headers["X-Frame-Options"]="DENY"; r.headers["Referrer-Policy"]="no-referrer"; r.headers["Cache-Control"]="no-store"
    return r

@app.route("/")
def index(): return render_template("index.html",authorized=session.get("email","").lower()==ALLOWED_EMAIL,email=session.get("email"))

@app.route("/auth/google")
def auth_google():
    cfg=client_config()
    if not cfg:return "Google OAuth credentials are not configured.",500
    flow=Flow.from_client_config(cfg,scopes=SCOPES)
    flow.redirect_uri=os.getenv("GOOGLE_REDIRECT_URI") or url_for("oauth_callback",_external=True)
    u,state=flow.authorization_url(access_type="offline",include_granted_scopes="true",prompt="consent")
    session["oauth_state"]=state
    return redirect(u)

@app.route("/oauth/callback")
def oauth_callback():
    cfg=client_config(); flow=Flow.from_client_config(cfg,scopes=SCOPES,state=session.get("oauth_state"))
    flow.redirect_uri=os.getenv("GOOGLE_REDIRECT_URI") or url_for("oauth_callback",_external=True)
    flow.fetch_token(authorization_response=request.url)
    c=flow.credentials
    email=build("oauth2","v2",credentials=c,cache_discovery=False).userinfo().get().execute().get("email","").lower()
    if email!=ALLOWED_EMAIL: session.clear(); return "Access denied",403
    save_creds(c); session["email"]=email; return redirect("/")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

@app.route("/api/status")
def status(): return jsonify(google_connected=bool(load_creds()),authorized=session.get("email","").lower()==ALLOWED_EMAIL,email=session.get("email"),openai_configured=bool(os.getenv("OPENAI_API_KEY")))

@app.route("/api/dashboard")
@owner_required
def dashboard():
    out={"companies":[],"calendar":[],"raw_dashboard":[]}
    sheets=svc("sheets","v4"); sid=CONFIG["control_center_sheet_id"]
    vr=sheets.spreadsheets().values().batchGet(spreadsheetId=sid,ranges=["Dashboard!A1:H30","'Dossier Index'!A1:L100"]).execute().get("valueRanges",[])
    out["raw_dashboard"]=vr[0].get("values",[]) if vr else []
    ds=vr[1].get("values",[]) if len(vr)>1 else []
    if ds:
        h=ds[0]
        for r in ds[1:]:
            o={h[i]:r[i] if i<len(r) else "" for i in range(len(h))}
            if o.get("Organization"): out["companies"].append(o)
    cal=svc("calendar","v3"); now=datetime.now(timezone.utc)
    ev=cal.events().list(calendarId="primary",timeMin=now.isoformat(),timeMax=(now+timedelta(days=21)).isoformat(),singleEvents=True,orderBy="startTime",maxResults=50).execute().get("items",[])
    out["calendar"]=[{"summary":e.get("summary"),"start":e.get("start",{}).get("dateTime") or e.get("start",{}).get("date"),"htmlLink":e.get("htmlLink")} for e in ev if "Board" in ((e.get("summary") or "")+" "+(e.get("description") or ""))]
    return jsonify(out)

@app.route("/api/companies")
@owner_required
def companies():
    d=svc("drive","v3"); p=CONFIG["company_dossiers_folder_id"]
    return jsonify(d.files().list(q=f"'{p}' in parents and trashed=false",fields="files(id,name,mimeType,modifiedTime,webViewLink)",orderBy="name",pageSize=200).execute().get("files",[]))

@app.route("/api/sheet/<name>")
@owner_required
def sheet(name):
    allowed={"Pipeline","Relationships","Targets","Outreach Log","Content & Visibility","Weekly Review","Dossier Index","Distribution Queue"}
    if name not in allowed:return jsonify(error="Not allowed"),400
    s=svc("sheets","v4")
    return jsonify(s.spreadsheets().values().get(spreadsheetId=CONFIG["control_center_sheet_id"],range=f"'{name}'!A1:Z500").execute().get("values",[]))

@app.route("/api/ai",methods=["POST"])
@owner_required
def ai():
    if not os.getenv("OPENAI_API_KEY"): return jsonify(error="OPENAI_API_KEY not configured"),400
    from openai import OpenAI
    p=request.get_json(force=True); prompt=p.get("prompt",""); ctx=p.get("context","")
    c=OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    r=c.responses.create(model=os.getenv("OPENAI_MODEL","gpt-4.1-mini"),instructions="You are Jeff Barnes' private Board Command copilot. Be a board-search strategist, governance researcher, editor and critical-thinking partner. Distinguish facts from inference. Never leak private CRM data into public drafts unless explicitly asked.",input=f"CONTEXT:\n{ctx[:18000]}\n\nREQUEST:\n{prompt}")
    return jsonify(answer=r.output_text)

if __name__=="__main__": app.run("127.0.0.1",8787,debug=False)
