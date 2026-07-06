import json
import re

from config import APIFOOTBALL_KEY
from utils import clean_html, safe_get, rand_headers


async def scrape_mackolik(query, sess):
    results = []
    msg = query.lower()
    do_score = any(w in msg for w in ["skor", "sonuç", "maç", "kazandı", "bitti", "gol"])
    do_table = any(w in msg for w in ["puan durumu", "puan tablosu", "sıralama", "lider"])
    if do_score:
        status, html = await safe_get(sess, "https://www.mackolik.com/canli-sonuclar",
            headers=rand_headers({"Referer": "https://www.mackolik.com/"}), timeout=12)
        if status == 200 and html:
            matches = re.findall(
                r'class="[^"]*match-home[^"]*"[^>]*>(.*?)</[^>]+>.*?'
                r'class="[^"]*match-score[^"]*"[^>]*>(.*?)</[^>]+>.*?'
                r'class="[^"]*match-away[^"]*"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
            for home, score, away in matches[:8]:
                h, s, a = clean_html(home), clean_html(score), clean_html(away)
                if h and a:
                    results.append({"snippet": f"{h} {s} {a}", "src": "mackolik"})
    if do_table:
        status, html = await safe_get(sess, "https://www.mackolik.com/lig/turkiye/super-lig/puan-durumu",
            headers=rand_headers({"Referer": "https://www.mackolik.com/"}), timeout=12)
        if status == 200 and html:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            for row in rows[:6]:
                cells = [clean_html(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL) if clean_html(c)]
                if len(cells) >= 3:
                    results.append({"snippet": " | ".join(cells[:7]), "src": "mackolik_table"})
    return results


async def scrape_flashscore(query, sess):
    results = []
    msg = query.lower()
    spor_teams = ["fenerbahçe", "galatasaray", "beşiktaş", "trabzonspor", "başakşehir",
                  "sivasspor", "konyaspor", "antalyaspor", "alanyaspor", "kasımpaşa", "kayserispor"]
    team = next((t for t in spor_teams if t in msg), None)
    status, html = await safe_get(sess, "https://www.flashscore.com.tr/futbol/turkiye/super-lig/sonuclar/",
        headers=rand_headers({"Referer": "https://www.flashscore.com.tr/"}), timeout=14)
    if status == 200 and html:
        blocks = re.findall(r'class="[^"]*event__match[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        for block in blocks[:15]:
            home_m  = re.search(r'class="[^"]*event__participant--home[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            away_m  = re.search(r'class="[^"]*event__participant--away[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            score_m = re.search(r'class="[^"]*event__score[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            if home_m and away_m:
                h = clean_html(home_m.group(1))
                a = clean_html(away_m.group(1))
                s = clean_html(score_m.group(1)) if score_m else "-"
                if team:
                    if team.lower() in h.lower() or team.lower() in a.lower():
                        results.append({"snippet": f"{h} {s} {a}", "src": "flashscore"})
                elif any(w in msg for w in ["maç", "skor", "sonuç", "puan"]):
                    results.append({"snippet": f"{h} {s} {a}", "src": "flashscore"})
    return results[:6]


async def scrape_apifootball(query, sess):
    if not APIFOOTBALL_KEY:
        return []
    results = []
    msg = query.lower()
    team_ids = {
        "fenerbahce": 636, "fenerbahçe": 636, "galatasaray": 506,
        "besiktas": 635, "beşiktaş": 635, "trabzonspor": 641,
        "basaksehir": 7457, "başakşehir": 7457,
    }
    team_id = next((tid for team, tid in team_ids.items() if team in msg), None)
    if not team_id:
        return results
    status, text = await safe_get(sess, "https://v3.football.api-sports.io/fixtures",
        params={"team": team_id, "last": 3, "timezone": "Europe/Istanbul"},
        headers=rand_headers({"Accept": "application/json", "x-rapidapi-key": APIFOOTBALL_KEY, "x-rapidapi-host": "v3.football.api-sports.io"}), timeout=12)
    if status == 200 and text:
        try:
            d = json.loads(text)
            for fix in d.get("response", [])[:3]:
                f      = fix.get("fixture", {})
                teams  = fix.get("teams", {})
                goals  = fix.get("goals", {})
                home   = teams.get("home", {}).get("name", "?")
                away   = teams.get("away", {}).get("name", "?")
                gh, ga = goals.get("home"), goals.get("away")
                score  = f"{gh}-{ga}" if gh is not None else "Oynanmadı"
                date_s = f.get("date", "")[:10]
                stat   = f.get("status", {}).get("long", "")
                results.append({"snippet": f"{date_s} | {home} {score} {away} ({stat})", "src": "apifootball"})
        except Exception as e:
            print(f"API-Football parse: {e}")
    return results
