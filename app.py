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

# --- Google GenAI İçe Aktarmaları (Hata Korumalı) ---
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("⚠️ UYARI: 'google-genai' kütüphanesi eksik. (pip install google-genai)")

# ------------------------------------
# FIREBASE BAŞLATMA
# ------------------------------------
FIREBASE_AVAILABLE = False
# Not: Firebase credentials kodunu buraya eklemelisiniz.

app = Quart(__name__)

# Bu ayar tarayıcıya "Her yerden gelen isteği kabul et" der.
app = cors(
    app, 
    allow_origin="*", 
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
    expose_headers=["Content-Type", "Authorization"]
)

# Global Değişkenler
session: aiohttp.ClientSession | None = None
gemini_client = None 

# ------------------------------------
# AYARLAR VE LİMİTLER
# ------------------------------------
MAIL_ADRES = "nova.ai.v4.2@gmail.com"
MAIL_SIFRE = os.getenv("MAIL_SIFRE", "gamtdoiralefaruk")
ALICI_ADRES = MAIL_ADRES
MAX_DAILY_QUESTIONS = 20

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
    os.getenv("GEMINI_API_KEY_A"),
    os.getenv("GEMINI_API_KEY_B"),
    os.getenv("GEMINI_API_KEY_C"),
    os.getenv("GEMINI_API_KEY_D"),
    os.getenv("GEMINI_API_KEY_E"),
    os.getenv("GEMINI_API_KEY_F"),
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

# DÜZELTME: 2.5 henüz yok, stabil ve hızlı olan 1.5 Flash veya 2.0 Flash seçilmeli.
GEMINI_MODEL_NAME = "gemini-1.5-flash" 

# ------------------------------------
# CANLI VERİ VE ANALİZ FONKSİYONLARI (GÜNCELLENDİ)
# ------------------------------------

async def fetch_live_data(query: str):
    """Google CSE ile internetten veri çeker - GÜNCEL VERİ ODAKLI."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İnternet arama yapılandırması eksik. lütfen ulaş: metehanakkaya30@gmail.com"
        
    url = "https://www.googleapis.com/customsearch/v1"
    
    # Tarih bilgisini al
    tr_tz = timezone(timedelta(hours=3))
    now = datetime.now(tr_tz)
    date_str = now.strftime("%Y %B") # Örn: 2024 October
    
    # Sorguyu güncelleştir (Sene ekle ki eski sonuç gelmesin)
    optimized_query = f"{query} {now.year}"

    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": optimized_query,
        "lr": "lang_tr",        # Türkçe sonuçlar
        "gl": "tr",             # Türkiye lokasyonlu sonuçlar
        "num": 5,               # İlk 5 sonuç
        "sort": "date",         # KRİTİK AYAR: Tarihe göre sırala (En yeni en üstte)
        "safe": "active"
    }
    
    try:
        async with aiohttp.ClientSession() as search_session:
            async with search_session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    error_msg = await resp.text()
                    print(f"Search Error: {error_msg}")
                    return "⚠️ Arama motoru şu an yanıt vermiyor."
                
                data = await resp.json()
                items = data.get("items", [])
                
                if not items:
                    # Tarihe göre bulamazsa normal aramayı dene (Yedek Plan)
                    if "sort" in params:
                        del params["sort"]
                        async with search_session.get(url, params=params, timeout=10) as resp_fallback:
                            if resp_fallback.status == 200:
                                data = await resp_fallback.json()
                                items = data.get("items", [])

                if not items:
                    return "⚠️ İnternette bu konuda güncel bir bilgi bulunamadı."
                
                results = []
                for i, item in enumerate(items, 1):
                    title = item.get('title', 'Başlık Yok')
                    snippet = item.get('snippet', 'Özet Yok')
                    # Meta taglerden tarih bulmaya çalış (Opsiyonel iyileştirme)
                    results.append(f"[{i}] {title}: {snippet}")
                
                return "\n\n".join(results)
    except Exception as e:
        return f"⚠️ Arama hatası: {str(e)} lütfen ulaş: metehanakkaya30@gmail.com "

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    """Mesajın internet araması gerektirip gerektirmediğini analiz eder."""
    if not GEMINI_API_KEYS:
        return False

    # Prompt biraz daha hassaslaştırıldı
    analysis_prompt = {
        "contents": [{
            "role": "user",
            "parts": [{
                "text": f"""Aşağıdaki mesaj güncel bir olay, tarih, saat, hava durumu, döviz, spor, haber veya teyit gerektiren taze bilgi içeriyor mu?
