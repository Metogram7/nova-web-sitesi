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

# === Global aiohttp session ve semaphore ===
session = None
API_SEMAPHORE = asyncio.Semaphore(5)  # Maksimum 5 eş zamanlı API isteği

# === Keep-alive ping ===
async def keep_alive():
    while True:
        try:
            async with session.get("https://nova-chat-d50f.onrender.com") as resp:
                print("✅ Keep-alive ping gönderildi (Nova Web aktif tutuluyor).")
        except Exception as e:
            print("⚠️ Keep-alive hatası:", e)
        await asyncio.sleep(600)  # 10 dakikada bir ping

@app.before_serving
async def startup():
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    asyncio.create_task(keep_alive())

# === Dosya ayarları ===
HISTORY_FILE = "chat_history.json"
LAST_SEEN_FILE = "last_seen.json"

for file in [HISTORY_FILE, LAST_SEEN_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump({}, f)

history_lock = asyncio.Lock()
last_seen_lock = asyncio.Lock()

# === Batch save sistemi ===
save_queue = []

async def save_json(file_path, data, lock):
    async with lock:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

async def batch_save_worker():
    while True:
        if save_queue:
            for file, data, lock in save_queue:
                await save_json(file, data, lock)
            save_queue.clear()
        await asyncio.sleep(1)

def queue_save(file, data, lock):
    save_queue.append((file, data, lock))

# === JSON yükleme ===
async def load_json(file_path, lock):
    async with lock:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

# === Nova zamanı ===
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
def get_system_prompt(user_name=None):
    nova_date = get_nova_date()
    user_part = f"\nNova, kullanıcının adı {user_name}. Ona samimi ve doğal biçimde cevap ver." if user_name else ""
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
{user_part}
"""

# === Gemini API isteği ===
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
    MODEL_NAME = "gemini-2.5-flash"
    API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

    last_msgs = conversation[-3:]  # hız için son 3 mesaj
    prompt = get_system_prompt(user_name) + "\n\n"
    for msg in last_msgs:
        role = "Kullanıcı" if msg.get("role") == "user" else "Nova"
        prompt += f"{role}: {msg.get('content')}\n"
    prompt += f"Kullanıcı: {message}\nNova:"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    async with API_SEMAPHORE:  # eş zamanlı isteği sınırla
        try:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        # Emoji mantığı
                        if "?" in text: emoji = "🤔"
                        elif "!" in text: emoji = "✨"
                        elif len(text.split()) <= 5: emoji = "😉"
                        else: emoji = "😊"
                        if not text.endswith(emoji): text += " " + emoji
                        advance_nova_time(1)
                        text += f"\n(Nova saati: {get_nova_date()})"
                        return text
                    else:
                        return "❌ API yanıtı beklenenden farklı."
                else:
                    return f"❌ API Hatası ({resp.status})"
        except asyncio.TimeoutError:
            return "❌ API yanıt vermiyor (timeout)"
        except Exception as e:
            return f"❌ Hata: {e}"

# === Özlem mesajları (1 ve 3 gün) ===
async def check_inactive_users():
    while True:
        last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
        history = await load_json(HISTORY_FILE, history_lock)
        now = datetime.utcnow()
        for user_id, last_time in list(last_seen.items()):
            try:
                last_dt = datetime.fromisoformat(last_time)
                days_diff = (now - last_dt).days
                messages = []
                if days_diff >= 1: messages.append("Seni 1 gündür görmüyorum 😢")
                if days_diff >= 3: messages.append("Hey, seni 3 gündür görmüyorum 😢 Gel biraz konuşalım! 💫")
                for text in messages:
                    history.setdefault(user_id, {}).setdefault("default", [])
                    already_sent = any(msg.get("text") == text for msg in history[user_id]["default"])
                    if not already_sent:
                        history[user_id]["default"].append({
                            "sender": "nova",
                            "text": text,
                            "ts": datetime.utcnow().isoformat(),
                            "auto": True
                        })
                        queue_save(HISTORY_FILE, history, history_lock)
            except Exception:
                continue
        await asyncio.sleep(600)

# === Arka plan mesaj üretme ===
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
    queue_save(HISTORY_FILE, hist, history_lock)

# === /api/chat ===
@app.route("/api/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    if not data: return jsonify({"response": "❌ Geçersiz JSON"}), 400

    userId = data.get("userId", "anonymous")
    chatId = data.get("currentChat", "default")
    message = data.get("message", "")
    userInfo = data.get("userInfo", {})

    if not message.strip(): return jsonify({"response": "❌ Mesaj boş."})

    last_seen = await load_json(LAST_SEEN_FILE, last_seen_lock)
    last_seen[userId] = datetime.utcnow().isoformat()
    queue_save(LAST_SEEN_FILE, last_seen, last_seen_lock)

    hist = await load_json(HISTORY_FILE, history_lock)
    hist.setdefault(userId, {}).setdefault(chatId, [])
    conversation = [
        {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
        for msg in hist[userId][chatId]
    ]

    hist[userId][chatId].append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    queue_save(HISTORY_FILE, hist, history_lock)

    # Quick-reply
    quick_reply = "Merhaba! Dediğini anlamadım lütfen birdaha yazarmısın 🙂"
    asyncio.create_task(background_fetch_and_save(userId, chatId, message, userInfo.get("nickname")))
    return jsonify({"response": quick_reply, "chatId": chatId, "updatedUserInfo": userInfo})

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
    if not userId or not chatId: return jsonify({"success": False, "error": "Eksik parametre"}), 400
    history = await load_json(HISTORY_FILE, history_lock)
    if userId in history and chatId in history[userId]:
        del history[userId][chatId]
        queue_save(HISTORY_FILE, history, history_lock)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# === Ana sayfa ===
@app.route("/")
async def home():
    return "Nova Web aktif ✅"

# === Başlat ===
async def main():
    asyncio.create_task(batch_save_worker())  # Batch save worker
    asyncio.create_task(check_inactive_users())  # Özlem mesaj sistemi
    port = int(os.environ.get("PORT", 5000))
    await app.run_task(host="0.0.0.0", port=port, debug=True)

if __name__ == "__main__":
    print("Nova Web aktif ✅")
    asyncio.run(main())
