"""Один раз скачивает фото товаров из ваших постов через Threads API в photos/.

photos_src.json: {файл: [отпечаток 4x6 RGB (base64), [shortcode поста, ...]]}.
Для каждого файла берутся картинки из указанных постов, выбирается самая похожая.
"""
import base64, io, json, os, sys, urllib.parse, urllib.request
import numpy as np
from PIL import Image

API = "https://graph.threads.net/v1.0"
TOKEN = os.environ["THREADS_TOKEN"]


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def norm(a):
    a = a.astype(float); return (a - a.mean()) / (a.std() + 1e-6)


def fp_img(data):
    im = Image.open(io.BytesIO(data)).convert("RGB").resize((4, 6), Image.BILINEAR)
    return norm(np.asarray(im, np.uint8).ravel())


def posts_by_shortcode(need):
    fields = "id,permalink,media_type,media_url,children{media_type,media_url}"
    url = f"{API}/me/threads?" + urllib.parse.urlencode({"fields": fields, "limit": 100, "access_token": TOKEN})
    found = {}
    while url and need - set(found):
        page = json.loads(get(url))
        for p in page.get("data", []):
            sc = p.get("permalink", "").rstrip("/").split("/")[-1]
            items = p.get("children", {}).get("data") or [p]
            found[sc] = [it["media_url"] for it in items if it.get("media_type") == "IMAGE" and it.get("media_url")]
        url = page.get("paging", {}).get("next")
    return found


def main():
    src = json.load(open("photos_src.json"))
    need = {sc for _, posts in src.values() for sc in posts}
    posts = posts_by_shortcode(need)
    cache, missing = {}, []
    os.makedirs("photos", exist_ok=True)
    for name, (fp, scs) in sorted(src.items()):
        target = norm(np.frombuffer(base64.b64decode(fp), np.uint8))
        best = (-1.0, None)
        for sc in scs:
            for u in posts.get(sc, []):
                if u not in cache:
                    try:
                        cache[u] = get(u)
                    except Exception as e:
                        print("skip", str(e)[:80]); cache[u] = None
                if cache[u]:
                    c = float((fp_img(cache[u]) * target).mean())
                    if c > best[0]:
                        best = (c, cache[u])
        if best[1] is not None and best[0] >= 0.95:
            open(os.path.join("photos", name), "wb").write(best[1])
            print(f"ok {name} {best[0]:.3f}")
        else:
            missing.append(name); print(f"MISSING {name} {best[0]:.3f}")
    print(f"готово: {len(src) - len(missing)}/{len(src)}")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
