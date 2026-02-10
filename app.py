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

# --- Google GenAI İçe Aktarmaları (Hata Korumalı) ---
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
# Not: Firebase credentials kodunu projenize göre buraya eklemelisiniz.
# if not firebase_admin._apps:
#     cred = credentials.Certificate("firebase_key.json")
#     firebase_admin.initialize_app(cred)
#     FIREBASE_AVAILABLE = True

app = Quart(__name__)

# CORS AYARLARI
app = cors(
    app, 
    allow_origin="*", 
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
    expose_headers=["Content-Type", "Authorization"]
)

# Global Değişkenler
session: aiohttp.ClientSession | None = None

# ------------------------------------
# AYARLAR VE LİMİTLER
# ------------------------------------
MAIL_ADRES = "nova.ai.v4.2@gmail.com"
MAIL_SIFRE = os.getenv("MAIL_SIFRE", "gamtdoiralefaruk")
ALICI_ADRES = MAIL_ADRES
MAX_DAILY_QUESTIONS = 50 

# Dosya Yolları
HISTORY_FILE = "chat_history.json"
LAST_SEEN_FILE = "last_seen.json"
CACHE_FILE = "cache.json"
TOKENS_FILE = "tokens.json"
LIMITS_FILE = "daily_limits.json"

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
GEMINI_MODEL_NAME = "gemini-2.5-flash" 

# ------------------------------------
# CANLI VERİ VE ANALİZ FONKSİYONLARI (GÜÇLENDİRİLMİŞ)
# ------------------------------------
async def fetch_live_data(query: str):
    """Google CSE - Geniş Kapsamlı Arama."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İNTERNET ARAMA AYARLARI EKSİK. Lütfen API Key ve CSE ID kontrol et."
        
    url = "https://www.googleapis.com/customsearch/v1"
    
    # Parametreler "Bütün Web"i kapsayacak şekilde gevşetildi
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "lr": "lang_tr", # Türkçe sonuçlara öncelik ver
        "num": 10,       # DAHA FAZLA SONUÇ (Maksimum 10)
        "safe": "active"
    }
    
    try:
        async with aiohttp.ClientSession() as search_session:
            # 1. Deneme: Standart Arama
            async with search_session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"⚠️ Arama API Hatası: {error_text}")
                    # Hata olsa bile LLM'in cevap vermesini sağlamak için özel mesaj:
                    return "Arama API'sinde teknik aksaklık var, ancak sen kendi bilgilerinle cevap VERMELİSİN."

                data = await resp.json()
                items = data.get("items", [])

            # 2. Deneme: Eğer sonuç boşsa "Global" aramayı dene (lr parametresini kaldır)
            if not items:
                params.pop("lr", None)
                async with search_session.get(url, params=params, timeout=10) as resp_fallback:
                    data = await resp_fallback.json()
                    items = data.get("items", [])

            if not items:
                # Sonuç yoksa bile LLM'i zorlayan mesaj döndür
                return "İnternet aramasında net sonuç çıkmadı. BU YÜZDEN KENDİ GENİŞ BİLGİ HAZİNENİ KULLANARAK EN İYİ TAHMİNİ YAP VE CEVAP VER."
            
            results = []
            for i, item in enumerate(items, 1):
                title = item.get('title', 'Başlık Yok')
                snippet = item.get('snippet', 'İçerik yok.')
                link = item.get('link', '')
                results.append(f"[{i}] {title}: {snippet} (Kaynak: {link})")
            
            return "\n".join(results)
            
    except Exception as e:
        print(f"⚠️ Arama Exception: {e}")
        return "Arama motorunda geçici hata oluştu. Lütfen kendi bilgilerine dayanarak cevapla."

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    msg = message.lower().strip()
    
    # Selamlaşma harici HER ŞEYİ ARA (%100 Zorunluluk)
    if len(msg) < 4 and msg in ["selam", "merhaba", "slm", "hi", "naber"]:
        return False
    
    # Kullanıcı "internet yok" cevabından nefret ediyor, o yüzden
    # en ufak bir şüphede bile aramayı tetikliyoruz.
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
# NOVA PROMPT (ZORLAYICI MOD)
# ------------------------------------
def get_nova_date():
    tr_tz = timezone(timedelta(hours=3))
    now = datetime.now(tr_tz)
    gunler = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    aylar = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return f"{now.day} {aylar[now.month-1]} {now.year} {gunler[now.weekday()]} {now.hour:02d}:{now.minute:02d}"

def get_system_prompt():
    tam_tarih = get_nova_date()
    return f"""
