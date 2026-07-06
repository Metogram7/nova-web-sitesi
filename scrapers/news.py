import json
import re
import asyncio

from utils import clean_html, safe_get, safe_post, rand_headers


async def scrape_google_news_rss(query, sess):
    results = []
    url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=tr&gl=TR&ceid=TR:tr"
    status, xml = await safe_get(sess, url, timeout=10)
    if status == 200 and xml:
        items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
        for item in items[:6]:
            title_m   = re.search(r'<title>(.*?)</title>', item)
            pubdate_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
            title   = clean_html(title_m.group(1)) if title_m else ""
            pubdate = pubdate_m.group(1).strip()[:16] if pubdate_m else ""
            if title:
                results.append({"snippet": f"[{pubdate}] {title}" if pubdate else title, "src": "gnews_rss"})
    return results


async def scrape_rss_news(query, sess):
    rss_sources = [
        "https://www.ntv.com.tr/son-dakika.rss",
        "https://www.hurriyet.com.tr/rss/gundem",
        "https://www.sabah.com.tr/rss/anasayfa.xml",
        "https://www.milliyet.com.tr/rss/rssNew/sondakikaRss.xml",
        "https://www.haberturk.com/rss/anasayfa.xml",
        "https://www.cumhuriyet.com.tr/rss/son_dakika.xml",
        "https://www.sozcu.com.tr/feed/",
        "https://www.bloomberght.com/rss",
        "https://www.ekonomim.com/rss",
    ]
    msg = query.lower()
    keywords = [w for w in re.split(r'\s+', msg) if len(w) > 3][:5]

    async def fetch_rss(url):
        site_results = []
        status, xml = await safe_get(sess, url, timeout=10)
        if status == 200 and xml:
            items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
            for item in items[:10]:
                title_m   = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
                desc_m    = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item, re.DOTALL)
                pubdate_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
                title   = clean_html(title_m.group(1)) if title_m else ""
                desc    = clean_html(desc_m.group(1))[:150] if desc_m else ""
                pubdate = pubdate_m.group(1).strip()[:16] if pubdate_m else ""
                if not title:
                    continue
                if keywords and not any(kw in title.lower() for kw in keywords):
                    continue
                site = url.split("/")[2].replace("www.", "")
                snippet = f"[{pubdate}] {title}"
                if desc and desc != title:
                    snippet += f" — {desc}"
                site_results.append({"snippet": snippet, "src": site})
        return site_results

    nested = await asyncio.gather(*[fetch_rss(u) for u in rss_sources], return_exceptions=True)
    results = []
    for r in nested:
        if isinstance(r, list):
            results.extend(r)
    return results[:10]


async def scrape_turkish_news_sites(query, sess):
    sources = [
        {"name": "ntv",       "url": f"https://www.ntv.com.tr/arama?query={query.replace(' ', '+')}", "pat": r'class="[^"]*card-title[^"]*"[^>]*>(.*?)</[^>]+>'},
        {"name": "hurriyet",  "url": f"https://www.hurriyet.com.tr/arama/?q={query.replace(' ', '+')}", "pat": r'<h[23][^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h[23]>'},
        {"name": "sabah",     "url": f"https://www.sabah.com.tr/ara?q={query.replace(' ', '+')}", "pat": r'<h[23][^>]*>(.*?)</h[23]>'},
        {"name": "haberturk", "url": f"https://www.haberturk.com/arama?q={query.replace(' ', '+')}", "pat": r'class="[^"]*content-title[^"]*"[^>]*>(.*?)</[a-z]+>'},
        {"name": "milliyet",  "url": f"https://www.milliyet.com.tr/arama/{query.replace(' ', '-')}/", "pat": r'class="[^"]*card__title[^"]*"[^>]*>(.*?)</[^>]+>'},
    ]
    async def fetch_site(src):
        site_results = []
        status, html = await safe_get(sess, src["url"], timeout=12)
        if status == 200 and html:
            matches = re.findall(src["pat"], html, re.DOTALL)
            for m in matches[:3]:
                t = clean_html(m)
                if t and 15 < len(t) < 300:
                    site_results.append({"snippet": t, "src": src["name"]})
        return site_results
    nested = await asyncio.gather(*[fetch_site(s) for s in sources], return_exceptions=True)
    results = []
    for r in nested:
        if isinstance(r, list):
            results.extend(r)
    return results[:8]
