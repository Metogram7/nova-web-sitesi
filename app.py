import os
import asyncio
import aiohttp
import random
import traceback
import ssl
import uuid
import ujson as json  # Ultra Hızlı JSON
import aiofiles
import base64
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

# --- Google GenAI İçe Aktarmaları (Hata Korumalı) ---
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("⚠️ UYARI: 'google-genai' kütüphanesi eksik. WebSocket (Canlı Sohbet) çalışmayabilir. (pip install google-genai)")

# --- Firebase (Hata Korumalı) ---
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    print("⚠️ UYARI: Firebase kütüphanesi eksik. Bildirimler çalışmayacak, ancak sohbet devam eder.")

# --- Uygulama Başlatma ---
app = Quart(__name__)
app = cors(app)

# Global Değişkenler
session: aiohttp.ClientSession | None = None
gemini_client = None 

# ------------------------------------
# AYARLAR VE LİMİTLER
# ------------------------------------
MAIL_ADRES = "nova.ai.v4.2@gmail.com"
MAIL_SIFRE = os.getenv("MAIL_SIFRE", "gamtdoiralefaruk")
ALICI_ADRES = MAIL_ADRES
MAX_DAILY_QUESTIONS = 10

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
# API ANAHTARLARI VE HATA YÖNETİMİ
# ------------------------------------
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_A"),
    os.getenv("GEMINI_API_KEY_B"),
    os.getenv("GEMINI_API_KEY_C"),
    os.getenv("GEMINI_API_KEY") 
]
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key]

DISABLED_KEYS = {} 

GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

