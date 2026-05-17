import smtplib
import os
import io # Dosya yükleme için sanal dosya (in-memory file) kullanmak için

# E-posta kütüphaneleri: Eklenti göndermek için MIMEMultipart'a geçiyoruz
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from quart import Quart, request, jsonify
from quart_cors import cors
from werkzeug.datastructures import FileStorage # Quart'ın dosya işleme objesi

app = Quart(__name__)
app = cors(app, allow_origin="*")

# --- KENDİ BİLGİLERİNİZİ DOLDURUN ---
MAIL_ADRES = "nova.ai.v4.2@gmail.com" # ← BURAYA KENDİ GMAIL ADRESİNİZİ YAZIN
MAIL_SIFRE = "gamtdoiralefaruk"       # ← BURAYA UYGULAMA ŞİFRENİZİ YAZIN (Çok ÖNEMLİ: Gerçek şifre yerine Google Uygulama Şifresi kullanın!)
ALICI_ADRES = MAIL_ADRES              # ← E-postayı alacak adres
# ------------------------------------

@app.post("/send-mail")
async def send_mail():
    # Dosya yüklemesini destekleyen form verilerini alma
    form = await request.form
    files = await request.files

    # Zorunlu alanları çekme
    username = form.get("username", "").strip()
    user_email = form.get("user_email", "").strip()
    message = form.get("message", "").strip()
    
    # İsteğe bağlı dosyayı çekme
    uploaded_file: FileStorage = files.get("photo")

    # Zorunlu alan kontrolü
    if not username or not user_email or not message:
        return jsonify({"status": "Kullanıcı Adı, Gmail Adresi ve Mesaj zorunludur."}), 400

    # MIMEMultipart oluştur
    msg = MIMEMultipart()
    
    # E-posta Başlıklarını Ayarlama
    msg["Subject"] = f"[HATA BİLDİRİMİ] {username} ({user_email})'dan Yeni Bildirim"
    msg["From"] = MAIL_ADRES
    msg["To"] = ALICI_ADRES

    # 1. Metin İçeriğini MIMEText olarak ekleme
    email_body = f"""
Kullanıcı Adı: {username}
E-posta: {user_email}

Mesaj:
---
{message}
---
"""
    msg.attach(MIMEText(email_body, 'plain', 'utf-8'))


    # 2. İsteğe bağlı dosyayı eklenti olarak ekleme
    if uploaded_file:
        try:
            # Dosya adını ve MIME tipini alma
            file_name = uploaded_file.filename
            mime_type = uploaded_file.mimetype or 'application/octet-stream' # Varsayılan MIME tipi
            
            # Dosya içeriğini sanal belleğe oku
            file_data = uploaded_file.stream.read()
            
            # MIMEBase objesini oluşturma
            part = MIMEBase(*mime_type.split('/')) # Örn: ('image', 'jpeg')
            part.set_payload(file_data)
            
            # İçeriği Base64 ile kodla ve başlıkları ekle
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{file_name}"',
            )
            
            # Eklentiyi mesaja ekle
            msg.attach(part)
            
        except Exception as e:
            # Dosya eklenirken hata oluşursa, yine de maili göndeririz, ancak uyarı ekleriz
            print(f"Eklenti eklenirken hata: {e}")
            msg.attach(MIMEText(f"\n\n[UYARI: Eklenti yüklenirken bir hata oluştu: {e}]", 'plain', 'utf-8'))


    # 3. Maili Gönderme
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(MAIL_ADRES, MAIL_SIFRE)
        server.sendmail(MAIL_ADRES, ALICI_ADRES, msg.as_string())
        server.quit()

        status_msg = "Bildirim başarıyla gönderildi!"
        if uploaded_file:
            status_msg += f" (Eklenti: {file_name})"
            
        return jsonify({"status": status_msg})

    except Exception as e:
        print(f"Mail gönderme hatası: {e}")
        return jsonify({"status": f"Mail gönderilemedi. Sunucu/SMTP Hatası: {type(e).__name__}. Detay: {e}"}), 500

if __name__ == "__main__":
    print(f"Quart sunucusu başlatılıyor... http://0.0.0.0:5000 adresini dinliyor.")
    app.run(host="0.0.0.0", port=5000)