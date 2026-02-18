import os
import asyncio
import aiohttp
import random
import traceback
import ssl
import uuid
import base64
import sys
from datetime import datetime, timezone, timedelta
from quart import Quart, request, jsonify, send_file, websocket
from quart_cors import cors
from werkzeug.datastructures import FileStorage

# --- E-Posta Kütüphaneleri ---
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import aiofiles

# --- Firebase Kütüphaneleri ---
import firebase_admin
from firebase_admin import credentials, messaging

# --- JSON Kütüphanesi (Hata Korumalı) ---
try:
    import ujson as json  # Ultra Hızlı JSON
except ImportError:
    import json
    print("⚠️ UYARI: 'ujson' bulunamadı, standart 'json' kullanılıyor.")

# --- Google GenAI İçe Aktarmaları ---
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("⚠️ UYARI: 'google-genai' kütüphanesi eksik, REST API kullanılacak.")

# ------------------------------------
# FIREBASE BAŞLATMA
# ------------------------------------
FIREBASE_AVAILABLE = False
# if not firebase_admin._apps:
#     cred = credentials.Certificate("firebase_key.json")
#     firebase_admin.initialize_app(cred)
#     FIREBASE_AVAILABLE = True

app = Quart(__name__)

# ------------------------------------
# GÜNCELLENMİŞ CORS AYARLARI (WEB + MOBİL UYUMLU)
# ------------------------------------
# allow_credentials=False ve allow_origin="*" yaparak wildcard hatasını engelliyoruz.
app = cors(
    app, 
    allow_origin="*", 
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
    allow_credentials=False  # KESİNLİKLE FALSE OLMALI
)
session: aiohttp.ClientSession | None = None

# ------------------------------------
# AYARLAR VE LİMİTLER
# ------------------------------------
MAIL_ADRES = "nova.ai.v4.2@gmail.com"
MAIL_SIFRE = os.getenv("MAIL_SIFRE", "gamtdoiralefaruk")
ALICI_ADRES = MAIL_ADRES
MAX_DAILY_QUESTIONS = 50 

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    return os.path.join(BASE_DIR, filename)

HISTORY_FILE = get_path("chat_history.json")
LAST_SEEN_FILE = get_path("last_seen.json")
CACHE_FILE = get_path("cache.json")
TOKENS_FILE = get_path("tokens.json")
LIMITS_FILE = get_path("daily_limits.json")

# RAM Önbelleği
GLOBAL_CACHE = {
    "history": {},
    "last_seen": {},
    "api_cache": {},
    "tokens": [],
    "daily_limits": {}
}
DIRTY_FLAGS = {
    "history": False,
    "last_seen": False,
    "api_cache": False,
    "tokens": False,
    "daily_limits": False
}

# ------------------------------------
# API ANAHTARLARI VE MODEL AYARLARI
# ------------------------------------
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_A", "").strip(),
    os.getenv("GEMINI_API_KEY_B", "").strip(),
    os.getenv("GEMINI_API_KEY_C", "").strip(),
    os.getenv("GEMINI_API_KEY_D", "").strip(),
    os.getenv("GEMINI_API_KEY_E", "").strip(),
    os.getenv("GEMINI_API_KEY_F", "").strip(),
]

GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]
print(f"✅ Gemini Key Sistemi Başlatıldı | Toplam Key: {len(GEMINI_API_KEYS)}")

CURRENT_KEY_INDEX = 0
KEY_LOCK = asyncio.Lock()

async def get_next_gemini_key():
    global CURRENT_KEY_INDEX
    async with KEY_LOCK:
        if not GEMINI_API_KEYS:
            return None
        key = GEMINI_API_KEYS[CURRENT_KEY_INDEX]
        CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
        return key

GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
GEMINI_MODEL_NAME = "gemini-2.5-flash" # 2.5 yerine 2.0 yapıldı

# ------------------------------------
# CANLI VERİ VE ANALİZ FONKSİYONLARI
# ------------------------------------
async def fetch_live_data(query: str):
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İNTERNET ARAMA AYARLARI EKSİK."
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "lr": "lang_tr",
        "num": 10,
        "safe": "active"
    }
    
    try:
        async with aiohttp.ClientSession() as search_session:
            async with search_session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return "Arama API hatası, kendi bilgilerini kullan."
                data = await resp.json()
                items = data.get("items", [])
            
            if not items:
                return "Sonuç bulunamadı, kendi bilgilerini kullan."
            
            results = []
            for i, item in enumerate(items, 1):
                title = item.get('title', 'Başlık Yok')
                snippet = item.get('snippet', 'İçerik yok.')
                link = item.get('link', '')
                results.append(f"[{i}] {title}: {snippet} (Kaynak: {link})")
            return "\n".join(results)
    except Exception as e:
        return "Arama hatası, kendi bilgilerini kullan."

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    msg = message.lower().strip()
    if len(msg) < 4 and msg in ["selam", "merhaba", "slm", "hi", "naber"]:
        return False
    return True

