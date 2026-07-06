import random
import re

import aiohttp

from config import UA_POOL


def rand_headers(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }
    if extra:
        h.update(extra)
    return h


def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def safe_get(sess, url, *, params=None, headers=None, timeout=12):
    try:
        async with sess.get(url, params=params, headers=headers or rand_headers(),
                            timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as r:
            return r.status, await r.text(errors='replace')
    except Exception as e:
        print(f"[!] GET [{url[:55]}]: {e}")
        return 0, ""


async def safe_post(sess, url, *, data=None, json_body=None, headers=None, timeout=12):
    try:
        async with sess.post(url, data=data, json=json_body, headers=headers or rand_headers(),
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return r.status, await r.text(errors='replace')
    except Exception as e:
        print(f"[!] POST [{url[:55]}]: {e}")
        return 0, ""
