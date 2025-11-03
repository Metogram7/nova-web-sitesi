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

HISTORY_FILE = "chat_history.json"
history_lock = asyncio.Lock()

# === Dosya yoksa oluştur ===
if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)

# === Yardımcı Fonksiyonlar ===
async def load_history():
    async with history_lock:
        try:
            return await asyncio.to_thread(lambda: json.load(open(HISTORY_FILE, "r", encoding="utf-8")))
        except Exception:
            return {}

async def save_history(history):
    async with history_lock:
        await asyncio.to_thread(
            lambda: open(HISTORY_FILE, "w", encoding="utf-8").write(
                json.dumps(history, ensure_ascii=False, indent=2)
            )
        )

# === Nova'nın dahili tarih/saat sistemi ===
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

# === Sistem Prompt ===
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

# === Global Aiohttp Session ===
class GeminiClient:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
        self.model = "gemini-2.5-flash"
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self):
        await self.session.close()

    async def generate(self, prompt: str):
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        for attempt in range(3):  # Otomatik yeniden deneme (3 kez)
            try:
                async with self.session.post(self.url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "candidates" in data and len(data["candidates"]) > 0:
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            advance_nova_time(1)
                            return text
                        else:
                            return "❌ API yanıtı beklenenden farklı."
                    elif resp.status == 503:
                        await asyncio.sleep(1)
                        continue
                    else:
                        return f"❌ API Hatası ({resp.status})"
            except asyncio.TimeoutError:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                return "❌ API geç cevap verdi (timeout)"
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                return f"❌ Hata: {e}"

        return "❌ API başarısız (denemeler tükendi)."

gemini_client = GeminiClient()

# === Gemini Cevap Fonksiyonu ===
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    prompt = get_system_prompt() + "\n\n"
    last_msgs = conversation[-5:] if len(conversation) > 5 else conversation
    for msg in last_msgs:
        role = "Kullanıcı" if msg.get("role") == "user" else "Nova"
        prompt += f"{role}: {msg.get('content')}\n"

    if user_name:
        prompt += f"\nNova, kullanıcının adı {user_name}. Ona samimi ve doğal biçimde cevap ver.\n"

    prompt += f"Kullanıcı: {message}\nNova:"
    text = await gemini_client.generate(prompt)

    emojis = ["😊", "😉", "🤖", "😄", "✨", "💬"]
    if random.random() < 0.3 and not text.endswith(tuple(emojis)):
        text += " " + random.choice(emojis)

    return text

# === Arka Plan Görevi ===
async def background_fetch_and_save(userId, chatId, message, user_name):
    try:
        hist = await load_history()
        conversation = [
            {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
            for msg in hist.get(userId, {}).get(chatId, [])
        ]
        reply = await gemma_cevap_async(message, conversation, user_name)
        hist = await load_history()
        hist.setdefault(userId, {}).setdefault(chatId, [])
        hist[userId][chatId].append({
            "sender": "nova",
            "text": reply,
            "from_bg": True,
            "ts": datetime.utcnow().isoformat()
        })
        await save_history(hist)
    except Exception as e:
        print("⚠️ Background hata:", e)

# === Chat Endpoint ===
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

    hist = await load_history()
    hist.setdefault(userId, {}).setdefault(chatId, [])
    conversation = [
        {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
        for msg in hist[userId][chatId]
    ]

    hist[userId][chatId].append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    await save_history(hist)

    existing_nova_replies = any(m.get("sender") == "nova" for m in hist[userId][chatId])
    if not existing_nova_replies:
        quick_reply = "Merhaba! Hemen bakıyorum... 🤖"
        hist[userId][chatId].append({
            "sender": "nova",
            "text": quick_reply,
            "ts": datetime.utcnow().isoformat(),
            "quick": True
        })
        await save_history(hist)
        asyncio.create_task(background_fetch_and_save(userId, chatId, message, userInfo.get("name")))
        return jsonify({"response": quick_reply, "chatId": chatId, "note": "quick_reply_shown"})

    reply = await gemma_cevap_async(message, conversation, userInfo.get("name"))
    hist[userId][chatId].append({"sender": "nova", "text": reply, "ts": datetime.utcnow().isoformat()})
    await save_history(hist)
    return jsonify({"response": reply, "chatId": chatId})

# === Geçmiş ve Silme ===
@app.route("/api/history", methods=["GET"])
async def get_history():
    userId = request.args.get("userId", "anonymous")
    history = await load_history()
    return jsonify(history.get(userId, {}))

@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    data = await request.get_json()
    userId = data.get("userId")
    chatId = data.get("chatId")
    if not userId or not chatId:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400
    history = await load_history()
    if userId in history and chatId in history[userId]:
        del history[userId][chatId]
        await save_history(history)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# === Sunucu Başlat ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    try:
        asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=True))
    finally:
        asyncio.run(gemini_client.close())
