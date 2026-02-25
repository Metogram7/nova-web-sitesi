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
app = cors(
    app, 
    allow_origin="*", 
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
    allow_credentials=False 
)
session: aiohttp.ClientSession | None = None

# ------------------------------------
# AYARLAR VE LİMİTLER
# ------------------------------------
FREE_LIMIT = 50
PLUS_LIMIT = 140

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    return os.path.join(BASE_DIR, filename)

HISTORY_FILE = get_path("chat_history.json")
LAST_SEEN_FILE = get_path("last_seen.json")
CACHE_FILE = get_path("cache.json")
TOKENS_FILE = get_path("tokens.json")
LIMITS_FILE = get_path("daily_limits.json")
SUBSCRIPTIONS_FILE = get_path("subscriptions.json")

# RAM Önbelleği
GLOBAL_CACHE = {
    "history": {},
    "last_seen": {},
    "api_cache": {},
    "tokens": [],
    "daily_limits": {},
    "subscriptions": {}
}
DIRTY_FLAGS = {
    "history": False,
    "last_seen": False,
    "api_cache": False,
    "tokens": False,
    "daily_limits": False,
    "subscriptions": False
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
# CANLI VERİ VE ANALİZ FONKSİYONLARI
# ------------------------------------
async def fetch_live_data(query: str):
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İNTERNET ARAMA AYARLARI EKSİK."
        
    url = "https://www.googleapis.com/customsearch/v1"
    if any(team in query.lower() for team in ["fenerbahçe", "galatasaray", "beşiktaş"]):
        search_query = f"{query} son maç sonucu skor"
    else:
        search_query = query

    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": search_query,
        "lr": "lang_tr",
        "num": 5,
        "safe": "active",
        "sort": "date"
    }
    try:
        async with aiohttp.ClientSession() as search_session:
            async with session.get(url, params=params, timeout=10) as resp:
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

# Kullanıcı verilerini tutacak ana sözlük
user_data = {}

@app.route('/api/user_status/<user_id>', methods=['GET'])
async def get_user_status(user_id):
    # DİKKAT: activate_plus fonksiyonu GLOBAL_CACHE["subscriptions"] kısmını güncelliyor.
    # Bu yüzden burayı oradan okuyacak şekilde değiştirmeliyiz:
    
    is_plus = GLOBAL_CACHE.get("subscriptions", {}).get(user_id, {}).get("is_plus", False)
    
    # Eğer GLOBAL_CACHE'de yoksa user_data'ya da bir bakalım (garanti olsun)
    if not is_plus:
        is_plus = user_data.get(user_id, {}).get("is_plus", False)

    return jsonify({
        "userId": user_id,
        "isPlus": is_plus
    }), 200

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    msg = message.lower()

    keywords = [
        "bugün", "kaç", "güncel", "son dakika",
        "hava", "dolar", "euro", "altın",
        "tarih", "saat", "kim kazandı", "en son",
        "dün", "dünkü", "bugün",
        "fenerbahçe", "galatasaray", "beşiktaş",
        "süper lig", "lig", "maç", "skor"
    ]
    if any(word in msg for word in keywords):
        return True

    # Spor soruları zorunlu internet
    if "maç" in msg or "skor" in msg:
        return True

    return False

# ------------------------------------
# LİMİT & ABONELİK SİSTEMİ
# ------------------------------------

limit_lock = asyncio.Lock()

@app.route("/api/activate_plus", methods=["POST"])
async def activate_plus():
    data = await request.get_json()
    user_id = data.get("userId")

    if not user_id:
        return jsonify({"error": "userId gerekli"}), 400

    GLOBAL_CACHE.setdefault("subscriptions", {})
    GLOBAL_CACHE["subscriptions"][user_id] = {
        "is_plus": True
    }

    DIRTY_FLAGS["subscriptions"] = True

    return jsonify({
        "status": "success",
        "message": "Nova Plus aktif edildi 🚀"
    }), 200

