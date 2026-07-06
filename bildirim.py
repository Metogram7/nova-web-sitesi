from quart import Quart, request, jsonify, send_file
from quart_cors import cors
import firebase_admin
from firebase_admin import credentials, messaging
import os

# -----------------------------
# UYGULAMA
# -----------------------------
app = Quart(__name__)

app = cors(
    app,
    allow_origin=[
        "https://novawebb.com",
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ]
)

# -----------------------------
# FIREBASE ADMIN BAŞLAT
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-admin.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)

print("✅ Firebase Admin hazır")

# -----------------------------
# TEST TOKEN (ŞİMDİLİK MANUEL)
# -----------------------------
# 📌 Telefonundan aldığın FCM token buraya yazılacak
TEST_FCM_TOKEN = "BDMDVEtUfabWh6LAnM15zLGcK2R-1kxuSvjwegdx0q-I46l9GnBSSKimwAoIxUhOxh5QtRxAtt0Hj9PBl19qlxU"

# -----------------------------
# SAĞLIK KONTROL
# -----------------------------
@app.route("/", methods=["GET"])
async def health_check():
    return jsonify({
        "status": "ok",
        "service": "bildirim-backend",
        "message": "Backend ayakta 🚀"
    })

# -----------------------------
# ADMIN PANEL (HTML)
# -----------------------------
@app.route("/admin", methods=["GET"])
async def admin_panel():
    return await send_file(
        os.path.join(BASE_DIR, "bildirim.html")
    )

# -----------------------------
# BİLDİRİM GÖNDER
# -----------------------------
@app.route("/bildirim-test", methods=["POST"])
async def bildirim_test():
    data = await request.get_json()

    if not data:
        return jsonify({"success": False, "error": "JSON body yok"}), 400

    title = data.get("title")
    message_text = data.get("message")

    if not title or not message_text:
        return jsonify({"success": False, "error": "title ve message zorunlu"}), 400

    print("📨 Bildirim Alındı")
    print("Başlık:", title)
    print("Mesaj:", message_text)

    if not TEST_FCM_TOKEN or "BURAYA" in TEST_FCM_TOKEN:
        return jsonify({
            "success": False,
            "error": "FCM token tanımlı değil"
        }), 500

    # 🔔 Firebase bildirimi
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=message_text
        ),
        token=TEST_FCM_TOKEN
    )

    try:
        response = messaging.send(message)
        print("✅ Bildirim gönderildi:", response)

        return jsonify({
            "success": True,
            "firebase_response": response
        })

    except Exception as e:
        print("❌ Firebase gönderim hatası:", e)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# -----------------------------
# ÇALIŞTIR
# -----------------------------
if __name__ == "__main__":
    print("🔥 Bildirim backend başlatılıyor...")
    app.run(host="0.0.0.0", port=5001, debug=True)
