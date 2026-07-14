import os
import asyncio
import json
import uuid
import traceback
from datetime import datetime, timezone, timedelta

import aiofiles
import aiohttp
from quart import Quart, request, jsonify, websocket, Response
from quart_cors import cors

from config import (
    HISTORY_FILE, LAST_SEEN_FILE, CACHE_FILE, TOKENS_FILE, SHARED_CHATS_FILE,
    GEMINI_API_KEYS, GEMINI_MODEL_NAME, DEEPSEEK_API_KEY,
)
from cache import _resp_cache as resp_cache_inst
from gemini import gemma_cevap_async, gemma_cevap_stream, get_nova_date
import templates

app = Quart(__name__)
app = cors(app, allow_origin="*", allow_headers="*", allow_methods="*")

# ============================================================
# GLOBAL DURUM
# ============================================================
session: aiohttp.ClientSession | None = None
GLOBAL_CACHE = {"history": {}, "last_seen": {}, "api_cache": {}, "tokens": []}
DIRTY_FLAGS  = {"history": False, "last_seen": False, "api_cache": False, "tokens": False}
SHARED_CHATS: dict[str, dict] = {}

# ============================================================
# YAŞAM DÖNGÜSÜ
# ============================================================
@app.before_serving
async def startup():
    print("=" * 50)
    print(f"[START] Nova 5.0 baslatiliyor... Port: {os.environ.get('PORT', 5000)}")
    ds = " + DeepSeek" if DEEPSEEK_API_KEY else ""
    print(f"[KEYS] {len(GEMINI_API_KEYS)} Gemini{ds} API anahtari yuklendi.")
    print("=" * 50)
    global session
    connector = aiohttp.TCPConnector(ssl=False, limit=200, limit_per_host=15)
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=45, connect=10),
        connector=connector,
        json_serialize=json.dumps,
    )
    await load_data_to_memory()
    await load_shared_chats()
    app.add_background_task(keep_alive)
    app.add_background_task(background_save_worker)


@app.after_serving
async def cleanup():
    global session
    await save_memory_to_disk(force=True)
    await save_shared_chats()
    if session:
        await session.close()


def get_path(filename):
    from config import BASE_DIR
    return os.path.join(BASE_DIR, filename)

# ============================================================
# VERİ YÖNETİMİ
# ============================================================
async def load_data_to_memory():
    files_map = {"history": HISTORY_FILE, "last_seen": LAST_SEEN_FILE,
                 "api_cache": CACHE_FILE, "tokens": TOKENS_FILE}
    for key, fn in files_map.items():
        if os.path.exists(fn):
            async with aiofiles.open(fn, mode='r', encoding='utf-8') as f:
                content = await f.read()
                if content:
                    try:
                        GLOBAL_CACHE[key] = json.loads(content)
                    except Exception:
                        GLOBAL_CACHE[key] = [] if key == "tokens" else {}
        else:
            GLOBAL_CACHE[key] = [] if key == "tokens" else {}


