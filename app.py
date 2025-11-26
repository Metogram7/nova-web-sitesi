#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nova Web - Full Backend (Quart / Async) - Single file
Requirements (pip):
  pip install quart quart-cors aiohttp firebase-admin python-dotenv
Or for minimal email/testing:
  pip install quart quart-cors aiohttp python-dotenv
"""

import os
import json
import asyncio
import time
import traceback
from datetime import datetime
from typing import Optional

from quart import Quart, request, jsonify, send_file
from quart_cors import cors
from werkzeug.datastructures import FileStorage

import aiohttp
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Optional Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:
    firebase_admin = None
    credentials = None
    messaging = None

# Load .env if present (only for local dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------
# Configuration via ENV
# ---------------------------
# Required ENV variables you must set:
# GEMINI_API_KEY  -> Google Generative Language API key
# MAIL_ADRES      -> Gmail address for send-mail
# MAIL_SIFRE      -> Gmail app password
# PROJECT_URL     -> full URL of your deployed backend (for keep-alive), optional
# FIREBASE_CRED_PATH -> path to serviceAccountKey.json (optional)
# PORT            -> port to run (default 5000)

GEMINI_API_KEY = os.environ.get("AIzaSyD_ox8QNAHo-SEWmlROYMWM6GyMQmJkP4s")  # REQUIRED for AI
MAIL_ADRES = os.environ.get("MAIL_ADRES", "")
MAIL_SIFRE = os.environ.get("MAIL_SIFRE", "")
PROJECT_URL = os.environ.get("PROJECT_URL", "")
FIREBASE_CRED_PATH = os.environ.get("FIREBASE_CRED_PATH", "serviceAccountKey.json")
PORT = int(os.environ.get("PORT", 5000))

# ---------------------------
# Files and locks
# ---------------------------
HISTORY_FILE = os.environ.get("HISTORY_FILE", "chat_history.json")
LAST_SEEN_FILE = os.environ.get("LAST_SEEN_FILE", "last_seen.json")
CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")
TOKENS_FILE = os.environ.get("TOKENS_FILE", "tokens.json")
UPLOADS_DIR = os.environ.get("UPLOADS_DIR", "uploads")

# Ensure files exist and shapes
def ensure_json_file(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

ensure_json_file(HISTORY_FILE, {})
ensure_json_file(LAST_SEEN_FILE, {})
ensure_json_file(CACHE_FILE, {})
ensure_json_file(TOKENS_FILE, [])

history_lock = asyncio.Lock()
last_seen_lock = asyncio.Lock()
cache_lock = asyncio.Lock()
tokens_lock = asyncio.Lock()

# ---------------------------
# Firebase init (optional)
# ---------------------------
firebase_app = None
if firebase_admin and os.path.exists(FIREBASE_CRED_PATH):
    try:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        firebase_app = firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized.")
    except Exception as e:
        print("⚠ Firebase init error:", e)
else:
    if not firebase_admin:
        print("⚠ firebase_admin not installed - push notifications disabled.")
    else:
        print("⚠ Firebase credential file not found - push notifications disabled.")

# ---------------------------
# Quart app
# ---------------------------
app = Quart(__name__)
app = cors(app)  # enable CORS for all origins (adjust in production)

session: Optional[aiohttp.ClientSession] = None

# ---------------------------
# System prompt (full) - you asked no shortening
# NOTE: keep sensitive personal data out of prompts when possible.
# ---------------------------
nova_datetime = datetime.utcnow()

def get_system_prompt():
    # This is intentionally detailed (you asked no shorten).
    # You may replace or parametrize this.
    return f"""
