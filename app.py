import os
import json
import asyncio
import aiohttp
from quart import Quart, request, jsonify
from quart_cors import cors
from datetime import datetime
import aiofiles

# --- Uygulama Ayarları ---
app = Quart(__name__)
app = cors(app, allow_origin="*")  # 🔥 Render için CORS tamamen açık

# --- Dosya Kilitleri ---
developer_lock = asyncio.Lock()
train_lock = asyncio.Lock()

DEVELOPER_FILE = "developer_data.json"
TRAIN_FILE = "training_data.json"

# --- Gemini API Anahtarı ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# --- Async JSON Yardımcı Fonksiyonları ---
async def load_json(filename, lock):
    async with lock:
        if not os.path.exists(filename):
            return {}
        async with aiofiles.open(filename, "r", encoding="utf-8") as f:
            try:
                content = await f.read()
                return json.loads(content) if content else {}
            except json.JSONDecodeError:
                return {}

async def save_json(filename, data, lock):
    async with lock:
        async with aiofiles.open(filename, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))

# --- Gemini İstemcisi ---
class GeminiClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = aiohttp.ClientSession()

    async def ask(self, prompt):
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        url = f"{GEMINI_API_URL}?key={self.api_key}"
        async with self.session.post(url, json=payload, timeout=120) as resp:
            if resp.status != 200:
                text = await resp.text()
                return f"⚠️ API hatası: {text}"
            data = await resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return "⚠️ Boş yanıt alındı."

    async def close(self):
        await self.session.close()

gemini_client = GeminiClient(GEMINI_API_KEY)

# --- Geliştirici Alanı API'si ---
@app.route("/api/dev", methods=["POST"])
async def dev_chat():
    data = await request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Mesaj boş olamaz."}), 400

    response = await gemini_client.ask(f"Nova Developer Mode: {message}")

    dev_data = await load_json(DEVELOPER_FILE, developer_lock)
    if "messages" not in dev_data:
        dev_data["messages"] = []
    dev_data["messages"].append({
        "role": "user",
        "content": message,
        "response": response,
        "time": datetime.now().isoformat()
    })
    await save_json(DEVELOPER_FILE, dev_data, developer_lock)
    return jsonify({"reply": response})


@app.route("/api/dev_history", methods=["GET"])
async def get_dev_history():
    dev_data = await load_json(DEVELOPER_FILE, developer_lock)
    return jsonify({"messages": dev_data.get("messages", [])})

# --- Eğitme Alanı API'si ---
@app.route("/api/train", methods=["POST"])
async def train_nova():
    data = await request.get_json()
    lesson = data.get("lesson", "").strip()
    if not lesson:
        return jsonify({"error": "Ders verisi boş olamaz."}), 400

    response = await gemini_client.ask(f"Nova eğitim modu: {lesson}")

    train_data = await load_json(TRAIN_FILE, train_lock)
    if "trainings" not in train_data:
        train_data["trainings"] = []
    train_data["trainings"].append({
        "lesson": lesson,
        "response": response,
        "time": datetime.now().isoformat()
    })
    await save_json(TRAIN_FILE, train_data, train_lock)
    return jsonify({"reply": response})


@app.route("/api/trainings", methods=["GET"])
async def get_trainings():
    train_data = await load_json(TRAIN_FILE, train_lock)
    return jsonify({"trainings": train_data.get("trainings", [])})  # 🔥 JSON yapısı düzeltildi

# --- Sunucu Başlat ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # 🔥 Render portu
    try:
        asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=True))
    finally:
        asyncio.run(gemini_client.close())
