"""Буст: находит пост за последние 24 часа с наибольшим числом просмотров
и публикует РЕМИКС того же товара — другое фото и/или другую подпись
(не точную копию: Threads в 2026 году занижает охват повторяющегося контента,
поэтому побайтово идентичный повтор скорее вредит, чем помогает).

Запуск: python boost.py [--dry-run]
Секреты (env): THREADS_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID

Логика:
- кандидаты — посты из state.json/recent_posts за последние 24 часа,
  которые сами не являются бустом (буст не бустим повторно);
- у каждого спрашиваем просмотры через Threads Insights API
  (нужно право приложения threads_manage_insights);
- побеждает пост с максимумом просмотров, если товар всё ещё в наличии;
- публикуем тот же товар, но другое фото и/или другую подпись из каталога
  (если вариантов нет — публикуем как есть), артикулы в ответе свежие.

Подписи в catalog.json — список {"text": ..., "style": "soft"|"edgy"}
(старый формат — просто строка — тоже поддерживается на случай, если
catalog.json ещё не обновлён).
"""
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.threads.net/v1.0"
TZ = dt.timezone(dt.timedelta(hours=7))  # Asia/Saigon
DRY = "--dry-run" in sys.argv[1:]

now = lambda: dt.datetime.now(TZ)


def load(name, default=None):
    try:
        with open(name, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, data):
    with open(name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def cap_text(c):
    return c["text"] if isinstance(c, dict) else c


def cap_style(c):
    return c.get("style", "soft") if isinstance(c, dict) else "soft"


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
    """Просмотры поста. None — если Insights недоступен (например, не хватает права приложения)."""
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


def wait_ready(cid):
    for _ in range(30):
        st = call("GET", cid, fields="status,error_message")
        if st.get("status") == "FINISHED":
            return
        if st.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"контейнер {cid}: {st}")
        time.sleep(5)
    raise RuntimeError(f"контейнер {cid} не готов за 150 с")


def publish(cid):
    wait_ready(cid)
    return call("POST", "me/threads_publish", creation_id=cid)["id"]


def telegram(text):
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        print("Telegram не настроен:\n" + text); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30)
    except Exception as e:
        print("Telegram error:", e)


# ---------- наличие (тот же алгоритм, что в post.py) ----------

def live_wb(nms):
    out = {}
    for i in range(0, len(nms), 20):
        chunk = ";".join(str(n) for n in nms[i:i + 20])
        url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={chunk}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:
            print("WB live check недоступен:", str(e)[:120])
            return {}
        prods = data.get("products") or data.get("data", {}).get("products") or []
        for p in prods:
            qty = p.get("totalQuantity")
            if qty is None:
                qty = sum(s.get("qty", 0) for size in p.get("sizes", []) for s in size.get("stocks", []))
            out[p["id"]] = qty > 0
        for n in nms[i:i + 20]:
            out.setdefault(n, False)
    return out


def stock_status():
    st = load("stock.json")
    wb, oz = dict(st["wb"]), dict(st["ozon"])
    source = f"stock.json от {st.get('checked')}"
    nm = st.get("wb_nm", {})
    live = live_wb([nm[c] for c in wb if c in nm])
    if live:
        for code, n in nm.items():
            if n in live:
                wb[code] = live[n]
        source = "WB — живая проверка, Ozon — " + source
    return wb, oz, source


def product_codes(cat, key, wb, oz):
    p = cat["products"][key]
    keys = p.get("parts", [key])
    w = [(cat["products"][k]["short"], c) for k in keys for c in cat["products"][k]["wb"][:5] if wb.get(c)]
    o = [(cat["products"][k]["short"], c) for k in keys for c in cat["products"][k]["ozon"][:5] if oz.get(c)]

    def first(items):
        seen, res = set(), []
        for s, c in items:
            if s not in seen:
                seen.add(s); res.append((s, c))
        return res
    return first(w), first(o)


def reply_text(cat, key, wb, oz):
    w, o = product_codes(cat, key, wb, oz)
    lines = [f"Арт вб {s}: #{c}" for s, c in w] + [f"Арт озон {s}: #{c}" for s, c in o]
    if any(c.startswith("WW") for _, c in w):
        lines.append("(копировать вместе с WW)")
    return "\n".join(lines)