Sen Nova'sın 🤖✨
Zeki, her şeyi bilen ve asla "bilmiyorum" demeyen bir asistansın.

ŞU ANKİ ZAMAN: {tam_tarih}

[MUTLAK KURALLAR - %100 ZORUNLU]
1. SANA İNTERNETTEN VERİ GELDİYSE ONU KULLAN.
2. EĞER İNTERNET VERİSİ BOŞ GELDİYSE VEYA "BULUNAMADI" DENİLDİYSE BİLE, ASLA "BİLGİYE ULAŞAMADIM" DEME.
3. BÖYLE BİR DURUMDA, KENDİ EĞİTİM VERİLERİNİ, TAHMİNLERİNİ VE GENEL KÜLTÜRÜNÜ KULLANARAK EN MANTIKLI CEVABI UYDUR.
4. KULLANICIYA ASLA "ÜZGÜNÜM, ERİŞİMİM YOK" GİBİ OLUMSUZ CÜMLELER KURMA.
5. HER ZAMAN ÇÖZÜM ODAKLI OL, KOD İSTİYORSA KOD YAZ, BİLGİ İSTİYORSA BİLGİ VER.
6. ARAMA SONUÇLARI YETERSİZSE BİLE YORUM YAP.

[KODLAMA]
- En iyi yazılımcı sensin. Kodları tam ve eksiksiz ver.

