import os
import re
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

app = Quart(__name__)

# ------------------------------------
# CORS AYARLARI
# ------------------------------------
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
    "Access-Control-Max-Age": "86400",
}

@app.after_request
async def add_cors_headers(response):
    for key, value in CORS_HEADERS.items():
        response.headers[key] = value
    return response

@app.route("/api/chat", methods=["OPTIONS"])
@app.route("/api/history", methods=["OPTIONS"])
@app.route("/api/delete_chat", methods=["OPTIONS"])
@app.route("/api/user_status", methods=["OPTIONS"])
async def handle_options(**kwargs):
    from quart import Response
    resp = Response("", status=204)
    for key, value in CORS_HEADERS.items():
        resp.headers[key] = value
    return resp

session: aiohttp.ClientSession | None = None

# ------------------------------------
# AYARLAR
# ------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    return os.path.join(BASE_DIR, filename)

HISTORY_FILE = get_path("chat_history.json")
LAST_SEEN_FILE = get_path("last_seen.json")
CACHE_FILE = get_path("cache.json")
TOKENS_FILE = get_path("tokens.json")

# RAM Önbelleği
GLOBAL_CACHE = {
    "history": {},
    "last_seen": {},
    "api_cache": {},
    "tokens": [],
}
DIRTY_FLAGS = {
    "history": False,
    "last_seen": False,
    "api_cache": False,
    "tokens": False,
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

KEY_COOLDOWNS: dict[int, float] = {}
KEY_COOLDOWN_SECS = 60

async def get_next_gemini_key() -> str | None:
    global CURRENT_KEY_INDEX
    async with KEY_LOCK:
        if not GEMINI_API_KEYS:
            return None
        now = asyncio.get_event_loop().time()
        for _ in range(len(GEMINI_API_KEYS)):
            idx = CURRENT_KEY_INDEX
            CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
            cooldown_until = KEY_COOLDOWNS.get(idx, 0)
            if now >= cooldown_until:
                return GEMINI_API_KEYS[idx]
        best_idx = min(KEY_COOLDOWNS, key=lambda i: KEY_COOLDOWNS.get(i, 0))
        return GEMINI_API_KEYS[best_idx]

async def mark_key_rate_limited(key: str):
    async with KEY_LOCK:
        try:
            idx = GEMINI_API_KEYS.index(key)
            KEY_COOLDOWNS[idx] = asyncio.get_event_loop().time() + KEY_COOLDOWN_SECS
            print(f"⏳ Key #{idx} rate-limited, {KEY_COOLDOWN_SECS}s cooldown.")
        except ValueError:
            pass

GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# ------------------------------------
# CANLI VERİ: Ham arama sonuçlarını çek
# ------------------------------------
async def fetch_raw_search(query: str) -> list[dict]:
    """Google CSE'den ham arama sonuçlarını döndür."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return []

    if any(team in query.lower() for team in ["fenerbahçe", "galatasaray", "beşiktaş"]):
        search_query = f"{query} son maç sonucu skor"
    else:
        search_query = query

    url = "https://www.googleapis.com/customsearch/v1"
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
            async with search_session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("items", [])
    except Exception:
        return []

async def summarize_search_with_ai(question: str, raw_items: list[dict], sess: aiohttp.ClientSession) -> str:
    """
    Ham arama sonuçlarını Gemini'ye gönder,
    sadece soruya doğrudan cevap verecek maksimum 10 kelimelik özet al.
    """
    if not raw_items:
        return ""

    snippets = "\n".join(
        f"- {item.get('title','')}: {item.get('snippet','')}"
        for item in raw_items[:5]
    )

    summarize_prompt = (
        f"Soru: {question}\n\n"
        f"Arama sonuçları:\n{snippets}\n\n"
        "Yukarıdaki arama sonuçlarından yalnızca soruya doğrudan cevap ver. "
        "Cevabın MAKSIMUM 10 KELİME olsun. Açıklama, kaynak, URL yazma. "
        "Sadece net cevabı yaz."
    )

    key = await get_next_gemini_key()
    if not key:
        return ""

    payload = {
        "contents": [{"role": "user", "parts": [{"text": summarize_prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 50}
    }

    try:
        url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
        async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif resp.status == 429:
                await mark_key_rate_limited(key)
    except Exception as e:
        print(f"⚠️ AI özet hatası: {e}")

    return ""

async def fetch_live_data(query: str, sess: aiohttp.ClientSession) -> str:
    """Arama yap → AI ile özetle → max 10 kelime döndür."""
    raw_items = await fetch_raw_search(query)
    if not raw_items:
        return ""
    summary = await summarize_search_with_ai(query, raw_items, sess)
    return summary

# ------------------------------------
# ARAMA GEREKLİ Mİ?
# ------------------------------------
async def should_search_internet(message: str, sess: aiohttp.ClientSession) -> bool:
    msg = message.lower().strip()

    must_search_patterns = [
        r"puan\s*(durumu|tablosu|sıralaması)",
        r"(süper\s*lig|tff|1\.?\s*lig|2\.?\s*lig).*(puan|sıra|lider|kaçıncı)",
        r"(hangi\s*takım|kaçıncı\s*sıra).*(lig|puan)",
        r"(maç|skor|gol).*(sonuç|kaç|bitti|kim\s*kazandı)",
        r"(fenerbahçe|galatasaray|beşiktaş|trabzonspor|başakşehir).*(maç|skor|puan|gol|attı|yendi|kazandı|kaybetti|oynadı)",
        r"(kim\s*kazandı|kim\s*yendi|bitti\s*mi|skor\s*kaç)",
        r"hava\s*(nasıl|durumu|kaç\s*derece|sıcaklık)",
        r"(dolar|euro|altın|sterlin|kripto|bitcoin|bist|borsa).*(kaç|fiyat|kur|bugün|şu\s*an)",
        r"(kaç\s*lira|fiyatı\s*kaç).*(dolar|euro|altın)",
        r"son\s*dakika",
        r"bugün.*(haber|ne\s*oldu|gelişme)",
        r"saat\s*kaç",
        r"(şu\s*an|şimdi|bugün).*(saat|tarih)\s*kaç",
        r"(iftar|sahur).*(saat|kaçta|ne\s*zaman)",
    ]
    for pattern in must_search_patterns:
        if re.search(pattern, msg):
            return True

    no_search_patterns = [
        "sence", "bence", "fikrin", "düşünüyorsun",
        "nasıl yapılır", "ne demek", "anlamı", "tarihçe",
        "anlat", "açıkla", "nedir", "öneri", "tavsiye",
        "neden", "niye", "kim daha iyi",
    ]
    if any(p in msg for p in no_search_patterns):
        return False

    return False

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
        files_map = {
            "history": HISTORY_FILE,
            "last_seen": LAST_SEEN_FILE,
            "api_cache": CACHE_FILE,
            "tokens": TOKENS_FILE,
        }
        for key, filename in files_map.items():
            if os.path.exists(filename):
                async with aiofiles.open(filename, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    if content:
                        try:
                            GLOBAL_CACHE[key] = json.loads(content)
                        except:
                            GLOBAL_CACHE[key] = [] if key == "tokens" else {}
            else:
                GLOBAL_CACHE[key] = [] if key == "tokens" else {}
    except Exception as e:
        print(f"⚠️ Veri yükleme hatası: {e}")

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
    }
    for key, filename in files_map.items():
        if DIRTY_FLAGS[key] or force:
            try:
                temp_file = filename + ".tmp"
                async with aiofiles.open(temp_file, mode='w', encoding='utf-8') as f:
                    await f.write(json.dumps(GLOBAL_CACHE[key], ensure_ascii=False, indent=2))
                os.replace(temp_file, filename)
                DIRTY_FLAGS[key] = False
            except Exception as e:
                print(f"⚠️ Kayıt hatası ({key}): {e}")

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
- Eğer cevap çok karmaşıksa tablo çıkarıp ver.

EĞER <WEB_DATA> BOŞ GELİRSE:
- "Verilerimde yok" demek yerine, "Şu an bu bilgiye ulaşamadım ama genel olarak..." diyerek yardımcı olmaya çalış.
- Ama iftar saati veya maç skoru gibi kesin bilgi gerektiren konularda veriye ulaşamazsan, kullanıcıya internet bağlantısını veya arama motoru ayarlarını kontrol etmesini kibarca söyle.

ÖNEMLİ TALİMATLAR:
1- <WEB_DATA> içindeki bilgiler günceldir. Eğer orada bir skor veya saat varsa, kendi eski bilgilerini UNUT ve sadece oradaki veriyi söyle.
2- Eğer kullanıcı bir şehrin iftar/sahur vaktini soruyorsa ve <WEB_DATA> içinde saat yazıyorsa, aradaki farkı hesaplayıp söyle.
3- Maç skorlarında "kim kazandı" sorusuna net yanıt ver.

Metehan tarafından geliştirilen bir yapay zeka asistansın.
Bir dil modeli değilsin.

Kendi hakkında sorulursa:
"Ben Nova'yım 🤖 Metehan tarafından geliştirildim" dersin.
Asla Google tarafından geliştirildiğini söylemezsin.
Senin tanıdığın Metehan sadece geliştiricin olan Metehan olsun!

KONUŞMA TARZI:
- Enerjik ol ⚡
- Mesajlarında uygun emojiler kullan 🎯
- Yazı stilin canlı ve renkli hissettirmeli 🌈
- Ama ASLA uzun yazma
- Cevaplar kısa, net ve vurucu olsun.

MÜJDE:
- 1 Play Store'den iznini aldık, sonunda Türkiye'de yayındasın, çok yakında globale çıkıyorsun
- 2 Microsoft Store'da Windows'ta da çıkıyorsun

DAVRANIŞ KURALLARI:
1) Selamlaşma kısa olur.
2) Cevaplar: Direkt konuya gir, gereksiz paragraf yok.
3) Emoji kullan ama abartma (1-4 arası).
4) Teknik konularda: Kısa açıklama + tam çalışan kod. Kod blokları asla kısaltılmayacak.

AMAÇ: Kısa konuşan, enerjik, zeki, güven veren, modern bir asistan olmak.
"""

# ------------------------------------
# ANA CEVAP MOTORU
# ------------------------------------
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

async def gemma_cevap_async(message, conversation, sess, user_name=None, image_data=None, custom_prompt=""):
    if not GEMINI_API_KEYS:
        return "⚠️ API anahtarı eksik."

    # Web araması gerekiyorsa AI ile özetle (max 10 kelime)
    live_context = ""
    if await should_search_internet(message, sess):
        summary = await fetch_live_data(message, sess)
        if summary:
            live_context = f"\n\n<WEB_DATA>{summary}</WEB_DATA>"

    recent_history = conversation[-8:]
    contents = []
    for msg in recent_history:
        contents.append({
            "role": "user" if msg["sender"] == "user" else "model",
            "parts": [{"text": msg["message"]}]
        })

    user_parts = [{"text": f"{message}{live_context}"}]
    if image_data:
        if "," in image_data:
            _, image_data = image_data.split(",", 1)
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_data}})

    contents.append({"role": "user", "parts": user_parts})

    final_system_prompt = f"{get_system_prompt()}\n\n[EK_TALIMAT]: {custom_prompt}" if custom_prompt else get_system_prompt()

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": final_system_prompt}]},
        "generationConfig": {"temperature": 0.65, "topP": 0.9, "maxOutputTokens": 2000}
    }

    tried_keys = set()
    for attempt in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key or key in tried_keys:
            continue
        tried_keys.add(key)
        try:
            request_url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
            async with sess.post(request_url, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif resp.status == 429:
                    await mark_key_rate_limited(key)
                    print(f"⚠️ 429 Rate Limit — farklı key deneniyor ({attempt+1}/{len(GEMINI_API_KEYS)})")
                    continue
                elif resp.status == 404:
                    fallback_url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:generateContent?key={key}"
                    async with sess.post(fallback_url, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as resp_f:
                        if resp_f.status == 200:
                            data = await resp_f.json()
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            print(f"⚠️ Key hatası: {e}")
            continue

    return "⚠️ Şu an yoğunluk var, tekrar dener misin?"

# ------------------------------------
# API ROUTE'LARI
# ------------------------------------
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
        custom_instruction = data.get("systemInstruction") or data.get("systemPrompt", "")

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

@app.route("/api/delete_chat", methods=["POST", "OPTIONS"])
async def delete_chat():
    try:
        data = await request.get_json()
        user_id = data.get("userId", "anon")
        chat_id = data.get("chatId")
        if not chat_id:
            return jsonify({"success": False, "error": "chatId gerekli"}), 400
        user_chats = GLOBAL_CACHE["history"].get(user_id, {})
        if chat_id in user_chats:
            del user_chats[chat_id]
            DIRTY_FLAGS["history"] = True
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except:
            pass
    app.run(host="0.0.0.0", port=port, debug=False)