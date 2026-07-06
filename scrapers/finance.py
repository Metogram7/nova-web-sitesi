import json
import re
import asyncio

from config import EXCHANGERATE_API_KEY, COINGECKO_API_KEY, ALPHA_VANTAGE_KEY
from utils import rand_headers, safe_get


async def _try_frankfurter(currency, sess):
    """Fallback: frankfurter.app (ücretsiz, güvenilir)"""
    try:
        status, text = await safe_get(sess,
            f"https://api.frankfurter.app/latest?from={currency}&to=TRY", timeout=8)
        if status == 200 and text:
            d = json.loads(text)
            rate = d.get("rates", {}).get("TRY")
            if rate:
                date = d.get("date", "")
                return {"snippet": f"1 {currency} = {rate:.4f} TRY (Güncelleme: {date}).", "src": "frankfurter"}
    except Exception:
        pass
    return None


async def _try_erapi(currency, sess):
    """Önce ücretli API, yoksa ücretsiz open.er-api.com"""
    er_url = (f"https://v6.exchangerate-api.com/v6/{EXCHANGERATE_API_KEY}/latest/{currency}"
              if EXCHANGERATE_API_KEY else f"https://open.er-api.com/v6/latest/{currency}")
    status, text = await safe_get(sess, er_url, timeout=8)
    if status == 200 and text:
        try:
            d = json.loads(text)
            if d.get("result") == "success":
                rate = d["rates"].get("TRY")
                update_time = d.get("time_last_update_utc", "")[:16]
                if rate:
                    return {"snippet": f"1 {currency} = {rate:.4f} TRY (Güncelleme: {update_time} UTC).", "src": "exchangerate_api"}
        except Exception:
            pass
    return None


async def scrape_exchange_rate(query, sess):
    results = []
    msg = query.lower()
    targets = []
    if any(w in msg for w in ["dolar", "usd", "$"]):    targets.append("USD")
    if any(w in msg for w in ["euro", "eur", "€"]):     targets.append("EUR")
    if any(w in msg for w in ["sterlin", "gbp", "£"]):  targets.append("GBP")
    if any(w in msg for w in ["yen", "jpy"]):           targets.append("JPY")
    if any(w in msg for w in ["frank", "chf"]):         targets.append("CHF")
    if any(w in msg for w in ["riyal", "sar"]):         targets.append("SAR")
    if any(w in msg for w in ["ruble", "rub"]):         targets.append("RUB")
    for currency in targets[:2]:
        result = await _try_erapi(currency, sess)
        if not result:
            result = await _try_frankfurter(currency, sess)
        if not result:
            result = await _try_scrape_bigpara(currency, sess)
        if result:
            results.append(result)
        await asyncio.sleep(0.05)
    return results


async def _try_scrape_bigpara(currency, sess):
    """Son çare: bigpara.hurriyet.com.tr'den HTML scrape"""
    symbol_map = {"USD": "dolar", "EUR": "euro", "GBP": "sterlin", "CHF": "frank",
                  "JPY": "yen", "SAR": "riyal", "RUB": "ruble"}
    slug = symbol_map.get(currency)
    if not slug:
        return None
    try:
        status, html = await safe_get(sess,
            f"https://bigpara.hurriyet.com.tr/doviz/{slug}/",
            headers=rand_headers({"Referer": "https://bigpara.hurriyet.com.tr/"}),
            timeout=10)
        if status == 200 and html:
            # BigPara'da kur bilgisi genelde span.band veya benzeri elementte
            m = re.search(r'class="[^"]*value[^"]*"[^>]*>([\d.,]+)', html)
            if not m:
                m = re.search(r'data-value="([\d.]+)"', html)
            if not m:
                m = re.search(r'([\d]+[.,][\d]+)\s*TL', html)
            if m:
                rate_str = m.group(1).replace(",", ".")
                try:
                    rate = float(rate_str)
                    return {"snippet": f"1 {currency} ≈ {rate:.4f} TRY (bigpara).", "src": "bigpara"}
                except ValueError:
                    pass
    except Exception:
        pass
    return None


async def scrape_coingecko(query, sess):
    results = []
    msg = query.lower()
    coin_map = {
        "bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum",
        "bnb": "binancecoin", "xrp": "ripple", "solana": "solana", "sol": "solana",
        "dogecoin": "dogecoin", "doge": "dogecoin", "cardano": "cardano", "ada": "cardano",
        "avalanche": "avalanche-2", "avax": "avalanche-2", "tether": "tether", "usdt": "tether",
        "shiba": "shiba-inu", "shib": "shiba-inu", "polkadot": "polkadot", "dot": "polkadot",
        "litecoin": "litecoin", "ltc": "litecoin", "chainlink": "chainlink", "link": "chainlink",
    }
    ids = list({coin_id for kw, coin_id in coin_map.items() if kw in msg})
    if not ids:
        return results
    cg_headers = rand_headers({"Accept": "application/json"})
    if COINGECKO_API_KEY:
        cg_headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
        cg_url = "https://pro-api.coingecko.com/api/v3/simple/price"
    else:
        cg_url = "https://api.coingecko.com/api/v3/simple/price"
    status, text = await safe_get(sess, cg_url,
        params={"ids": ",".join(ids[:4]), "vs_currencies": "usd,try", "include_24hr_change": "true"},
        headers=cg_headers, timeout=10)
    if status == 200 and text:
        try:
            d = json.loads(text)
            for coin_id, prices in d.items():
                usd    = prices.get("usd", "?")
                try_p  = prices.get("try", "?")
                change = prices.get("usd_24h_change", 0)
                change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else ""
                results.append({"snippet": f"{coin_id.title()}: ${usd} USD / {try_p} TRY {change_str} 24s.", "src": "coingecko"})
        except Exception:
            pass
    return results


