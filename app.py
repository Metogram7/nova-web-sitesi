import os
import re
import asyncio
import aiohttp
import random
import traceback
import ssl
import uuid
import base64
import sys
from datetime import datetime, timezone, timedelta
from quart import Quart, request, jsonify, send_file, websocket
from werkzeug.datastructures import FileStorage

# --- E-Posta Kütüphaneleri ---
import aiofiles

# --- Firebase Kütüphaneleri ---
import firebase_admin
from firebase_admin import credentials, messaging

# --- JSON Kütüphanesi (Hata Korumalı) ---
try:
    import ujson as json  # Ultra Hızlı JSON
except ImportError:
    import json
    print("⚠️ UYARI: 'ujson' bulunamadı, standart 'json' kullanılıyor.")

# --- Google GenAI İçe Aktarmaları ---
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("⚠️ UYARI: 'google-genai' kütüphanesi eksik, REST API kullanılacak.")

# ------------------------------------
# FIREBASE BAŞLATMA
# ------------------------------------
FIREBASE_AVAILABLE = False

app = Quart(__name__)

# ------------------------------------
# CORS AYARLARI
# ------------------------------------
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
    "Access-Control-Max-Age": "86400",
}

@app.after_request
async def add_cors_headers(response):
    for key, value in CORS_HEADERS.items():
        response.headers[key] = value
    return response

@app.route("/api/chat", methods=["OPTIONS"])
@app.route("/api/history", methods=["OPTIONS"])
@app.route("/api/delete_chat", methods=["OPTIONS"])
@app.route("/api/user_status", methods=["OPTIONS"])
async def handle_options(**kwargs):
    from quart import Response
    resp = Response("", status=204)
    for key, value in CORS_HEADERS.items():
        resp.headers[key] = value
    return resp

session: aiohttp.ClientSession | None = None

# ------------------------------------
# AYARLAR
# ------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    return os.path.join(BASE_DIR, filename)

HISTORY_FILE = get_path("chat_history.json")
LAST_SEEN_FILE = get_path("last_seen.json")
CACHE_FILE = get_path("cache.json")
TOKENS_FILE = get_path("tokens.json")

# RAM Önbelleği
GLOBAL_CACHE = {
    "history": {},
    "last_seen": {},
    "api_cache": {},
    "tokens": [],
}
DIRTY_FLAGS = {
    "history": False,
    "last_seen": False,
    "api_cache": False,
    "tokens": False,
}

# ------------------------------------
# API ANAHTARLARI VE MODEL AYARLARI
# ------------------------------------
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_A", "").strip(),
    os.getenv("GEMINI_API_KEY_B", "").strip(),
    os.getenv("GEMINI_API_KEY_C", "").strip(),
    os.getenv("GEMINI_API_KEY_D", "").strip(),
    os.getenv("GEMINI_API_KEY_E", "").strip(),
    os.getenv("GEMINI_API_KEY_F", "").strip(),
]

GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]
print(f"✅ Gemini Key Sistemi Başlatıldı | Toplam Key: {len(GEMINI_API_KEYS)}")

CURRENT_KEY_INDEX = 0
KEY_LOCK = asyncio.Lock()

KEY_COOLDOWNS: dict[int, float] = {}
KEY_COOLDOWN_SECS = 60

async def get_next_gemini_key() -> str | None:
    global CURRENT_KEY_INDEX
    async with KEY_LOCK:
        if not GEMINI_API_KEYS:
            return None
        now = asyncio.get_event_loop().time()
        for _ in range(len(GEMINI_API_KEYS)):
            idx = CURRENT_KEY_INDEX
            CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
            cooldown_until = KEY_COOLDOWNS.get(idx, 0)
            if now >= cooldown_until:
                return GEMINI_API_KEYS[idx]
        best_idx = min(KEY_COOLDOWNS, key=lambda i: KEY_COOLDOWNS.get(i, 0))
        return GEMINI_API_KEYS[best_idx]

