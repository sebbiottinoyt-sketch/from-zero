"""Pubblica un episodio già montato come Reel su Instagram.

Il video deve essere raggiungibile da Instagram: si usa il link "raw" di GitHub (repository pubblico).
Uso: python automation/publish.py 2
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

import ig
from common import MOCK, ROOT, config, ep_folder, load_json, log, save_state, state


def video_url(name):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo and not MOCK:
        raise SystemExit("GITHUB_REPOSITORY non impostato (va eseguito su GitHub Actions)")
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        sha = "main"
    return f"https://raw.githubusercontent.com/{repo or 'utente/from-zero'}/{sha or 'main'}/output/{name}.mp4"


def publish(n):
    cfg = config()
    st = state()
    rec = next((e for e in st["episodes"] if e["episode"] == n), None)
    if rec is None:
        raise SystemExit(f"L'episodio {n} non è nello stato: va prima creato")
    if rec.get("media_id") and not MOCK:
        log(f"Episodio {n} già pubblicato: {rec.get('permalink')}")
        return rec
    name = f"ep{n:02d}"
    if not os.path.exists(os.path.join(ROOT, "output", f"{name}.mp4")):
        raise SystemExit(f"Manca output/{name}.mp4")
    ep = load_json(os.path.join(ep_folder(n), "episode.json"))
    caption = ep.get("instagram_caption", "")
    url = video_url(name)
    log(f"Pubblico l'episodio {n} da {url}")
    info = ig.publish_reel(url, caption, ai_label=cfg.get("ai_label", True))
    acct = ig.me()
    rec["media_id"] = info["id"]
    rec["permalink"] = info.get("permalink")
    rec["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec["followers_at_publish"] = acct.get("followers_count")
    save_state(st)
    log(f"Pubblicato: {rec['permalink']}")
    return rec


if __name__ == "__main__":
    publish(int(sys.argv[1]) if len(sys.argv) > 1 else state()["episodes"][-1]["episode"])