async def load_shared_chats():
    global SHARED_CHATS
    if os.path.exists(SHARED_CHATS_FILE):
        async with aiofiles.open(SHARED_CHATS_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            if content:
                try:
                    SHARED_CHATS = json.loads(content)
                    print(f"[OK] {len(SHARED_CHATS)} paylasilan sohbet yuklendi.")
                except Exception:
                    SHARED_CHATS = {}


async def save_shared_chats():
    try:
        tmp = SHARED_CHATS_FILE + ".tmp"
        async with aiofiles.open(tmp, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(SHARED_CHATS, ensure_ascii=False, indent=2))
        os.replace(tmp, SHARED_CHATS_FILE)
    except Exception as e:
        print(f"[!] Shared chats kayit hatasi: {e}")


async def background_save_worker():
    while True:
        await asyncio.sleep(20)
        await save_memory_to_disk()


async def save_memory_to_disk(force=False):
    files_map = {"history": HISTORY_FILE, "last_seen": LAST_SEEN_FILE,
                 "api_cache": CACHE_FILE, "tokens": TOKENS_FILE}
    for key, fn in files_map.items():
        if DIRTY_FLAGS[key] or force:
            try:
                tmp = fn + ".tmp"
                async with aiofiles.open(tmp, mode='w', encoding='utf-8') as f:
                    await f.write(json.dumps(GLOBAL_CACHE[key], ensure_ascii=False, indent=2))
                os.replace(tmp, fn)
                DIRTY_FLAGS[key] = False
            except Exception as e:
                print(f"[!] Kayit ({key}): {e}")


# ============================================================
# ROUTE'LAR
# ============================================================

@app.route("/")
async def home():
    return (f"Nova 5.0 — {get_nova_date()} | 17 kaynak aktif ✅ | "
            f"Cache: {len(resp_cache_inst)} | Paylaşımlar: {len(SHARED_CHATS)}")


@app.route("/.well-known/assetlinks.json", methods=["GET"])
async def assetlinks():
    data = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.novawebb.app",
                "sha256_cert_fingerprints": [
                    "EA:47:D9:95:CC:7E:54:72:93:8F:C6:22:F1:D3:2F:C4:F9:F0:01:12:B0:85:27:80:CF:A8:88:47:CD:C0:60:05"
                ]
            },
        }
    ]
    return Response(json.dumps(data), mimetype="application/json")


@app.route("/api/min_version", methods=["GET"])
async def min_version():
    return jsonify({
        "min_version": "7.0.0",
        "latest_version": "7.0.1",
        "update_message": "Yeni özellikler seni bekliyor! 🚀",
        "force_update": False,
    }), 200


