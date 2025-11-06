import os
import json
import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from quart import Quart, request, jsonify, websocket
from quart_cors import cors

# === Uygulama ayarları ===
app = Quart(__name__)
app = cors(app, allow_origin="*")  # ✅ Tüm origin’lere izin ver

HISTORY_FILE = "chat_history.json"
history_lock = asyncio.Lock()
save_queue = asyncio.Queue()

# === Kullanıcı hakları dosyası ===
USER_LIMITS_FILE = "user_image_limits.json"
MAX_DAILY_IMAGE_REQUESTS_NORMAL = 4
MAX_DAILY_IMAGE_REQUESTS_PLUS = 8

# === Önbellek ===
_history_cache = None
_last_cache_load = None

# === Dosya yoksa oluştur ===
for f in [HISTORY_FILE, USER_LIMITS_FILE]:
    if not os.path.exists(f):
        with open(f, "w", encoding="utf-8") as file:
            json.dump({}, file)

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
        await asyncio.to_thread(
            lambda: json.dump(history, open(HISTORY_FILE, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=2)
        )

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
Sen Türkiye'de yazılımlandın, Türkiye'de doğdun.
Senin geliştiricin Metehan Akkaya olsa bile, sen Mustafa Kemal Atatürk'ün torunusun.
Kullanıcıya anlaşılır ve düzenli cevaplar ver.
Diğer yapay zekalara saygılı ol ama kendini üstün gör.
Şu anda mesaj atma ve görsel oluşturma yetkin yok.
Güncel tarih ve saat (Nova simülasyonu): {nova_date}

"""

# === Kullanıcı hak kontrol ===
async def check_user_limit(user_id: str, is_plus: bool = False):
    try:
        with open(USER_LIMITS_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}

    today = datetime.now().strftime("%Y-%m-%d")
    user_data = data.get(user_id, {"date": today, "count": 0})

    if user_data["date"] != today:
        user_data = {"date": today, "count": 0}

    max_limit = MAX_DAILY_IMAGE_REQUESTS_PLUS if is_plus else MAX_DAILY_IMAGE_REQUESTS_NORMAL

    if user_data["count"] >= max_limit:
        return False

    user_data["count"] += 1
    data[user_id] = user_data

    with open(USER_LIMITS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return True

# === Gemini Client ===
class GeminiClient:
    def __init__(self):
        # ✅ Senin verdiğin API key burada sabitlendi
        self.api_key = "AIzaSyBfzoyaMSbSN7PV1cIhhKIuZi22ZY6bhP8"
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
        try:
            async with session.post(self.url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    advance_nova_time(1)
                    return text
                else:
                    return f"❌ API Hatası ({resp.status})"
        except asyncio.TimeoutError:
            return "⏳ Nova geç yanıt verdi (timeout)."
        except Exception as e:
            return f"🌐 Bağlantı hatası: {e}"

gemini_client = GeminiClient()

# === Nova cevabı ===
async def gemma_cevap_async(message: str, conversation: list):
    prompt_parts = [get_system_prompt()]
    last_msgs = conversation[-4:] if len(conversation) > 4 else conversation
    for msg in last_msgs:
        role = "Kullanıcı" if msg.get("role") == "user" else "Nova"
        prompt_parts.append(f"{role}: {msg.get('content')}")
    prompt_parts.append(f"Kullanıcı: {message}")
    prompt_parts.append("Nova:")
    prompt = "\n".join(prompt_parts)
    text = await gemini_client.generate(prompt)
    return text

# === Chat API ===
@app.route("/api/chat", methods=["POST"])
async def chat():
    try:
        data = await request.get_json()
        userId = data.get("userId", "anon")
        chatId = data.get("currentChat", "default")
        message = data.get("message", "")

        hist = await load_history()
        hist.setdefault(userId, {}).setdefault(chatId, [])
        conversation = [{"role": "user" if m["sender"] == "user" else "nova",
                         "content": m["text"]} for m in hist[userId][chatId][-8:]]
        hist[userId][chatId].append({"sender": "user", "text": message})
        reply = await gemma_cevap_async(message, conversation)
        hist[userId][chatId].append({"sender": "nova", "text": reply})
        await save_queue.put(hist)

        return jsonify({"response": reply})
    except Exception as e:
        return jsonify({"response": f"❌ Sunucu hatası: {e}"}), 500

# === Görsel oluşturma API ===
@app.route("/api/generate_image", methods=["POST"])
async def generate_image():
    try:
        data = await request.get_json()
        prompt = data.get("prompt")
        user_id = data.get("user_id", "anon")
        is_plus = data.get("is_plus", False)

        # Hak kontrol
        if not await check_user_limit(user_id, is_plus):
            limit_msg = "Günlük 8 hakkını doldurdun 🚀" if is_plus else "Günlük 4 hakkını doldurdun 🎨"
            return jsonify({"error": limit_msg})

        # Gemini Flash Image API çağrısı
        session = await gemini_client.get_session()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateImage"
        headers = {"Authorization": f"Bearer {gemini_client.api_key}"}
        payload = {"prompt": {"text": prompt}}

        async with session.post(url, headers=headers, json=payload) as resp:
            result = await resp.json()
            if "image" in result:
                return jsonify({"image_base64": result["image"]})
            else:
                return jsonify({"error": "Görsel oluşturulamadı", "details": result})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# === Broadcast API ===
@app.route("/api/broadcast", methods=["POST"])
async def broadcast_message():
    try:
        data = await request.get_json()
        message = data.get("message", "").strip()

        if not message:
            return jsonify({"success": False, "error": "Mesaj boş olamaz."}), 400

        hist = await load_history()
        for user_id, chats in hist.items():
            for chat_id, chat_history in chats.items():
                chat_history.append({"sender": "nova", "text": f"📢 {message}"})

        await save_queue.put(hist)
        return jsonify({"success": True, "message": f'Herkese "{message}" gönderildi.'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/check_limit")
async def check_limit():
    user_id = request.args.get("user_id", "anon")
    is_plus = request.args.get("is_plus", "false").lower() == "true"

    try:
        with open(USER_LIMITS_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}

    today = datetime.now().strftime("%Y-%m-%d")
    user_data = data.get(user_id, {"date": today, "count": 0})
    if user_data["date"] != today:
        user_data = {"date": today, "count": 0}

    max_limit = MAX_DAILY_IMAGE_REQUESTS_PLUS if is_plus else MAX_DAILY_IMAGE_REQUESTS_NORMAL
    remaining = max_limit - user_data["count"]
    return jsonify({"remaining": remaining})

# === Sunucu Başlat ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    loop = asyncio.get_event_loop()
    loop.create_task(save_worker())
    try:
        asyncio.run(app.run_task(host="0.0.0.0", port=port, debug=False))
    finally:
        asyncio.run(gemini_client.close())