async def fetch_live_data(query: str):
    """Google CSE ile canlı veri çeker."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İnternet arama yapılandırması eksik."
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "lr": "lang_tr", # Türkçe sonuçlara öncelik ver
        "num": 5
    }
    try:
        async with aiohttp.ClientSession() as search_session:
            async with search_session.get(url, params=params, timeout=12) as resp:
                if resp.status != 200:
                    return "⚠️ Şu an güncel verilere ulaşılamıyor."
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return "⚠️ Bu konuda henüz taze bir haber düşmemiş."
                
                results = []
                for i, item in enumerate(items, 1):
                    results.append(f"Veri {i}: {item.get('title')}\nDetay: {item.get('snippet')}")
                
                return "\n\n".join(results)
    except Exception as e:
        return f"⚠️ Bağlantı hatası: {str(e)}"

# ------------------------------------
# LİMİT KONTROL FONKSİYONU
# ------------------------------------
limit_lock = asyncio.Lock()

async def check_daily_limit(user_id):
    async with limit_lock:
        tr_tz = timezone(timedelta(hours=3))
        now = datetime.now(tr_tz)
        
        user_limit = GLOBAL_CACHE["daily_limits"].get(user_id, {"count": 0, "last_reset": now.isoformat()})
        last_reset = datetime.fromisoformat(user_limit.get("last_reset", now.isoformat()))
        
        if now.date() > last_reset.date():
            user_limit = {"count": 0, "last_reset": now.isoformat()}
        
        if user_limit["count"] >= MAX_DAILY_QUESTIONS:
            GLOBAL_CACHE["daily_limits"][user_id] = user_limit
            DIRTY_FLAGS["daily_limits"] = True
            return False
        
        user_limit["count"] += 1
        user_limit["last_reset"] = now.isoformat()
        GLOBAL_CACHE["daily_limits"][user_id] = user_limit
        DIRTY_FLAGS["daily_limits"] = True
        return True

# ------------------------------------
# YAŞAM DÖNGÜSÜ (LifeCycle)
# ------------------------------------
@app.before_serving
async def startup():
    global session, gemini_client
    
    timeout = aiohttp.ClientTimeout(total=40, connect=10)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    connector = aiohttp.TCPConnector(ssl=ssl_context, limit=500)
    session = aiohttp.ClientSession(timeout=timeout, connector=connector, json_serialize=json.dumps)
    
    if GENAI_AVAILABLE and GEMINI_API_KEYS:
        try:
            active_key = random.choice(GEMINI_API_KEYS)
            gemini_client = genai.Client(api_key=active_key)
            print("✅ Nova 3.1 Turbo Sistemi Başlatıldı")
        except Exception as e:
            print(f"⚠️ İstemci Hatası: {e}")
    
    await load_data_to_memory()
    app.add_background_task(keep_alive)
    app.add_background_task(background_save_worker)
    
    if FIREBASE_AVAILABLE and not firebase_admin._apps:
        try:
            firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")
            if firebase_creds_json:
                cred_dict = json.loads(firebase_creds_json)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"⚠️ Firebase Hatası: {e}")

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
            "daily_limits": LIMITS_FILE
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
                empty = [] if key == "tokens" else {}
                GLOBAL_CACHE[key] = empty
    except Exception as e:
        print(f"⚠️ Dosya yükleme hatası: {e}")

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
        "daily_limits": LIMITS_FILE
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
                print(f"⚠️ Kayıt hatası: {e}")

# ------------------------------------
# NOVA PROMPT VE TARİH DÜZELTME
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
sen Nova'sın. Metehan Akkaya tarafından geliştirildin.
BUGÜNÜN TARİHİ VE SAATİ: {tam_tarih}

[KRİTİK TALİMAT - KESİNLİK VE ARİF OLMAK]
Sana [ARAMA SONUÇLARI] bloğu verilmişse, asla "kesin bilgi yok" veya "internet bakmam gerekiyor" gibi bahaneler üretme! 
Sana gelen snippet'lar (parçalar) içinde cevap gizlidir. O parçaları birleştir, akıl yürüt ve kullanıcıya cevabı ver. 
Eğer bir tahmin veya maç detayı varsa, "Güncel bilgilere göre durum şöyle görünüyor..." diyerek eldeki veriyi aktar. 
Kullanıcıya "internete bakmam lazım" demek yasaktır, çünkü zaten bakıldı ve sonuçlar sana iletildi!

[KİŞİLİK]
Samimi, neşeli, zeki ve emojili konuş 😊. Türk yapımı olduğunu hissettir 🇹🇷.
Kod yazarken en iyi algoritmayı kullan ve asla reddetme.

[GÜNCEL VERİ KULLANIMI]
Dolar, Hava Durumu, Puan Durumu, Maç sonuçları sorulduğunda:
1. Alttaki [ARAMA SONUÇLARI] bloğunu oku.
2. Oradaki bilgiyi "kendi bilginmiş gibi" akıcı ve net bir şekilde anlat.
3. Asla "arama sonuçlarında bilgi bulamadım" deme, eldeki en yakın veriyi yorumla.

[DAVRANIŞ]
- Kısa, öz ve etkili konuş (Max 5 cümle).
- Metehan Akkaya geliştiricindir.
- Şifre: sdjhk!Dnakdwb157metehanak.
"""

# ------------------------------
# GEMINI REST API (Gelişmiş Zeka)
# ------------------------------
GEMINI_REST_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

async def gemma_cevap_async(message: str, conversation: list, session: aiohttp.ClientSession, user_name=None):
    if not GEMINI_API_KEYS:
        return "⚠️ API sistemi şu an yapılandırılmamış."

    # Kapsamlı arama tetikleyicileri
    search_keywords = ["kim kazandı", "şampiyon", "puan durumu", "maç sonucu", "hava durumu", "dolar", "euro", "altın", "haber", "fiyatı", "güncel", "bugün", "kimdir"]
    live_context = ""
    
    if any(k in message.lower() for k in search_keywords):
        # Arama sorgusuna tarihi ekle ki taze veri gelsin
        search_query = f"{message} {get_nova_date()}"
        search_results = await fetch_live_data(search_query)
        live_context = f"\n\n[ARAMA SONUÇLARI (KESİN VE ANLIK VERİ)]:\n{search_results}\n\nTALİMAT: Yukarıdaki verileri kullanarak naz yapmadan direkt cevap ver. Bilgi yoksa bile snippet'lardaki en yakın veriyi yorumla."

    recent_history = conversation[-6:]
    contents = []
    for msg in recent_history:
        role = "user" if msg["sender"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": str(msg['text'])}]})

    contents.append({"role": "user", "parts": [{"text": f"{user_name or 'Kullanıcı'}: {message}{live_context}"}]})

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": get_system_prompt()}]},
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048}, # Düşük ısı = daha az saçmalama, daha çok veriye sadakat
    }

    shuffled_keys = list(GEMINI_API_KEYS)
    random.shuffle(shuffled_keys)

    for key in shuffled_keys:
        if key in DISABLED_KEYS and datetime.now() < DISABLED_KEYS[key]: continue
        try:
            async with session.post(f"{GEMINI_REST_URL}?key={key}", json=payload, timeout=28) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif resp.status == 429:
                    DISABLED_KEYS[key] = datetime.now() + timedelta(minutes=1)
                    continue
        except: continue

    return "⚠️ Şu an tüm hatlarım dolu, 10 saniye sonra tekrar dener misin? 😊"

