"""Animazione automatica del ragazzo nelle immagini nuove.

GPT guarda ogni immagine e indica dove sono occhi, testa, collo, busto e mani (e che effetto mettere).
Gli occhi vengono poi rifiniti cercando il bianco dell'occhio; il battito di ciglia viene tenuto solo se
le misure concordano e se GPT, guardando l'occhio chiuso disegnato, lo giudica naturale.
"""
import base64
import io
import os
import sys

import cv2
import numpy as np
from PIL import Image

from common import MOCK, ROOT, ask_json, config, ep_folder, load_json, log, save_json
from eye_refine import refine_eye

sys.path.insert(0, ROOT)
import render  # noqa: E402

PT = {"type": "object", "additionalProperties": False, "required": ["x", "y"],
      "properties": {"x": {"type": "number"}, "y": {"type": "number"}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["character_visible", "eyes", "head", "neck", "torso", "hands", "fx", "fx_point", "coin_source",
                 "lights"],
    "properties": {
        "character_visible": {"type": "boolean"},
        "eyes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["x", "y", "width", "open"],
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "width": {"type": "number"},
                           "open": {"type": "boolean"}}}},
        "head": {"type": "object", "additionalProperties": False, "required": ["x", "y", "size"],
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "size": {"type": "number"}}},
        "neck": PT,
        "torso": {"type": "object", "additionalProperties": False, "required": ["x", "y", "width", "height"],
                  "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "width": {"type": "number"},
                                 "height": {"type": "number"}}},
        "hands": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["x", "y", "size", "action"],
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "size": {"type": "number"},
                           "action": {"type": "string",
                                      "enum": ["still", "gesture", "scrub", "work", "hold", "wave"]}}}},
        "fx": {"type": "string", "enum": ["none", "rain", "dust", "embers", "bubbles", "sparks"]},
        "fx_point": PT,
        "coin_source": PT,
        "lights": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["x", "y", "size", "color"],
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "size": {"type": "number"},
                           "color": {"type": "string", "enum": ["warm", "cool", "red"]}}}},
    },
}
SYSTEM = (
    "You annotate animation rigs for still images. Coordinates are on a 0-1000 grid: x from the left edge, "
    "y from the top edge, sizes in the same units relative to the image width. Be precise."
)
ASK = (
    "Annotate this image of the main character for subtle animation. eyes: each visible eye of the main character "
    "(centre of the eye opening, width of the eye opening, open=true only if clearly open and facing the camera "
    "enough to see the eyeball). head: centre of the head and its width. neck: base of the neck. torso: centre and "
    "size of the chest/upper body. hands: each visible hand of the main character with its action (still, gesture = "
    "beckoning/talking, scrub = cleaning/rubbing, work = precise tool work, hold = holding an object, wave). "
    "fx: one atmospheric particle effect that fits (rain at night in the rain, dust in sunlight indoors/outdoors, "
    "embers at sunset or fire, bubbles with soap/water, sparks with electronics/tools, none). fx_point: where the "
    "effect comes from (bubbles/sparks), else the image centre. coin_source: the spot money would come from in this "
    "scene (hands, a customer, an object). lights: up to 2 visible light sources (lamps, sun, neon, traffic light). "
    "If the main character is not in the image set character_visible=false and give plausible defaults."
)
QA_ASK = (
    "Left: an eye region of a cartoon character. Right: the same region where an eyelid was drawn to show the eyes "
    "closed (a blink frame). Does the right picture look like natural, correctly placed closed eyelids, with no "
    "smudges, no eyelid drawn on the eyebrow, cheek or nose, and no part of the open eye still visible?"
)
QA_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok", "reason"],
             "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}}}


def _png_part(arr):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "detail": "high"}


def annotate(path, model):
    if MOCK:
        return None
    return ask_json(model, SYSTEM, ASK, SCHEMA, "rig", images=[path])