@app.route("/api/user_status", methods=["GET"])
async def user_status():
    user_id = request.args.get("userId", "anon")
    PLUS_USERS = set(filter(None, os.getenv("PLUS_USER_IDS", "").split(",")))
    is_plus = user_id in PLUS_USERS
    return jsonify({
        "userId": user_id,
        "is_plus": is_plus,
        "plan": "plus" if is_plus else "free",
        "daily_limit": 9999 if is_plus else 20,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200


@app.route("/api/chat", methods=["POST"])
async def chat():
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400

        user_id   = data.get("userId", "anon")
        chat_id   = data.get("currentChat", "default")
        user_msg  = data.get("message", "")
        image_b64 = data.get("image")
        custom    = data.get("systemInstruction") or data.get("systemPrompt", "")
        stream    = data.get("stream", False)

        history = GLOBAL_CACHE["history"].setdefault(user_id, {}).setdefault(chat_id, [])

        if stream or request.headers.get("Accept") == "text/event-stream":
            async def generate():
                full_resp = ""
                async for chunk in gemma_cevap_stream(user_msg, history, session, user_id, image_b64, custom):
                    if chunk.startswith("__STATUS__:"):
                        yield f"data: {json.dumps({'type': 'status', 'text': chunk[11:]}, ensure_ascii=False)}\n\n"
                    elif chunk:
                        full_resp += chunk
                        yield f"data: {json.dumps({'type': 'token', 'text': chunk}, ensure_ascii=False)}\n\n"

                if full_resp:
                    history.append({"sender": "user", "message": user_msg})
                    history.append({"sender": "nova", "message": full_resp})
                    DIRTY_FLAGS["history"] = True
                yield "data: [DONE]\n\n"

            return Response(generate(), mimetype="text/event-stream")

        response = await gemma_cevap_async(user_msg, history, session, user_id, image_b64, custom)

        if not response or response.startswith("⚠️"):
            return jsonify({"response": response or "Bir hata olustu.", "status": "error"}), 200

        history.append({"sender": "user", "message": user_msg})
        history.append({"sender": "nova", "message": response})
        DIRTY_FLAGS["history"] = True

        return jsonify({
            "response": response,
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "model": GEMINI_MODEL_NAME,
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"response": f"[!] Sunucu hatasi: {str(e)}", "status": "error"}), 500


@app.route("/api/delete_chat", methods=["POST"])
async def delete_chat():
    try:
        data    = await request.get_json()
        user_id = data.get("userId", "anon")
        chat_id = data.get("chatId")
        if not chat_id:
            return jsonify({"success": False, "error": "chatId gerekli"}), 400
        chats = GLOBAL_CACHE["history"].get(user_id, {})
        if chat_id in chats:
            del chats[chat_id]
            DIRTY_FLAGS["history"] = True
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/history", methods=["GET"])
async def get_history():
    user_id = request.args.get("userId", "anon")
    return jsonify(GLOBAL_CACHE["history"].get(user_id, {})), 200


@app.route("/api/share_chat", methods=["POST"])
async def share_chat_api():
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400

        share_id = str(uuid.uuid4())[:8].upper()
        shared_data = {
            "id": share_id,
            "title": data.get("title", "Nova AI Sohbeti"),
            "user_name": data.get("userName", "Kullanıcı"),
            "messages": data.get("messages", [])[:100],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "view_count": 0,
        }

        SHARED_CHATS[share_id] = shared_data
        asyncio.create_task(save_shared_chats())

        return jsonify({
            "success": True,
            "share_id": share_id,
            "id": share_id,
            "share_url": f"https://nova-chat-d50f.onrender.com/share/{share_id}",
            "expires_at": shared_data["expires_at"],
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/share/<share_id>", methods=["GET"])
async def view_shared_chat(share_id):
    share_id = share_id.upper()
    data = SHARED_CHATS.get(share_id)

    if not data:
        accept = request.headers.get("Accept", "")
        if "application/json" in accept:
            return jsonify({"error": "Sohbet bulunamadı veya süresi doldu."}), 404
        return "<h2 style='font-family:sans-serif;text-align:center;margin-top:60px'>Sohbet bulunamadı veya 30 günlük süresi doldu 😔</h2>", 404

    data["view_count"] = data.get("view_count", 0) + 1
    asyncio.create_task(save_shared_chats())

    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return jsonify(data), 200

    return Response(templates.shared_chat_html(share_id, data), mimetype="text/html")


@app.route("/join/<room_code>", methods=["GET"])
async def join_room(room_code):
    room_code = room_code.upper()
    return Response(templates.join_room_html(room_code), mimetype="text/html")


@app.route("/api/debug/search")
async def debug_search():
    from cache import _search_cache as sc
    from scrapers import fetch_live_data_full
    q = request.args.get("q", "dolar kaç")
    result = await fetch_live_data_full(q, session)
    return jsonify({
        "query": q,
        "result": result,
        "search_cache": len(sc),
        "resp_cache": len(resp_cache_inst),
        "shared_chats": len(SHARED_CHATS),
    })


# ============================================================
# WEBSOCKET
# ============================================================
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()
    while True:
        try:
            raw = await websocket.receive()
            if not raw:
                break
            msg = json.loads(raw)
            user_id   = msg.get("userId", "anon")
            chat_id   = msg.get("chatId", "live")
            user_msg  = msg.get("message", "")
            image_b64 = msg.get("image")
            custom    = msg.get("systemInstruction") or msg.get("systemPrompt", "")

            history = GLOBAL_CACHE["history"].setdefault(user_id, {}).setdefault(chat_id, [])

            full_resp = ""
            async for chunk in gemma_cevap_stream(user_msg, history, session, user_id, image_b64, custom):
                if chunk.startswith("__STATUS__:"):
                    await websocket.send(json.dumps({"type": "status", "text": chunk[11:]}, ensure_ascii=False))
                elif chunk:
                    full_resp += chunk
                    await websocket.send(json.dumps({"type": "token", "text": chunk}, ensure_ascii=False))

            await websocket.send("[END]")

            if full_resp and not full_resp.startswith("[!]"):
                history.append({"sender": "user", "message": user_msg})
                history.append({"sender": "nova", "message": full_resp})
                DIRTY_FLAGS["history"] = True

        except Exception as e:
            print(f"[WS-ERR]: {e}")
            try:
                await websocket.send(f"[!] Hata olustu: {str(e)}")
                await websocket.send("[END]")
            except Exception:
                pass
            break


async def keep_alive():
    while True:
        await asyncio.sleep(600)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.name == 'nt':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    app.run(host="0.0.0.0", port=port, debug=False)
