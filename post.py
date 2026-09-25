"""Автовыкладка JELAI в Threads: фото товара + подпись, в ветке ответ с артикулами.

Запуск: python post.py morning|midday|evening [--dry-run] [--no-wait] [--count N]
Секреты (env): THREADS_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID

Правила:
- публикуются только товары в наличии (stock.json + попытка живой проверки WB);
- в ответе только те площадки, где товар есть;
- в одном слоте товар не повторяется; фото берутся самые давно не использованные;
- подписи для товара идут по кругу.
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
START = {"morning": (8, 0), "midday": (13, 30), "evening": (19, 0)}
GAP_MIN = 5          # минут между постами
PHOTO_COOLDOWN_H = 72  # одно фото не чаще раза в 3 дня (если хватает фото)

ARGS = sys.argv[1:]
DRY = "--dry-run" in ARGS
NO_WAIT = "--no-wait" in ARGS
COUNT = int(ARGS[ARGS.index("--count") + 1]) if "--count" in ARGS else int(os.environ.get("POST_COUNT", "10"))

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


# ---------- наличие ----------

def live_wb(nms):
    """Пробует узнать остатки WB по номенклатурам. Возвращает {nm: bool} или {} если WB не ответил."""
    out = {}
    for i in range(0, len(nms), 20):
        chunk = ";".join(str(n) for n in nms[i:i + 20])
        url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={chunk}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:  # WB часто режет запросы с серверов — тогда берём stock.json
            print("WB live check недоступен:", str(e)[:120])
            return {}
        prods = data.get("products") or data.get("data", {}).get("products") or []
        for p in prods:
            qty = p.get("totalQuantity")
            if qty is None:
                qty = sum(s.get("qty", 0) for size in p.get("sizes", []) for s in size.get("stocks", []))
            out[p["id"]] = qty > 0
        for n in nms[i:i + 20]:
            out.setdefault(n, False)  # карточки нет в ответе — считаем, что нет в наличии
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


# ---------- выбор постов ----------

def product_codes(cat, key, wb, oz):
    p = cat["products"][key]
    keys = p.get("parts", [key])
    w = [(cat["products"][k]["short"], c) for k in keys for c in cat["products"][k]["wb"][:5] if wb.get(c)]
    o = [(cat["products"][k]["short"], c) for k in keys for c in cat["products"][k]["ozon"][:5] if oz.get(c)]
    # по одному (первому доступному) артикулу на товар
    def first(items):
        seen, res = set(), []
        for s, c in items:
            if s not in seen:
                seen.add(s); res.append((s, c))
        return res
    return first(w), first(o)


def available(cat, key, wb, oz):
    p = cat["products"][key]
    if "parts" in p:
        parts_ok = [bool(sum(map(len, product_codes(cat, k, wb, oz)))) for k in p["parts"]]
        return all(parts_ok) if key.startswith("set_") else any(parts_ok)
    w, o = product_codes(cat, key, wb, oz)
    return bool(w or o)


def reply_text(cat, key, wb, oz):
    w, o = product_codes(cat, key, wb, oz)
    lines = [f"Арт вб {s}: #{c}" for s, c in w] + [f"Арт озон {s}: #{c}" for s, c in o]
    if any(c.startswith("WW") for _, c in w):
        lines.append("(копировать вместе с WW)")
    return "\n".join(lines)


def pick(cat, state, wb, oz, n):
    t = now()
    ok = {k for k in cat["products"] if available(cat, k, wb, oz)}
    by_prod = {}
    for ph in cat["photos"]:
        if ph["product"] in ok:
            by_prod.setdefault(ph["product"], []).append(ph)
    last = lambda pid: dt.datetime.fromisoformat(state["photo_last"].get(pid, "2000-01-01T00:00:00+07:00"))
    fresh = lambda ph: (t - last(ph["id"])).total_seconds() > PHOTO_COOLDOWN_H * 3600
    # товары, у которых есть «свежие» фото, в приоритете; дальше — кто дольше не выходил
    prod_last = lambda k: state["product_last"].get(k, "2000")
    order = sorted(by_prod, key=lambda k: (not any(fresh(p) for p in by_prod[k]), prod_last(k), random.random()))
    posts = []
    for k in order[:n]:
        photos = sorted(by_prod[k], key=lambda ph: (not fresh(ph), last(ph["id"]), ph["kind"] != "товар"))
        chosen = [photos[0]]
        # второе фото того же товара для карусели, если есть свежее
        if len(photos) > 1 and fresh(photos[1]) and random.random() < 0.5:
            chosen.append(photos[1])
        caps = cat["products"][k]["captions"] or [cat["products"][k]["name"]]
        ci = state["caption_idx"].get(k, 0) % len(caps)
        posts.append({"product": k, "photos": chosen, "text": caps[ci], "reply": reply_text(cat, k, wb, oz), "ci": ci})
    return posts, sorted(set(cat["products"]) - ok)


# ---------- Threads API ----------

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


def post_one(post, photo_url):
    urls = [photo_url(ph["file"]) for ph in post["photos"]]
    if len(urls) == 1:
        cid = call("POST", "me/threads", media_type="IMAGE", image_url=urls[0], text=post["text"])["id"]
    else:
        kids = [call("POST", "me/threads", media_type="IMAGE", image_url=u, is_carousel_item="true")["id"] for u in urls]
        for k in kids:
            wait_ready(k)
        cid = call("POST", "me/threads", media_type="CAROUSEL", children=",".join(kids), text=post["text"])["id"]
    media_id = publish(cid)
    rid = call("POST", "me/threads", media_type="TEXT", text=post["reply"], reply_to_id=media_id)["id"]
    publish(rid)
    permalink = call("GET", media_id, fields="permalink").get("permalink", media_id)
    return media_id, permalink


def telegram(text):
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        print("Telegram не настроен:\n" + text); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30)
    except Exception as e:
        print("Telegram error:", e)


# ---------- main ----------

def main():
    slot = ARGS[0] if ARGS and not ARGS[0].startswith("--") else "morning"
    cat, state = load("catalog.json"), load("state.json", {"photo_last": {}, "caption_idx": {}, "product_last": {}})
    wb, oz, source = stock_status()
    posts, skipped = pick(cat, state, wb, oz, COUNT)
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    photo_url = lambda f: f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(f)}"

    print(f"Наличие: {source}. Не в наличии/пропущено: {', '.join(skipped) or '—'}")
    today = now().date()
    h, m = START[slot]
    base = max(dt.datetime(today.year, today.month, today.day, h, m, tzinfo=TZ), now())
    links, errors = [], []
    for i, post in enumerate(posts, 1):
        if DRY:
            print(f"\n[dry] #{i} {post['product']} {[p['id'] for p in post['photos']]}\n  {post['text']}\n  ↳ {post['reply'].replace(chr(10), ' | ')}")
            continue
        target = base + dt.timedelta(minutes=GAP_MIN * (i - 1))
        delay = (target - now()).total_seconds()
        if delay > 0 and not NO_WAIT:
            time.sleep(delay)
        try:
            media_id, permalink = post_one(post, photo_url)
            links.append(permalink)
            print(f"ok #{i}: {permalink}")
            stamp = now().isoformat(timespec="seconds")
            for ph in post["photos"]:
                state["photo_last"][ph["id"]] = stamp
            state["product_last"][post["product"]] = stamp
            state["caption_idx"][post["product"]] = post["ci"] + 1
            state.setdefault("recent_posts", []).append({
                "ts": stamp, "media_id": media_id, "product": post["product"],
                "photos": [ph["file"] for ph in post["photos"]], "text": post["text"],
                "permalink": permalink, "is_boost": False,
            })
            keep_after = now() - dt.timedelta(hours=72)
            state["recent_posts"] = [p for p in state["recent_posts"] if dt.datetime.fromisoformat(p["ts"]) > keep_after]
            save("state.json", state)
        except Exception as e:
            errors.append(f"#{i} {post['product']}: {e}")
            print("ERROR", errors[-1])

    if DRY:
        return
    title = {"morning": "Утро", "midday": "День", "evening": "Вечер"}.get(slot, slot)
    msg = f"✅ {title} {today:%d.%m}: опубликовано {len(links)}/{len(posts)}\nНаличие: {source}\n\n" + "\n".join(links)
    if skipped:
        msg += "\n\nНе публикуются (нет в наличии): " + ", ".join(skipped)
    if errors:
        msg += "\n\n⚠️ Ошибки:\n" + "\n".join(e[:300] for e in errors)
    telegram(msg)
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/{today:%Y-%m}.md", "a", encoding="utf-8") as f:
        f.write(f"\n## {today} {title}\n" + "\n".join(f"- {l}" for l in links) + "".join(f"\n- ⚠️ {e[:300]}" for e in errors) + "\n")
    if errors and not links:
        sys.exit(1)


if __name__ == "__main__":
    main()
