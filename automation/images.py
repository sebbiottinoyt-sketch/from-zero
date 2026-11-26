"""Crea con GPT Image le immagini delle scene che non esistono ancora."""
import os
import shutil
import sys

from common import MOCK, ROOT, config, ep_folder, load_json, log, make_image

STYLE = (
    "Vertical still from a 3D animated feature film (Pixar-like), cinematic lighting, rich warm colours, "
    "soft depth of field, high detail. "
)
CHARACTER = (
    "The main character is EXACTLY the same young man shown in the reference images: about 19, slim, warm brown "
    "skin with freckles, big brown eyes, thick dark eyebrows, messy curly dark-brown hair with one white streak at "
    "the front, burnt-orange knitted beanie, small beige bandage across the nose, worn grey hoodie with a small star "
    "patch on the left sleeve, ripped dark jeans, dirty white sneakers, olive-green canvas backpack with a tan star "
    "patch. Keep his face, proportions and outfit identical to the references (unless the scene says he changed "
    "clothes). "
)
FRAMING = (
    " Composition: vertical portrait, the main subject and his face in the central area; keep the top 15% of the "
    "picture free of important details and nothing important in the bottom 20%. His eyes, when visible, are open "
    "and clearly drawn. No text, no letters, no numbers, no logos, no watermarks."
)


def make_images(n):
    cfg = config()
    folder = ep_folder(n)
    ep = load_json(os.path.join(folder, "episode.json"))
    refs = [os.path.join(ROOT, p) for p in cfg["character_refs"] if os.path.exists(os.path.join(ROOT, p))]
    oc = cfg["openai"]
    done = set()
    for i, s in enumerate(ep["scenes"]):
        prompt = s.get("image_prompt")
        out = os.path.join(folder, s["image"])
        if not prompt or s["image"] in done or os.path.exists(out):
            continue
        done.add(s["image"])
        if MOCK:
            pics = sorted(p for p in os.listdir(ep_folder(1)) if p.endswith(".png"))
            shutil.copy(os.path.join(ep_folder(1), pics[i % len(pics)]), out)
            log(f"  [prova] immagine {s['image']} copiata da ep01")
            continue
        full = STYLE + CHARACTER + "Scene: " + prompt + FRAMING
        log(f"  immagine {s['image']}: {prompt[:70]}...")
        make_image(oc["image_model"], full, refs, out, size=oc["image_size"], quality=oc["image_quality"])
    return ep


if __name__ == "__main__":
    make_images(int(sys.argv[1]))
