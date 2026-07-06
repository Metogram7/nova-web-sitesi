import json
import re
from datetime import datetime, timezone, timedelta

from config import NEWS_API_KEY
from utils import clean_html, safe_get, safe_post, rand_headers


async def scrape_ddg_instant(query, sess):
    results = []
    status, text = await safe_get(sess, "https://api.duckduckgo.com/", params={
        "q": query, "format": "json", "no_html": "1",
        "skip_disambig": "1", "kl": "tr-tr", "no_redirect": "1"}, timeout=8)
    if status == 200 and text:
        try:
            d = json.loads(text)
            if d.get("Answer"):
                results.append({"snippet": d["Answer"], "src": "ddg_instant"})
            if d.get("AbstractText"):
                results.append({"snippet": d["AbstractText"], "src": "ddg_abstract"})
            for t in d.get("RelatedTopics", [])[:3]:
                if isinstance(t, dict) and t.get("Text"):
                    results.append({"snippet": t["Text"], "src": "ddg_topic"})
        except Exception:
            pass
    return results


async def scrape_ddg_html(query, sess):
    results = []
    status, html = await safe_post(sess, "https://html.duckduckgo.com/html/",
                                   data={"q": query, "kl": "tr-tr"}, timeout=14)
    if status == 200 and html:
        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        for t, s in zip(titles[:7], snippets[:7]):
            clean_s = clean_html(s)
            if clean_s and len(clean_s) > 15:
                results.append({"title": clean_html(t), "snippet": clean_s, "src": "ddg_html"})
    return results


async def scrape_bing(query, sess):
    results = []
    status, html = await safe_get(sess, "https://www.bing.com/search",
        params={"q": query, "setlang": "tr", "cc": "TR", "mkt": "tr-TR", "count": "8"},
        headers=rand_headers({"Referer": "https://www.bing.com/"}), timeout=14)
    if status == 200 and html:
        blocks = re.findall(r'<li[^>]*class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)
        for block in blocks[:6]:
            h2 = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
            p  = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if p:
                snippet = clean_html(p.group(1))
                if snippet and len(snippet) > 20:
                    results.append({"title": clean_html(h2.group(1)) if h2 else "", "snippet": snippet, "src": "bing"})
    return results


async def scrape_wikipedia(query, sess):
    results = []
    status, text = await safe_get(sess, "https://tr.wikipedia.org/w/api.php", params={
        "action": "query", "format": "json", "list": "search",
        "srsearch": query, "srlimit": 3, "utf8": 1}, timeout=10)
    if status == 200 and text:
        try:
            d = json.loads(text)
            for item in d.get("query", {}).get("search", []):
                snippet = clean_html(item.get("snippet", ""))
                if snippet:
                    results.append({"title": item.get("title", ""), "snippet": snippet, "src": "wikipedia_tr"})
        except Exception:
            pass
    return results


async def scrape_earthquake(query, sess):
    results = []
    msg = query.lower()
    if not any(w in msg for w in ["deprem", "sarsıntı", "kandilli", "richter", "büyüklük", "kaç şiddet"]):
        return results
    status, html = await safe_get(sess, "http://www.koeri.boun.edu.tr/scripts/lst0.asp", timeout=12)
    if status == 200 and html:
        rows = re.findall(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
        if rows:
            lines = rows[0].strip().split('\n')
            for line in lines[1:6]:
                parts = line.split()
                if len(parts) >= 7:
                    results.append({"snippet": f"Deprem: {parts[0]} {parts[1]} | Büyüklük {parts[6]} | {' '.join(parts[8:]) if len(parts) > 8 else ''}", "src": "kandilli"})
    return results[:4]


async def get_clock():
    tr_tz = timezone(timedelta(hours=3))
    now   = datetime.now(tr_tz)
    gunler = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    aylar  = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
               "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    return [{"snippet": f"Türkiye saati: {now.strftime('%H:%M:%S')} ({now.day} {aylar[now.month-1]} {now.year} {gunler[now.weekday()]}).", "src": "system_clock"}]


async def scrape_newsapi(query, sess):
    if not NEWS_API_KEY:
        return []
    results = []
    status, text = await safe_get(sess, "https://newsapi.org/v2/everything",
        params={"q": query, "language": "tr", "sortBy": "publishedAt", "pageSize": 6, "apiKey": NEWS_API_KEY},
        headers=rand_headers({"Accept": "application/json"}), timeout=10)
    if status == 200 and text:
        try:
            d = json.loads(text)
            for art in d.get("articles", [])[:6]:
                title     = art.get("title", "")
                desc      = art.get("description", "")
                published = art.get("publishedAt", "")[:10]
                source    = art.get("source", {}).get("name", "")
                if title:
                    results.append({"snippet": f"[{published}] [{source}] {title}. {desc}"[:250], "src": "newsapi"})
        except Exception as e:
            print(f"NewsAPI parse: {e}")
    return results