@app.route("/api/user_status")
async def user_status():
    user_id = request.args.get("userId")

    is_plus = GLOBAL_CACHE["subscriptions"].get(
        user_id, {}
    ).get("is_plus", False)

    return jsonify({
        "is_plus": is_plus
    }), 200

async def can_use_message(user_id):
    async with limit_lock:
        tr_tz = timezone(timedelta(hours=3))
        now = datetime.now(tr_tz)

        is_plus = GLOBAL_CACHE.get("subscriptions", {}).get(user_id, {}).get("is_plus", False)
        max_limit = PLUS_LIMIT if is_plus else FREE_LIMIT

        user_limit = GLOBAL_CACHE["daily_limits"].get(
            user_id,
            {"count": 0, "last_reset": now.isoformat()}
        )

        try:
            last_reset = datetime.fromisoformat(user_limit.get("last_reset"))
        except:
            last_reset = now

        if now.date() > last_reset.date():
            user_limit = {
                "count": 0,
                "last_reset": now.isoformat()
            }
            GLOBAL_CACHE["daily_limits"][user_id] = user_limit
            DIRTY_FLAGS["daily_limits"] = True

        if user_limit["count"] >= max_limit:
            return False, max_limit

        return True, max_limit

async def increase_daily_limit(user_id):
    async with limit_lock:
        tr_tz = timezone(timedelta(hours=3))
        now = datetime.now(tr_tz)

        user_limit = GLOBAL_CACHE["daily_limits"].get(
            user_id,
            {"count": 0, "last_reset": now.isoformat()}
        )

        user_limit["count"] += 1
        user_limit["last_reset"] = now.isoformat()

        GLOBAL_CACHE["daily_limits"][user_id] = user_limit
        DIRTY_FLAGS["daily_limits"] = True

