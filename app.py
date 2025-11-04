import os
import json
import asyncio
import aiohttp
import aiofiles
from quart import Quart, request, jsonify
from quart_cors import cors
from datetime import datetime, timedelta

# --- Uygulama Ayarları ---
app = Quart(__name__)
app = cors(app, allow_origin="*")  # 🔥 Render için CORS tamamen açık

# --- Dosya Kilitleri ---
developer_lock = asyncio.Lock()
train_lock = asyncio.Lock()

DEVELOPER_FILE = "developer_data.json"
TRAIN_FILE = "training_data.json"

# --- Gemini API Ayarları ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# --- Yardımcı Fonksiyon: JSON kaydetme ---
async def save_json(filepath, data):
    async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

# --- Yardımcı Fonksiyon: JSON okuma ---
async def load_json(filepath):
    if not os.path.exists(filepath):
        return []
    async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
        content = await f.read()
        return json.loads(content or "[]")

# --- Geliştirici eğitim verisini kaydetme ---
@app.route("/api/train", methods=["POST"])
async def train_nova():
    try:
        data = await request.get_json()
        prompt = data.get("prompt")
        response = data.get("response")
        if not prompt or not response:
            return jsonify({"error": "Eksik veri"}), 400

        async with train_lock:
            train_data = await load_json(TRAIN_FILE)
            train_data.append({
                "prompt": prompt,
                "response": response,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            await save_json(TRAIN_FILE, train_data)

        # Gemini'ye gönderim (isteğe bağlı)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            payload = {
                "contents": [{"parts": [{"text": f"Nova'yı eğit: {prompt} => {response}"}]}]
            }
            headers = {"Content-Type": "application/json"}
            async with session.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                                    headers=headers, json=payload) as r:
                _ = await r.text()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Eğitim verilerini listeleme ---
@app.route("/api/trainings", methods=["GET"])
async def get_trainings():
    try:
        async with train_lock:
            data = await load_json(TRAIN_FILE)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Ana sohbet ucu (Nova) ---
@app.route("/api/chat", methods=["POST"])
async def chat():
    try:
        data = await request.get_json()
        message = data.get("message", "")

        if not message:
            return jsonify({"error": "Boş mesaj gönderilemez"}), 400

        # Sohbet geçmişini yükle
        async with developer_lock:
            history = await load_json(DEVELOPER_FILE)

        # Yeni mesajı ekle
        history.append({
            "role": "user",
            "message": message,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        # Gemini'ye gönder
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            payload = {
                "contents": [{"parts": [{"text": message}]}]
            }
            headers = {"Content-Type": "application/json"}
            async with session.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                                    headers=headers, json=payload) as r:
                result = await r.json()

        reply = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not reply:
            reply = "Üzgünüm, cevap oluşturulamadı."

        # Nova'nın cevabını ekle
        history.append({
            "role": "assistant",
            "message": reply,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        # Sohbet geçmişini kaydet
        async with developer_lock:
            await save_json(DEVELOPER_FILE, history)

        return jsonify({"response": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Test endpoint ---
@app.route("/")
async def home():
    return "Nova Backend Çalışıyor ✅"

# --- Uygulamayı Başlat ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