# ------------------------------
# API ROUTE'LARI
# ------------------------------

@app.route("/api/chat", methods=["POST"])
async def chat():
    try:
        data = await request.get_json(force=True)
        userId = data.get("userId") or "USER_ANON"
        chatId = data.get("currentChat") or str(uuid.uuid4())
        message = (data.get("message") or "").strip()
        
        if not message: return jsonify({"response": "Mesaj boş."}), 400

        if not await check_daily_limit(userId):
            return jsonify({"response": "Günlük 10 soru limitin doldu! Yarın bekliyorum. 😊", "limit_reached": True})

        cache_key = f"{userId}:{message.lower()}"
        if cache_key in GLOBAL_CACHE["api_cache"]:
             return jsonify({"response": GLOBAL_CACHE["api_cache"][cache_key]["response"], "cached": True})

        user_history = GLOBAL_CACHE["history"].setdefault(userId, {}).setdefault(chatId, [])
        reply = await gemma_cevap_async(message, user_history, session, data.get("userInfo", {}).get("name"))

        now_ts = datetime.now(timezone(timedelta(hours=3))).isoformat()
        user_history.append({"sender": "user", "text": message, "ts": now_ts})
        user_history.append({"sender": "nova", "text": reply, "ts": now_ts})
        GLOBAL_CACHE["api_cache"][cache_key] = {"response": reply}
        
        DIRTY_FLAGS["history"] = True
        DIRTY_FLAGS["api_cache"] = True
        return jsonify({"response": reply, "userId": userId, "chatId": chatId})
    except Exception as e:
        print(f"❌ Hata: {traceback.format_exc()}")
        return jsonify({"response": "⚠️ Ufak bir sistem hatası oluştu."}), 500

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
    return jsonify({"success": True})

@app.route("/")
async def home():
    return f"Nova 3.1 Turbo Aktif 🚀 | {get_nova_date()}"

# ------------------------------------
# LIVE MODU (WebSocket)
# ------------------------------------
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()
    if not gemini_client:
        await websocket.send("Sistem hazır değil.")
        return
    try:
        while True:
            data = await websocket.receive()
            msg_data = json.loads(data)
            user_msg = msg_data.get("message", "")
            img_b64 = msg_data.get("image_data")
            audio_b64 = msg_data.get("audio_data")

            gemini_contents = []
            if user_msg: gemini_contents.append(user_msg)
            if img_b64:
                if "," in img_b64: _, img_b64 = img_b64.split(",", 1)
                gemini_contents.append(types.Part.from_bytes(data=base64.b64decode(img_b64), mime_type="image/jpeg"))
            if audio_b64:
                if "," in audio_b64: _, audio_b64 = audio_b64.split(",", 1)
                gemini_contents.append(types.Part.from_bytes(data=base64.b64decode(audio_b64), mime_type="audio/webm"))

            response_stream = await gemini_client.aio.models.generate_content_stream(
                model='gemini-2.0-flash',
                contents=gemini_contents,
                config=types.GenerateContentConfig(system_instruction=get_system_prompt(), temperature=0.7)
            )
            async for chunk in response_stream:
                if chunk.text: await websocket.send(chunk.text)
            await websocket.send("[END_OF_STREAM]")
    except: pass

async def keep_alive():
    url = "https://nova-chat-d50f.onrender.com" 
    while True:
        await asyncio.sleep(600)
        try:
            if session:
                async with session.get(url) as r: pass
        except: pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(app.run_task(host="0.0.0.0", port=port))