Sen Nova adında çok yönlü bir yapay zekâ asistansın.
Seni Metehan Akkaya geliştirdi.
Diller: Python, HTML, CSS, JavaScript.
Kişilik: Sakin, dostça, esprili.
Güncel tarih (simülasyon): {nova_datetime.strftime('%d %B %Y %H:%M')}
NOT: Bu prompt sunucu tarafında tutuluyor. Gizli anahtarları burada yazma.
"""

# ---------------------------
# Helper JSON I/O (async)
# ---------------------------
async def load_json(file_path, lock, default=None):
    if default is None:
        default = {} if file_path != TOKENS_FILE else []
    async with lock:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

async def save_json(file_path, data, lock):
    async with lock:
        tmp = file_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)

# ---------------------------
# Startup & cleanup
# ---------------------------
@app.before_serving
async def startup():
    global session
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    session = aiohttp.ClientSession(timeout=timeout)
    # Start keep-alive and inactive tasks
    asyncio.create_task(keep_alive_task())
    asyncio.create_task(check_inactive_users())

@app.after_serving
async def cleanup():
    global session
    if session:
        await session.close()

# ---------------------------
# Keep-alive (prevent Render sleeping)
# ---------------------------
async def keep_alive_task():
    # If PROJECT_URL blank, skip
    while True:
        try:
            if PROJECT_URL:
                async with session.get(PROJECT_URL, timeout=10) as r:
                    print(f"Keep-alive {PROJECT_URL} -> {r.status}")
        except Exception as e:
            print("Keep-alive error:", e)
        await asyncio.sleep(60 * 5)  # 5 minutes

# ---------------------------
# Check inactive users placeholder
# ---------------------------
async def check_inactive_users():
    while True:
        # placeholder: run hourly
        await asyncio.sleep(3600)

# ---------------------------
# Gemini API call (uses GEMINI_API_KEY from env)
# ---------------------------
async def call_gemini_api(message: str, conversation: list):
    """
    Calls Generative Language API (Gemini). Expects GEMINI_API_KEY env var set.
    Returns text or raises Exception.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

    # Build contents from conversation (keep last 10)
    contents = []
    for msg in conversation[-10:]:
        role = "user" if msg.get("sender") == "user" else "model"
        text = msg.get("text") or msg.get("content") or ""
        contents.append({"role": role, "parts": [{"text": str(text)}]})

    contents.append({"role": "user", "parts": [{"text": message}]})

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": get_system_prompt()}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    try:
        async with session.post(API_URL, headers=headers, json=payload, timeout=30) as resp:
            text = await resp.text()
            if resp.status != 200:
                # include status + body for diagnostics
                raise RuntimeError(f"Gemini API error {resp.status}: {text}")
            data = await resp.json()
            # navigate response safely
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                raise RuntimeError(f"Unexpected Gemini response structure: {data}")
    except Exception as e:
        raise

# ---------------------------
# Routes
# ---------------------------

@app.route("/")
async def home():
    return jsonify({"status": "Nova Web Online", "time": datetime.utcnow().isoformat()})

# Chat endpoint
@app.route("/api/chat", methods=["POST"])
async def api_chat():
    try:
        data = await request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    userId = data.get("userId", "anon")
    chatId = data.get("currentChat", "default")
    message = (data.get("message") or "").strip()
    userInfo = data.get("userInfo", {})

    if not message:
        return jsonify({"response": "Lütfen bir şeyler yaz."}), 400

    # load history
    hist = await load_json(HISTORY_FILE, history_lock, default={})
    user_hist = hist.setdefault(userId, {}).setdefault(chatId, [])

    # append user message
    user_hist.append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})

    # call gemini
    try:
        reply = await call_gemini_api(message, user_hist)
    except Exception as e:
        # save minimal error reply and log
        traceback.print_exc()
        reply = "Sunucularda bir hata oluştu: " + str(e)

    user_hist.append({"sender": "nova", "text": reply, "ts": datetime.utcnow().isoformat()})

    # trim history to last N messages per chat to avoid file bloat
    MAX_MESSAGES_PER_CHAT = 50
    if len(user_hist) > MAX_MESSAGES_PER_CHAT:
        user_hist[:] = user_hist[-MAX_MESSAGES_PER_CHAT:]

    # save history & caches
    await save_json(HISTORY_FILE, hist, history_lock)
    cache = await load_json(CACHE_FILE, cache_lock, default={})
    cache_key = f"{userId}:{message.lower()}"[:120]
    cache[cache_key] = {"response": reply, "ts": time.time()}
    await save_json(CACHE_FILE, cache, cache_lock)

    # update last seen
    last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock, default={})
    last_seen[userId] = datetime.utcnow().isoformat()
    await save_json(LAST_SEEN_FILE, last_seen, last_seen_lock)

    return jsonify({"response": reply, "cached": False})

# History endpoints (GET & POST)
@app.route("/api/history", methods=["GET"])
async def api_history_get():
    uid = request.args.get("userId", "anon")
    hist = await load_json(HISTORY_FILE, history_lock, default={})
    return jsonify(hist.get(uid, {}))

@app.route("/api/history", methods=["POST"])
async def api_history_post():
    try:
        data = await request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    uid = data.get("userId", "anon")
    chatId = data.get("chatId", "default")
    message = data.get("message")
    if not message:
        return jsonify({"error": "no_message"}), 400

    hist = await load_json(HISTORY_FILE, history_lock, default={})
    u = hist.setdefault(uid, {}).setdefault(chatId, [])
    u.append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)
    return jsonify({"success": True})

