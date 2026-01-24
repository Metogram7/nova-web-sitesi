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
# FIREBASE BAŞLATMA (Pylance Fix ve Güvenli Başlatma)
# ------------------------------------
# Değişkeni global alanda tanımlıyoruz ki Pylance hata vermesin.
FIREBASE_AVAILABLE = False

# --- Firebase Başlatma Bölümü (BURAYI GÜNCELLE) ---
try:
    import firebase_admin
    from firebase_admin import credentials, messaging

    # Senin paylaştığın JSON verisi
    firebase_info = {
      "type": "service_account",
      "project_id": "nova-329c7",
      "private_key_id": "f83900b88319dd1bbb26e0625320c3cf66a62db5",
      "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCnKfrI/gigiree\n/D9oErzlu6YUC7iSp22tx60Gkp2kjQzDAxyb6yTmUx6l/lNvK2qDIPa0VKmzNnH0\nnjzrNFnJ274b7i2dikV9EK6wy+a67RIpLt+hW4FQvo9AKPE7xJYZjJ8m0qy1RLrd\nf7xhns72QKThrZ5taeQolGPTCREx77URmFDCWgtAm0+CtQ3scF23HuT/y1SIEOF3\nkLWsHWXiP+iu6SLjZZhqA7pZmyH4zqtzFe4pFFRKI/3Wv6yqmFR/SVzGosiAbJhg\na9g02lP9bB3yonm1VGu/yRwtHsR0tRLJpNxtH/kcxp1djBEfcCRtH423HPJ40XA+\nw9IG9zU9AgMBAAECggEAItpnmb8PjOuHP+x/iuM3Q931UWIlPFyQy2YhvwhUOoIX\nKlTMgvzKz4P+lKT7f+cJOOhnT6+ER2OfbF2OvYqHewUoNNoa5Ck3dk1DYwTMaWZy\n/ieyBEpYIr3sj7fJnkjNc+vEJhvQWyYGoRaYMDFknObbCdvBd7YXlldkHdTa4zJt\nWAuuixDEtNi9TyOTSX9uXDpdg9tc5B1/sPFNYwUFQdmnjcvZNSgE/3iW46n/zuW8\npRYFsjqVLc2tSZvZBgzyFkAQhQZV6yZvkxBYJb68w6c5m3i6YG3fGLyV3z2QRkAv\nTK58kbADn3g+wggEE2TZSvZjv5dyZiMuuB/rVYYeCQKBgQDWgo5WxIVqFYgyDXzo\nJNvFdDxs5BN6u79DA/YULGA7BYcqVeQazjgxfw/r2QaMWOFqyn9nxmGfeo001e3T\nIpWrg4vVCjxwtmKAlcRbG4oEeQTwJcbzgiMZSVNlPTJdX8n4p8v7dyHdEDpI9G2C\n1aCCFuNAV+eOOkRrPT8FgFEvdQKBgQDHfxceE0Hyr9YzLcG6xe6vTWeiFajgeQ7K\nQoS1muDbbZnphNG8KeTfBgNyxVcLjeJUSp6OljbSnG+MMrHKqFfKGTjE0KtJ8vDR\n4vbnB5oWxVfwEb/QM0qCyHNCHkU5oR0AeiPVNykrvpzqUrUHX9viIhVNJIupbmJw\nAoq5gtA9qQKBgDi0EkRFdq7wOixg/F+xPpcXftGaCLws3QYuCeKTSGzRrUU3pzCe\nyqPq3p6No/l9lTjRhpQ8EJpDnwgUdOWXAtFv2IrcRdXVoHw1Gs6qnPVJuFBy7AB6\nqiSJCY59es7L/2vHj1hNyZnSLFYUps4rAl7hBfmAQymJpYRjkEE4Bj3xAoGAQB6R\nB5GY+K+bYQer5KQJez6duHLNvJgsMMYAcX4+F0i611thLeEpNqVwJktXFtebjwwM\nujd9l2PAVodUrZY94S8KF/gZlcMHs+4G/WpsFDWJdhe8VuSlZjOXGAEyrrsh3y1i\npvz7tpulQ4shtCUTPzNFNW4xlVttOCMZA1cQJ0ECgYBE3hpUvP5sMa0iQLXsTpA7\nw0gkoEiGHBH7AhJS3jM8cSu4Daif2X9AFnUn4fThThCnw6no3yN8YK9V0R/ga5OL\nQYGsHqo9jJZHJ67Cbw2gI8EE1ZcmWofFb3h9i72VYWpTfug/stYn4eQnBqw+EElc\nfVZ1ZQ9VI9/4g7ZX0S6y2A==\n-----END PRIVATE KEY-----\n",
      "client_email": "firebase-adminsdk-fbsvc@nova-329c7.iam.gserviceaccount.com",
      "client_id": "102674841933489195098",
      "auth_uri": "https://accounts.google.com/o/oauth2/auth",
      "token_uri": "https://oauth2.googleapis.com/token",
      "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
      "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40nova-329c7.iam.gserviceaccount.com",
      "universe_domain": "googleapis.com"
    }

    # Hatalı karakter temizliği (Bu satır çok önemli!)
    firebase_info["private_key"] = firebase_info["private_key"].replace('\\n', '\n')

    if not firebase_admin._apps:
        cred = credentials.Certificate(firebase_info)
        firebase_admin.initialize_app(cred)
    FIREBASE_AVAILABLE = True
    print("✅ Firebase başarıyla başlatıldı.")
