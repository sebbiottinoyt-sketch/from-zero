"""Instagram API: follower, like, commenti, pubblicazione Reel.

Funziona con il token del "login unificato" di Meta (graph.facebook.com + ID dell'account Instagram,
token che non scade) e con il vecchio "login di Instagram" (graph.instagram.com, token da 60 giorni).
"""
import json
import os
import time

import requests

from common import MOCK, ROOT, config, load_json, log

MOCK_DIR = os.path.join(ROOT, "automation", "mock")


def _cfg():
    c = config()["instagram"]
    return c["api_host"].rstrip("/"), c["api_version"]


def unified():
    return "graph.facebook.com" in config()["instagram"]["api_host"]


def ig_id():
    """ID dell'account Instagram (serve con il login unificato)."""
    v = os.environ.get("IG_USER_ID") or str(config()["instagram"].get("user_id") or "").strip()
    if not v and unified() and not MOCK:
        raise SystemExit("Manca l'ID dell'account Instagram: mettilo in config.json -> instagram -> user_id")
    return v or "me"


def token():
    t = os.environ.get("IG_TOKEN")
    if not t and not MOCK:
        raise SystemExit("Manca IG_TOKEN (secret di GitHub)")
    return t


def _get(path, params=None):
    host, ver = _cfg()
    p = dict(params or {})
    p["access_token"] = token()
    url = path if path.startswith("http") else f"{host}/{ver}/{path.lstrip('/')}"
    for a in range(3):
        r = requests.get(url, params=p if not path.startswith("http") else None, timeout=60)
        if r.status_code < 500:
            break
        time.sleep(4 * (a + 1))
    if r.status_code >= 400:
        raise SystemExit(f"Instagram errore {r.status_code} su {path}: {r.text[:600]}")
    return r.json()


def _post(path, params):
    host, ver = _cfg()
    p = dict(params)
    p["access_token"] = token()
    r = requests.post(f"{host}/{ver}/{path.lstrip('/')}", data=p, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram errore {r.status_code} su {path}: {r.text[:600]}")
    return r.json()


def refresh_token():
    """Rinnova il token (vale 60 giorni). Restituisce il nuovo token o None."""
    if MOCK or unified():  # il token di sistema del login unificato non scade
        return None
    host, _ = _cfg()
    r = requests.get(f"{host}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token()}, timeout=60)
    if r.status_code != 200:
        log(f"  (token non rinnovato: {r.text[:200]})")
        return None
    return r.json().get("access_token")


def me():
    if MOCK:
        return load_json(os.path.join(MOCK_DIR, "me.json"))
    if unified():
        d = _get(ig_id(), {"fields": "id,username,followers_count,media_count"})
        d["user_id"] = d.get("id")
        return d
    return _get("me", {"fields": "user_id,username,followers_count,media_count"})


def media_info(media_id):
    if MOCK:
        return load_json(os.path.join(MOCK_DIR, "media.json"))
    return _get(media_id, {"fields": "id,like_count,comments_count,permalink,timestamp,media_product_type"})


def latest_reel():
    if MOCK:
        return load_json(os.path.join(MOCK_DIR, "media.json"))
    data = _get(f"{ig_id()}/media", {"fields": "id,media_product_type,timestamp,permalink", "limit": 10})
    for m in data.get("data", []):
        if m.get("media_product_type") == "REELS":
            return m
    return None


def comments(media_id):
    """Tutti i commenti (con risposte) di un Reel."""
    if MOCK:
        return load_json(os.path.join(MOCK_DIR, "comments.json"))
    out = []
    data = _get(f"{media_id}/comments", {
        "fields": "id,text,username,like_count,timestamp,replies{id,text,username,like_count,timestamp}",
        "limit": 50})
    while True:
        for c in data.get("data", []):
            reps = (c.pop("replies", None) or {}).get("data", [])
            out.append(c)
            out.extend(reps)
        nxt = data.get("paging", {}).get("next")
        if not nxt:
            break
        r = requests.get(nxt, timeout=60)
        if r.status_code >= 400:
            break
        data = r.json()
    return out


def publish_reel(video_url, caption, ai_label=True):
    """Crea il Reel da un URL pubblico del video, aspetta l'elaborazione e lo pubblica."""
    if MOCK:
        log(f"  [prova] pubblicherei {video_url}")
        return {"id": "MOCK_MEDIA", "permalink": "https://instagram.com/p/mock"}
    uid = me()["user_id"]
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
    if ai_label:
        params["is_ai_generated"] = "true"
    try:
        cont = _post(f"{uid}/media", params)
    except RuntimeError as e:
        if ai_label and "is_ai_generated" in str(e):
            params.pop("is_ai_generated")
            cont = _post(f"{uid}/media", params)
        else:
            raise SystemExit(str(e))
    cid = cont["id"]
    log(f"  contenitore {cid}: attendo l'elaborazione del video...")
    for _ in range(60):
        st = _get(cid, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Instagram non ha accettato il video: {json.dumps(st)}")
        time.sleep(10)
    else:
        raise SystemExit("Instagram ci mette troppo a elaborare il video")
    pub = _post(f"{uid}/media_publish", {"creation_id": cid})
    info = _get(pub["id"], {"fields": "id,permalink,timestamp"})
    return info