async def scrape_yahoo_finance(query, sess):
    results = []
    msg = query.lower()
    ticker_map = {
        "altın": "GC=F", "gold": "GC=F", "gümüş": "SI=F", "silver": "SI=F",
        "petrol": "CL=F", "oil": "CL=F", "nasdaq": "^IXIC",
        "s&p": "^GSPC", "s&p 500": "^GSPC", "dow": "^DJI",
        "apple": "AAPL", "google": "GOOGL", "microsoft": "MSFT",
        "tesla": "TSLA", "amazon": "AMZN", "nvidia": "NVDA", "meta": "META",
    }
    tickers = list({tick for kw, tick in ticker_map.items() if kw in msg})
    for ticker in tickers[:2]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        status, text = await safe_get(sess, url, params={"interval": "1d", "range": "1d"},
            headers=rand_headers({"Accept": "application/json"}), timeout=10)
        if status == 200 and text:
            try:
                d    = json.loads(text)
                meta = d["chart"]["result"][0]["meta"]
                price  = meta.get("regularMarketPrice", "?")
                prev   = meta.get("previousClose", price)
                change = ((price - prev) / prev * 100) if isinstance(price, (int, float)) and prev else 0
                cur    = meta.get("currency", "")
                name   = meta.get("shortName") or ticker
                results.append({"snippet": f"{name}: {price} {cur} ({change:+.2f}% bugün).", "src": "yahoo_finance"})
            except Exception:
                pass
        await asyncio.sleep(0.05)
    return results


async def scrape_bist(query, sess):
    results = []
    msg = query.lower()
    stock_map = {
        "thyao": "THYAO.IS", "thy": "THYAO.IS", "türk hava": "THYAO.IS",
        "arclk": "ARCLK.IS", "arçelik": "ARCLK.IS", "eregl": "EREGL.IS", "ereğli": "EREGL.IS",
        "sasa": "SASA.IS", "ekgyo": "EKGYO.IS", "bimas": "BIMAS.IS", "bim": "BIMAS.IS",
        "migros": "MGROS.IS", "mgros": "MGROS.IS", "krdmd": "KRDMD.IS",
        "asels": "ASELS.IS", "aselsan": "ASELS.IS", "tuprs": "TUPRS.IS", "tüpraş": "TUPRS.IS",
        "akbnk": "AKBNK.IS", "akbank": "AKBNK.IS", "garan": "GARAN.IS", "garanti": "GARAN.IS",
        "ykbnk": "YKBNK.IS", "yapı kredi": "YKBNK.IS", "sahol": "SAHOL.IS", "sabancı": "SAHOL.IS",
        "kchol": "KCHOL.IS", "koç holding": "KCHOL.IS",
        "bist": "XU100.IS", "borsa": "XU100.IS", "bist100": "XU100.IS",
    }
    tickers = list({tick for kw, tick in stock_map.items() if kw in msg})
    if not tickers and any(w in msg for w in ["borsa istanbul", "bist 100"]):
        tickers = ["XU100.IS"]
    for ticker in tickers[:2]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        status, text = await safe_get(sess, url, params={"interval": "1d", "range": "1d"},
            headers=rand_headers({"Accept": "application/json"}), timeout=10)
        if status == 200 and text:
            try:
                d    = json.loads(text)
                meta = d["chart"]["result"][0]["meta"]
                price  = meta.get("regularMarketPrice", "?")
                prev   = meta.get("previousClose", price)
                change = ((price - prev) / prev * 100) if isinstance(price, (int, float)) and prev else 0
                cur    = meta.get("currency", "TRY")
                name   = meta.get("shortName") or ticker
                results.append({"snippet": f"{name} ({ticker}): {price} {cur} ({change:+.2f}% bugün).", "src": "yahoo_bist"})
            except Exception:
                pass
        await asyncio.sleep(0.05)
    return results


async def scrape_alpha_vantage(query, sess):
    if not ALPHA_VANTAGE_KEY:
        return []
    results = []
    msg = query.lower()
    sym_map = {"dolar": "USD", "usd": "USD", "euro": "EUR", "eur": "EUR", "sterlin": "GBP", "gbp": "GBP"}
    from_sym = next((v for k, v in sym_map.items() if k in msg), None)
    if not from_sym:
        return results
    status, text = await safe_get(sess, "https://www.alphavantage.co/query",
        params={"function": "CURRENCY_EXCHANGE_RATE", "from_currency": from_sym, "to_currency": "TRY", "apikey": ALPHA_VANTAGE_KEY},
        headers=rand_headers({"Accept": "application/json"}), timeout=10)
    if status == 200 and text:
        try:
            d    = json.loads(text)
            info = d.get("Realtime Currency Exchange Rate", {})
            rate = info.get("5. Exchange Rate")
            upd  = info.get("6. Last Refreshed", "")
            if rate:
                results.append({"snippet": f"1 {from_sym} = {float(rate):.4f} TRY (AlphaVantage, {upd}).", "src": "alpha_vantage"})
        except Exception:
            pass
    return results
