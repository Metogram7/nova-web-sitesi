import os
import json
import asyncio
import aiohttp
import random
from flask import Flask, request, jsonify
from flask_cors import CORS
import nest_asyncio

# --- Async loop düzeltmesi ---
nest_asyncio.apply()
loop = asyncio.get_event_loop()

app = Flask(__name__)
CORS(app)

HISTORY_FILE = "chat_history.json"

# --- Dosya yoksa oluştur ---
if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)

# --- Sohbet geçmişini yükle ---
def load_history():
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# --- Gemini API ile cevap ---
async def gemma_cevap(message: str, conversation: list, user_name=None):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
    MODEL_NAME = "gemini-2.5-flash"
    API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

    last_msgs = conversation[-4:] if len(conversation) > 4 else conversation

    prompt = ""
    for msg in last_msgs:
        role = "Kullanıcı" if msg["role"] == "user" else "Nova"
        prompt += f"{role}: {msg['content']}\n"

    if user_name:
        prompt += f"\nNova, kullanıcının adı {user_name}. Samimi ve kısa cevap ver. Gerektiğinde emoji ekle.\n"

    prompt += f"Kullanıcı: {message}\nNova:"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
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

# --- Chat endpoint ---
@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data:
        return jsonify({"response": "❌ Geçersiz JSON"}), 400

    userId = data.get("userId")
    chatId = data.get("currentChat", "default")
    message = data.get("message")
    userInfo = data.get("userInfo", {})

    if not message or message.strip() == "":
        return jsonify({"response": "❌ Mesaj boş."})

    history = load_history()
    history.setdefault(userId, {})
    history[userId].setdefault(chatId, [])

    conversation = [
        {"role": "user" if msg["sender"] == "user" else "nova", "content": msg["text"]}
        for msg in history[userId][chatId]
    ]

    # Kullanıcı adı algılama
    textLower = message.lower()
    if "adım" in textLower or "benim adım" in textLower:
        name = message.split()[-1].capitalize()
        userInfo["name"] = name

    reply = loop.run_until_complete(gemma_cevap(message, conversation, userInfo.get("name")))

    history[userId][chatId].append({"sender": "user", "text": message})
    history[userId][chatId].append({"sender": "nova", "text": reply})
    save_history(history)

    return jsonify({
        "response": reply,
        "chatId": chatId,
        "updatedUserInfo": userInfo
    })

# --- Sohbet geçmişi ---
@app.route("/api/history", methods=["GET"])
def get_history():
    userId = request.args.get("userId")
    history = load_history()
    return jsonify(history.get(userId, {}))

# --- Sohbet silme endpoint ---
@app.route("/api/delete_chat", methods=["POST"])
def delete_chat():
    data = request.get_json()
    userId = data.get("userId")
    chatId = data.get("chatId")

    if not userId or not chatId:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400

    history = load_history()
    if userId in history and chatId in history[userId]:
        del history[userId][chatId]
        save_history(history)
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