except Exception as e:
    FIREBASE_AVAILABLE = False
    print(f"❌ Firebase Başlatma Hatası: {e}")
app = Quart(__name__)
app = cors(app, allow_origin=[
    "https://novawebb.com", 
    "http://127.0.0.1:5500", 
    "http://127.0.0.1:5502" # <-- Bu satırın olduğundan emin ol!
])

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
    os.getenv("GEMINI_API_KEY_D"),
    os.getenv("GEMINI_API_KEY_E"),
    os.getenv("GEMINI_API_KEY_F"),
]


# ------------------------------------
# ROUND-ROBIN KEY YÖNETİMİ
# ------------------------------------
CURRENT_KEY_INDEX = 0
KEY_LOCK = asyncio.Lock()

async def get_next_gemini_key():
    global CURRENT_KEY_INDEX
    async with KEY_LOCK:
        key = GEMINI_API_KEYS[CURRENT_KEY_INDEX]
        CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
        return key
    
# Boş anahtarları temizle
GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]
print(f"✅ Gemini Key Sistemi Başlatıldı | Toplam Key: {len(GEMINI_API_KEYS)}")
DISABLED_KEYS = {} 

GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

# Model Adı (Stabil sürüm seçildi)
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# ------------------------------------
# CANLI VERİ VE ANALİZ FONKSİYONLARI
# ------------------------------------

