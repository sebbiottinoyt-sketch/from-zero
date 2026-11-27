"""Crea con GPT Image le immagini delle scene che non esistono ancora.

Il volto di Leo e' sempre lo stesso (immagini di riferimento); il vestito cambia con la storia:
l'outfit attuale e' in data/state.json -> "look". Quando la puntata gli fa cambiare vestito
(campo "new_look" del copione), dalle inquadrature segnate "wears_new" in poi usa il nuovo outfit,
e le prime immagini col nuovo outfit diventano i nuovi riferimenti per le puntate successive.
"""
import os
import shutil
import sys

from common import MOCK, ROOT, config, ep_folder, load_json, log, make_image, save_state, state

STYLE = (
    "Vertical still from a 3D animated feature film (Pixar-like), cinematic lighting, rich warm colours, "
    "soft depth of field, high detail. "
)
IDENTITY = (
    "The main character is EXACTLY the same young man shown in the reference images: about 19, slim, warm brown "
    "skin with freckles, big brown eyes, thick dark eyebrows, messy curly dark-brown hair with one white streak at "
    "the front. Keep his face, hair and proportions identical to the references. "
)
DEFAULT_LOOK = (
    "burnt-orange knitted beanie, small beige bandage across the nose, worn grey hoodie with a small star patch on "
    "the left sleeve, ripped dark jeans, dirty white sneakers, olive-green canvas backpack with a tan star patch"
)
FRAMING = (
    " Composition: vertical portrait, the main subject and his face in the central area; keep the top 15% of the "
    "picture free of important details and nothing important in the bottom 20%. His eyes, when visible, are open "
    "and clearly drawn. No text, no letters, no numbers, no logos, no watermarks."
)


def outfit_text(look, changed):
    if changed:
        return (f"He now wears a NEW outfit (do NOT copy the clothes of the reference images): {look}. "
                "Only the face, hair and body come from the references. ")
    return f"He wears: {look}. "


def make_images(n):
    cfg = config()
    st = state()
    folder = ep_folder(n)
    ep = load_json(os.path.join(folder, "episode.json"))
    look = st.get("look") or {}
    cur_look = look.get("description") or DEFAULT_LOOK
    ref_paths = look.get("refs") or cfg["character_refs"]
    face_refs = [os.path.join(ROOT, p) for p in cfg["character_refs"] if os.path.exists(os.path.join(ROOT, p))]
    refs = [os.path.join(ROOT, p) for p in ref_paths if os.path.exists(os.path.join(ROOT, p))] or face_refs
    new_look = (ep.get("new_look") or "").strip()
    oc = cfg["openai"]
    done = set()
    new_look_imgs = []
    for i, s in enumerate(ep["scenes"]):
        prompt = s.get("image_prompt")
        out = os.path.join(folder, s["image"])
        if not prompt or s["image"] in done:
            continue
        done.add(s["image"])
        wears_new = bool(new_look and s.get("wears_new"))
        if wears_new and s.get("character", True):
            new_look_imgs.append(os.path.relpath(out, ROOT))
        if os.path.exists(out):
            continue
        if MOCK:
            pics = sorted(p for p in os.listdir(ep_folder(1)) if p.endswith(".png"))
            shutil.copy(os.path.join(ep_folder(1), pics[i % len(pics)]), out)
            log(f"  [prova] immagine {s['image']} copiata da ep01")
            continue
        if wears_new:
            # il nuovo vestito: riferimenti solo per il volto (le immagini originali), vestiti dal testo
            # oppure, se ci sono gia' immagini col nuovo vestito in questa puntata, anche quelle
            extra = [os.path.join(ROOT, p) for p in new_look_imgs[:-1][:2]]
            use_refs = (face_refs[:2] + extra)[:4]
            outfit = outfit_text(new_look, not extra)
        else:
            use_refs = refs[:4]
            outfit = outfit_text(cur_look, bool(look.get("description")) and ref_paths == cfg["character_refs"])
        full = STYLE + IDENTITY + outfit + "Scene: " + prompt + FRAMING
        log(f"  immagine {s['image']}{' (nuovo outfit)' if wears_new else ''}: {prompt[:70]}...")
        make_image(oc["image_model"], full, use_refs, out, size=oc["image_size"], quality=oc["image_quality"])
    if new_look:
        st = state()
        st["look"] = {"description": new_look, "since_episode": n,
                      "refs": new_look_imgs[:3] or (look.get("refs") or cfg["character_refs"])}
        save_state(st)
        log(f"  nuovo outfit di Leo da questa puntata: {new_look[:80]}")
    return ep


if __name__ == "__main__":
    make_images(int(sys.argv[1]))