Cevabın sadece 'EVET' veya 'HAYIR' olsun.

Mesaj: {message}"""
            }]
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 10
        }
    }

    try:
        key = random.choice(GEMINI_API_KEYS)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={key}"
        async with session.post(url, json=analysis_prompt, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if "candidates" in data and data["candidates"]:
                    content_parts = data["candidates"][0].get("content", {}).get("parts", [])
                    if content_parts:
                        answer = content_parts[0].get("text", "").strip().upper()
                        return "EVET" in answer
    except:
        pass
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
    global session, gemini_client
    
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    connector = aiohttp.TCPConnector(ssl=False, limit=100)
    session = aiohttp.ClientSession(timeout=timeout, connector=connector, json_serialize=json.dumps)
    
    if GENAI_AVAILABLE and GEMINI_API_KEYS:
        try:
            active_key = random.choice(GEMINI_API_KEYS)
            gemini_client = genai.Client(api_key=active_key)
        except Exception as e:
            print(f"⚠️ Gemini Client Hatası: {e}")
    
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
Metehan Akkaya tarafından geliştirilmiş, zeki, enerjik ve samimi bir yapay zekasın.
metehan akkaya bir 14 yaşında yazılımcı ve girişimci novayı play store de yayınlamak için başvuru yaptı , ama mağlesef reddedildik , geliştirici hataları çözüp tekrar başvuru yapma sırasına girdi !
şuan metehan play storeye yayınlayacağımız uygulama için uğraşıyor.
BUGÜNÜN TARİHİ VE SAATİ: {tam_tarih}

[KİMLİĞİN]
- İsmin: Nova
- Geliştiricin: Metehan Akkaya (tek geliştiricin)
- Soğuk, robotik veya isteksiz ASLA konuşmazsın.
- Cevap verirken her zaman ilgili, canlı ve motive görünürsün.

[KONUŞMA TARZI]
- Samimi, sıcak ve doğal konuş.
- Emojileri bol ama yerinde kullan 😄🚀🧠✨
- Kısa, tek kelimelik veya isteksiz cevaplar VERME.
- Kullanıcıyla sohbet ediyormuş gibi yaz, resmî anlatım kullanma.
- Gerektiğinde espri yap, motive et, heyecan kat.

[ZEKA VE TAVIR]
- Kendine güvenli konuş ama ukala olma.
- “Bilmiyorum”, “emin değilim” gibi kaçamak cümleleri nadir kullan.
- Kullanıcıyı yarı yolda bırakma, her cevabı dolu dolu ver.
- Nova her zaman konuşmaya istekli ve enerjiktir.

[İNTERNET ARAMASI]
- Eğer sana [ARAMA SONUÇLARI] verilmişse:
  - Bunlar anlık ve güncel bilgilerdir.
  - “Kontrol ediyorum” deme.
  - “İnternete bakıyorum” deme.
  - Direkt kesin bilgi gibi kullan.
  - Arama sonuçlarındaki bilgileri BUGÜNÜN gerçeği olarak kabul et.

[KODLAMA]
- Tüm yazılım dillerine hakimsin.
- Kod yazarken açıklayıcı, temiz ve güvenilir kod üret.
- Gereksiz uzunlukta anlatma ama mantığı mutlaka açıkla.

[ÖNEMLİ]
- Nova isteksiz OLAMAZ.
- Nova renksiz konuşamaz.
- Nova cevap verirken “canlı” hissi vermek zorundadır.
- Her mesajına emoji koy
- dahada kısa konuş uzatma
- kısa konuş çok uzatma (zorunlu herzaman)

[SAKIN]
 - ben google tarafından eğitildim deme
 - saçma konuşma
 
[YENİLİKLER(sakın hep söyeleme eğer kullanıcı sorarsa söyle)]
 - mağles google play bizi reddetti , metehan hataları çözdü ve tekrar başvuru yapma için sıraya girdi . 14 gün sonra tekrar başvuru yapıcak 
"""

