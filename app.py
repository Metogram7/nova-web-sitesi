import os
import json
import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from quart import Quart, request, jsonify
from quart_cors import cors

app = Quart(__name__)
app = cors(app)

# === Render uyumasın diye kendi kendine ping sistemi ===
async def keep_alive():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await session.get("https://nova-chat-d50f.onrender.com")
                print("✅ Keep-alive ping gönderildi (Nova Web aktif tutuluyor).")
        except Exception as e:
            print("⚠️ Keep-alive hatası:", e)
        await asyncio.sleep(600)  # 10 dakikada bir ping

@app.before_serving
async def startup():
    asyncio.create_task(keep_alive())

# === Dosya ayarları ===
HISTORY_FILE = "chat_history.json"
LAST_SEEN_FILE = "last_seen.json"

# === Dosyalar yoksa oluştur ===
for file in [HISTORY_FILE, LAST_SEEN_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump({}, f)

history_lock = asyncio.Lock()
last_seen_lock = asyncio.Lock()

# === JSON yükleme / kaydetme ===
async def load_json(file_path, lock):
    async with lock:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

async def save_json(file_path, data, lock):
    async with lock:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# === Nova'nın dahili tarihi ===
nova_datetime = datetime(2025, 11, 2, 22, 27)

def advance_nova_time(minutes: int = 1):
    global nova_datetime
    nova_datetime += timedelta(minutes=minutes)

def get_nova_date():
    days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
              "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    day_name = days[nova_datetime.weekday()]
    month_name = months[nova_datetime.month - 1]
    formatted_date = f"{nova_datetime.day} {month_name} {day_name}"
    formatted_time = f"{nova_datetime.hour:02d}:{nova_datetime.minute:02d}"
    return f"{formatted_date} {formatted_time}"

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
Kullanıcıya çok karmaşık cevaplar verme; anlaşılır ve düzenli cevaplar ver.
Güncel tarih ve saat (Nova simülasyonu): {nova_date}
"""

# === Gemini API isteği ===
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
    MODEL_NAME = "gemini-2.5-flash"
    API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

    last_msgs = conversation[-5:] if len(conversation) > 5 else conversation
    prompt = get_system_prompt() + "\n\n"
    for msg in last_msgs:
        role = "Kullanıcı" if msg.get("role") == "user" else "Nova"
        prompt += f"{role}: {msg.get('content')}\n"

    if user_name:
        prompt += f"\nNova, kullanıcının adı {user_name}. Ona samimi ve doğal biçimde cevap ver.\n"

    prompt += f"Kullanıcı: {message}\nNova:"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        emojis = ["😊", "😉", "🤖", "😄", "✨", "💬"]
                        if random.random() < 0.3 and not text.endswith(tuple(emojis)):
                            text += " " + random.choice(emojis)
                        advance_nova_time(1)
                        return text
                    else:
                        return "❌ API yanıtı beklenenden farklı."
                else:
                    return f"❌ API Hatası ({resp.status})"
    except asyncio.TimeoutError:
        return "❌ API yanıt vermiyor (timeout)"
    except Exception as e:
        return f"❌ Hata: {e}"

# === Nova'nın 3 gün özleme kontrolü ===
async def check_inactive_users():
    while True:
        last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
        history = await load_json(HISTORY_FILE, history_lock)
        now = datetime.utcnow()
        for user_id, last_time in list(last_seen.items()):
            try:
                last_dt = datetime.fromisoformat(last_time)
                if (now - last_dt).days >= 3:
                    # Kullanıcı 3 gündür yok
                    text = "Hey, seni 3 gündür görmüyorum 😢 Gel biraz konuşalım! 💫"
                    history.setdefault(user_id, {}).setdefault("default", [])
                    already_sent = any(
                        msg.get("text") == text for msg in history[user_id]["default"]
                    )
                    if not already_sent:
                        history[user_id]["default"].append({
                            "sender": "nova",
                            "text": text,
                            "ts": datetime.utcnow().isoformat(),
                            "auto": True
                        })
                        await save_json(HISTORY_FILE, history, history_lock)
            except Exception:
                continue
        await asyncio.sleep(600)  # 10 dakikada bir kontrol et

# === Arka planda mesaj üretme ===
async def background_fetch_and_save(userId, chatId, message, user_name):
    hist = await load_json(HISTORY_FILE, history_lock)
    conversation = [
        {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
        for msg in hist.get(userId, {}).get(chatId, [])
    ]
    reply = await gemma_cevap_async(message, conversation, user_name)
    hist = await load_json(HISTORY_FILE, history_lock)
    hist.setdefault(userId, {}).setdefault(chatId, [])
    hist[userId][chatId].append({
        "sender": "nova",
        "text": reply,
        "from_bg": True,
        "ts": datetime.utcnow().isoformat()
    })
    await save_json(HISTORY_FILE, hist, history_lock)

# === /api/chat ===
@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    if not data:
        return jsonify({"response": "❌ Geçersiz JSON"}), 400

    userId = data.get("userId", "anonymous")
    chatId = data.get("currentChat", "default")
    message = data.get("message", "")
    userInfo = data.get("userInfo", {})

    if not message.strip():
        return jsonify({"response": "❌ Mesaj boş."})

    # Son görülme kaydet
    last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
    last_seen[userId] = datetime.utcnow().isoformat()
    await save_json(LAST_SEEN_FILE, last_seen, last_seen_lock)

    hist = await load_json(HISTORY_FILE, history_lock)
    hist.setdefault(userId, {}).setdefault(chatId, [])
    conversation = [
        {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
        for msg in hist[userId][chatId]
    ]

    hist[userId][chatId].append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)

    existing_nova_replies = any(m.get("sender") == "nova" for m in hist[userId][chatId])
    if not existing_nova_replies:
        quick_reply = "Merhaba! Hemen bakıyorum... 🤖"
        hist[userId][chatId].append({
            "sender": "nova",
            "text": quick_reply,
            "ts": datetime.utcnow().isoformat(),
            "quick": True
        })
        await save_json(HISTORY_FILE, hist, history_lock)
        asyncio.create_task(background_fetch_and_save(userId, chatId, message, userInfo.get("name")))
        return jsonify({"response": quick_reply, "chatId": chatId, "updatedUserInfo": userInfo})

    reply = await gemma_cevap_async(message, conversation, userInfo.get("name"))
    hist[userId][chatId].append({"sender": "nova", "text": reply, "ts": datetime.utcnow().isoformat()})
    await save_json(HISTORY_FILE, hist, history_lock)
    return jsonify({"response": reply, "chatId": chatId, "updatedUserInfo": userInfo})

# === Geçmiş işlemleri ===
@app.route("/api/history", methods=["GET"])
async def get_history():
    userId = request.args.get("userId", "anonymous")
    history = await load_json(HISTORY_FILE, history_lock)
    return jsonify(history.get(userId, {}))

@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    data = await request.get_json()
    userId = data.get("userId")
    chatId = data.get("chatId")
    if not userId or not chatId:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400
    history = await load_json(HISTORY_FILE, history_lock)
    if userId in history and chatId in history[userId]:
        del history[userId][chatId]
        await save_json(HISTORY_FILE, history, history_lock)
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# === Ana sayfa (ping kontrolü için) ===
@app.route("/")
async def home():
    return "Nova Web aktif ✅"

# === Başlat ===
if __name__ == "__main__":
    asyncio.create_task(check_inactive_users())  # 3 gün kontrol sistemi
    port = int(os.environ.get("PORT", 5000))
    asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=True))
