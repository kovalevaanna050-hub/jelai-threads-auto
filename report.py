"""Дневная сводка: сколько постов вышло вчера и сколько просмотров они набрали.
Формат сообщения в Telegram: "кол-во постов - кол-во просмотров".

Запуск: python report.py
Секреты (env): THREADS_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.threads.net/v1.0"
TZ = dt.timezone(dt.timedelta(hours=7))  # Asia/Saigon
GOAL = 100000  # просмотров в сутки, к которым мы идём

now = lambda: dt.datetime.now(TZ)


def load(name, default=None):
    try:
        with open(name, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def call(method, path, **params):
    params["access_token"] = os.environ["THREADS_TOKEN"]
    data = urllib.parse.urlencode(params)
    url = f"{API}/{path}"
    req = urllib.request.Request(f"{url}?{data}") if method == "GET" else urllib.request.Request(url, data=data.encode(), method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code >= 500 and attempt < 3:
                time.sleep(10 * (attempt + 1)); continue
            raise RuntimeError(f"{e.code} {path}: {body}") from None


def views(media_id):
    try:
        r = call("GET", f"{media_id}/insights", metric="views")
    except Exception as e:
        print("insights error", media_id, str(e)[:200])
        return None
    for m in r.get("data", []):
        if m.get("name") == "views":
            vals = m.get("values", [])
            if vals:
                return vals[0].get("value", 0)
    return 0


def telegram(text):
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        print("Telegram не настроен:\n" + text); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30)
    except Exception as e:
        print("Telegram error:", e)


def main():
    state = load("state.json", {})
    recent = state.get("recent_posts", [])
    yday = (now() - dt.timedelta(days=1)).date()
    posts = [p for p in recent if dt.datetime.fromisoformat(p["ts"]).date() == yday]

    if not posts:
        msg = f"📊 Сводка за {yday:%d.%m}: 0 - 0\nЗа вчера не нашлось постов в state.json (бот мог быть только что подключён)."
        telegram(msg); print(msg)
        return

    total, ok, failed = 0, 0, 0
    for p in posts:
        v = views(p["media_id"])
        if v is None:
            failed += 1
        else:
            total += v
            ok += 1
        time.sleep(1)

    boosts = sum(1 for p in posts if p.get("is_boost"))
    pct = round(total / GOAL * 100)
    msg = f"📊 Сводка за {yday:%d.%m}: {len(posts)} - {total}"
    extra = [f"из них бустов: {boosts}", f"цель {GOAL:,} просмотров/день: {pct}%".replace(",", " ")]
    if failed:
        extra.append(f"⚠️ не удалось получить просмотры для {failed} из {len(posts)} (проверьте право threads_manage_insights)")
    msg += "\n" + "\n".join(extra)

    telegram(msg)
    print(msg)


if __name__ == "__main__":
    main()