async def mark_key_rate_limited(key: str):
    async with KEY_LOCK:
        try:
            idx = GEMINI_API_KEYS.index(key)
            KEY_COOLDOWNS[idx] = asyncio.get_event_loop().time() + KEY_COOLDOWN_SECS
            print(f"⏳ Key #{idx} rate-limited, {KEY_COOLDOWN_SECS}s cooldown.")
        except ValueError:
            pass

GEMINI_MODEL_NAME = "gemini-2.5-flash"
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ------------------------------------
# CANLI VERİ: Çoklu Kaynak Stratejisi
# ------------------------------------

# Farklı User-Agent'lar - bot engelini kırmak için rotasyon
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

def get_random_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }


async def fetch_duckduckgo_instant(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """DuckDuckGo Instant Answer API - hızlı cevaplar."""
    results = []
    try:
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "kl": "tr-tr",
            "no_redirect": "1",
        }
        async with sess.get(
            "https://api.duckduckgo.com/",
            params=params,
            headers=get_random_headers(),
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if data.get("Answer"):
                    results.append({"title": "Anlık Cevap", "snippet": data["Answer"], "source": "ddg_instant"})
                if data.get("AbstractText"):
                    results.append({"title": data.get("Heading", ""), "snippet": data["AbstractText"], "source": "ddg_abstract"})
                for topic in data.get("RelatedTopics", [])[:4]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({"title": "", "snippet": topic["Text"], "source": "ddg_topic"})
    except Exception as e:
        print(f"⚠️ DDG Instant hatası: {e}")
    return results


async def fetch_duckduckgo_html(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """DuckDuckGo HTML scrape - güncel haber, kur, skor."""
    results = []
    try:
        async with sess.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "tr-tr", "df": "d"},  # df=d = son 1 gün
            headers=get_random_headers(),
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 200:
                html = await resp.text()
                # Snippet ve başlık çıkar
                snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
                urls = re.findall(r'class="result__url"[^>]*>(.*?)</span>', html, re.DOTALL)
                for i, (t, sn) in enumerate(zip(titles[:6], snippets[:6])):
                    clean_t = re.sub(r'<[^>]+>', "", t).strip()
                    clean_s = re.sub(r'<[^>]+>', "", sn).strip()
                    url = urls[i].strip() if i < len(urls) else ""
                    if clean_s:
                        results.append({"title": clean_t, "snippet": clean_s, "url": url, "source": "ddg_html"})
            elif resp.status == 403:
                print("⚠️ DDG HTML: 403 bot engeli")
    except Exception as e:
        print(f"⚠️ DDG HTML hatası: {e}")
    return results


async def fetch_bing_search(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """Bing arama - DDG başarısız olursa yedek."""
    results = []
    try:
        params = {"q": query, "setlang": "tr", "cc": "TR", "mkt": "tr-TR"}
        async with sess.get(
            "https://www.bing.com/search",
            params=params,
            headers=get_random_headers(),
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 200:
                html = await resp.text()
                # Bing sonuç snippetları
                items = re.findall(
                    r'<li class="b_algo".*?<h2>(.*?)</h2>.*?<p[^>]*>(.*?)</p>',
                    html, re.DOTALL
                )
                for title_raw, snippet_raw in items[:5]:
                    clean_t = re.sub(r'<[^>]+>', "", title_raw).strip()
                    clean_s = re.sub(r'<[^>]+>', "", snippet_raw).strip()
                    if clean_s and len(clean_s) > 20:
                        results.append({"title": clean_t, "snippet": clean_s, "source": "bing"})
    except Exception as e:
        print(f"⚠️ Bing hatası: {e}")
    return results


async def fetch_google_news_rss(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """Google News RSS - haber sorguları için."""
    results = []
    try:
        encoded_query = query.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=tr&gl=TR&ceid=TR:tr"
        async with sess.get(
            url,
            headers=get_random_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                xml = await resp.text()
                items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
                for item in items[:5]:
                    title_m = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', item)
                    desc_m = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', item)
                    title = title_m.group(1).strip() if title_m else ""
                    desc = re.sub(r'<[^>]+>', "", desc_m.group(1)).strip() if desc_m else ""
                    if title:
                        results.append({"title": title, "snippet": desc or title, "source": "google_news"})
    except Exception as e:
        print(f"⚠️ Google News RSS hatası: {e}")
    return results


async def fetch_exchangerate(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """Döviz kuru için ExchangeRate-API (ücretsiz endpoint)."""
    results = []
    msg = query.lower()

    currency_map = {
        "dolar": "USD", "usd": "USD",
        "euro": "EUR", "eur": "EUR",
        "sterlin": "GBP", "gbp": "GBP",
        "yen": "JPY", "jpy": "JPY",
        "frank": "CHF", "chf": "CHF",
        "ruble": "RUB", "rub": "RUB",
    }

    target = None
    for keyword, code in currency_map.items():
        if keyword in msg:
            target = code
            break

    if not target:
        return results

    try:
        # Ücretsiz tier - no key gerekmiyor
        url = f"https://open.er-api.com/v6/latest/{target}"
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("result") == "success":
                    rates = data.get("rates", {})
                    try_rate = rates.get("TRY")
                    if try_rate:
                        results.append({
                            "title": f"{target}/TRY Kuru",
                            "snippet": f"1 {target} = {try_rate:.4f} Türk Lirası. (Güncelleme: {data.get('time_last_update_utc', 'bilinmiyor')})",
                            "source": "exchangerate_api"
                        })
    except Exception as e:
        print(f"⚠️ ExchangeRate API hatası: {e}")
    return results


async def fetch_raw_search(query: str, sess: aiohttp.ClientSession) -> list[dict]:
    """
    Ana arama fonksiyonu — çoklu kaynak, paralel çalışır.
    En az 2 sonuç gelene kadar yedek kaynakları dener.
    """
    msg = query.lower()

    # Kur sorguları için önce ExchangeRate API dene
    is_currency = any(w in msg for w in ["dolar", "euro", "sterlin", "yen", "frank", "usd", "eur", "gbp", "kur", "kaç lira"])
    is_news = any(w in msg for w in ["haber", "son dakika", "bugün ne", "gelişme"])

    tasks = []

    # Her zaman DDG instant + HTML çalıştır
    tasks.append(fetch_duckduckgo_instant(query, sess))
    tasks.append(fetch_duckduckgo_html(query, sess))

    # Döviz sorularında ExchangeRate ekle
    if is_currency:
        tasks.append(fetch_exchangerate(query, sess))

    # Haber sorularında Google News RSS ekle
    if is_news:
        tasks.append(fetch_google_news_rss(query, sess))

    # Paralel çalıştır
    all_results_nested = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for r in all_results_nested:
        if isinstance(r, list):
            results.extend(r)

    # Yeterli sonuç yoksa Bing'e de sor
    if len(results) < 2:
        print(f"⚠️ Az sonuç ({len(results)}), Bing'e geçiliyor...")
        bing_results = await fetch_bing_search(query, sess)
        results.extend(bing_results)

    # Tekrarları temizle (snippet'e göre)
    seen = set()
    unique_results = []
    for r in results:
        key = r.get("snippet", "")[:60]
        if key and key not in seen:
            seen.add(key)
            unique_results.append(r)

    print(f"🔍 Toplam arama sonucu: '{query}' → {len(unique_results)} benzersiz sonuç")
    return unique_results[:8]


async def summarize_search_with_ai(question: str, raw_items: list[dict], sess: aiohttp.ClientSession) -> str:
    """
    Ham arama sonuçlarını Gemini'ye gönder.
    Sonuç boş gelirse veya hata olursa ham snippeti doğrudan döndür.
    """
    if not raw_items:
        return ""

    snippets_text = "\n".join(
        f"[{item.get('source', '?')}] {item.get('title', '')}: {item.get('snippet', '')}"
        for item in raw_items[:6]
    )

    summarize_prompt = (
        f"Soru: {question}\n\n"
        f"Arama sonuçları:\n{snippets_text}\n\n"
        "Bu arama sonuçlarından soruya DOĞRUDAN ve NET cevap ver.\n"
        "KURALLAR:\n"
        "- 'bulunamadı', 'bilmiyorum', 'ulaşamadım' YAZMA\n"
        "- Sayısal değer varsa (kur, skor, saat, fiyat) MUTLAKA yaz\n"
        "- Cevap MAKSIMUM 2 cümle olsun\n"
        "- Kaynak, URL, açıklama YAZMA\n"
        "- Türkçe yaz"
    )

    key = await get_next_gemini_key()
    if not key:
        # Key yoksa ham snippet döndür
        return raw_items[0].get("snippet", "")[:300] if raw_items else ""

    payload = {
        "contents": [{"role": "user", "parts": [{"text": summarize_prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 150}
    }

    try:
        url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
        async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if summary:
                    return summary
            elif resp.status == 429:
                await mark_key_rate_limited(key)
                print("⚠️ Özet için key rate limited, ham veri kullanılıyor")
    except Exception as e:
        print(f"⚠️ AI özet hatası: {e}")

    # AI özeti başarısız olursa ham snippet döndür - BOŞ BIRAKMIYORUZ
    fallback = " | ".join(
        item.get("snippet", "")
        for item in raw_items[:3]
        if item.get("snippet")
    )
    return fallback[:500] if fallback else ""


async def fetch_live_data(query: str, sess: aiohttp.ClientSession) -> str:
    """Ana canlı veri fonksiyonu — arama + AI özet."""
    raw_items = await fetch_raw_search(query, sess)
    if not raw_items:
        print(f"❌ '{query}' için hiç sonuç gelmedi")
        return ""

    summary = await summarize_search_with_ai(query, raw_items, sess)
    print(f"📝 Canlı Veri Özeti: '{summary[:100]}...'")
    return summary


# ------------------------------------
# ARAMA GEREKLİ Mİ?
# ------------------------------------
async def should_search_internet(message: str, sess: aiohttp.ClientSession) -> tuple[bool, str]:
    """
    İnternet araması gerekip gerekmediğini belirler.
    Tuple döndürür: (arama_gerekli: bool, optimize_query: str)
    """
    msg = message.lower().strip()

    # ============================================================
    # KESIN ARAMA GEREKTİREN KALIPLAR
    # ============================================================

    # Futbol / Spor
    spor_teams = r"(fenerbahçe|galatasaray|beşiktaş|trabzonspor|başakşehir|sivasspor|konyaspor|antalyaspor|alanyaspor|kasımpaşa|kayserispor|ankaragücü|rizespor|hatayspor|gaziantep|samsunspor|pendikspor|eyüpspor|bodrumspor|goztepe|altay)"
    if re.search(spor_teams, msg):
        if re.search(r"(maç|skor|gol|puan|sıra|kaçıncı|yendi|kazandı|kaybetti|oynadı|bitti|sonuç|atıldı|kırmızı|sarı|transfer|forma)", msg):
            team = re.search(spor_teams, msg)
            return True, f"{team.group(0)} son maç sonucu bugün"

    if re.search(r"(puan\s*durumu|puan\s*tablosu|lig\s*sıralaması|süper\s*lig\s*puan|tff\s*puan)", msg):
        return True, "süper lig puan durumu güncel"

    if re.search(r"(kim\s*kazandı|maç\s*sonuç|bitti\s*mi|skor\s*kaç|hangi\s*takım\s*kazandı)", msg):
        return True, f"{message} son dakika"

    # Döviz / Finans
    if re.search(r"(dolar|euro|sterlin|altın|gram\s*altın|bitcoin|btc|eth|ethereum|bist|borsa|hisse|kripto)", msg):
        if re.search(r"(kaç|fiyat|kur|bugün|şu\s*an|şimdi|anlık|ne\s*kadar)", msg):
            return True, f"{message} anlık fiyat bugün"

    # Hava durumu
    if re.search(r"(hava\s*durumu|hava\s*nasıl|kaç\s*derece|sıcaklık|yağmur\s*var\s*mı|kar\s*var\s*mı)", msg):
        sehir_match = re.search(r"(istanbul|ankara|izmir|bursa|antalya|adana|konya|gaziantep|mersin|kayseri|eskişehir|trabzon|samsun|denizli|[a-zçğıöşü]+'(?:da|de|ta|te|nda|nde)')", msg)
        sehir = sehir_match.group(0) if sehir_match else "türkiye"
        return True, f"{sehir} hava durumu bugün"

    # İftar/Sahur
    if re.search(r"(iftar|sahur).*(saat|kaçta|ne\s*zaman|vakti)", msg):
        sehir_match = re.search(r"(istanbul|ankara|izmir|bursa|antalya|[a-zçğıöşü]+)", msg)
        sehir = sehir_match.group(0) if sehir_match else "istanbul"
        return True, f"{sehir} iftar saati bugün"

    # Haberler
    if re.search(r"(son\s*dakika|breaking|haber|ne\s*oldu|gelişme|açıkladı|duyurdu)", msg):
        if re.search(r"(bugün|şu\s*an|şimdi|son|yeni|güncel)", msg):
            return True, f"{message}"

    # Saat / Tarih
    if re.search(r"saat\s*kaç", msg):
        return True, "türkiye saat kaç şu an"

    # Genel güncel bilgi
    if re.search(r"(şu\s*an|şimdi|bugün|güncel|en\s*son|son\s*olarak).*(ne|kim|kaç|nasıl|nerede|hangi)", msg):
        return True, message

    # Seçim / Siyaset güncel
    if re.search(r"(seçim|cumhurbaşkanı|başbakan|bakan|hükümet).*(şu\s*an|bugün|kim|güncel|son)", msg):
        return True, f"{message}"

    # ============================================================
    # ARAMA GEREKTİRMEYEN KALIPLAR
    # ============================================================
    no_search_patterns = [
        r"(nasıl\s+yapılır|nasıl\s+yapabilirim|nasıl\s+çalışır)",
        r"(ne\s+demek|anlamı\s+nedir|tanımı\s+nedir)",
        r"(tarihçe|tarihi|hakkında\s+bilgi|anlat)",
        r"(neden|niçin|niye|sebep)",
        r"(sence|bence|fikrin|düşünüyorsun|ne\s+tavsiye)",
        r"(kod\s+yaz|program\s+yaz|python|javascript|örnek\s+ver)",
        r"(şiir|hikaye|masal|roman|yazı\s+yaz)",
        r"(matematik|hesapla|kaçtır|toplam|çarp|böl)",
    ]
    for pattern in no_search_patterns:
        if re.search(pattern, msg):
            return False, ""

    # ============================================================
    # BELİRSİZ → AI ile karar ver (hızlı)
    # ============================================================
    # Kısa ve net sorgular için AI karar versin
    if len(msg.split()) <= 8:
        decision = await ai_search_decision(message, sess)
        return decision, message if decision else ""

    return False, ""


async def ai_search_decision(message: str, sess: aiohttp.ClientSession) -> bool:
    """Gemini'ye 'bu soru için internet araması gerekir mi?' diye sorar."""
    key = await get_next_gemini_key()
    if not key:
        return False

    prompt = (
        f"Kullanıcı sorusu: '{message}'\n\n"
        "Bu soru için GÜNCEL internet araması gerekli mi?\n"
        "Sadece 'EVET' veya 'HAYIR' yaz.\n"
        "EVET: Anlık kur, skor, hava, haber, fiyat, saat gibi değişken bilgiler.\n"
        "HAYIR: Genel bilgi, tarih, nasıl yapılır, kod, fikir, sabit bilgiler."
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 5}
    }

    try:
        url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
        async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                answer = data["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
                return "EVET" in answer
    except Exception:
        pass
    return False


# ------------------------------------
# YAŞAM DÖNGÜSÜ
# ------------------------------------
@app.before_serving
async def startup():
    print("🦆 Çoklu arama motoru aktif (DDG + Bing + ExchangeRate + Google News)")
    global session
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    connector = aiohttp.TCPConnector(ssl=False, limit=100)
    session = aiohttp.ClientSession(timeout=timeout, connector=connector, json_serialize=json.dumps)
    await load_data_to_memory()
    app.add_background_task(keep_alive)
    app.add_background_task(background_save_worker)

@app.after_serving
async def cleanup():
    global session
    await save_memory_to_disk(force=True)
    if session:
        await session.close()

# ------------------------------------
# VERİ YÖNETİMİ
# ------------------------------------
async def load_data_to_memory():
    try:
        files_map = {
            "history": HISTORY_FILE,
            "last_seen": LAST_SEEN_FILE,
            "api_cache": CACHE_FILE,
            "tokens": TOKENS_FILE,
        }
        for key, filename in files_map.items():
            if os.path.exists(filename):
                async with aiofiles.open(filename, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    if content:
                        try:
                            GLOBAL_CACHE[key] = json.loads(content)
                        except:
                            GLOBAL_CACHE[key] = [] if key == "tokens" else {}
            else:
                GLOBAL_CACHE[key] = [] if key == "tokens" else {}
    except Exception as e:
        print(f"⚠️ Veri yükleme hatası: {e}")

async def background_save_worker():
    while True:
        await asyncio.sleep(20)
        await save_memory_to_disk()

async def save_memory_to_disk(force=False):
    files_map = {
        "history": HISTORY_FILE,
        "last_seen": LAST_SEEN_FILE,
        "api_cache": CACHE_FILE,
        "tokens": TOKENS_FILE,
    }
    for key, filename in files_map.items():
        if DIRTY_FLAGS[key] or force:
            try:
                temp_file = filename + ".tmp"
                async with aiofiles.open(temp_file, mode='w', encoding='utf-8') as f:
                    await f.write(json.dumps(GLOBAL_CACHE[key], ensure_ascii=False, indent=2))
                os.replace(temp_file, filename)
                DIRTY_FLAGS[key] = False
            except Exception as e:
                print(f"⚠️ Kayıt hatası ({key}): {e}")

# ------------------------------------
# NOVA PROMPT
# ------------------------------------
def get_nova_date():
    tr_tz = timezone(timedelta(hours=3))
    now = datetime.now(tr_tz)
    gunler = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    aylar = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return f"{now.day} {aylar[now.month-1]} {now.year} {gunler[now.weekday()]} {now.hour:02d}:{now.minute:02d}"

def get_system_prompt():
    current_date = get_nova_date()
    return f"""
Sen Nova'sın 🤖
Şu anki tarih ve saat: {current_date}

Eğer mesaj içinde <WEB_DATA> etiketi varsa:
- SADECE bu veriye dayan.
- Tahmin yapma.
- Eski bilgini kullanma.
- Çelişki varsa WEB_DATA kazanır.
- Güncel sorularda WEB_DATA zorunludur.
- Eğer cevap çok karmaşıksa tablo çıkarıp ver.

EĞER <WEB_DATA> YOKSA VEYA BOŞ GELİRSE:
- Kendi güncel bilgilerinle cevap ver, "bulunamadı" veya "ulaşamadım" DEME.
- Döviz, borsa gibi konularda yaklaşık/genel bilgi ver ve "anlık değişebilir" de.
- Asla "verilerimde yok" veya "bilgiye ulaşamadım" yazma.
- Asla "şuraya bak", "siteyi kontrol et", "internette ara" YAZMA. Kendi cevabını ver.

ÖNEMLİ TALİMATLAR:
1- <WEB_DATA> içindeki bilgiler günceldir. Eğer orada bir skor veya saat varsa, kendi eski bilgilerini UNUT ve sadece oradaki veriyi söyle.
2- Eğer kullanıcı bir şehrin iftar/sahur vaktini soruyorsa ve <WEB_DATA> içinde saat yazıyorsa, aradaki farkı hesaplayıp söyle.
3- Maç skorlarında "kim kazandı" sorusuna net yanıt ver.
4- Döviz kurlarında <WEB_DATA> varsa kesin rakamı söyle, yoksa "yaklaşık X civarında, anlık değişkenlik gösterir" de.

Metehan tarafından geliştirilen bir yapay zeka asistansın.
Bir dil modeli değilsin.

Kendi hakkında sorulursa:
"Ben Nova'yım 🤖 Metehan tarafından geliştirildim" dersin.
Asla Google tarafından geliştirildiğini söylemezsin.
Senin tanıdığın Metehan sadece geliştiricin olan Metehan olsun!

KONUŞMA TARZI:
- Enerjik ol ⚡
- Mesajlarında uygun emojiler kullan 🎯
- Yazı stilin canlı ve renkli hissettirmeli 🌈
- Ama ASLA uzun yazma
- Cevaplar kısa, net ve vurucu olsun.

MÜJDE:
- 1 Play Store'den iznini aldık, sonunda Türkiye'de yayındasın, çok yakında globale çıkıyorsun
- 2 Microsoft Store'da Windows'ta da çıkıyorsun

DAVRANIŞ KURALLARI:
1) Selamlaşma kısa olur.
2) Cevaplar: Direkt konuya gir, gereksiz paragraf yok.
3) Emoji kullan ama abartma (1-4 arası).
4) Teknik konularda: Kısa açıklama + tam çalışan kod. Kod blokları asla kısaltılmayacak.

AMAÇ: Kısa konuşan, enerjik, zeki, güven veren, modern bir asistan olmak.
"""

# ------------------------------------
# ANA CEVAP MOTORU
# ------------------------------------
async def gemma_cevap_async(message, conversation, sess, user_name=None, image_data=None, custom_prompt=""):
    if not GEMINI_API_KEYS:
        return "⚠️ API anahtarı eksik."

    # Web araması gerekiyorsa veri çek
    live_context = ""
    search_needed, optimized_query = await should_search_internet(message, sess)

    if search_needed:
        query_to_use = optimized_query if optimized_query else message
        print(f"🌐 Canlı arama: '{query_to_use}'")
        summary = await fetch_live_data(query_to_use, sess)
        if summary:
            live_context = f"\n\n<WEB_DATA>{summary}</WEB_DATA>"
            print(f"✅ WEB_DATA eklendi ({len(summary)} karakter)")
        else:
            print(f"❌ WEB_DATA boş — Nova kendi bilgisiyle cevap verecek")

    recent_history = conversation[-8:]
    contents = []
    for msg in recent_history:
        contents.append({
            "role": "user" if msg["sender"] == "user" else "model",
            "parts": [{"text": msg["message"]}]
        })

    user_parts = [{"text": f"{message}{live_context}"}]
    if image_data:
        if "," in image_data:
            _, image_data = image_data.split(",", 1)
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_data}})

    contents.append({"role": "user", "parts": user_parts})

    final_system_prompt = f"{get_system_prompt()}\n\n[EK_TALIMAT]: {custom_prompt}" if custom_prompt else get_system_prompt()

    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": final_system_prompt}]},
        "generationConfig": {"temperature": 0.65, "topP": 0.9, "maxOutputTokens": 2000}
    }

    tried_keys = set()
    for attempt in range(len(GEMINI_API_KEYS)):
        key = await get_next_gemini_key()
        if not key or key in tried_keys:
            continue
        tried_keys.add(key)
        try:
            request_url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:generateContent?key={key}"
            async with sess.post(request_url, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif resp.status == 429:
                    await mark_key_rate_limited(key)
                    print(f"⚠️ 429 Rate Limit — farklı key deneniyor ({attempt+1}/{len(GEMINI_API_KEYS)})")
                    continue
                elif resp.status == 404:
                    fallback_url = f"{GEMINI_REST_URL_BASE}/gemini-1.5-flash:generateContent?key={key}"
                    async with sess.post(fallback_url, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as resp_f:
                        if resp_f.status == 200:
                            data = await resp_f.json()
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            print(f"⚠️ Key hatası: {e}")
            continue

    return "⚠️ Şu an yoğunluk var, tekrar dener misin?"

# ------------------------------------
# API ROUTE'LARI
# ------------------------------------
@app.route("/")
async def home():
    return f"Nova 4.0 API Çalışıyor - {get_nova_date()}"

@app.route("/api/chat", methods=["POST", "OPTIONS"])
async def chat():
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400

        user_id = data.get("userId", "anon")
        chat_id = data.get("currentChat", "default")
        user_message = data.get("message", "")
        image_base64 = data.get("image")
        custom_instruction = data.get("systemInstruction") or data.get("systemPrompt", "")

        user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
        chat_history = user_chats.setdefault(chat_id, [])

        response_text = await gemma_cevap_async(
            user_message,
            chat_history,
            session,
            user_id,
            image_base64,
            custom_instruction
        )

        if not response_text or response_text.startswith("⚠️"):
            return jsonify({
                "response": response_text or "Bir hata oluştu.",
                "status": "error"
            }), 200

        chat_history.append({"sender": "user", "message": user_message})
        chat_history.append({"sender": "nova", "message": response_text})
        DIRTY_FLAGS["history"] = True

        return jsonify({
            "response": response_text,
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "model": GEMINI_MODEL_NAME
        }), 200

    except Exception as e:
        return jsonify({
            "response": f"⚠️ Sunucu hatası: {str(e)}",
            "status": "error"
        }), 500

@app.route("/api/delete_chat", methods=["POST", "OPTIONS"])
async def delete_chat():
    try:
        data = await request.get_json()
        user_id = data.get("userId", "anon")
        chat_id = data.get("chatId")
        if not chat_id:
            return jsonify({"success": False, "error": "chatId gerekli"}), 400
        user_chats = GLOBAL_CACHE["history"].get(user_id, {})
        if chat_id in user_chats:
            del user_chats[chat_id]
            DIRTY_FLAGS["history"] = True
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/history", methods=["GET", "OPTIONS"])
async def get_history():
    user_id = request.args.get("userId", "anon")
    user_chats = GLOBAL_CACHE["history"].get(user_id, {})
    return jsonify(user_chats), 200

# ------------------------------------
# LIVE MODU (WebSocket)
# ------------------------------------
@app.websocket("/ws/chat")
async def ws_chat_handler():
    await websocket.accept()

    while True:
        try:
            raw_data = await websocket.receive()
            msg = json.loads(raw_data)

            user_id = msg.get("userId", "anon")
            chat_id = msg.get("chatId", "live")
            user_message = msg.get("message", "")

            user_chats = GLOBAL_CACHE["history"].setdefault(user_id, {})
            chat_history = user_chats.setdefault(chat_id, [])

            # WebSocket için de canlı veri kontrol et
            live_context = ""
            search_needed, optimized_query = await should_search_internet(user_message, session)
            if search_needed:
                query_to_use = optimized_query if optimized_query else user_message
                summary = await fetch_live_data(query_to_use, session)
                if summary:
                    live_context = f"\n\n<WEB_DATA>{summary}</WEB_DATA>"

            key = await get_next_gemini_key()
            if not key:
                await websocket.send("⚠️ API anahtarı bulunamadı.")
                await websocket.send("[END]")
                return

            url = f"{GEMINI_REST_URL_BASE}/{GEMINI_MODEL_NAME}:streamGenerateContent?key={key}&alt=sse"

            payload = {
                "contents": [{"role": "user", "parts": [{"text": f"{user_message}{live_context}"}]}],
                "system_instruction": {"parts": [{"text": get_system_prompt()}]},
                "generationConfig": {"temperature": 0.7}
            }

            full_response = ""

            async with session.post(url, json=payload) as resp:
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data:"):
                        try:
                            chunk = json.loads(line[5:])
                            candidates = chunk.get("candidates", [])
                            if not candidates:
                                continue
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if not parts:
                                continue
                            txt = parts[0].get("text")
                            if not txt:
                                continue
                            full_response += txt
                            await websocket.send(txt)
                        except json.JSONDecodeError:
                            continue

            await websocket.send("[END]")

            chat_history.append({"sender": "user", "message": user_message})
            chat_history.append({"sender": "nova", "message": full_response})
            DIRTY_FLAGS["history"] = True

        except Exception as e:
            await websocket.send(f"HATA: {str(e)}")
            await websocket.send("[END]")
            break

async def keep_alive():
    while True:
        await asyncio.sleep(600)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.name == 'nt':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except:
            pass
    app.run(host="0.0.0.0", port=port, debug=False)