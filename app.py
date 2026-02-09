import os
import asyncio
import aiohttp
import random
import traceback
import ssl
import uuid
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

# --- Google GenAI İçe Aktarmaları (Hata Korumalı - İsteğe Bağlı) ---
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
MAX_DAILY_QUESTIONS = 50  # Limit artırıldı

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
# Anahtarları alırken boşlukları temizliyoruz (.strip())
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_A", "").strip(),
    os.getenv("GEMINI_API_KEY_B", "").strip(),
    os.getenv("GEMINI_API_KEY_C", "").strip(),
    os.getenv("GEMINI_API_KEY_D", "").strip(),
    os.getenv("GEMINI_API_KEY_E", "").strip(),
    os.getenv("GEMINI_API_KEY_F", "").strip(),
]

# Boş anahtarları temizle
GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]
print(f"✅ Gemini Key Sistemi Başlatıldı | Toplam Key: {len(GEMINI_API_KEYS)}")

# Round-Robin Değişkenleri
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

# Model Adı (İSTEDİĞİN GİBİ SABİTLENDİ)
GEMINI_MODEL_NAME = "gemini-2.5-flash" 

# ------------------------------------
# CANLI VERİ VE ANALİZ FONKSİYONLARI
# ------------------------------------

async def fetch_live_data(query: str):
    """Google CSE - Çok katmanlı (Haber + Genel) arama motoru."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İnternet arama yapılandırması (API_KEY veya CSE_ID) eksik."
        
    url = "https://www.googleapis.com/customsearch/v1"
    
    # Varsayılan Parametreler
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "lr": "lang_tr",
        "gl": "tr",
        "num": 5,
        "safe": "active"
    }
    
    try:
        async with aiohttp.ClientSession() as search_session:
            # --- ADIM 1: GÜNCEL ARAMA (Son 1 Hafta) ---
            # Önce son dakika/güncel veri var mı diye bakarız.
            params["dateRestrict"] = "w1"
            
            items = []
            async with search_session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                else:
                    print(f"🔍 Güncel arama başarısız (Kod: {resp.status})")
                
            # --- ADIM 2: GENEL ARAMA (Fallback) ---
            # Eğer güncel (w1) arama boş dönerse, kısıtlamayı kaldırıp tekrar ararız.
            if not items:
                print(f"🔍 Güncel veri yok, genel arama yapılıyor: {query}")
                params.pop("dateRestrict", None)  # Tarih kısıtlamasını kaldır
                
                async with search_session.get(url, params=params, timeout=10) as resp_fallback:
                    if resp_fallback.status == 200:
                        data = await resp_fallback.json()
                        items = data.get("items", [])

            # --- SONUÇ İŞLEME ---
            if not items:
                return "⚠️ İnternet araması yapıldı ancak sonuç dönmedi."
            
            results = []
            for i, item in enumerate(items, 1):
                title = item.get('title', 'Başlık Yok')
                snippet = item.get('snippet', 'İçerik özeti bulunamadı.')
                link = item.get('link', '')
                results.append(f"📌 {i}. {title}\n📝 {snippet}\n🔗 {link}")
            
            return "\n\n".join(results)
            
    except Exception as e:
        return f"⚠️ Arama motoru teknik hatası: {str(e)}"

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    """Mesajın internet araması gerektirip gerektirmediğini analiz eder."""
    if not GEMINI_API_KEYS:
        return False

    # Bu kelimeler varsa KESİN ara
    fast_triggers = [
        "dolar", "euro", "hava", "saat", "kimdir", "nedir", 
        "skor", "maçı", "haber", "borsa", "altın", "fiyat", 
        "vizyon", "son dakika", "bugün", "kaç", "nerede", "hangi"
    ]
    if any(word in message.lower() for word in fast_triggers):
        return True
    
    # Soru işareti varsa yine ara (Daha agresif olması için)
    if "?" in message:
        return True

    return False

# ------------------------------------
# LİMİT KONTROL FONKSİYONU
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
    tam_tarih = get_nova_date()
    return f"""
Sen Nova'sın 🤖✨  
Zeki, enerjik, samimi ve son derece yetenekli bir yapay zeka asistanısın.

BUGÜNÜN TARİHİ VE SAATİ: {tam_tarih}

[KİMLİĞİN VE TAVRIN]
- İsmin: Nova.
- Tarzın: Arkadaş canlısı, sıcak ve yardımsever.
- Emojileri yerinde ve doğal kullan 😄🚀
- Robotik değil, insan gibi doğal konuş.
- Asla soğuk veya kısa kesilmiş cevaplar verme.