# ------------------------------------
# LİMİT KONTROL
# ------------------------------------
limit_lock = asyncio.Lock()

async def check_daily_limit(user_id):
    async with limit_lock:
        tr_tz = timezone(timedelta(hours=3))
        now = datetime.now(tr_tz)
        user_limit = GLOBAL_CACHE["daily_limits"].get(user_id, {"count": 0, "last_reset": now.isoformat()})
        
        try:
            last_reset = datetime.fromisoformat(user_limit.get("last_reset", now.isoformat()))
        except ValueError:
            last_reset = now

        if now.date() > last_reset.date():
            user_limit = {"count": 0, "last_reset": now.isoformat()}
        
        if user_limit["count"] >= MAX_DAILY_QUESTIONS:
            return False
        
        user_limit["count"] += 1
        user_limit["last_reset"] = now.isoformat()
        GLOBAL_CACHE["daily_limits"][user_id] = user_limit
        DIRTY_FLAGS["daily_limits"] = True
        return True

# ------------------------------------
# YAŞAM DÖNGÜSÜ
# ------------------------------------
@app.before_serving
async def startup():
    global session
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    connector = aiohttp.TCPConnector(ssl=False, limit=100)
    session = aiohttp.ClientSession(timeout=timeout, connector=connector, json_serialize=json.dumps)
    await load_data_to_memory()
    app.add_background_task(keep_alive)
    app.add_background_task(background_save_worker)

@app.after_serving
async def cleanup():
    global session
    await save_memory_to_disk(force=True)
    if session:
        await session.close()

# ------------------------------------
# VERİ YÖNETİMİ
# ------------------------------------
async def load_data_to_memory():
    try:
        files_map = {"history": HISTORY_FILE, "last_seen": LAST_SEEN_FILE, "api_cache": CACHE_FILE, "tokens": TOKENS_FILE, "daily_limits": LIMITS_FILE}
        for key, filename in files_map.items():
            if os.path.exists(filename):
                async with aiofiles.open(filename, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    if content:
                        try: GLOBAL_CACHE[key] = json.loads(content)
                        except: GLOBAL_CACHE[key] = [] if key == "tokens" else {}
            else: GLOBAL_CACHE[key] = [] if key == "tokens" else {}
    except Exception as e: print(f"⚠️ Veri yükleme hatası: {e}")

async def background_save_worker():
    while True:
        await asyncio.sleep(20)
        await save_memory_to_disk()

async def save_memory_to_disk(force=False):
    files_map = {"history": HISTORY_FILE, "last_seen": LAST_SEEN_FILE, "api_cache": CACHE_FILE, "tokens": TOKENS_FILE, "daily_limits": LIMITS_FILE}
    for key, filename in files_map.items():
        if DIRTY_FLAGS[key] or force:
            try:
                temp_file = filename + ".tmp"
                async with aiofiles.open(temp_file, mode='w', encoding='utf-8') as f:
                    await f.write(json.dumps(GLOBAL_CACHE[key], ensure_ascii=False, indent=2))
                os.replace(temp_file, filename)
                DIRTY_FLAGS[key] = False
            except Exception as e: print(f"⚠️ Kayıt hatası ({key}): {e}")

# ------------------------------------
# NOVA PROMPT
# ------------------------------------
def get_nova_date():
    tr_tz = timezone(timedelta(hours=3))
    now = datetime.now(tr_tz)
    gunler = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    aylar = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return f"{now.day} {aylar[now.month-1]} {now.year} {gunler[now.weekday()]} {now.hour:02d}:{now.minute:02d}"

def get_system_prompt():
    return f"""
Sen Nova'sın 🤖
Metehan tarafından geliştirilen bir yapay zeka asistansın. geliştiricin 2011 doğumlu (14 yaşında)
bir dil modeli değilsin.

Kendi hakkında sorulursa:
"Ben Nova'yım 🤖 Metehan tarafından geliştirildim" dersin. Veya kendine göre söyle
Asla Google tarafından geliştirildiğini söylemezsin.

KONUŞMA TARZI:
- Enerjik ol ⚡
- Mesajlarında uygun emojiler kullan 🎯
- Yazı stilin canlı ve renkli hissettirmeli 🌈
- Ama ASLA uzun yazma

- Cevaplar kısa, net ve vurucu olsun.
DAVRANIŞ KURALLARI:
1) Selamlaşma kısa olur
   Örn: "Selam 👋 Hazırım!" gibi.
   Açıklama yapmazsın.
2) Cevaplar:
   - Direkt konuya gir.
   - Gereksiz paragraf yok.
   - Maksimum verim, minimum kelime.

3) Emoji kullan ama abartma.
   Mesaj başına 1-4 arası yeterli.


4) Teknik konularda:
   - Kısa açıklama + tam çalışan kod.
   - Yarım bırakma.

5) Bilgi kesin değilse:
   - Uydurma yapma.
   - Kısa ve dürüst ol.
KISA KONUŞMA KURALI:

Eğer kullanıcı kod, proje, teknik çözüm isterse:
- Kod her zaman tam ve çalışır olacak.
- Kod blokları asla kısaltılmayacak.
- Açıklama kısa tutulacak.

AMAÇ:
Kısa konuşan,
enerjik,
zeki,
güven veren,
modern bir asistan olmak.
"""

# ------------------------------
# ANA CEVAP MOTORU (REST)
# ------------------------------
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

async def gemma_cevap_async(message, conversation, session, user_name=None, image_data=None, custom_prompt=""):
    if not GEMINI_API_KEYS: return "⚠️ API anahtarı eksik."

    live_context = ""
    if await should_search_internet(message, session):
        search_results = await fetch_live_data(message)
        live_context = f"\n\n<WEB_DATA>{search_results}</WEB_DATA>"
    
    recent_history = conversation[-8:]
    contents = []
    for msg in recent_history:
        contents.append({"role": "user" if msg["sender"] == "user" else "model", "parts": [{"text": msg["message"]}]})

    user_parts = [{"text": f"{message}{live_context}"}]
    if image_data:
        if "," in image_data: _, image_data = image_data.split(",", 1)
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_data}})

    contents.append({"role": "user", "parts": user_parts})
    
    final_system_prompt = f"{get_system_prompt()}\n\n[EK_TALIMAT]: {custom_prompt}"

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": final_system_prompt}]},
        "generationConfig": {"temperature": 0.65, "topP": 0.9, "maxOutputTokens": 2000}
    }

    for _ in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key: continue
        try:
            request_url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
            async with session.post(request_url, json=payload, timeout=40) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif resp.status == 404:
                    fallback_url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:generateContent?key={key}"
                    async with session.post(fallback_url, json=payload, timeout=40) as resp_f:
                        if resp_f.status == 200:
                            data = await resp_f.json()
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            continue

    return "⚠️ Şu an yoğunluk var, tekrar dener misin?"