def remix(cat, key, top):
    """Тот же товар, но по возможности другое фото и другая подпись, чтобы
    Threads не расценил публикацию как дубликат и не срезал охват.
    Возвращает (photos, text, style, is_exact)."""
    all_photos = [p for p in cat.get("photos", []) if p.get("product") == key]
    used = set(top.get("photos", []))
    alt_photos = [p for p in all_photos if p["file"] not in used]
    if alt_photos:
        chosen = [random.choice(alt_photos)]
        rest = [p for p in all_photos if p["file"] != chosen[0]["file"]]
        if rest and random.random() < 0.5:
            chosen.append(random.choice(rest))
        photos = [p["file"] for p in chosen]
        is_exact = False
    else:
        photos = top["photos"]
        is_exact = True

    caps = cat["products"][key]["captions"] or [cat["products"][key]["name"]]
    alt_caps = [c for c in caps if cap_text(c) != top["text"]]
    if alt_caps:
        chosen_cap = random.choice(alt_caps)
    else:
        chosen_cap = top["text"]
    text = cap_text(chosen_cap)
    style = cap_style(chosen_cap) if alt_caps else top.get("style", "soft")
    is_exact = is_exact and (text == top["text"])
    return photos, text, style, is_exact


# ---------- main ----------

def main():
    cat = load("catalog.json")
    state = load("state.json", {})
    recent = state.get("recent_posts", [])
    cutoff = now() - dt.timedelta(hours=24)
    candidates = [p for p in recent if not p.get("is_boost") and dt.datetime.fromisoformat(p["ts"]) > cutoff]

    if not candidates:
        print("Нет постов за последние 24 часа — бустить нечего")
        return

    scored = []
    for p in candidates:
        v = views(p["media_id"])
        if v is not None:
            scored.append((v, p))
        time.sleep(1)

    if not scored:
        telegram("⚠️ Буст: не удалось получить просмотры ни для одного поста. Проверьте, что у приложения включено право threads_manage_insights и токен обновлён.")
        return

    scored.sort(key=lambda x: -x[0])

    wb, oz, source = stock_status()
    winner = None
    for v, p in scored:
        key = p.get("product")
        if key in cat["products"] and reply_text(cat, key, wb, oz):
            winner = (v, p)
            break

    if not winner:
        print("Все посты-кандидаты сейчас не в наличии — буст пропущен")
        return

    top_views, top = winner
    key = top["product"]
    reply = reply_text(cat, key, wb, oz)
    photos, text, style, is_exact = remix(cat, key, top)

    if DRY:
        tag = "точная копия (других фото/подписей нет)" if is_exact else "ремикс — другое фото/подпись"
        print(f"[dry] буст {key} ({top_views} просм., {tag}, стиль={style}) фото={photos}\n  {text}\n  ↳ {reply.replace(chr(10), ' | ')}")
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    photo_url = lambda f: f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(f)}"
    urls = [photo_url(f) for f in photos]

    try:
        if not urls:
            cid = call("POST", "me/threads", media_type="TEXT", text=text)["id"]
        elif len(urls) == 1:
            cid = call("POST", "me/threads", media_type="IMAGE", image_url=urls[0], text=text)["id"]
        else:
            kids = [call("POST", "me/threads", media_type="IMAGE", image_url=u, is_carousel_item="true")["id"] for u in urls]
            for k in kids:
                wait_ready(k)
            cid = call("POST", "me/threads", media_type="CAROUSEL", children=",".join(kids), text=text)["id"]
        media_id = publish(cid)
        rid = call("POST", "me/threads", media_type="TEXT", text=reply, reply_to_id=media_id)["id"]
        publish(rid)
        permalink = call("GET", media_id, fields="permalink").get("permalink", media_id)
    except Exception as e:
        telegram(f"⚠️ Не удалось опубликовать буст ({key}): {str(e)[:300]}")
        raise

    stamp = now().isoformat(timespec="seconds")
    recent.append({
        "ts": stamp, "media_id": media_id, "product": key,
        "photos": photos, "text": text, "style": style,
        "permalink": permalink, "is_boost": True, "boost_of_views": top_views,
    })
    keep_after = now() - dt.timedelta(hours=72)
    state["recent_posts"] = [p for p in recent if dt.datetime.fromisoformat(p["ts"]) > keep_after]
    save("state.json", state)

    tag = " (точная копия — вариантов фото/подписи не нашлось)" if is_exact else " (ремикс: другое фото/подпись)"
    print(f"Буст: {key} ({top_views} просм. на оригинале){tag} -> {permalink}")
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/{now():%Y-%m}.md", "a", encoding="utf-8") as f:
        f.write(f"- 🔁 буст {key} (у оригинала {top_views} просм.){tag}: {permalink}\n")


if __name__ == "__main__":
    main()
