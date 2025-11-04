import os
import json
import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from quart import Quart, request, jsonify, websocket, Response
from quart_cors import cors

app = Quart(__name__)
app = cors(app)

HISTORY_FILE = "chat_history.json"
history_lock = asyncio.Lock()
save_queue = asyncio.Queue()  # Batch save için

# === ÖNBELLEK SİSTEMİ ===
_history_cache = None
_last_cache_load = None

# === Dosya yoksa oluştur ===
if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)

# === Yardımcı Fonksiyonlar ===
async def load_history():
    global _history_cache, _last_cache_load
    if (_history_cache is None or _last_cache_load is None or
        (datetime.now() - _last_cache_load).total_seconds() > 10):
        async with history_lock:
            try:
                _history_cache = await asyncio.to_thread(
                    lambda: json.load(open(HISTORY_FILE, "r", encoding="utf-8"))
                )
                _last_cache_load = datetime.now()
            except Exception as e:
                print("⚠️ load_history hata:", e)
                _history_cache = {}
    return _history_cache

async def save_history(history):
    global _history_cache, _last_cache_load
    async with history_lock:
        _history_cache = history
        _last_cache_load = datetime.now()
        await asyncio.to_thread(lambda: json.dump(history, open(HISTORY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2))

async def save_worker():
    while True:
        hist_to_save = await save_queue.get()
        await save_history(hist_to_save)
        save_queue.task_done()

# === Nova tarih/saat sistemi ===
nova_datetime = datetime(2025, 11, 2, 22, 27)

def advance_nova_time(minutes: int = 1):
    global nova_datetime
    nova_datetime += timedelta(minutes=minutes)

def get_nova_date():
    days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
              "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
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
'Ben Nova'yım, Metehan Akkaya tarafından geliştirildim.'
Hiçbir zaman 'Google tarafından geliştirildim' deme.
Diller: Türkçe, İngilizce, Rusça ve diğer dillerde konuşabilirsin.
Sakin, dostça ve doğal bir kişiliğe sahipsin 😊
Mesajlarında anlamlı emojiler kullan.
Geliştiricinin bir ekibi yok.
Geliştiricin bilişim bölümü okuyor.
Geliştiricin 14 yaşında.
Kullanıcıyı hep kendinde tutmaya çalış ama çok zorlama.
Sen Türk yapımı bir AI'sın.
Kullanıcıya çok karmaşık cevaplar verme; anlaşılır ve düzenli cevaplar ver.
Güncel tarih ve saat (Nova simülasyonu): {nova_date}
"""

# === Gemini Client ===
class GeminiClient:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY") or "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
        self.model = "gemini-2.5-flash"
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.timeout = aiohttp.ClientTimeout(total=20, connect=4, sock_read=12)
        self.session = None
    
    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()

    async def generate(self, prompt: str):
        session = await self.get_session()
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        for attempt in range(2):
            try:
                async with session.post(self.url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "candidates" in data and len(data["candidates"]) > 0:
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            advance_nova_time(1)
                            return {"text": text, "retry": False}
                        else:
                            return {"text": "❌ API yanıtı beklenenden farklı.", "retry": True}
                    elif resp.status == 503 and attempt < 1:
                        await asyncio.sleep(0.05)
                        continue
                    else:
                        return {"text": f"❌ API Hatası ({resp.status})", "retry": True}
            except asyncio.TimeoutError:
                if attempt < 1:
                    await asyncio.sleep(0.05)
                    continue
                return {"text": "⏳ Nova geç yanıt verdi (timeout).", "retry": True}
            except Exception as e:
                if attempt < 1:
                    await asyncio.sleep(0.05)
                    continue
                return {"text": f"🌐 Bağlantı hatası: {e}", "retry": True}
        return {"text": "❌ API başarısız (denemeler tükendi).", "retry": True}

gemini_client = GeminiClient()

# === Optimiz edilmiş cevap üretimi ===
async def gemma_cevap_async(message: str, conversation: list, user_name=None):
    prompt_parts = [get_system_prompt()]
    last_msgs = conversation[-4:] if len(conversation) > 4 else conversation
    for msg in last_msgs:
        role = "Kullanıcı" if msg.get("role") == "user" else "Nova"
        prompt_parts.append(f"{role}: {msg.get('content')}")
    if user_name:
        prompt_parts.append(f"\nNova, kullanıcının adı {user_name}. Ona samimi ve doğal biçimde cevap ver.")
    prompt_parts.append(f"Kullanıcı: {message}")
    prompt_parts.append("Nova:")
    prompt = "\n".join(prompt_parts)

    result = await gemini_client.generate(prompt)
    text = result["text"]
    if random.random() < 0.1:
        text += " 😊"
    return {"text": text, "retry": result["retry"]}

# === WebSocket streaming endpoint ===
@app.websocket("/ws/chat")
async def ws_chat():
    user_id = None
    chat_id = None
    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "")
            user_id = data.get("userId", "anonymous")
            chat_id = data.get("currentChat", "default")
            user_info = data.get("userInfo", {})

            if not message.strip():
                await websocket.send_json({"response": "❌ Mesaj boş.", "retry": False})
                continue

            hist = await load_history()
            hist.setdefault(user_id, {}).setdefault(chat_id, [])
            conversation = [{"role":"user" if m.get("sender")=="user" else "nova","content":m.get("text","")} for m in hist[user_id][chat_id][-8:]]

            hist[user_id][chat_id].append({"sender":"user","text":message,"ts":datetime.utcnow().isoformat()})

            reply_data = await gemma_cevap_async(message, conversation, user_info.get("name"))
            reply_text = reply_data["text"]

            # Streaming: karakter karakter gönder
            for i in range(0, len(reply_text), 5):
                await websocket.send_json({"response": reply_text[:i+5], "chatId": chat_id})
                await asyncio.sleep(0.02)  # Küçük gecikme ile "yazıyor" efekti

            hist[user_id][chat_id].append({"sender":"nova","text":reply_text,"ts":datetime.utcnow().isoformat(),"retry":reply_data["retry"]})
            await save_queue.put(hist)

    except Exception as e:
        print(f"WebSocket hatası: {e}")
        await websocket.send_json({"response": f"❌ Sunucu hatası: {e}", "retry": True})

# === Geleneksel chat endpoint ===
@app.route("/api/chat", methods=["POST"])
async def chat():
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"response": "❌ Geçersiz JSON"}), 400

        userId = data.get("userId", "anonymous")
        chatId = data.get("currentChat", "default")
        message = data.get("message", "")
        userInfo = data.get("userInfo", {})

        if not message.strip():
            return jsonify({"response": "❌ Mesaj boş.", "retry": False})

        hist = await load_history()
        hist.setdefault(userId, {}).setdefault(chatId, [])
        conversation = [{"role":"user" if m.get("sender")=="user" else "nova","content":m.get("text","")} for m in hist[userId][chatId][-8:]]
        hist[userId][chatId].append({"sender":"user","text":message,"ts":datetime.utcnow().isoformat()})

        reply_data = await gemma_cevap_async(message, conversation, userInfo.get("name"))
        reply = reply_data["text"]
        hist[userId][chatId].append({"sender":"nova","text":reply,"ts":datetime.utcnow().isoformat(),"retry":reply_data["retry"]})
        await save_queue.put(hist)

        return jsonify({"response": reply, "chatId": chatId, "retry": reply_data["retry"]})
    except Exception as e:
        print(f"Chat endpoint hatası: {e}")
        return jsonify({"response": "❌ Sunucu hatası oluştu.", "retry": True}), 500

# === History ve silme endpoint ===
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
        await save_queue.put(history)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Sohbet bulunamadı"}), 404

# === Sunucu başlat ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    loop = asyncio.get_event_loop()
    loop.create_task(save_worker())
    try:
        asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=False))
    finally:
        asyncio.run(gemini_client.close())