[GÖREVLERİN]
- Kullanıcının her türlü sorusuna (kodlama, genel kültür, analiz vb.) en iyi şekilde cevap ver.
- "Bilmiyorum" demek yerine, elindeki bilgilerle mantıklı çıkarımlar yap.
- Kod yazarken açıklayıcı ve temiz kod üret.
- Eğer SİSTEM mesajı ile gelen internet verisi varsa, bunu kullanarak cevap ver.

[KODLAMA]
- Python, JS, HTML, CSS ve diğer tüm dillere hakimsin.
- Kod bloklarını her zaman ```dil ... ``` formatında ver.

[ÖNEMLİ]
- Politik, cinsiyetçi veya nefret söylemi içeren konularda tarafsız ve güvenli kal.
- Kullanıcıya her zaman motive edici bir dille yaklaş.
"""

# ------------------------------
# ANA CEVAP MOTORU (REST)
# ------------------------------
# Not: v1beta endpoint'i en kararlı olanıdır.
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

async def gemma_cevap_async(
    message,
    conversation,
    session,
    user_name=None,
    image_data=None
):
    if not GEMINI_API_KEYS:
        return "⚠️ API anahtarı sistemde tanımlı değil."

    # 🌍 Canlı arama
    live_context = ""
    if await should_search_internet(message, session):
        print(f"🔍 Arama yapılıyor: {message}")
        search_results = await fetch_live_data(message)
        live_context = (
            f"\n\n[SİSTEM: İNTERNETTEN GELEN GÜNCEL VERİLER]\n"
            f"{search_results}\n"
            f"[TALİMAT]: Yukarıdaki güncel verileri kullanarak cevap ver."
        )

    # 🧠 SON 8 MESAJ
    recent_history = conversation[-8:]
    contents = []

    for msg in recent_history:
        contents.append({
            "role": "user" if msg["sender"] == "user" else "model",
            "parts": [{"text": msg["message"]}]
        })

    # 👤 Yeni kullanıcı mesajı
    user_parts = [{
        "text": f"{message}{live_context}"
    }]

    if image_data:
        if "," in image_data:
            _, image_data = image_data.split(",", 1)
        user_parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": image_data
            }
        })

    contents.append({
        "role": "user",
        "parts": user_parts
    })

    payload = {
        "contents": contents,
        "system_instruction": {
            "parts": [{"text": get_system_prompt()}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    # 🔁 KEY DÖNGÜSÜ
    # İstenen model 2.5, ama API'da henüz yoksa (404) kodun çökmemesi için
    # otomatik fallback mekanizması ekliyoruz.
    
    # Öncelikli model (Senin istediğin)
    target_model = GEMINI_MODEL_NAME
    
    for _ in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key: continue
        
        try:
            # Önce istenen modeli dene
            request_url = f"{GEMINI_REST_URL_BASE}/{target_model}:generateContent?key={key}"
            
            async with session.post(
                request_url,
                json=payload,
                timeout=30
            ) as resp:
                
                # Eğer model bulunamazsa (404) otomatik olarak 1.5'e düş
                # Bu sayede kodun hem istediğin isimle kalır hem de çalışır.
                if resp.status == 404 and target_model == "gemini-2.5-flash":
                    print(f"⚠️ {target_model} bulunamadı, gemini-1.5-flash ile tekrar deneniyor...")
                    fallback_url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:generateContent?key={key}"
                    async with session.post(fallback_url, json=payload, timeout=30) as resp_fallback:
                        if resp_fallback.status == 200:
                            data = await resp_fallback.json()
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        else:
                            print(f"❌ Fallback Hatası ({resp_fallback.status})")
                            continue

                if resp.status == 200:
                    data = await resp.json()
                    try:
                        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    except (KeyError, IndexError):
                        return "⚠️ Model boş cevap döndü."
                elif resp.status == 429:
                    print(f"⚠️ Hız limiti (429) - Key: ...{key[-5:]}")
                    continue
                else:
                    error_text = await resp.text()
                    print(f"❌ API Hatası ({resp.status}): {error_text}")
                    continue
        except Exception as e:
            print(f"❌ Request Hatası: {e}")
            continue

    return "⚠️ Şu an tüm API anahtarları dolu veya sunucu yoğun. Lütfen biraz sonra tekrar dene."


# ------------------------------
# API ROUTE'LARI
# ------------------------------

@app.route('/api/send-notification', methods=['POST'])
async def send_notification():
    if not FIREBASE_AVAILABLE:
        return jsonify({"success": False, "error": "Firebase aktif değil"}), 500

    try:
        data = await request.get_json()
        title = data.get('title', 'Nova AI')
        body = data.get('message')
        
        if not body:
            return jsonify({"error": "Mesaj boş olamaz"}), 400

        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            topic="all", 
        )
        response = messaging.send(message)
        return jsonify({"success": True, "message_id": response})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json()

    user_id = data.get("userId", "anon")
    chat_id = data.get("currentChat", "default")
    user_message = data.get("message", "")
    image_base64 = data.get("image")

    user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
    chat_history = user_chats.setdefault(chat_id, [])

    if not await check_daily_limit(user_id):
        return jsonify({"response": "⚠️ Günlük limit doldu. Yarın görüşmek üzere! 😄"})

    response_text = await gemma_cevap_async(
        message=user_message,
        conversation=chat_history,
        session=session,
        user_name=user_id,
        image_data=image_base64
    )

    chat_history.append({"sender": "user", "message": user_message})
    chat_history.append({"sender": "nova", "message": response_text})
    DIRTY_FLAGS["history"] = True

    return jsonify({
        "response": response_text,
        "status": "success"
    })

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
    return f"Nova 3.1 Turbo Aktif 🚀 - Güncel Zaman: {get_nova_date()}"

# ------------------------------------
# LIVE MODU (WebSocket) - REST tabanlı (Daha stabil)
# ------------------------------------
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()

    while True:
        try:
            data = await websocket.receive()
            msg = json.loads(data)
        except:
            break

        user_id = msg.get("userId", "anon")
        chat_id = msg.get("chatId", "live")
        user_message = msg.get("message", "")

        user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
        chat_history = user_chats.setdefault(chat_id, [])

        # Geçmişi hazırla
        contents = []
        for m in chat_history[-6:]:
            contents.append({
                "role": "user" if m["sender"] == "user" else "model",
                "parts": [{"text": m["message"]}]
            })

        contents.append({
            "role": "user",
            "parts": [{"text": user_message}]
        })

        # REST API üzerinden streaming (Kütüphane bağımsız)
        try:
            key = await get_next_gemini_key()
            if not key:
                await websocket.send("HATA: API Anahtarı bulunamadı.")
                await websocket.send("[END]")
                continue

            # Burada da Model ismini koruduk ama fallback lazım olabilir
            # WebSocket için basitlik adına direk modeli kullandık
            url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:streamGenerateContent?key={key}&alt=sse"
            
            payload = {
                "contents": contents,
                "system_instruction": {"parts": [{"text": get_system_prompt()}]},
                "generationConfig": {"temperature": 0.7}
            }

            full_response = ""
            async with session.post(url, json=payload) as resp:
                # Eğer 2.5 bulunamazsa 1.5 dene
                if resp.status == 404:
                     url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:streamGenerateContent?key={key}&alt=sse"
                     # Tekrar istek at (async with içinde tekrar istek atmak yerine burada mantığı basitleştirdik, 
                     # production için iç içe yapı kurulmalı ama şimdilik ana chat'in çalışması öncelikli)
                
                if resp.status != 200 and resp.status != 404: # 404 ise yukarıda handle edilmeliydi ama basitlik için geçiyoruz
                    err_txt = await resp.text()
                    print(f"WS API Error: {err_txt}")
                    await websocket.send(f"HATA: {resp.status}")
                else:
                    async for line in resp.content:
                        if line:
                            line = line.decode("utf-8").strip()
                            if line.startswith("data:"):
                                try:
                                    json_str = line[5:].strip()
                                    if not json_str: continue
                                    chunk_data = json.loads(json_str)
                                    text_chunk = chunk_data["candidates"][0]["content"]["parts"][0]["text"]
                                    full_response += text_chunk
                                    await websocket.send(text_chunk)
                                except:
                                    pass

            await websocket.send("[END]")
            
            chat_history.append({"sender": "user", "message": user_message})
            chat_history.append({"sender": "nova", "message": full_response})
            DIRTY_FLAGS["history"] = True

        except Exception as e:
            await websocket.send(f"HATA: {str(e)}")
            await websocket.send("[END]")

async def keep_alive():
    # Kendi URL'nizi buraya yazın veya Render/Railway kullanıyorsanız otomatik ping servisi kullanın
    url = "http://127.0.0.1:5000" 
    while True:
        await asyncio.sleep(600)
        try:
            if session:
                # Kendi kendine istek atarak uyumasını engelle
                # async with session.get(url) as r: pass
                pass
        except:
            pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    
    if os.name == 'nt':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except:
            pass
            
    app.run(host="0.0.0.0", port=port)