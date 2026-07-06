import re
import asyncio
from datetime import datetime, timezone, timedelta

from config import SCRAPER_RULES, DEFAULT_SCRAPERS, LIVE_DATA_TIMEOUT_SECS, MUST_SEARCH, NO_SEARCH
from cache import cache_get, cache_set
from . import general, finance, news, sports, weather

SCRAPER_REGISTRY = {
    "ddg_instant": general.scrape_ddg_instant,
    "ddg_html": general.scrape_ddg_html,
    "bing": general.scrape_bing,
    "gnews": news.scrape_google_news_rss,
    "exchange": finance.scrape_exchange_rate,
    "crypto": finance.scrape_coingecko,
    "yahoo": finance.scrape_yahoo_finance,
    "bist": finance.scrape_bist,
    "turkish_news": news.scrape_turkish_news_sites,
    "mackolik": sports.scrape_mackolik,
    "flashscore": sports.scrape_flashscore,
    "weather": weather.scrape_weather,
    "prayer": weather.scrape_prayer_times,
    "clock": general.get_clock,
    "earthquake": general.scrape_earthquake,
    "wikipedia": general.scrape_wikipedia,
    "rss_news": news.scrape_rss_news,
    "newsapi": general.scrape_newsapi,
    "alpha_vantage": finance.scrape_alpha_vantage,
    "apifootball": sports.scrape_apifootball,
}


async def run_scrapers(query, names, sess):
    tasks = []
    for n in dict.fromkeys(names):
        fn = SCRAPER_REGISTRY.get(n)
        if fn:
            tasks.append(fn(query, sess))
    nested = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for r in nested:
        if isinstance(r, list):
            results.extend(r)
    return results


def _fallback_raw(items):
    parts = []
    for r in items[:4]:
        s = r.get("snippet", "")
        if s and s not in parts:
            parts.append(s)
    return " | ".join(parts)[:600] if parts else ""


async def fetch_live_data_full(query, sess):
    cached = cache_get(query)
    if cached:
        return cached
    msg = query.lower()
    selected = set(DEFAULT_SCRAPERS)
    for pattern, scrapers in SCRAPER_RULES:
        if re.search(pattern, msg):
            selected.update(scrapers)
    print(f"[WEB] '{query}' -> scraper'lar: {sorted(selected)}")
    try:
        results = await asyncio.wait_for(
            run_scrapers(query, list(selected), sess),
            timeout=LIVE_DATA_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        print(f"[!] Web toplama timeout ({LIVE_DATA_TIMEOUT_SECS}s): {query}")
        results = []
    if len(results) < 2:
        print(f"[!] Az sonuc ({len(results)}), Bing ekleniyor...")
        try:
            results.extend(await asyncio.wait_for(general.scrape_bing(query, sess), timeout=4))
        except asyncio.TimeoutError:
            print("[!] Bing fallback timeout")
    seen, unique = set(), []
    for r in results:
        k = r.get("snippet", "")[:70]
        if k and k not in seen:
            seen.add(k)
            unique.append(r)
    print(f"[DATA] {len(unique)} benzersiz sonuc")
    if not unique:
        return ""
    summary = _fallback_raw(unique)
    if summary:
        cache_set(query, summary)
    return summary


async def should_search(message, sess):
    msg = message.lower().strip()
    for pat in NO_SEARCH:
        if re.search(pat, msg):
            return False, ""
    for pat in MUST_SEARCH:
        if re.search(pat, msg):
            return True, _optimize_query(message, msg)
    return False, ""


def _optimize_query(original, msg):
    today = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    teams = ["fenerbahçe", "galatasaray", "beşiktaş", "trabzonspor", "başakşehir",
             "sivasspor", "konyaspor", "antalyaspor", "alanyaspor"]
    for team in teams:
        if team in msg:
            return f"{team} son maç sonucu {today}"
    if re.search(r"(puan\s*durumu|puan\s*tablosu)", msg): return f"süper lig puan durumu {today}"
    if re.search(r"(dolar|usd)", msg):                    return f"dolar TL kuru anlık {today}"
    if re.search(r"(euro|eur)", msg):                     return f"euro TL kuru anlık {today}"
    if re.search(r"(altın|gram\s*altın)", msg):           return f"gram altın fiyatı {today}"
    if re.search(r"(bitcoin|btc)", msg):                  return "bitcoin fiyatı usd try bugün"
    if "saat kaç" in msg:                                 return "türkiye saat"
    if re.search(r"(son\s*dakika|haber)", msg):           return f"{original} {today}"
    return original
