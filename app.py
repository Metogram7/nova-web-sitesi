import os
import json
import asyncio
import aiohttp
import random
import traceback
import ssl
from datetime import datetime, timedelta

# Flask importlarını Quart ile çakışmaması için düzenledik
from quart import Quart, request, jsonify, send_file
from quart_cors import cors
from werkzeug.datastructures import FileStorage

# E-posta/SMTP Kütüphane İçe Aktarımları
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Firebase
import firebase_admin
from firebase_admin import credentials, messaging

# --- Uygulama Başlatma ---
app = Quart(__name__)
app = cors(app)
session: aiohttp.ClientSession | None = None

# ------------------------------------
# E-POSTA AYARLARI
# ------------------------------------
# Render Environment'dan okur, bulamazsa varsayılanı kullanır
MAIL_ADRES = "nova.ai.v4.2@gmail.com"
# ÖNCE Render Environment'a bakar, yoksa koddakini alır
MAIL_SIFRE = os.getenv("MAIL_SIFRE", "gamtdoiralefaruk") 
ALICI_ADRES = MAIL_ADRES

# ------------------------------------

# --- Uygulama Yaşam Döngüsü (Startup/Cleanup) ---
@app.before_serving
async def startup():
    global session
    # Timeout ayarları
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    
    # SSL Hatalarını önlemek için (Render ve Local uyumlu)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    
    # Arka plan görevlerini başlat
    asyncio.create_task(keep_alive())
    asyncio.create_task(check_inactive_users())

@app.after_serving
async def cleanup():
    global session
    if session:
        await session.close()

# --- Arka Plan Görevleri ---
async def keep_alive():
    """Render gibi platformlarda uygulamanın uykuya dalmasını engeller."""
    # Kendi URL'nizi Environment'dan alabilir veya hardcode edebilirsiniz
    url = "https://nova-chat-d50f.onrender.com" 
    
    while True:
        try:
            await asyncio.sleep(600) # 10 dakika bekle
            if session and not session.closed:
                async with session.get(url, timeout=10) as r:
                    if r.status == 200:
                        print("✅ Keep-alive başarılı.")
                    else:
                        print(f"⚠️ Keep-alive status: {r.status}")
        except Exception as e:
            # Hata olsa bile döngüyü kırma, sadece logla
            print(f"⚠️ Keep-alive bağlantı uyarısı: {e}")

# --- Dosya ve Kilit (Lock) Yönetimi ---
HISTORY_FILE = "chat_history.json"
LAST_SEEN_FILE = "last_seen.json"
CACHE_FILE = "cache.json"
TOKENS_FILE = "tokens.json" 

files_to_check = [HISTORY_FILE, LAST_SEEN_FILE, CACHE_FILE, TOKENS_FILE]
for file in files_to_check:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            if file == TOKENS_FILE:
                json.dump([], f)
            else:
                json.dump({}, f)

history_lock = asyncio.Lock()
last_seen_lock = asyncio.Lock()
cache_lock = asyncio.Lock()
tokens_lock = asyncio.Lock()

async def load_json(file, lock):
    async with lock:
        try:
            with open(file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return [] if file == TOKENS_FILE else {}

async def save_json(file, data, lock):
    async with lock:
        tmp = file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file)

# --- Nova Simülasyonu Zamanı ---
nova_datetime = datetime(2025, 11, 2, 22, 27)

def get_nova_date():
    d = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    m = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return f"{nova_datetime.day} {m[nova_datetime.month-1]} {d[nova_datetime.weekday()]} {nova_datetime.hour:02d}:{nova_datetime.minute:02d}"

# --- Google CSE ayarları ---
# Environment'dan okumayı dener, yoksa sabit değeri kullanır
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "AIzaSyBhARNUY0O6_CRWx9n9Ajbw4W4cyydYgVg")
GOOGLE_CSE_ID = "e1d96bb25ff874031"

# --- Gemini API ayarları (DÜZELTİLEN KISIM) ---
# Render Environment Variables kısmından anahtarları çeker
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_A"),
    os.getenv("GEMINI_API_KEY_B"),
    os.getenv("GEMINI_API_KEY_C"),
]
# None (boş) olanları listeden temizler
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key is not None]

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