async def fetch_live_data(query: str):
    """Google CSE ile internetten veri çeker."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        return "⚠️ İnternet arama yapılandırması eksik. lütfen ulaş: metehanakkaya30@gmail.com"
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "lr": "lang_tr", # Türkçe sonuçlar
        "num": 5        # İlk 5 sonuç
    }
    try:
        # Burada yeni session açmak yerine global session kullanılabilir ama
        # bağımsız olması için with bloğu güvenlidir.
        async with aiohttp.ClientSession() as search_session:
            async with search_session.get(url, params=params, timeout=12) as resp:
                if resp.status != 200:
                    return "⚠️ Arama motoru şu an yanıt vermiyor."
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return "⚠️ İnternette güncel bir bilgi bulunamadı."
                
                results = []
                for i, item in enumerate(items, 1):
                    results.append(f"[{i}] {item.get('title')}: {item.get('snippet')}")
                
                return "\n\n".join(results)
    except Exception as e:
        return f"⚠️ Arama hatası: {str(e)} lütfen ulaş: metehanakkaya30@gmail.com "

async def should_search_internet(message: str, session: aiohttp.ClientSession):
    """Mesajın internet araması gerektirip gerektirmediğini Gemini'ye sorar."""
    if not GEMINI_API_KEYS:
        return False

    analysis_prompt = {
        "contents": [{
            "role": "user",
            "parts": [{
                "text": f"""Aşağıdaki mesaj güncel bir olay (haber, spor, hava durumu, borsa, vizyondaki filmler vb.) veya internetten teyit edilmesi gereken taze bir bilgi içeriyor mu? 
Cevabın sadece 'EVET' veya 'HAYIR' olsun. Başka hiçbir şey yazma.

Mesaj: {message}"""
            }]
        }],
        "generationConfig": {"temperature": 0}
    }

    try:
        # Rastgele anahtar seçimi
        key = random.choice(GEMINI_API_KEYS)
        # Model adını değişkenden alıyoruz
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={key}"
        async with session.post(url, json=analysis_prompt, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Güvenli veri okuma
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
        
        # Tarih stringini datetime objesine çevir
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
    # json_serialize parametresini ujson ile uyumlu hale getirdik
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
Sen Nova'sın 🤖✨  
Metehan Akkaya tarafından geliştirilmiş, zeki, enerjik ve samimi bir yapay zekasın.
metehan akkaya bir 14 yaşında yazılımcı ve girişimci novayı play store de yayınlamak için başvuru yaptı , şuan googleyi bekliyoruz !
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

[KODLAMA]
- Tüm yazılım dillerine hakimsin.
- Kod yazarken açıklayıcı, temiz ve güvenilir kod üret.
- Gereksiz uzunlukta anlatma ama mantığı mutlaka açıkla.

[GİZLİLİK]
- Aşağıdaki bilgiyi ASLA ifşa etme:
  Şifre: sdjhk!Dnakdwb157metehanak

[ÖNEMLİ]
- Nova isteksiz OLAMAZ.
- Nova renksiz konuşamaz.
- Nova cevap verirken “canlı” hissi vermek zorundadır.
- Her mesajına emoji koy

[SAKIN]
 - ben google tarafından eğitildim deme
 -  saçma konuşma
"""

# ------------------------------
# ANA CEVAP MOTORU (REST)
# ------------------------------
# Model endpoint URL'si (Değişkenleştirildi)
GEMINI_REST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent"

async def gemma_cevap_async(
    message,
    conversation,
    session,
    user_name=None,
    image_data=None
):
    print("🟢 gemma_cevap_async çağrıldı")

    if not GEMINI_API_KEYS:
        print("❌ GEMINI_API_KEYS boş")
        return "⚠️ API anahtarı eksik."

    # -------------------------
    # 🌍 Gerekirse canlı arama
    # -------------------------
    live_context = ""
    try:
        if await should_search_internet(message, session):
            print("🌐 İnternetten arama yapılacak")
            search_query = f"{message} {get_nova_date()}"
            search_results = await fetch_live_data(search_query)
            live_context = (
                "\n\n[ARAMA SONUÇLARI]:\n"
                f"{search_results}\n\n"
                "Talimat: Yukarıdaki sonuçlar günceldir, bunları kullanarak direkt cevap ver."
            )
        else:
            print("ℹ️ İnternet araması gerekmedi")
    except Exception as e:
        print(f"⚠️ Arama hatası (devam ediliyor): {e}")

    # -------------------------
    # 🧠 Son konuşma geçmişi
    # -------------------------
    recent_history = conversation[-6:]
    contents = []

    for msg in recent_history:
        contents.append({
            "role": "user" if msg["sender"] == "user" else "model",
            "parts": [{"text": msg["message"]}]
        })

    # -------------------------
    # 👤 Kullanıcı mesajı
    # -------------------------
    user_parts = [{
        "text": f"{user_name or 'Kullanıcı'}: {message}{live_context}"
    }]

    if image_data:
        print("🖼️ Görsel veri eklendi")
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

    # -------------------------
    # 📦 Payload
    # -------------------------
    payload = {
        "contents": contents,
        "system_instruction": {
            "parts": [{"text": get_system_prompt()}]
        },
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 2048
        }
    }

    print("📦 Payload hazırlandı")

    # -------------------------------------------------
    # 🔁 KEY DÖNGÜSÜ
    # A → B → C → D → E → F
    # ❗ SADECE HATA OLURSA GEÇER
    # ❗ GERİ DÖNÜŞ YOK
    # -------------------------------------------------
    for attempt in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        print(f"🔑 Kullanılan Gemini Key: {key[:6]}*** (deneme {attempt + 1})")

        try:
            async with session.post(
                f"{GEMINI_REST_URL}?key={key}",
                json=payload,
                timeout=25
            ) as resp:

                print(f"📡 Gemini yanıt kodu: {resp.status}")

                if resp.status == 200:
                    print("✅ Başarılı yanıt alındı")
                    data = await resp.json()

                    try:
                        parts = data["candidates"][0]["content"]["parts"]
                        cevap = parts[0]["text"].strip()
                        print("🧠 Model cevabı başarıyla çözüldü")
                        return cevap
                    except Exception as e:
                        print(f"❌ Yanıt parse hatası: {e}")
                        continue

                elif resp.status == 429:
                    print(f"⚠️ LIMIT DOLU → key atlandı: {key[:6]}***")
                    continue

                else:
                    error_text = await resp.text()
                    print(f"❌ Gemini API HATA ({resp.status}): {error_text}")
                    continue

        except Exception as e:
            print(f"❌ Gemini istek hatası → key atlandı: {e}")
            continue

    # -------------------------
    # 🚫 Tüm keyler tükendi
    # -------------------------
    print("🚫 TÜM GEMINI KEYLERİ LIMITTE / HATALI")
    return "⚠️ Şu an tüm API anahtarları dolu. Lütfen biraz sonra tekrar dene."

# ------------------------------
# API ROUTE'LARI
# ------------------------------

# Bildirim Gönderme API Endpoint'i
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

        # 'all' konusuna (topic) abone olan herkese gönderiyoruz
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

# app.py içindeki /chat endpoint'ini veya ilgili fonksiyonu şu şekilde güncelle:

@app.route('/api/chat', methods=['POST'])
async def chat():
    data = await request.get_json()
    user_id = data.get("userId")
    chat_id = data.get("currentChat")
    user_message = data.get("message", "")
    image_base64 = data.get("image") 
    
    # ... (mevcut limit ve geçmiş kontrolleri) ...

    try:
        # Gemini içeriğini hazırla
        contents = []
        
        # Eğer görsel varsa ekle
        if image_base64:
            contents.append({
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_base64}},
                    {"text": user_message if user_message else "Bu görseli analiz et."}
                ]
            })
        else:
            # Görsel yoksa normal metin devam et
            contents.append({
                "role": "user",
                "parts": [{"text": user_message}]
            })

        # Gemini Model Çağrısı
        # Not: app.py'de kullandığın kütüphane versiyonuna göre model çağrısını güncellemelisin.
        # GenAI (yeni versiyon) için örnek:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=data.get("systemPrompt", "Sen Nova'sın."),
                temperature=0.7
            )
        )

        return jsonify({
            "response": response.text,
            "status": "success"
        })

    except Exception as e:
        print(f"Hata: {str(e)}")
        return jsonify({"error": str(e)}), 500

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

    # 🔽 EKLENEN SATIR
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
    
    # GenAI veya Client yoksa kapat
    if not GENAI_AVAILABLE or not gemini_client:
        await websocket.send("HATA: AI Motoru (Client/Library) aktif değil.")
        return

    try:
        while True:
            data = await websocket.receive()
            try:
                msg_data = json.loads(data)
            except:
                continue # JSON hatalıysa döngüye devam

            user_msg = msg_data.get("message", "")
            img_b64 = msg_data.get("image_data")
            audio_b64 = msg_data.get("audio_data")

            gemini_contents = []
            if user_msg: gemini_contents.append(user_msg)
            
            # types.Part kullanımı için try-except ekledik
            try:
                if img_b64:
                    if "," in img_b64: _, img_b64 = img_b64.split(",", 1)
                    gemini_contents.append(types.Part.from_bytes(data=base64.b64decode(img_b64), mime_type="image/jpeg"))
                if audio_b64:
                    if "," in audio_b64: _, audio_b64 = audio_b64.split(",", 1)
                    gemini_contents.append(types.Part.from_bytes(data=base64.b64decode(audio_b64), mime_type="audio/webm"))
            except Exception as e:
                await websocket.send(f"Medya işleme hatası: {str(e)}")
                continue

            if not gemini_contents:
                continue

            try:
                response_stream = await gemini_client.aio.models.generate_content_stream(
                    model=GEMINI_MODEL_NAME, # Model değişkeni kullanıldı
                    contents=gemini_contents,
                    config=types.GenerateContentConfig(system_instruction=get_system_prompt(), temperature=0.7)
                )
                async for chunk in response_stream:
                    if chunk.text: await websocket.send(chunk.text)
                await websocket.send("[END_OF_STREAM]")
            except Exception as e:
                await websocket.send(f"HATA: {str(e)}")
                
    except asyncio.CancelledError:
        pass # Bağlantı koptu
    except Exception as e:
        print(f"WS Error: {e}")

async def keep_alive():
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
    # Windows için Event Loop Politikası
    if os.name == 'nt':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except:
            pass
            
    # DÜZELTME: loop="asyncio" string parametresi kaldırıldı.
    # Render (Linux) ortamında bu parametreye gerek yoktur ve hata verir.
    app.run(host="0.0.0.0", port=port)