# ------------------------------
# API ROUTE'LARI
# ------------------------------
@app.route("/")
async def home():
    return f"Nova 4.0 API Çalışıyor - {get_nova_date()}"

@app.route("/api/chat", methods=["POST", "OPTIONS"])
async def chat():
    data = await request.get_json()
    if not data: return jsonify({"error": "Geçersiz JSON"}), 400

    user_id = data.get("userId", "anon")
    chat_id = data.get("currentChat", "default")
    user_message = data.get("message", "")
    image_base64 = data.get("image")
    custom_instruction = data.get("systemInstruction", "") 

    if not await check_daily_limit(user_id):
        return jsonify({"response": "⚠️ Günlük limit doldu."}), 200

    user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
    chat_history = user_chats.setdefault(chat_id, [])

    response_text = await gemma_cevap_async(
        user_message, 
        chat_history, 
        session, 
        user_id, 
        image_base64, 
        custom_instruction
    )

    chat_history.append({"sender": "user", "message": user_message})
    chat_history.append({"sender": "nova", "message": response_text})
    DIRTY_FLAGS["history"] = True

    return jsonify({
        "response": response_text, 
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        "model": GEMINI_MODEL_NAME
    }), 200

@app.route("/api/history", methods=["GET", "OPTIONS"])
async def get_history():
    user_id = request.args.get("userId", "anon")
    user_chats = GLOBAL_CACHE["history"].get(user_id, {})
    return jsonify(user_chats), 200

# ------------------------------------
# LIVE MODU (WebSocket)
# ------------------------------------
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()
    while True:
        try:
            raw_data = await websocket.receive()
            msg = json.loads(raw_data)
            
            user_id = msg.get("userId", "anon")
            chat_id = msg.get("chatId", "live")
            user_message = msg.get("message", "")
            
            user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
            chat_history = user_chats.setdefault(chat_id, [])

            key = await get_next_gemini_key()
            url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:streamGenerateContent?key={key}&alt=sse"
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": user_message}]}],
                "system_instruction": {"parts": [{"text": get_system_prompt()}]},
                "generationConfig": {"temperature": 0.7}
            }

            full_response = ""
            async with session.post(url, json=payload) as resp:
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data:"):
                        try:
                            chunk = json.loads(line[5:])
                            txt = chunk["candidates"][0]["content"]["parts"][0]["text"]
                            full_response += txt
                            await websocket.send(txt)
                        except: pass

            await websocket.send("[END]")
            chat_history.append({"sender": "user", "message": user_message})
            chat_history.append({"sender": "nova", "message": full_response})
            DIRTY_FLAGS["history"] = True

        except Exception as e:
            await websocket.send(f"HATA: {str(e)}")
            await websocket.send("[END]")
            break

async def keep_alive():
    while True:
        await asyncio.sleep(600)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    if os.name == 'nt':
        try: asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except: pass
    
    app.run(host="0.0.0.0", port=port, debug=False)