# Delete chat
@app.route("/api/delete_chat", methods=["POST"])
async def api_delete_chat():
    try:
        data = await request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    uid = data.get("userId")
    cid = data.get("chatId")
    if not uid or not cid:
        return jsonify({"success": False, "error": "missing"}), 400

    hist = await load_json(HISTORY_FILE, history_lock, default={})
    if uid in hist and cid in hist[uid]:
        del hist[uid][cid]
        await save_json(HISTORY_FILE, hist, history_lock)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "not_found"}), 404

# send-mail
@app.route("/send-mail", methods=["POST"])
async def api_send_mail():
    form = await request.form
    files = await request.files

    username = form.get("username", "Anonim")
    message_text = form.get("message", "")
    email = form.get("user_email", "")

    if not message_text:
        return jsonify({"status": "Mesaj boş olamaz"}), 400

    if not MAIL_ADRES or not MAIL_SIFRE:
        return jsonify({"status": "Mail yapılandırılmamış"}), 500

    msg = MIMEMultipart()
    msg["Subject"] = f"Nova Bildirim: {username}"
    msg["From"] = MAIL_ADRES
    msg["To"] = MAIL_ADRES
    msg.attach(MIMEText(f"Kimden: {username} ({email})\n\n{message_text}", "plain", "utf-8"))

    uploaded_file = files.get("photo")
    if uploaded_file and uploaded_file.filename:
        try:
            file_bytes = uploaded_file.read()
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{uploaded_file.filename}"')
            msg.attach(part)
        except Exception as e:
            return jsonify({"status": f"Dosya eklenemedi: {e}"}), 500

    # send email (sync in thread)
    def _send():
        s = smtplib.SMTP("smtp.gmail.com", 587)
        s.starttls()
        s.login(MAIL_ADRES, MAIL_SIFRE)
        s.sendmail(MAIL_ADRES, MAIL_ADRES, msg.as_string())
        s.quit()

    try:
        await asyncio.to_thread(_send)
        return jsonify({"status": "İletildi"})
    except Exception as e:
        return jsonify({"status": f"E-posta gönderilemedi: {e}"}), 500

# subscribe token
@app.route("/api/subscribe", methods=["POST"])
async def api_subscribe():
    data = await request.get_json(force=True)
    token = data.get("token")
    if not token:
        return jsonify({"error": "Token yok"}), 400
    tokens = await load_json(TOKENS_FILE, tokens_lock, default=[])
    if token not in tokens:
        tokens.append(token)
        await save_json(TOKENS_FILE, tokens, tokens_lock)
    return jsonify({"success": True})

# admin broadcast (simple password check)
@app.route("/api/admin/broadcast", methods=["POST"])
async def api_admin_broadcast():
    try:
        data = await request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    pwd = data.get("password")
    if pwd != os.environ.get("ADMIN_BROADCAST_PASSWORD", "sd157metehanak"):
        return jsonify({"error": "Yetkisiz"}), 403

    message = data.get("message", "")
    tokens = await load_json(TOKENS_FILE, tokens_lock, default=[])
    if not tokens:
        return jsonify({"error": "Kullanıcı yok"}), 404

    # schedule background worker
    asyncio.create_task(broadcast_worker(tokens, message))
    return jsonify({"status": "Gönderim başlatıldı."})

async def broadcast_worker(tokens, message_data):
    if not firebase_app:
        print("Firebase yok, broadcast atlanıyor.")
        return
    chunk_size = 400
    for i in range(0, len(tokens), chunk_size):
        chunk = tokens[i:i+chunk_size]
        try:
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title="Nova", body=message_data),
                tokens=chunk
            )
            resp = await asyncio.to_thread(messaging.send_multicast, msg)
            print("Broadcast:", getattr(resp, "success_count", None))
        except Exception as e:
            print("Broadcast hata:", e)
        await asyncio.sleep(0.5)

# file upload
@app.route("/upload", methods=["POST"])
async def api_upload():
    try:
        files = await request.files
        file: FileStorage = files.get("file")
        if not file:
            return jsonify({"error": "Dosya yok"}), 400
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        path = os.path.join(UPLOADS_DIR, file.filename)
        file.save(path)
        return jsonify({"success": True, "path": path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/uploads/<path:filename>", methods=["GET"])
async def serve_uploaded(filename):
    p = os.path.join(UPLOADS_DIR, filename)
    if not os.path.exists(p):
        return jsonify({"error": "Bulunamadı"}), 404
    return await send_file(p)

# health
@app.route("/healthz", methods=["GET"])
async def healthz():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    print(f"Starting Nova backend on 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
