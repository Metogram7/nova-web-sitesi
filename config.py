import os
import re

# ============================================================
# DOSYA YOLLARI
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    return os.path.join(BASE_DIR, filename)

HISTORY_FILE      = get_path("chat_history.json")
LAST_SEEN_FILE    = get_path("last_seen.json")
CACHE_FILE        = get_path("cache.json")
TOKENS_FILE       = get_path("tokens.json")
SHARED_CHATS_FILE = get_path("shared_chats.json")

# ============================================================
# API KEY'LER
# ============================================================
GEMINI_API_KEYS = [k.strip() for k in [
    os.getenv("GEMINI_API_KEY_A", ""),
    os.getenv("GEMINI_API_KEY_B", ""),
    os.getenv("GEMINI_API_KEY_C", ""),
    os.getenv("GEMINI_API_KEY_D", ""),
    os.getenv("GEMINI_API_KEY_E", ""),
    os.getenv("GEMINI_API_KEY_F", ""),
] if k.strip()]

COINGECKO_API_KEY    = os.getenv("COINGECKO_API_KEY", "").strip()
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "").strip()
OPENWEATHER_API_KEY  = os.getenv("OPENWEATHER_API_KEY", "").strip()
NEWS_API_KEY         = os.getenv("NEWS_API_KEY", "").strip()
ALPHA_VANTAGE_KEY    = os.getenv("ALPHA_VANTAGE_KEY", "").strip()
APIFOOTBALL_KEY      = os.getenv("APIFOOTBALL_KEY", "").strip()

# ============================================================
# GEMINI AYARLARI
# ============================================================
GEMINI_MODEL_NAME    = "gemini-2.5-flash-lite"
GEMINI_REST_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL_TIMEOUT_SECS   = 18
LIVE_DATA_TIMEOUT_SECS = 8
KEY_COOLDOWN_SECS    = 60

# ============================================================
# CACHE AYARLARI
# ============================================================
RESP_CACHE_TTL  = 300
RESP_CACHE_MAX  = 200
SEARCH_CACHE_TTL = 180

_NO_CACHE_RE = re.compile(
    r"(saat|bugün|şimdi|anlık|dolar|euro|bitcoin|btc|hava|fiyat|kur|"
    r"skor|maç|borsa|hisse|haber|deprem|puan\s*durumu)",
    re.IGNORECASE | re.UNICODE,
)

def is_cacheable(msg: str) -> bool:
    return not _NO_CACHE_RE.search(msg)

# ============================================================
# USER AGENT POOL
# ============================================================
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 Version/17.3 Mobile/15E148 Safari/604.1",
]

# ============================================================
# SCRAPER KURALLARI
# ============================================================
SCRAPER_RULES = [
    (r"(dolar|euro|sterlin|gbp|usd|eur|kur|döviz|frank|yen|riyal|ruble)", ["exchange", "alpha_vantage", "ddg_html"]),
    (r"(bitcoin|btc|ethereum|eth|kripto|bnb|solana|dogecoin|coin|xrp|cardano|ada)", ["crypto", "ddg_html"]),
    (r"(bist|borsa\s*istanbul|thyao|thy|garan|akbnk|garanti|akbank|yapı\s*kredi|ykbnk|arçelik|arclk|ereğli|eregl|aselsan|asels|tupraş|tuprs|sabancı|sahol|koç|kchol|bimas|bim|migros)", ["bist", "ddg_html"]),
    (r"(altın|gram\s*altın|çeyrek\s*altın|gold|gümüş|petrol|emtia)", ["yahoo", "exchange"]),
    (r"(nasdaq|s&p|dow\s*jones|apple\s*hisse|tesla\s*hisse|nvidia\s*hisse)", ["yahoo"]),
    (r"(hava\s*durumu|hava\s*nasıl|kaç\s*derece|sıcaklık|yağmur\s*var|kar\s*var|rüzgar|nem\s*oranı)", ["weather"]),
    (r"(iftar|sahur|namaz\s*vakti|ezan|imsak|akşam\s*ezanı)", ["prayer"]),
    (r"saat\s*kaç", ["clock"]),
    (r"(deprem|sarsıntı|kandilli|richter|kaç\s*şiddet)", ["earthquake", "gnews"]),
    (r"(puan\s*durumu|puan\s*tablosu|süper\s*lig\s*(sıra|puan|lider|kaçıncı))", ["mackolik", "flashscore", "ddg_html"]),
    (r"(fenerbahçe|galatasaray|beşiktaş|trabzonspor|başakşehir|sivasspor|konyaspor|alanyaspor|kasımpaşa|antalyaspor)", ["flashscore", "mackolik", "gnews", "apifootball"]),
    (r"(maç\s*sonuç|skor\s*kaç|kim\s*kazandı|bitti\s*mi|gol\s*attı|transfer\s*haberi)", ["flashscore", "mackolik", "gnews", "apifootball"]),
    (r"(son\s*dakika|breaking|acil\s*haber|flaş)", ["rss_news", "gnews", "newsapi"]),
    (r"(gündem|ne\s*oluyor|bugün\s*ne\s*var|haberler|önemli\s*haber)", ["rss_news", "gnews", "turkish_news", "newsapi"]),
    (r"(nedir|kimdir|nerede|ne\s*zaman|tarihçe|hakkında|tarihi)", ["wikipedia", "ddg_instant"]),
    (r"(seçim|cumhurbaşkanı|bakan|hükümet|tbmm|meclis).*(güncel|son|bugün|şu\s*an)", ["rss_news", "gnews"]),
    (r"(ekonomi|enflasyon|faiz|tüfe|büyüme|gdp|gsyh).*(son|güncel|bugün|açıklandı)", ["rss_news", "ddg_html"]),
]
DEFAULT_SCRAPERS = ["ddg_instant", "ddg_html"]