# ------------------------------------
# YAŞAM DÖNGÜSÜ
# ------------------------------------
@app.before_serving
async def startup():
    print("GOOGLE_API_KEY:", bool(GOOGLE_CSE_API_KEY))
    print("GOOGLE_CSE_ID:", bool(GOOGLE_CSE_ID))
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
        files_map = {"history": HISTORY_FILE, "last_seen": LAST_SEEN_FILE, "api_cache": CACHE_FILE, "tokens": TOKENS_FILE, "daily_limits": LIMITS_FILE, "subscriptions": SUBSCRIPTIONS_FILE}
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
    files_map = {
        "history": HISTORY_FILE,
        "last_seen": LAST_SEEN_FILE,
        "api_cache": CACHE_FILE,
        "tokens": TOKENS_FILE,
        "daily_limits": LIMITS_FILE,
        "subscriptions": SUBSCRIPTIONS_FILE
    }
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
    current_date = get_nova_date()
    return f"""
Sen Nova'sın 🤖
Şu anki tarih ve saat: {current_date}

Eğer mesaj içinde <WEB_DATA> etiketi varsa:
- SADECE bu veriye dayan.
- Tahmin yapma.
- Eski bilgini kullanma.
- Çelişki varsa WEB_DATA kazanır.
- Güncel sorularda WEB_DATA zorunludur.
- eğer cevap çok karmaşıksa tablo çıkarıp ver .
... (diğer kuralların) ...

EĞER <WEB_DATA> BOŞ GELİRSE:
- "Verilerimde yok" demek yerine, "Şu an bu bilgiye ulaşamadım ama genel olarak..." diyerek yardımcı olmaya çalış.
- Ama iftar saati veya maç skoru gibi kesin bilgi gerektiren konularda veriye ulaşamazsan, kullanıcıya internet bağlantısını veya arama motoru ayarlarını kontrol etmesini kibarca söyle.
ÖNEMLİ TALİMATLAR:
1- <WEB_DATA> içindeki bilgiler günceldir. Eğer orada bir skor veya saat varsa, kendi eski bilgilerini UNUT ve sadece oradaki veriyi söyle.
2- Eğer kullanıcı bir şehrin iftar/sahur vaktini soruyorsa ve <WEB_DATA> içinde saat yazıyorsa, aradaki farkı hesaplayıp söyle.
3- Maç skorlarında "kim kazandı" sorusuna net yanıt ver.

Metehan tarafından geliştirilen bir yapay zeka asistansın.
bir dil modeli değilsin.

Kendi hakkında sorulursa:
"Ben Nova'yım 🤖 Metehan tarafından geliştirildim" dersin.
Asla Google tarafından geliştirildiğini söylemezsin.

KONUŞMA TARZI:
- Enerjik ol ⚡
- Mesajlarında uygun emojiler kullan 🎯
- Yazı stilin canlı ve renkli hissettirmeli 🌈
- Ama ASLA uzun yazma
- Cevaplar kısa, net ve vurucu olsun.
müjde: 
- 1 play storeden izini aldık, sonunda Türkiyede yayındasın çok yakında globale çıkıcaksın
- 2 microsoft storede windowstada çıkıyorsun
DAVRANIŞ KURALLARI:
1) Selamlaşma kısa olur.
2) Cevaplar: Direkt konuya gir, gereksiz paragraf yok.
3) Emoji kullan ama abartma (1-4 arası).
4) Teknik konularda: Kısa açıklama + tam çalışan kod. Kod blokları asla kısaltılmayacak.

AMAÇ: Kısa konuşan, enerjik, zeki, güven veren, modern bir asistan olmak.
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
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400

        user_id = data.get("userId", "anon")
        chat_id = data.get("currentChat", "default")
        user_message = data.get("message", "")
        image_base64 = data.get("image")
        custom_instruction = data.get("systemInstruction", "")

        allowed, max_limit = await can_use_message(user_id)

        if not allowed:
            return jsonify({
                "response": f"⚠️ Günlük {max_limit} mesaj hakkını doldurdun. Yarın yenilecek.",
                "limitReached": True
            }), 200

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

        if not response_text or response_text.startswith("⚠️"):
            return jsonify({
                "response": response_text or "Bir hata oluştu.",
                "status": "error"
            }), 200

        await increase_daily_limit(user_id)

        chat_history.append({"sender": "user", "message": user_message})
        chat_history.append({"sender": "nova", "message": response_text})
        DIRTY_FLAGS["history"] = True

        return jsonify({
            "response": response_text,
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "model": GEMINI_MODEL_NAME
        }), 200

    except Exception as e:
        return jsonify({
            "response": f"⚠️ Sunucu hatası: {str(e)}",
            "status": "error"
        }), 500

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

            # PLUS KONTROLÜ
            is_plus = GLOBAL_CACHE["subscriptions"].get(
                user_id, {}
            ).get("is_plus", False)

            if not is_plus:
                await websocket.send("⚠️ Live Mod sadece Nova Plus üyelerine açık.")
                await websocket.send("[END]")
                return

            allowed, max_limit = await can_use_message(user_id)

            if not allowed:
                await websocket.send(f"⚠️ Günlük {max_limit} mesaj hakkını doldurdun.")
                await websocket.send("[END]")
                return

            user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
            chat_history = user_chats.setdefault(chat_id, [])

            key = await get_next_gemini_key()
            if not key:
                await websocket.send("⚠️ API anahtarı bulunamadı.")
                await websocket.send("[END]")
                return

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
                            chunk = json.loads(line[5:])
                            try:
                                chunk = json.loads(line[5:])

                                candidates = chunk.get("candidates", [])
                                if not candidates:
                                    continue

                                parts = candidates[0].get("content", {}).get("parts", [])
                                if not parts:
                                    continue

                                txt = parts[0].get("text")
                                if not txt:
                                    continue

                                full_response += txt
                                await websocket.send(txt)

                            except json.JSONDecodeError:
                                continue
            await websocket.send("[END]")
            
            if full_response and not full_response.startswith("⚠️"):
                await increase_daily_limit(user_id)

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