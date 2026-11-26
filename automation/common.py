"""Funzioni condivise: percorsi, configurazione, stato, chiamate a OpenAI."""
import base64
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP_DIR = os.path.join(ROOT, "episodes")
DATA = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA, "state.json")
MOCK = os.environ.get("FZ_MOCK") == "1"  # prova senza Instagram/OpenAI


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def config():
    return load_json(os.path.join(ROOT, "config.json"))


def state():
    return load_json(STATE_PATH)


def save_state(st):
    save_json(STATE_PATH, st)


def ep_folder(n):
    return os.path.join(EP_DIR, f"ep{n:02d}")


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------------
# OpenAI (chiamate HTTP dirette, senza librerie extra)
# ----------------------------------------------------------------------------
def _key():
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
        raise SystemExit("Manca OPENAI_API_KEY (secret di GitHub)")
    return k


def _post(url, retries=3, **kw):
    last = None
    for a in range(retries):
        try:
            r = requests.post(url, timeout=600, **kw)
            if r.status_code < 500 and r.status_code != 429:
                if r.status_code >= 400:
                    raise SystemExit(f"OpenAI errore {r.status_code}: {r.text[:800]}")
                return r.json()
            last = f"{r.status_code}: {r.text[:300]}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5 * (a + 1))
    raise SystemExit(f"OpenAI non risponde: {last}")


def _image_part(path, detail="high"):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = "png" if path.lower().endswith(".png") else "jpeg"
    return {"type": "input_image", "image_url": f"data:image/{ext};base64,{b64}", "detail": detail}


def ask_json(model, system, user_text, schema, name, images=()):
    """Chiede al modello una risposta JSON che rispetta lo schema."""
    content = [{"type": "input_text", "text": user_text}]
    for p in images:
        content.append(p if isinstance(p, dict) else _image_part(p))
    body = {
        "model": model,
        "input": [{"role": "system", "content": [{"type": "input_text", "text": system}]},
                  {"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
    }
    data = _post("https://api.openai.com/v1/responses",
                 headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}, json=body)
    text = data.get("output_text")
    if not text:
        for item in data.get("output", []):
            for c in item.get("content", []) or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    text = c["text"]
    if not text:
        raise SystemExit(f"Risposta senza testo: {str(data)[:500]}")
    return json.loads(text)


def make_image(model, prompt, refs, out_path, size="1024x1536", quality="medium"):
    """Crea un'immagine. Con refs usa le immagini di riferimento per tenere lo stesso personaggio."""
    headers = {"Authorization": f"Bearer {_key()}"}
    if refs:
        files = [("image[]", (os.path.basename(p), open(p, "rb"), "image/png")) for p in refs]
        data = {"model": model, "prompt": prompt, "size": size, "quality": quality, "n": "1"}
        res = _post("https://api.openai.com/v1/images/edits", headers=headers, data=data, files=files)
    else:
        res = _post("https://api.openai.com/v1/images/generations", headers=headers,
                    json={"model": model, "prompt": prompt, "size": size, "quality": quality, "n": 1})
    item = res["data"][0]
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    else:
        raw = requests.get(item["url"], timeout=120).content
    with open(out_path, "wb") as f:
        f.write(raw)
    return out_path