def eyes_for(img, ann, kx, ky):
    """Rifinisce gli occhi; restituisce la lista o [] se non affidabile."""
    opened = [e for e in ann["eyes"] if e["open"]]
    if not opened or len(opened) > 2:
        return []
    out = []
    rng = np.random.default_rng(7)
    for e in opened:
        cx, cy, size = e["x"] * kx, e["y"] * ky, max(12, e["width"] * kx)
        tries = []
        for j in range(3):
            jx, jy = (0, 0) if j == 0 else (rng.uniform(-0.25, 0.25) * size, rng.uniform(-0.2, 0.2) * size)
            r = refine_eye(img, cx + jx, cy + jy, size * (1 if j == 0 else rng.uniform(0.85, 1.2)))
            if r and r[4] >= 0.45:
                tries.append(r)
        if len(tries) < 2:
            return []
        arr = np.array([t[:4] for t in tries], np.float32)
        if arr[:, 0].std() > size * 0.12 or arr[:, 1].std() > size * 0.12:
            return []
        m = np.median(arr, axis=0)
        out.append([int(m[0]), int(m[1]), int(m[2]), int(m[3])])
    return out


def qa_blink(img, eyes, model):
    if MOCK or not eyes:
        return bool(eyes)
    xs = [e[0] - e[2] * 2.2 for e in eyes] + [e[0] + e[2] * 2.2 for e in eyes]
    ys = [e[1] - e[3] * 3.5 for e in eyes] + [e[1] + e[3] * 3.0 for e in eyes]
    x0, x1 = int(max(0, min(xs))), int(min(img.shape[1], max(xs)))
    y0, y1 = int(max(0, min(ys))), int(min(img.shape[0], max(ys)))
    closed = img.copy()
    for e in eyes:
        render.apply_blink(closed, e, 1.0, render.lid_color(img, e))
    a, b = img[y0:y1, x0:x1], closed[y0:y1, x0:x1]
    scale = max(1, int(300 / max(1, a.shape[0])))
    if scale > 1:
        a = cv2.resize(a, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        b = cv2.resize(b, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    pair = np.concatenate([a, np.full((a.shape[0], 12, 3), 255, np.uint8), b], axis=1)
    res = ask_json(model, "You check animation frames for visual errors. Be strict.", QA_ASK, QA_SCHEMA, "qa",
                   images=[_png_part(pair)])
    if not res["ok"]:
        log(f"    battito scartato: {res['reason'][:90]}")
    return res["ok"]


def build_anim(img, ann, kx, ky, eyes):
    s = img.shape[1] / 941.0  # ampiezze tarate sulle immagini dell'episodio 1
    k = kx                    # le dimensioni sono relative alla larghezza
    P = lambda p: [int(p["x"] * kx), int(p["y"] * ky)]  # noqa: E731
    movers = []
    t = ann["torso"]
    movers.append({"c": P(t), "r": [int(max(80, t["width"] * k * 0.6)), int(max(80, t["height"] * k * 0.6))],
                   "dy": round(-3.2 * s, 1), "period": 3.6})
    h = ann["head"]
    hr = int(max(60, h["size"] * k * 0.75))
    movers.append({"c": P(h), "r": [hr, int(hr * 1.1)], "pivot": P(ann["neck"]), "rot": 1.0, "dy": round(-1.5 * s, 1),
                   "period": 4.2})
    movers.append({"c": P(h), "r": [hr, int(hr * 1.1)], "dx": round(1.5 * s, 1), "period": 6.5, "phase": 0.3})
    for i, hd in enumerate(ann["hands"][:2]):
        c = P(hd)
        r = int(max(50, hd["size"] * k * 0.9))
        act = hd["action"]
        if act == "scrub":
            movers.append({"c": c, "r": [int(r * 1.4), r], "dx": round(9 * s, 1), "dy": round(6 * s, 1),
                           "period": 0.9, "wave": "circle"})
        elif act in ("gesture", "wave"):
            movers.append({"c": c, "r": [int(r * 1.3), int(r * 1.1)], "pivot": [c[0] - r // 2, c[1] + r],
                           "rot": 2.6 if act == "gesture" else 4.0, "period": 1.5 if act == "gesture" else 1.0})
        elif act == "work":
            movers.append({"c": c, "r": [r, int(r * 0.8)], "pivot": [c[0] + r, c[1] + r // 2], "rot": 3.5,
                           "period": 0.7})
        else:
            movers.append({"c": c, "r": [r, r], "dx": round(2 * s, 1), "dy": round(3 * s, 1), "period": 2.6,
                           "phase": 0.5 * i})
    anim = {"movers": movers}
    if eyes:
        anim["blink"] = eyes
    if ann["fx"] != "none":
        anim["fx"] = [ann["fx"]]
        anim["fx_point"] = P(ann["fx_point"])
    cols = {"warm": [255, 170, 60], "cool": [170, 220, 255], "red": [255, 40, 30]}
    glows = []
    for L in ann["lights"][:2]:
        glows.append([int(L["x"] * kx), int(L["y"] * ky), int(max(60, L["size"] * k * 0.8)), cols[L["color"]],
                      0.45, 2.0 + len(glows) * 0.6])
    if glows:
        anim["glow"] = glows
    return anim


def generic_anim(img):
    h, w = img.shape[:2]
    return {"movers": [{"c": [w // 2, int(h * 0.6)], "r": [int(w * 0.35), int(h * 0.25)], "dy": -3, "period": 3.6}],
            "fx": ["dust"]}


def animate(n):
    cfg = config()
    folder = ep_folder(n)
    path = os.path.join(folder, "episode.json")
    ep = load_json(path)
    model = cfg["openai"]["vision_model"]
    cache = {}
    for s in ep["scenes"]:
        if s.get("anim") or s.get("supporters"):
            continue
        name = s["image"]
        if name not in cache:
            ip = os.path.join(folder, name)
            img = cv2.cvtColor(cv2.imread(ip), cv2.COLOR_BGR2RGB)
            kx, ky = img.shape[1] / 1000.0, img.shape[0] / 1000.0
            ann = annotate(ip, model)
            pts = {}
            if not ann or not ann["character_visible"]:
                cache[name] = (generic_anim(img), None, pts)
                if ann:
                    pts["detail"] = (ann["coin_source"]["x"] / 1000, ann["coin_source"]["y"] / 1000)
            else:
                eyes = eyes_for(img, ann, kx, ky)
                if eyes and not qa_blink(img, eyes, model):
                    eyes = []
                anim = build_anim(img, ann, kx, ky, eyes)
                h = ann["head"]
                pts["closeup_face"] = (h["x"] / 1000, h["y"] / 1000)
                pts["medium"] = (h["x"] / 1000, min(0.75, h["y"] / 1000 + 0.15))
                if ann["hands"]:
                    pts["closeup_hands"] = (ann["hands"][0]["x"] / 1000, ann["hands"][0]["y"] / 1000)
                pts["detail"] = (ann["coin_source"]["x"] / 1000, ann["coin_source"]["y"] / 1000)
                pts.setdefault("closeup_hands", pts["detail"])
                cache[name] = (anim, [int(ann["coin_source"]["x"] * kx), int(ann["coin_source"]["y"] * ky)], pts)
            log(f"  animazione {name}: {len(cache[name][0]['movers'])} movimenti"
                + (", battito di ciglia" if cache[name][0].get("blink") else ""))
        anim, coin, pts = cache[name]
        s["anim"] = anim
        fr = s.get("framing")
        if fr and "focus" not in s.get("camera", {}):
            s.setdefault("camera", {})["focus"] = list(pts.get(fr, (0.5, 0.45 if fr != "wide" else 0.5)))
        for m in s.get("money", []):
            if coin and "from" not in m:
                m["from"] = coin
    save_json(path, ep)
    return ep


if __name__ == "__main__":
    animate(int(sys.argv[1]))