async def gemma_cevap_async(message: str, conversation: list, session: aiohttp.ClientSession, user_name=None):
    """Mesajı işleyip Gemini API'den yanıt alır."""
    
    # Eğer hiç anahtar yoksa hata dön
    if not GEMINI_API_KEYS:
        return "⚠️ Sistem Hatası: API Anahtarı bulunamadı (Environment Variables kontrol edin)."

    # --- Google araması gereksinimi ---
    keywords = ["bugün", "güncel", "döviz", "euro", "dolar", "hava durumu", "skor", "haber", "son dakika"]
    use_google = any(kw in message.lower() for kw in keywords)

    google_result_text = ""
    if use_google:
        try:
            params = {
                "key": GOOGLE_CSE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": message,
                "num": 3
            }
            async with session.get("https://www.googleapis.com/customsearch/v1", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    results = []
                    for it in items:
                        results.append(f"{it.get('title')}\n{it.get('snippet')}\n{it.get('link')}")
                    if results:
                        google_result_text = "Güncel bilgiler:\n" + "\n\n".join(results)
        except Exception as e:
            google_result_text = f"❌ Google arama hatası: {e}"

    # --- Gemini payload hazırlama ---
    contents = []
    # Son 15 mesajı geçmişe ekle
    for msg in conversation[-15:]:
        role = "user" if msg["sender"] == "user" else "model"
        if msg.get("content") and str(msg["content"]).strip():
            contents.append({"role": role, "parts": [{"text": str(msg['content'])}]})

    current_message_text = f"{user_name}: {message}" if user_name else f"Kullanıcı: {message}"
    
    if google_result_text:
        current_message_text += f"\n\n{google_result_text}"
        
    contents.append({"role": "user", "parts": [{"text": current_message_text}]})

    # System Prompt (Kısa versiyon, tam metni yukarıdan alabilirsiniz veya buraya gömebilirsiniz)
    system_text = f"Sen Nova'sın. Tarih: {get_nova_date()}. Metehan Akkaya tarafından geliştirildin. Kod yazman istenirse yaz. Google araması sonucuna göre güncel bilgi ver."

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": system_text}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    # --- Gemini API çağrısı ---
    for key in GEMINI_API_KEYS:
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        # 3 deneme hakkı
        for attempt in range(1, 4):
            try:
                async with session.post(GEMINI_API_URL, headers=headers, json=payload, timeout=30) as resp:
                    if resp.status != 200:
                        # Hata detayı için log
                        # print(f"API Hata: {resp.status} - {await resp.text()}")
                        continue
                    
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(part.get("text", "") for part in parts if "text" in part).strip()
                        if text:
                            return text
            except Exception:
                await asyncio.sleep(1)
                continue

    if google_result_text:
        return google_result_text

    return "❌ Bağlantı hatası veya yanıt alınamadı."

# ------------------------------
# Inaktif Kullanıcı Kontrolü
# ------------------------------
async def check_inactive_users():
    while True:
        try:
            last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
            hist = await load_json(HISTORY_FILE, history_lock)
            now = datetime.utcnow()
            for uid, last in list(last_seen.items()):
                if (now - datetime.fromisoformat(last)).days >= 3:
                    msg = "Hey, seni 3 gündür görmüyorum 😢 Gel konuşalım 💫"
                    hist.setdefault(uid, {}).setdefault("default", [])
                    if not any(m.get("text") == msg for m in hist[uid]["default"]): 
                        hist[uid]["default"].append({"sender": "nova", "text": msg, "ts": datetime.utcnow().isoformat(), "auto": True})
                        await save_json(HISTORY_FILE, hist, history_lock)
        except Exception:
            pass
        await asyncio.sleep(600)

# ------------------------------
# HATA BİLDİRİMİ ROUTE
# ------------------------------
@app.post("/send-mail")
async def send_mail():
    form = await request.form
    files = await request.files
    username = form.get("username", "").strip()
    user_email = form.get("user_email", "").strip()
    message = form.get("message", "").strip()
    uploaded_file: FileStorage = files.get("photo")

    if not username or not user_email or not message:
        return jsonify({"status": "Eksik bilgi."}), 400

    msg = MIMEMultipart()
    msg["Subject"] = f"[HATA] {username}"
    msg["From"] = MAIL_ADRES
    msg["To"] = ALICI_ADRES

    email_body = f"Kullanıcı: {username}\nMail: {user_email}\nMesaj:\n{message}"
    file_name = None

    if uploaded_file and uploaded_file.filename:
        try:
            file_name = uploaded_file.filename
            mime_type = uploaded_file.mimetype or 'application/octet-stream'
            file_data = uploaded_file.read() 
            maintype, subtype = mime_type.split('/', 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(file_data)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{file_name}"')
            msg.attach(part)
        except Exception:
            email_body += "\n\n[Dosya yükleme hatası]"

    msg.attach(MIMEText(email_body, 'plain', 'utf-8'))

    try:
        def send_sync_mail():
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(MAIL_ADRES, MAIL_SIFRE)
            server.sendmail(MAIL_ADRES, ALICI_ADRES, msg.as_string())
            server.quit()

        await asyncio.to_thread(send_sync_mail)
        return jsonify({"status": "Bildirim gönderildi."})

    except Exception as e:
        return jsonify({"status": f"Hata: {str(e)}"}), 500

# ------------------------------
# Ana API route'ları
# ------------------------------
@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json(force=True)
    userId = data.get("userId", "anon")
    chatId = data.get("currentChat", "default")
    message = (data.get("message") or "").strip()
    userInfo = data.get("userInfo", {})

    if not message:
        return jsonify({"response": "Mesaj boş olamaz."}), 400

    cache = await load_json(CACHE_FILE, cache_lock)
    cache_key = f"{userId}:{message.lower()}"
    if cache_key in cache:
        return jsonify({"response": cache[cache_key]["response"], "cached": True})

    last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
    last_seen[userId] = datetime.utcnow().isoformat()
    await save_json(LAST_SEEN_FILE, last_seen, last_seen_lock)

    hist = await load_json(HISTORY_FILE, history_lock)
    chat_list = hist.setdefault(userId, {}).setdefault(chatId, [])
    chat_list.append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)

    conv_for_prompt = [{"sender": msg["sender"], "content": msg["text"]} for msg in chat_list]
    global session
    reply = await gemma_cevap_async(message, conv_for_prompt, session, userInfo.get("name"))

    chat_list.append({"sender": "nova", "text": reply, "ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)

    cache[cache_key] = {"response": reply}
    await save_json(CACHE_FILE, cache, cache_lock)

    return jsonify({"response": reply, "cached": False})

@app.route("/")
async def home():
    return "Nova Web aktif ✅ (v4.2)"

@app.route("/api/history")
async def history():
    uid = request.args.get("userId", "anon")
    data = await load_json(HISTORY_FILE, history_lock)
    return jsonify(data.get(uid, {}))

@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    data = await request.get_json()
    uid, cid = data.get("userId"), data.get("chatId")
    hist = await load_json(HISTORY_FILE, history_lock)
    if uid in hist and cid in hist[uid]:
        del hist[uid][cid]
    await save_json(HISTORY_FILE, hist, history_lock)
    return jsonify({"success": True})

@app.route("/download_txt", methods=["POST"])
async def download_txt():
    try:
        data = await request.get_json()
        text_content = data.get("text", "")
        filename = f"nova_text_{int(datetime.now().timestamp())}.txt"
        # Render'da /tmp klasörü yazılabilir alandır
        filepath = os.path.join("/tmp", filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text_content)
        
        return await send_file(filepath, as_attachment=True, attachment_filename=filename)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==========================================
# NOVA BİLDİRİM SİSTEMİ (Firebase)
# ==========================================
try:
    if not firebase_admin._apps:
        # serviceAccountKey.json dosyasının varlığını kontrol et
        if os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred)
            print("✅ Nova Bildirim Sistemi Aktif.")
        else:
            print("⚠️ serviceAccountKey.json bulunamadı, bildirimler devre dışı.")
except Exception as e:
    print(f"⚠️ Bildirim hatası: {e}")

@app.route("/api/subscribe", methods=["POST"])
async def subscribe():
    data = await request.get_json()
    token = data.get("token")
    if token:
        async with tokens_lock:
            tokens = await load_json(TOKENS_FILE, tokens_lock)
            if token not in tokens:
                tokens.append(token)
                await save_json(TOKENS_FILE, tokens, tokens_lock)
    return jsonify({"success": True})

async def broadcast_worker(tokens, message_data):
    try:
        chunk_size = 500
        chunks = [tokens[i:i + chunk_size] for i in range(0, len(tokens), chunk_size)]
        for chunk in chunks:
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title="Nova 📢", body=message_data),
                tokens=chunk
            )
            await asyncio.to_thread(messaging.send_multicast, msg)
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Broadcast Error: {e}")

@app.route("/api/admin/broadcast", methods=["POST"])
async def send_broadcast_message():
    data = await request.get_json(force=True)
    if data.get("password") != "sd157metehanak":
        return jsonify({"success": False, "error": "Hatalı Şifre"}), 403
    
    tokens = await load_json(TOKENS_FILE, tokens_lock)
    if tokens:
        app.add_background_task(broadcast_worker, tokens, data.get("message"))
    return jsonify({"success": True, "count": len(tokens)})

if __name__ == "__main__":
    print("Nova Web başlatılıyor...")
    # Port'u environment'dan alır
    port = int(os.getenv("PORT", 5000))
    asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=False))