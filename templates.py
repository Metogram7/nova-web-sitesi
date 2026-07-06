"""Static HTML pages for shared chat view and room join."""


STORE_URL = "https://play.google.com/store/apps/details?id=com.novawebb.app"


def _intent_url(fallback_url: str) -> str:
    encoded = fallback_url.replace(":", "%3A").replace("/", "%2F").replace("?", "%3F").replace("=", "%3D")
    return f"intent://open#Intent;scheme=novawebb;package=com.novawebb.app;S.browser_fallback_url={encoded};end"


def shared_chat_html(share_id: str, data: dict) -> str:
    title = data.get("title", "Nova AI Sohbeti")
    created = data.get("created_at", "")[:10]
    messages = data.get("messages", [])
    user_name = data.get("user_name", "Kullanıcı")

    msgs_html = ""
    for msg in messages:
        sender = msg.get("sender", "user")
        text = (msg.get("text", "") or "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        is_nova = sender in ("bot", "nova")
        bg = "#1a2744" if is_nova else "#151f35"
        border = "#38bdf8" if is_nova else "#8b5cf6"
        label = "🤖 Nova AI" if is_nova else f"👤 {user_name}"
        msgs_html += f"""
        <div style="margin:10px 0;padding:14px 16px;background:{bg};border-left:3px solid {border};border-radius:0 10px 10px 0;">
          <div style="color:{border};font-size:11px;font-weight:bold;margin-bottom:6px;text-transform:uppercase;letter-spacing:1px">{label}</div>
          <div style="color:#e2e8f0;font-size:14px;line-height:1.65;">{text}</div>
        </div>"""

    open_app_url = _intent_url(STORE_URL)

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Nova AI</title>
<meta property="og:title" content="{title}">
<meta property="og:description" content="Nova AI ile yapılmış sohbeti görüntüle — nova-chat-d50f.onrender.com">
<meta property="og:url" content="https://nova-chat-d50f.onrender.com/share/{share_id}">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#090e1c;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}}
.wrap{{max-width:720px;margin:0 auto;padding:20px 16px 70px}}
.hdr{{text-align:center;padding:30px 0 22px;border-bottom:1px solid #1a2744;margin-bottom:22px}}
.logo{{font-size:26px;font-weight:900;color:#38bdf8;letter-spacing:7px;margin-bottom:4px}}
.sub{{font-size:11px;color:#334155;letter-spacing:2px}}
.badge{{display:inline-block;background:#0f2040;color:#38bdf8;padding:4px 12px;border-radius:20px;font-size:10px;font-weight:bold;letter-spacing:2px;margin-bottom:10px}}
.chat-title{{font-size:17px;font-weight:bold;color:#f1f5f9;margin:10px 0 4px}}
.meta{{font-size:11px;color:#334155;margin-bottom:18px}}
.open-app-bar{{position:sticky;top:0;z-index:99;background:#090e1c;border-bottom:1px solid #1e3a5f;
              padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
.btn-open{{display:inline-block;padding:10px 22px;border-radius:12px;
           background:linear-gradient(135deg,#38bdf8,#0ea5e9);
           color:#090e1c;font-size:14px;font-weight:900;text-decoration:none;
           box-shadow:0 4px 18px rgba(56,189,248,.3);white-space:nowrap}}
.btn-store-sm{{display:inline-block;padding:10px 16px;border-radius:12px;
              border:1.5px solid #1e3a5f;color:#64748b;
              font-size:13px;font-weight:700;text-decoration:none;white-space:nowrap}}
.footer{{text-align:center;margin-top:40px;padding-top:18px;border-top:1px solid #1a2744}}
.footer a{{color:#38bdf8;text-decoration:none;font-weight:bold;font-size:14px}}
</style>
</head>
<body>
<div class="open-app-bar">
  <span style="color:#94a3b8;font-size:13px">Bu sohbet Nova AI'a ait</span>
  <div style="display:flex;gap:8px">
    <a href="{open_app_url}" class="btn-open">📱 Uygulamada Aç</a>
    <a href="{STORE_URL}" class="btn-store-sm">İndir</a>
  </div>
</div>
<div class="wrap">
  <div class="hdr" style="padding-top:20px">
    <div class="logo">NOVA AI</div>
    <div class="sub">NOVA AI</div>
  </div>
  <div class="badge">PAYLAŞILAN SOHBET</div>
  <div class="chat-title">{title}</div>
  <div class="meta">📅 {created} &nbsp;·&nbsp; 👁 {data.get('view_count',1)} görüntülenme &nbsp;·&nbsp; 💬 {len(messages)} mesaj</div>
  {msgs_html}
  <div class="footer">
    <p style="color:#334155;font-size:12px;margin-bottom:14px">Nova AI ile oluşturuldu</p>
    <a href="{STORE_URL}">📱 Nova AI'ı İndir</a>
  </div>
</div>
</body>
</html>"""


def join_room_html(room_code: str) -> str:
    intent_url = f"intent://join/{room_code}#Intent;scheme=novawebb;package=com.novawebb.app;S.browser_fallback_url={STORE_URL.replace(':', '%3A').replace('/', '%2F').replace('?', '%3F').replace('=', '%3D')};end"

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nova — Odaya Katıl</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%}}
body{{background:#090e1c;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.wrap{{max-width:380px;width:100%;text-align:center}}
.logo{{font-size:13px;font-weight:700;color:#334155;letter-spacing:4px;margin-bottom:40px}}
.avatar{{width:80px;height:80px;background:linear-gradient(135deg,#38bdf8,#8b5cf6);
         border-radius:24px;display:flex;align-items:center;justify-content:center;
         font-size:36px;margin:0 auto 20px}}
h1{{font-size:22px;font-weight:800;color:#f1f5f9;margin-bottom:8px}}
.desc{{color:#64748b;font-size:14px;margin-bottom:32px;line-height:1.5}}
.code-box{{background:#0f172a;border:1.5px solid #1e3a5f;border-radius:16px;
           padding:16px 20px;margin-bottom:32px;display:inline-block}}
.code-lbl{{font-size:10px;color:#334155;letter-spacing:3px;margin-bottom:4px}}
.code-val{{font-size:28px;font-weight:900;color:#38bdf8;letter-spacing:6px}}
.btn-join{{display:block;width:100%;padding:18px;border-radius:16px;
           background:linear-gradient(135deg,#38bdf8,#0ea5e9);
           color:#090e1c;font-size:17px;font-weight:900;text-decoration:none;
           box-shadow:0 8px 32px rgba(56,189,248,.35);
           transition:transform .15s,box-shadow .15s;margin-bottom:12px}}
.btn-join:active{{transform:scale(0.97);box-shadow:0 4px 16px rgba(56,189,248,.2)}}
.btn-store{{display:block;width:100%;padding:14px;border-radius:16px;
            border:1.5px solid #1e3a5f;color:#38bdf8;
            font-size:14px;font-weight:700;text-decoration:none;
            transition:border-color .15s}}
.btn-store:active{{border-color:#38bdf8}}
.hint{{font-size:11px;color:#1e3a5f;margin-top:20px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">NOVA AI</div>
  <div class="avatar">💬</div>
  <h1>Sohbet Odası Daveti</h1>
  <p class="desc">Seni bir sohbet odasına davet ettiler.<br>Aşağıdaki butona bas ve odaya katıl.</p>
  <div class="code-box">
    <div class="code-lbl">ODA KODU</div>
    <div class="code-val">{room_code}</div>
  </div>
  <br>
  <a href="{intent_url}" class="btn-join">
    🚀 Odaya Katıl
  </a>
  <a href="{STORE_URL}" class="btn-store">
    📱 Nova Yüklü Değilse İndir
  </a>
  <p class="hint">Nova yüklüyse "Odaya Katıl"a basınca uygulama otomatik açılır.</p>
</div>
</body>
</html>"""