# ============================================================
# ARAMA GEREKLİLİK KONTROLÜ
# ============================================================
MUST_SEARCH = [
    r"(puan\s*durumu|puan\s*tablosu|lig\s*sıralaması|süper\s*lig)",
    r"(fenerbahçe|galatasaray|beşiktaş|trabzonspor|başakşehir|sivasspor|konyaspor|antalyaspor|alanyaspor)",
    r"(maç\s*sonuç|skor\s*kaç|kim\s*kazandı|bitti\s*mi|gol\s*attı|transfer)",
    r"(dolar|euro|sterlin|gbp|usd|eur|kur|döviz|altın|gram\s*altın|çeyrek|gümüş|petrol)",
    r"(bitcoin|btc|ethereum|eth|kripto|bnb|solana|dogecoin|coin|xrp)",
    r"(bist|borsa\s*istanbul|borsa|hisse|thyao|garan|akbnk|garanti|akbank)",
    r"(nasdaq|s&p|dow|tesla|apple|nvidia|amazon|meta).*(hisse|fiyat|bugün)",
    r"(hava\s*durumu|hava\s*nasıl|kaç\s*derece|sıcaklık|yağmur\s*var|kar\s*var)",
    r"saat\s*kaç",
    r"(iftar|sahur|namaz\s*vakti|ezan|imsak)",
    r"(son\s*dakika|breaking|gündem|bugün\s*ne\s*oldu)",
    r"(haber|gelişme|açıkladı|duyurdu|atandı|istifa).*(bugün|şu\s*an|son)",
    r"(deprem|sarsıntı|richter|kandilli)",
    r"(enflasyon|faiz|tüfe|büyüme).*(son|güncel|açıklandı|kaç)",
    r"(şu\s*an|şimdi|bugün|anlık|güncel|en\s*son).*(ne|kim|kaç|nasıl|nerede|hangi)",
    r"(kim|ne|kaç).*(şu\s*an|şimdi|bugün|güncel|hâlâ|hala)",
]

NO_SEARCH = [
    r"(nasıl\s+yapılır|nasıl\s+çalışır|nasıl\s+yapabilirim)",
    r"(ne\s+demek|anlamı\s+nedir|tanımı\s+nedir|ne\s+anlama\s+gelir)",
    r"(tarihçe|tarihi|eskiden|antik|kadim|m\.ö|milattan)",
    r"(neden|niçin|niye\s+böyle)",
    r"(kod\s+yaz|program\s+yaz|python|javascript|html|css|örnek\s+kod|algoritma)",
    r"(şiir\s+yaz|hikaye\s+yaz|masal|roman|kompozisyon|metin\s+yaz)",
    r"(matematik|hesapla|kaçtır|toplam|çarp|böl|integral|türev|denklem)",
    r"(tarif\s+ver|nasıl\s+pişirilir|malzeme\s+listesi|yemek\s+tarifi)",
    r"(felsefe|teori|kavram|ilke|prensip|ideoloji)",
]
