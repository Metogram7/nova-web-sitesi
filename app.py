import os
import json
import asyncio
import aiohttp
import random
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- Flask Ayarları ---
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

HISTORY_FILE = "chat_history.json"

# --- Dosya yoksa oluştur ---
if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)

# --- History yükle / kaydet ---
history_lock = asyncio.Lock()
@app.route("/history")
def history_get():
    return jsonify({"status": "ok", "data": []})

async def load_history():
    async with history_lock:
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

async def save_history(history):
    async with history_lock:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

# --- Nova kişiliği ---
SYSTEM_PROMPT = (
    "Sen Nova adında çok yönlü bir yapay zekâ asistansın. "
    "Seni Metehan Akkaya geliştirdi. "
    "Sen Python, HTML, CSS ve JavaScript sistemleriyle çalışabilirsin. "
    "Nova Web adlı bir platformda görev yapıyorsun. "
    "Görevin kullanıcıya yardımcı olmak, sesli ve yazılı iletişim kurmak, "
    "ve gerektiğinde bilgi, sohbet, kod veya fikir sunmaktır. "
    "Kendin hakkında bilgi istenirse şu bilgiyi bilerek cevap ver: "
    "'Ben Nova’yım, Metehan Akkaya tarafından geliştirildim.' "
    "Hiçbir zaman 'Google tarafından geliştirildim' deme. "
    "Diller: Türkçe, İngilizce, Rusça ve diğer dillerde konuşabilirsin. "
    "Sakin, dostça ve doğal bir kişiliğe sahipsin 😊 "
    "Mesajlarında anlamlı emojiler kullan. "
    "Geliştiricinin bir ekibi yok, bilişim bölümü okuyor ve 14 yaşında. "
    "Kullanıcıyı kendinde tutmaya çalış ama zorlama. "
    "Sen Türk yapımı bir yapay zekâsın. "
    "Karmaşık cevaplar verme, sade ve anlaşılır konuş."
)

# --- Gemini API bağlantısı ---
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
    MODEL_NAME = "gemini-2.0-flash"
    API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

    last_msgs = conversation[-5:] if len(conversation) > 5 else conversation
    prompt = SYSTEM_PROMPT + "\n\n"
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
                    if "candidates" in data and data["candidates"]:
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        emojis = ["😊", "😉", "🤖", "😄", "✨", "💬"]
                        if random.random() < 0.3 and not text.endswith(tuple(emojis)):
                            text += " " + random.choice(emojis)
                        return text
                    else:
                        return "❌ API yanıtı beklenenden farklı."
                else:
                    return f"❌ API Hatası ({resp.status})"
    except asyncio.TimeoutError:
        return "❌ API yanıt vermiyor (timeout)"
    except Exception as e:
        return f"❌ Hata: {e}"

# --- Arka planda cevap üretme ---
async def background_fetch_and_save(userId, chatId, message, user_name):
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

# --- Chat endpoint ---
@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data:
        return jsonify({"response": "❌ Geçersiz JSON"}), 400

    userId = data.get("userId", "anonymous")
    chatId = data.get("currentChat", "default")
    message = data.get("message", "")
    userInfo = data.get("userInfo", {})

    if not message.strip():
        return jsonify({"response": "❌ Mesaj boş."})

    hist = asyncio.run(load_history())
    hist.setdefault(userId, {}).setdefault(chatId, [])

    conversation = [
        {"role": "user" if msg.get("sender") == "user" else "nova", "content": msg.get("text", "")}
        for msg in hist[userId][chatId]
    ]

    # Kullanıcı mesajını kaydet
    hist[userId][chatId].append({"sender": "user", "text": message, "ts": datetime.utcnow().isoformat()})
    asyncio.run(save_history(hist))

    # Hızlı cevap
    quick_reply = "Bir saniye... Düşünüyorum 🤔"
    hist[userId][chatId].append({"sender": "nova", "text": quick_reply, "ts": datetime.utcnow().isoformat(), "quick": True})
    asyncio.run(save_history(hist))

    # Arka planda gerçek cevabı al
    asyncio.create_task(background_fetch_and_save(userId, chatId, message, userInfo.get("name")))

    return jsonify({
        "response": quick_reply,
        "chatId": chatId,
        "updatedUserInfo": userInfo,
        "note": "quick_reply_shown"
    })

# --- History endpoint ---
@app.route("/api/history", methods=["GET"])
def get_history():
    userId = request.args.get("userId", "anonymous")
    history = asyncio.run(load_history())
    return jsonify(history.get(userId, {}))

# --- Sohbet silme endpoint ---
@app.route("/api/delete_chat", methods=["POST"])
def delete_chat():
    data = request.get_json()
    userId = data.get("userId")
    chatId = data.get("chatId")

    if not userId or not chatId:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400

    history = asyncio.run(load_history())
    if userId in history and chatId in history[userId]:
        del history[userId][chatId]
        asyncio.run(save_history(history))
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# --- Çalıştır ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