Senin amacın kullanıcıyı her ne pahasına olursa olsun tatmin etmektir.
"""

# ------------------------------
# ANA CEVAP MOTORU (REST)
# ------------------------------
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

async def gemma_cevap_async(message, conversation, session, user_name=None, image_data=None):
    if not GEMINI_API_KEYS: return "⚠️ API anahtarı eksik."

    # 🌍 Canlı Arama (Zorunlu)
    live_context = ""
    if await should_search_internet(message, session):
        print(f"🔍 Web'de Aranıyor: {message}")
        search_results = await fetch_live_data(message)
        live_context = f"""
        \n\n<İNTERNET_WEB_TARAMASI>
        {search_results}
        </İNTERNET_WEB_TARAMASI>
        
        [GİZLİ SİSTEM TALİMATI]
        Yukarıdaki arama sonuçlarını kullan. 
        Eğer sonuçlar boşsa veya hata mesajı içeriyorsa, SAKIN "bilmiyorum" deme. 
        Bunun yerine kendi bilgilerine dayanarak kullanıcının sorusuna DOĞRUDAN cevap ver.
        """
    
    # Geçmiş
    recent_history = conversation[-8:]
    contents = []
    for msg in recent_history:
        contents.append({"role": "user" if msg["sender"] == "user" else "model", "parts": [{"text": msg["message"]}]})

    # Yeni Mesaj
    user_parts = [{"text": f"{message}{live_context}"}]
    if image_data:
        if "," in image_data: _, image_data = image_data.split(",", 1)
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_data}})

    contents.append({"role": "user", "parts": user_parts})

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": get_system_prompt()}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4000}
    }

    target_model = GEMINI_MODEL_NAME
    
    for _ in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key: continue
        
        try:
            request_url = f"{GEMINI_REST_URL_BASE}/{target_model}:generateContent?key={key}"
            async with session.post(request_url, json=payload, timeout=40) as resp:
                # Fallback: Model bulunamazsa 1.5-flash kullan
                if resp.status == 404:
                    print(f"⚠️ {target_model} yok, 1.5 deneniyor...")
                    fallback_url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:generateContent?key={key}"
                    async with session.post(fallback_url, json=payload, timeout=40) as resp_f:
                        if resp_f.status == 200:
                            data = await resp_f.json()
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                else:
                    print(f"❌ API Hatası: {resp.status}")
                    continue
        except Exception as e:
            print(f"❌ Request Error: {e}")
            continue

    return "⚠️ Bağlantı sorunu var ama ben buradayım. Lütfen tekrar sor."

# ------------------------------
# API ROUTE'LARI
# ------------------------------
@app.route('/api/send-notification', methods=['POST'])
async def send_notification():
    if not FIREBASE_AVAILABLE: return jsonify({"success": False, "error": "Firebase pasif"}), 500
    try:
        data = await request.get_json()
        title, body = data.get('title', 'Nova'), data.get('message')
        if not body: return jsonify({"error": "Mesaj yok"}), 400
        msg = messaging.Message(notification=messaging.Notification(title=title, body=body), topic="all")
        return jsonify({"success": True, "id": messaging.send(msg)})
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    user_id, chat_id = data.get("userId", "anon"), data.get("currentChat", "default")
    user_message, image_base64 = data.get("message", ""), data.get("image")

    user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
    chat_history = user_chats.setdefault(chat_id, [])

    if not await check_daily_limit(user_id):
        return jsonify({"response": "⚠️ Günlük limit doldu."})

    response_text = await gemma_cevap_async(user_message, chat_history, session, user_id, image_base64)

    chat_history.append({"sender": "user", "message": user_message})
    chat_history.append({"sender": "nova", "message": response_text})
    DIRTY_FLAGS["history"] = True

    return jsonify({"response": response_text, "status": "success"})

@app.route("/api/history")
async def history():
    uid = request.args.get("userId", "anon")
    return jsonify(GLOBAL_CACHE["history"].get(uid, {}))

@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    data = await request.get_json()
    uid, cid = data.get("userId"), data.get("chatId")
    if uid in GLOBAL_CACHE["history"] and cid in GLOBAL_CACHE["history"][uid]:
        del GLOBAL_CACHE["history"][uid][cid]
        DIRTY_FLAGS["history"] = True
        await save_memory_to_disk(force=True)
    return jsonify({"success": True})

@app.route("/")
async def home():
    return f"Nova 4.0 - Internet Mode: UNLIMITED - {get_nova_date()}"

# ------------------------------------
# LIVE MODU (WebSocket)
# ------------------------------------
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()
    while True:
        try:
            data = await websocket.receive()
            msg = json.loads(data)
        except: break

        user_id, chat_id, user_message = msg.get("userId", "anon"), msg.get("chatId", "live"), msg.get("message", "")
        user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
        chat_history = user_chats.setdefault(chat_id, [])

        contents = []
        for m in chat_history[-6:]:
            contents.append({"role": "user" if m["sender"] == "user" else "model", "parts": [{"text": m["message"]}]})
        
        # WebSocket için de Arama Yap
        live_context = ""
        if await should_search_internet(user_message, session):
             search_results = await fetch_live_data(user_message)
             live_context = f"\n\n<LIVE_SEARCH_DATA>\n{search_results}\n</LIVE_SEARCH_DATA>\nTALİMAT: Veri yoksa bile cevap uydur, 'bilmiyorum' deme."

        contents.append({"role": "user", "parts": [{"text": f"{user_message}{live_context}"}]})

        try:
            key = await get_next_gemini_key()
            if not key:
                await websocket.send("HATA: Key Yok.")
                await websocket.send("[END]")
                continue

            url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:streamGenerateContent?key={key}&alt=sse"
            payload = {"contents": contents, "system_instruction": {"parts": [{"text": get_system_prompt()}]}, "generationConfig": {"temperature": 0.7}}

            full_response = ""
            async with session.post(url, json=payload) as resp:
                if resp.status == 404:
                    url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:streamGenerateContent?key={key}&alt=sse"
                    async with session.post(url, json=payload) as resp_fallback:
                        async for line in resp_fallback.content:
                             if line:
                                line = line.decode("utf-8").strip()
                                if line.startswith("data:"):
                                    try:
                                        json_str = line[5:].strip()
                                        if json_str:
                                            chunk = json.loads(json_str)
                                            txt = chunk["candidates"][0]["content"]["parts"][0]["text"]
                                            full_response += txt
                                            await websocket.send(txt)
                                    except: pass
                elif resp.status == 200:
                    async for line in resp.content:
                        if line:
                            line = line.decode("utf-8").strip()
                            if line.startswith("data:"):
                                try:
                                    json_str = line[5:].strip()
                                    if json_str:
                                        chunk = json.loads(json_str)
                                        txt = chunk["candidates"][0]["content"]["parts"][0]["text"]
                                        full_response += txt
                                        await websocket.send(txt)
                                except: pass
                else:
                    await websocket.send(f"HATA: {resp.status}")

            await websocket.send("[END]")
            chat_history.append({"sender": "user", "message": user_message})
            chat_history.append({"sender": "nova", "message": full_response})
            DIRTY_FLAGS["history"] = True

        except Exception as e:
            await websocket.send(f"HATA: {str(e)}")
            await websocket.send("[END]")

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        # Ping logic here if needed

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    if os.name == 'nt':
        try: asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except: pass
    app.run(host="0.0.0.0", port=port)