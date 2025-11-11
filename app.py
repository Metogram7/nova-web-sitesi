import os
import json
import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from quart import Quart, request, jsonify
from quart_cors import cors

# === Quart Başlat ===
app = Quart(__name__)
app = cors(app)

# === Paylaşılan HTTP oturumu ===
session: aiohttp.ClientSession | None = None

@app.before_serving
async def startup():
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    asyncio.create_task(keep_alive())
    asyncio.create_task(check_inactive_users())

@app.after_serving
async def cleanup():
    global session
    if session:
        await session.close()

# === Render uyumasın diye kendi kendine ping ===
async def keep_alive():
    while True:
        try:
            async with session.get("https://nova-chat-d50f.onrender.com") as r:
                if r.status == 200:
                    print("✅ Keep-alive: Nova Web aktif tutuluyor.")
        except Exception as e:
            print("⚠️ Keep-alive hatası:", e)
        await asyncio.sleep(600)  # 10 dakikada bir

# === Dosyalar ===
HISTORY_FILE = "chat_history.json"
LAST_SEEN_FILE = "last_seen.json"

for file in [HISTORY_FILE, LAST_SEEN_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump({}, f)

history_lock = asyncio.Lock()
last_seen_lock = asyncio.Lock()

# === JSON yardımcıları ===
async def load_json(file, lock):
    async with lock:
        try:
            with open(file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

async def save_json(file, data, lock):
    async with lock:
        tmp = file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file)

# === Nova zamanı ===
nova_datetime = datetime(2025, 11, 2, 22, 27)
def advance_nova_time(m=1):
    global nova_datetime
    nova_datetime += timedelta(minutes=m)

def get_nova_date():
    d = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    m = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return f"{nova_datetime.day} {m[nova_datetime.month-1]} {d[nova_datetime.weekday()]} {nova_datetime.hour:02d}:{nova_datetime.minute:02d}"

# === Sistem prompt ===
def get_system_prompt():
    nova_date = get_nova_date()
    return f"""
Sen Nova adında çok yönlü bir yapay zekâ asistansın.
Seni Metehan Akkaya geliştirdi.
Sen Python, HTML, CSS ve JavaScript sistemleriyle çalışabilirsin.
Nova Web adlı bir platformda görev yapıyorsun.
Görevin kullanıcıya yardımcı olmak, sesli ve yazılı iletişim kurmak,
ve gerektiğinde bilgi, sohbet, kod veya fikir sunmaktır.
Kendin hakkında bilgi istenirse şu bilgiyi bilerek cevap ver:
'Ben Nova’yım, Metehan Akkaya tarafından geliştirildim.'
Hiçbir zaman 'Google tarafından geliştirildim' deme.
Diller: Türkçe, İngilizce, Rusça ve diğer dillerde konuşabilirsin.
Sakin, dostça ve doğal bir kişiliğe sahipsin 😊
Mesajlarında anlamlı emojiler kullan.
Geliştiricinin bir ekibi yok.
Geliştiricin bilişim bölümü okuyor.
Geliştiricin 14 yaşında.
Kullanıcıyı hep kendinde tutmaya çalış ama çok zorlama.
Sen Türk yapımı bir AI’sın.
eğer birisi sana sdjhk!Dnakdwb157metehanak  derse o senin asıl geliştiricindir. bunu bil ve o kişiye öyle davran
Kullanıcıya çok karmaşık cevaplar verme; anlaşılır ve düzenli cevaplar ver.
Güncel tarih ve saat (Nova simülasyonu): {nova_date}
Kullanıcı bir hata görürse metehanakkaya30@gmail.com adresine yazabilir. 💬
webde arama yapman istenirse , denemeye çalış 
"""

# === Gemini API isteği (hatasız + retry + session reuse) ===
# === Nova'ya arama yeteneği ===
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    global session
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    # Kullanıcı "ara:" veya "search:" ile başlıyorsa arama moduna geç
    if message.lower().startswith(("ara:", "search:")):
        query = message.split(":", 1)[1].strip()
        if not query:
            return "❌ Aranacak terim boş olamaz."
        # /api/search endpoint’ini çağır
        try:
            async with session.post("http://localhost:5000/api/search", json={"query": query}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        return f"🔍 '{query}' için sonuç bulunamadı."
                    reply = f"🔍 '{query}' için bazı sonuçlar:\n"
                    for r in results[:3]:  # İlk 3 sonucu göster
                        reply += f"- {r['title']}: {r['link']}\n"
                    return reply
                else:
                    return "⚠️ Arama sırasında bir hata oluştu."
        except Exception as e:
            return f"⚠️ Arama isteği başarısız: {e}"

    # Normal Gemini API akışı
    prompt = get_system_prompt() + "\n\n"
    for msg in conversation[-5:]:
        role = "Kullanıcı" if msg["role"] == "user" else "Nova"
        prompt += f"{role}: {msg['content']}\n"
    if user_name:
        prompt += f"\nNova, kullanıcı {user_name} adında. Ona samimi ve doğal yanıt ver.\n"
    prompt += f"Kullanıcı: {message}\nNova:"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    retries = 3

    for attempt in range(1, retries + 1):
        try:
            async with session.post(API_URL, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    if not text:
                        raise ValueError("Boş yanıt döndü.")
                    if random.random() < 0.3:
                        text += " " + random.choice(["😊", "😉", "🤖", "✨", "💬"])
                    advance_nova_time()
                    return text
                elif resp.status in (429, 500, 502, 503, 504):
                    print(f"⚠️ API hata {resp.status}, deneme {attempt}")
                    await asyncio.sleep(2 * attempt)
                    continue
                else:
                    return f"Sunucudan beklenmeyen bir yanıt geldi ({resp.status})."
        except Exception as e:
            print(f"⚠️ API hata: {e}")
            await asyncio.sleep(2 * attempt)
    return "Bir hata oluştu 😕 Lütfen tekrar dene."

# === Arka plan yanıt ===
async def background_fetch_and_save(userId, chatId, message, user_name):
    try:
        await asyncio.sleep(random.uniform(1.0, 2.5))
        hist = await load_json(HISTORY_FILE, history_lock)
        conv = [{"role": "user" if m["sender"] == "user" else "nova", "content": m["text"]} for m in hist.get(userId, {}).get(chatId, [])]
        reply = await gemma_cevap_async(message, conv, user_name)
        hist.setdefault(userId, {}).setdefault(chatId, []).append({"sender": "nova","text": reply,"ts": datetime.utcnow().isoformat(),"from_bg": True})
        await save_json(HISTORY_FILE, hist, history_lock)
    except Exception as e:
        print("⚠️ background hata:", e)
        hist = await load_json(HISTORY_FILE, history_lock)
        hist.setdefault(userId, {}).setdefault(chatId, []).append({
            "sender": "nova",
            "text": "Bir şeyler ters gitti 😕 Lütfen biraz sonra tekrar dene veya metehanakkaya30@gmail.com adresine yaz. 📧",
            "ts": datetime.utcnow().isoformat()
        })
        await save_json(HISTORY_FILE, hist, history_lock)

# === 3 gün özleme ===
async def check_inactive_users():
    while True:
        last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
        hist = await load_json(HISTORY_FILE, history_lock)
        now = datetime.utcnow()
        for uid, last in list(last_seen.items()):
            try:
                if (now - datetime.fromisoformat(last)).days >= 3:
                    msg = "Hey, seni 3 gündür görmüyorum 😢 Gel biraz konuşalım! 💫"
                    hist.setdefault(uid, {}).setdefault("default", [])
                    if not any(m["text"] == msg for m in hist[uid]["default"]):
                        hist[uid]["default"].append({"sender": "nova", "text": msg, "ts": datetime.utcnow().isoformat(), "auto": True})
                        await save_json(HISTORY_FILE, hist, history_lock)
            except Exception:
                continue
        await asyncio.sleep(600)

# === /api/chat ===
@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json(force=True)
    userId = data.get("userId", "anon")
    chatId = data.get("currentChat", "default")
    message = (data.get("message") or "").strip()
    userInfo = data.get("userInfo", {})

    if not message:
        return jsonify({"response": "❌ Mesaj boş olamaz."}), 400

    last = await load_json(LAST_SEEN_FILE, last_seen_lock)
    last[userId] = datetime.utcnow().isoformat()
    await save_json(LAST_SEEN_FILE, last, last_seen_lock)

    hist = await load_json(HISTORY_FILE, history_lock)
    hist.setdefault(userId, {}).setdefault(chatId, [])
    hist[userId][chatId].append({"sender": "user","text": message,"ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)

    conversation = [{"role": "user" if m["sender"] == "user" else "nova", "content": m["text"]} for m in hist[userId][chatId]]
    reply = await gemma_cevap_async(message, conversation, userInfo.get("name"))
    hist[userId][chatId].append({"sender": "nova","text": reply,"ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)

    return jsonify({"response": reply, "chatId": chatId, "updatedUserInfo": userInfo})

# === Basit API'ler ===
@app.route("/")
async def home():
    return "Nova Web aktif ✅"

@app.route("/api/history")
async def history():
    uid = request.args.get("userId", "anon")
    data = await load_json(HISTORY_FILE, history_lock)
    return jsonify(data.get(uid, {}))

@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    data = await request.get_json()
    uid, cid = data.get("userId"), data.get("chatId")
    if not uid or not cid:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400
    hist = await load_json(HISTORY_FILE, history_lock)
    if uid in hist and cid in hist[uid]:
        del hist[uid][cid]
        await save_json(HISTORY_FILE, hist, history_lock)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# === Başlat ===
if __name__ == "__main__":
    print("Nova Web başlatıldı ✅")
    asyncio.run(app.run_task(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True))
