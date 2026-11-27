"""Regista della pipeline.

  python automation/run_next.py make      -> legge Instagram, scrive il copione, crea immagini e video
  python automation/run_next.py publish   -> pubblica l'ultimo episodio (solo se auto_publish è true)
  python automation/run_next.py publish --force -> pubblica anche con auto_publish false

Se un passaggio si interrompe, rilanciando riparte da dove era arrivato.
"""
import os
import sys
from datetime import datetime, timezone

from common import DATA, MOCK, ROOT, config, ep_folder, load_json, log, save_state, state

sys.path.insert(0, ROOT)


def final_stats(ep):
    st = dict(ep.get("stats") or {"home": "NO", "job": "NO"})
    for s in ep["scenes"]:
        st.update(s.get("stats") or {})
    return st


def make():
    import auto_anim
    import collect
    import images
    import render
    import write_episode

    st = state()
    last = st["episodes"][-1]
    n = last["episode"] + 1
    folder = ep_folder(n)
    if os.path.exists(os.path.join(ROOT, "output", f"ep{n:02d}.mp4")):
        log(f"L'episodio {n} è già pronto: pubblicalo prima di crearne un altro.")
        return None
    if last["episode"] > 1 and not last.get("media_id") and not MOCK:
        raise SystemExit(f"L'episodio {last['episode']} non risulta pubblicato: pubblicalo (workflow "
                         "'Pubblica episodio') prima di crearne uno nuovo.")
    gap = config().get("min_hours_between_episodes", 20)
    published = last.get("published_at")
    if not published and not MOCK:  # pubblicato a mano: l'ora la prende dal Reel su Instagram
        import ig
        lr = ig.latest_reel()
        known = {e.get("media_id") for e in st["episodes"][:-1] if e.get("media_id")}
        if lr and lr.get("timestamp") and lr["id"] not in known:
            published = lr["timestamp"].replace("+0000", "+00:00")
    if published and not MOCK:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(published)).total_seconds() / 3600
        if age < gap:
            log(f"L'ultimo episodio è uscito {age:.0f} ore fa (minimo {gap}): aspetto ancora.")
            return None

    col_path = os.path.join(DATA, f"collect_ep{n:02d}.json")
    if not os.path.exists(col_path):
        collect.collect(n)
    else:
        log(f"Dati Instagram già raccolti per l'episodio {n}")
    ep_json = os.path.join(folder, "episode.json")
    if not os.path.exists(ep_json):
        write_episode.write(n)
    else:
        log(f"Copione dell'episodio {n} già scritto")
    images.make_images(n)
    auto_anim.animate(n)
    os.environ.setdefault("KOKORO_DIR", os.path.join(ROOT, "kokoro"))
    render.render_episode(folder)

    ep = load_json(ep_json)
    script = load_json(os.path.join(DATA, f"script_ep{n:02d}.json")) or {}
    st = state()
    st["episodes"].append({
        "episode": n, "folder": f"ep{n:02d}", "day_label": ep.get("day_label"), "title": ep.get("title"),
        "summary": script.get("summary", ""), "vote_options": next(
            ([o["label"] if isinstance(o, dict) else o for o in s["cta"]["options"]]
             for s in ep["scenes"] if s.get("cta")), []),
        "stats": final_stats(ep), "arc": ep.get("arc"),
        "final_net_worth": render.final_worth(ep), "media_id": None, "permalink": None,
        "published_at": None, "followers_at_publish": None,
    })
    save_state(st)
    log(f"Episodio {n} pronto: output/ep{n:02d}.mp4")
    return n


def publish(force=False):
    import publish as pub
    if not config().get("auto_publish") and not force:
        log("Pubblicazione automatica disattivata (auto_publish: false): guarda il video e usa "
            "il workflow 'Pubblica episodio'.")
        return
    st = state()
    last = st["episodes"][-1]
    if last.get("media_id"):
        log("Niente da pubblicare.")
        return
    pub.publish(last["episode"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "make"
    if cmd == "make":
        make()
    elif cmd == "publish":
        publish(force="--force" in sys.argv)
    else:
        raise SystemExit(__doc__)