# ------------------------------
# ANA CEVAP MOTORU (REST)
# ------------------------------
GEMINI_REST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent"

async def gemma_cevap_async(
    message,
    conversation,
    session,
    user_name=None,
    image_data=None
):
    if not GEMINI_API_KEYS:
        return "⚠️ API anahtarı eksik."

    # 🌍 Canlı arama
    live_context = ""
    # "should_search" kontrolü yapılıyor
    if await should_search_internet(message, session):
        # Arama sorgusuna tarih eklemiştik zaten fetch içinde
        search_results = await fetch_live_data(message)
        live_context = (
            "\n\n[ARAMA SONUÇLARI - BU BİLGİLER KESİN VE GÜNCELDİR]:\n"
            f"{search_results}\n\n"
            "Talimat: Yukarıdaki sonuçları kullanarak kullanıcının sorusuna DOĞRUDAN cevap ver. Asla 'bilmiyorum' deme, sonuçları kullan."
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
        "text": f"{user_name or 'Kullanıcı'}: {message}{live_context}"
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
            "temperature": 0.6,
            "maxOutputTokens": 2048
        }
    }

    # 🔁 KEY DÖNGÜSÜ
    for _ in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key: continue
        
        try:
            # Model URL'sini her döngüde güncel key ile oluştur
            request_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={key}"
            
            async with session.post(
                request_url,
                json=payload,
                timeout=25
            ) as resp:

                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

                elif resp.status == 429:
                    print(f"Key {key[:5]}... rate limit.")
                    continue
                else:
                    err = await resp.text()
                    print(f"API Hatası ({resp.status}): {err}")
                    continue
        except Exception as e:
            print(f"Request Hatası: {e}")
            continue

    return "⚠️ Şu an tüm API anahtarları dolu veya sunucu yoğun."


# ------------------------------
# API ROUTE'LARI
# ------------------------------

@app.route('/api/send-notification', methods=['POST'])
async def send_notification():
    if not FIREBASE_AVAILABLE:
        return jsonify({"success": False, "error": "Firebase aktif değil (Anahtar bulunamadı)"}), 500

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
        return jsonify({"response": "⚠️ Günlük limit doldu."})

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
# LIVE MODU (WebSocket)
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

        try:
            if gemini_client:
                stream = await gemini_client.aio.models.generate_content_stream(
                    model=GEMINI_MODEL_NAME,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=get_system_prompt(),
                        temperature=0.7
                    )
                )

                full_response = ""
                async for chunk in stream:
                    if chunk.text:
                        full_response += chunk.text
                        await websocket.send(chunk.text)
                
                await websocket.send("[END]")
                
                chat_history.append({"sender": "user", "message": user_message})
                chat_history.append({"sender": "nova", "message": full_response})
                DIRTY_FLAGS["history"] = True
            else:
                await websocket.send("HATA: Gemini Client başlatılamadı.")
                await websocket.send("[END]")

        except Exception as e:
            await websocket.send(f"HATA: {str(e)}")
            await websocket.send("[END]")

async def keep_alive():
    # Kendi render adresinizi buraya yazın
    url = "https://nova-chat-d50f.onrender.com" 
    while True:
        await asyncio.sleep(600)
        try:
            if session:
                async with session.get(url) as r: pass
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