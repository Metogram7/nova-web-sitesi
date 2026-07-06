import json
from datetime import datetime, timezone, timedelta

from utils import rand_headers, safe_get


_CITY_COORDS = {
    "istanbul": (41.0082, 28.9784), "ankara": (39.9334, 32.8597),
    "izmir": (38.4192, 27.1287), "bursa": (40.1885, 29.0610),
    "antalya": (36.8969, 30.7133), "adana": (37.0000, 35.3213),
    "konya": (37.8714, 32.4846), "gaziantep": (37.0662, 37.3833),
    "mersin": (36.8000, 34.6333), "kayseri": (38.7205, 35.4826),
    "trabzon": (41.0015, 39.7178), "samsun": (41.2867, 36.3300),
    "eskişehir": (39.7767, 30.5206), "diyarbakır": (37.9144, 40.2306),
    "erzurum": (39.9086, 41.2769), "van": (38.4891, 43.4089),
    "bodrum": (37.0345, 27.4305), "alanya": (36.5432, 31.9999),
    "marmaris": (36.8544, 28.2693), "fethiye": (36.6558, 29.1024),
    "muğla": (37.2153, 28.3636), "kapadokya": (38.6431, 34.8289),
    "pamukkale": (37.9215, 29.1207),
}

_CITY_PRAYER_MAP = {
    "istanbul": "Istanbul", "ankara": "Ankara", "izmir": "Izmir",
    "bursa": "Bursa", "antalya": "Antalya", "konya": "Konya",
    "trabzon": "Trabzon", "kayseri": "Kayseri", "adana": "Adana",
    "gaziantep": "Gaziantep", "eskişehir": "Eskisehir",
    "samsun": "Samsun", "denizli": "Denizli", "mersin": "Mersin",
    "diyarbakır": "Diyarbakir", "erzurum": "Erzurum",
}


def _wmo_code(code):
    m = {
        0: "Açık", 1: "Az bulutlu", 2: "Parçalı bulutlu", 3: "Bulutlu",
        45: "Sisli", 48: "Dondurucu sis", 51: "Hafif çisenti", 53: "Orta çisenti",
        55: "Yoğun çisenti", 61: "Hafif yağmur", 63: "Orta yağmur", 65: "Kuvvetli yağmur",
        71: "Hafif kar", 73: "Orta kar", 75: "Yoğun kar",
        80: "Hafif sağanak", 81: "Orta sağanak", 82: "Kuvvetli sağanak",
        95: "Gök gürültülü fırtına", 96: "Dolu'lu fırtına", 99: "Şiddetli fırtına",
    }
    return m.get(code, "Değişken")


async def scrape_weather(query, sess):
    results = []
    msg = query.lower()
    lat, lon, city_name = 41.0082, 28.9784, "İstanbul"
    for city, coords in _CITY_COORDS.items():
        if city in msg:
            lat, lon = coords
            city_name = city.title()
            break
    status, text = await safe_get(sess, "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "Europe/Istanbul", "forecast_days": 3,
        }, headers=rand_headers({"Accept": "application/json"}), timeout=10)
    if status == 200 and text:
        try:
            d   = json.loads(text)
            cur = d.get("current", {})
            temp   = cur.get("temperature_2m", "?")
            feels  = cur.get("apparent_temperature", "?")
            humid  = cur.get("relative_humidity_2m", "?")
            wind   = cur.get("wind_speed_10m", "?")
            precip = cur.get("precipitation", 0)
            wcode  = cur.get("weather_code", 0)
            results.append({
                "snippet": (
                    f"{city_name}: {temp}°C (hissedilen {feels}°C), {_wmo_code(wcode)}. "
                    f"Nem %{humid}, Rüzgar {wind} km/s, Yağış {precip} mm."
                ), "src": "open_meteo"
            })
            daily = d.get("daily", {})
            dates = daily.get("time", [])
            maxes = daily.get("temperature_2m_max", [])
            mins  = daily.get("temperature_2m_min", [])
            for i in range(min(3, len(dates))):
                results.append({"snippet": f"{dates[i]}: max {maxes[i]}°C / min {mins[i]}°C", "src": "open_meteo_daily"})
        except Exception as e:
            print(f"[!] Hava durumu parse: {e}")
    return results


async def scrape_prayer_times(query, sess):
    results = []
    msg = query.lower()
    if not any(w in msg for w in ["iftar", "sahur", "namaz", "ezan", "imsak", "akşam vakti"]):
        return results
    city_key = next((k for k in _CITY_PRAYER_MAP if k in msg), "istanbul")
    city_api = _CITY_PRAYER_MAP[city_key]
    now = datetime.now(timezone(timedelta(hours=3)))
    status, text = await safe_get(sess,
        f"https://api.aladhan.com/v1/timingsByCity/{now.strftime('%d-%m-%Y')}",
        params={"city": city_api, "country": "Turkey", "method": 13},
        headers=rand_headers({"Accept": "application/json"}), timeout=10)
    if status == 200 and text:
        try:
            d = json.loads(text)
            t = d["data"]["timings"]
            results.append({
                "snippet": (
                    f"{city_key.title()} ({now.strftime('%d.%m.%Y')}): "
                    f"İmsak/Sahur {t.get('Fajr','?')}, Güneş {t.get('Sunrise','?')}, "
                    f"Öğle {t.get('Dhuhr','?')}, İkindi {t.get('Asr','?')}, "
                    f"İftar/Akşam {t.get('Maghrib','?')}, Yatsı {t.get('Isha','?')}."
                ), "src": "aladhan_api"
            })
        except Exception as e:
            print(f"[!] Prayer times parse: {e}")
    return results
