"""
From Zero - montaggio automatico degli episodi (v2).

Uso:  python render.py episodes/ep01
      python render.py --all                 (monta gli episodi che non hanno ancora un video)
      python render.py episodes/ep01 --preview 3:2.5 5:4.0   (salva solo alcuni fotogrammi PNG)

Per ogni episodio legge episode.json e le immagini nella stessa cartella e crea
output/<cartella>.mp4 in 1080x1920 (Reel) con:
  - il ragazzo animato (battito di ciglia, respiro, testa e mani che si muovono)
  - movimento di camera morbido + effetti (pioggia, polvere, bolle, scintille, bagliori)
  - barra in alto con logo, episodio, contatore NET WORTH a rulli ed elenco episodi
  - monete che volano nel contatore con suono "cha-ching"
  - didascalie animate, voce narrante (edge-tts; Kokoro offline come riserva)
  - musica di sottofondo se esiste assets/music.mp3
"""
import glob
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SR = 1080, 1920, 30, 44100
ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_BLACK = os.path.join(ROOT, "fonts", "Inter-Black.otf")
FONT_BOLD = os.path.join(ROOT, "fonts", "Inter-Bold.otf")
MUSIC = os.path.join(ROOT, "assets", "music.mp3")
OUT_DIR = os.path.join(ROOT, "output")

GREEN = (64, 236, 128)
GOLD = (255, 200, 60)
_fonts = {}


def font(path, size):
    key = (path, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


def run(cmd, inp=None):
    p = subprocess.run(cmd, capture_output=True, input=inp)
    if p.returncode != 0:
        print(" ".join(map(str, cmd)))
        print(p.stderr.decode(errors="ignore")[-3000:])
        raise SystemExit(f"Comando fallito: {cmd[0]}")
    return p.stdout


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ease_back(x):
    x = min(max(x, 0.0), 1.0)
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def money(v):
    return "$" + f"{int(v):,}"


# ----------------------------------------------------------------------------
# Voce
# ----------------------------------------------------------------------------
def load_audio(path):
    """Decodifica qualsiasi file audio in float32 mono a 44.1 kHz."""
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"])
    return np.frombuffer(raw, dtype=np.float32).copy()


def load_audio_fx(path, fx):
    """Come load_audio ma con un filtro ffmpeg (es. tono piu' alto per una voce giovane)."""
    if not fx:
        return load_audio(path)
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-af", fx, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"])
    return np.frombuffer(raw, dtype=np.float32).copy()


def _kokoro(text, ep, path_base):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    kd = os.environ.get("KOKORO_DIR", os.path.join(ROOT, "kokoro"))
    k = Kokoro(os.path.join(kd, "kokoro-v1.0.onnx"), os.path.join(kd, "voices-v1.0.bin"))
    a, sr = k.create(text, voice=ep.get("kokoro_voice", "am_fenrir"), speed=ep.get("kokoro_speed", 1.08),
                     lang="en-us")
    sf.write(path_base + ".wav", a, sr)
    return path_base + ".wav"


def _edge(text, ep, path_base):
    import asyncio
    import edge_tts
    mp3 = path_base + ".mp3"
    asyncio.run(edge_tts.Communicate(text, ep.get("voice", "en-US-AndrewNeural"),
                                     rate=ep.get("voice_rate", "+0%")).save(mp3))
    return mp3


def make_voice(text, ep, path_base, ep_dir=None, idx=0):
    """Voce narrante. Le voci gia' generate restano in episodes/epXX/voice/ e vengono riusate
    finche' il testo e le impostazioni non cambiano (cosi' il video esce sempre uguale)."""
    if not text:
        return None
    tts = ep.get("tts", "edge")
    fx = ep.get("voice_fx", "")
    key = json.dumps([text, tts, ep.get("kokoro_voice"), ep.get("kokoro_speed"), ep.get("voice"),
                      ep.get("voice_rate"), fx])
    cache_wav = cache_key = None
    if ep_dir:
        vd = os.path.join(ep_dir, "voice")
        cache_wav, cache_key = os.path.join(vd, f"v{idx + 1:02d}.wav"), os.path.join(vd, f"v{idx + 1:02d}.txt")
        if os.path.exists(cache_wav) and os.path.exists(cache_key) and open(cache_key).read() == key:
            return load_audio(cache_wav)
    order = [_kokoro, _edge] if tts == "kokoro" else [_edge, _kokoro]
    for fn in order:
        try:
            path = fn(text, ep, path_base)
            a = load_audio_fx(path, fx)
            if cache_wav:
                os.makedirs(os.path.dirname(cache_wav), exist_ok=True)
                import soundfile as sf
                sf.write(cache_wav, a, SR, subtype="PCM_16")
                open(cache_key, "w").write(key)
            return a
        except Exception as e:
            print(f"  ({fn.__name__[1:]} non disponibile: {type(e).__name__}: {str(e)[:80]})")
    print("  (voce non generata)")
    return None


# ----------------------------------------------------------------------------
# Effetti sonori sintetizzati
# ----------------------------------------------------------------------------
def sfx_coin():
    t = np.arange(int(SR * 0.45)) / SR
    a = np.zeros_like(t)
    for start, f in ((0.0, 1975.5), (0.075, 2637.0)):
        tt = np.clip(t - start, 0, None)
        env = np.where(t >= start, np.exp(-tt * 11), 0)
        a += env * (np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(2 * np.pi * f * 2.01 * tt))
    return (a * 0.16).astype(np.float32)


def sfx_whoosh():
    n = int(SR * 0.4)
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(n)
    # passa-banda che sale: rumore filtrato con media mobile variabile
    out = np.zeros(n)
    acc = 0.0
    for i in range(n):
        k = 0.02 + 0.5 * (i / n)
        acc += k * (noise[i] - acc)
        out[i] = acc
    env = np.sin(np.pi * np.arange(n) / n) ** 2
    return (out * env * 0.10).astype(np.float32)


def sfx_cash():
    """Suono piu' ricco per un incasso grande."""
    a = sfx_coin()
    b = np.concatenate([np.zeros(int(SR * 0.09), np.float32), sfx_coin()])
    c = np.concatenate([np.zeros(int(SR * 0.18), np.float32), sfx_coin()])
    n = len(c)
    out = np.zeros(n, np.float32)
    out[:len(a)] += a
    out[:len(b)] += b * 0.8
    out += c * 0.7
    return out


# ----------------------------------------------------------------------------
# Animazione del personaggio (deformazioni locali)
# ----------------------------------------------------------------------------
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def mover_disp(m, X, Y, t):
    """Spostamento (dx, dy) del contenuto nei punti X,Y (coordinate immagine)."""
    cx, cy = m["c"]
    rx, ry = m["r"]
    d = np.sqrt(((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2)
    w = 1 - smoothstep(m.get("plateau", 0.45), 1.0, d)
    th = 2 * math.pi * t / m.get("period", 3.6) + m.get("phase", 0.0) * 2 * math.pi
    dx = m.get("dx", 0) * (math.cos(th) if m.get("wave") == "circle" else math.sin(th))
    dy = m.get("dy", 0) * math.sin(th)
    DX = np.full_like(X, dx)
    DY = np.full_like(X, dy)
    rot = m.get("rot", 0)
    if rot:
        a = math.radians(rot * math.sin(th + 0.6))
        px, py = m.get("pivot", m["c"])
        ca, sa = math.cos(a), math.sin(a)
        rxp, ryp = X - px, Y - py
        DX = DX + (ca * rxp - sa * ryp) - rxp
        DY = DY + (sa * rxp + ca * ryp) - ryp
    return DX * w, DY * w


def blink_amount(t, times):
    best = 0.0
    for b in times:
        x = t - b
        if 0 <= x < 0.09:
            best = max(best, ease_in_out(x / 0.09))
        elif 0.09 <= x < 0.14:
            best = 1.0
        elif 0.14 <= x < 0.30:
            best = max(best, 1 - ease_in_out((x - 0.14) / 0.16))
    return best


_lid_cache = {}


def lid_color(img, eye):
    """Colore della pelle preso sotto l'occhio (guancia)."""
    key = (id(img.base) if img.base is not None else id(img), tuple(eye))
    cx, cy, rx, ry = eye
    y0 = int(cy + ry + 0.25 * ry)
    y1 = int(cy + ry + 0.8 * ry) + 2
    region = img[max(y0, 0):y1, int(cx - rx * 0.6):int(cx + rx * 0.6)].reshape(-1, 3).astype(np.float32)
    return np.median(region, axis=0)


def apply_blink(img, eye, c, skin):
    """Disegna la palpebra superiore che scende sull'occhio (c: 0 aperto, 1 chiuso)."""
    cx, cy, rx, ry = eye
    rx, ry = rx * 1.1, ry * 1.12
    x0, x1 = int(cx - rx - 3), int(cx + rx + 4)
    y0, y1 = int(cy - ry - 4), int(cy + ry + 6)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    X, Y = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
    u = (X - cx) / rx
    inside_x = np.clip(1 - u * u, 0, 1)
    half = ry * np.sqrt(inside_x)
    top = cy - half
    bot = cy + half
    yl = top + c * (bot - top)
    # copertura morbida dentro l'ellisse e sopra la linea della palpebra
    ell = np.sqrt(u ** 2 + ((Y - cy) / ry) ** 2)
    cov_e = np.clip((1.0 - ell) * ry / 1.6, 0, 1)
    cov_l = np.clip((yl - Y) / 1.4 + 0.5, 0, 1)
    cov = (cov_e * cov_l)[..., None]
    cov = cv2.GaussianBlur(cov[..., 0], (0, 0), max(0.8, ry * 0.06))[..., None]
    # texture della pelle: guancia sotto l'occhio specchiata verso l'alto
    ty = (cy + ry * 1.05) + (cy + ry * 1.05 - Y) * 0.55 + ry * 0.35
    tex = cv2.remap(img, X, ty.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT).astype(np.float32)
    tex = cv2.GaussianBlur(tex, (0, 0), max(0.6, ry * 0.05))
    m = tex.reshape(-1, 3).mean(axis=0) + 1e-3
    tex = tex * (skin / m)[None, None, :]
    # ombreggiatura bombata: piu' scura in alto (piega) e ai lati
    rel = np.clip((Y - top) / np.maximum(yl - top, 1), 0, 1)[..., None]
    shade = 0.80 + 0.24 * rel - 0.14 * (u ** 2)[..., None]
    lid = np.clip(tex * shade, 0, 255)
    patch = img[y0:y1, x0:x1].astype(np.float32)
    patch = patch * (1 - cov) + lid * cov
    # ciglia: linea scura lungo il bordo della palpebra
    lash_w = max(1.6, ry * 0.16)
    dist = np.abs(Y - yl)
    lash = np.clip(1 - dist / lash_w, 0, 1) * np.clip((1.02 - np.abs(u)) * 6, 0, 1) * min(1, c * 1.6)
    lash = lash[..., None] * 0.92
    patch = patch * (1 - lash) + np.array([38, 22, 18], np.float32) * lash
    img[y0:y1, x0:x1] = np.clip(patch, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------
# Grafica: barra in alto, contatore, monete, didascalie
# ----------------------------------------------------------------------------
def soft_circle(r, color, blur):
    s = int(r * 2 + blur * 4 + 4)
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = s / 2
    d.ellipse([c - r, c - r, c + r, c + r], fill=color)
    return im.filter(ImageFilter.GaussianBlur(blur)) if blur else im


def coin_sprite(size=64):
    s = size * 3
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([6, 10, s - 6, s - 2], fill=(150, 95, 10, 255))
    d.ellipse([6, 4, s - 6, s - 8], fill=(255, 205, 60, 255))
    d.ellipse([22, 20, s - 22, s - 24], fill=(255, 225, 110, 255), outline=(215, 150, 20, 255), width=6)
    f = font(FONT_BLACK, int(s * 0.5))
    tw = d.textlength("$", font=f)
    d.text(((s - tw) / 2, s * 0.18), "$", font=f, fill=(190, 120, 10, 255))
    return im.resize((size, size), Image.LANCZOS)


def padlock(d, x, y, s, fill):
    d.rounded_rectangle([x, y + s * 0.42, x + s, y + s], radius=s * 0.15, fill=fill)
    d.arc([x + s * 0.18, y, x + s * 0.82, y + s * 0.8], 180, 360, fill=fill, width=max(2, int(s * 0.14)))
    d.line([x + s * 0.18, y + s * 0.4, x + s * 0.18, y + s * 0.5], fill=fill, width=max(2, int(s * 0.14)))
    d.line([x + s * 0.82, y + s * 0.4, x + s * 0.82, y + s * 0.5], fill=fill, width=max(2, int(s * 0.14)))


class Hud:
    BAR_Y, BAR_H = 90, 116
    STRIP_Y = 222
    POP_Y = 300

    def __init__(self, ep, history):
        self.ep = ep
        self.history = history  # [(numero, soldi_finali)] degli episodi precedenti
        self.coin = coin_sprite(58)
        self.coin_big = coin_sprite(78)
        self.coin_icon = coin_sprite(56)
        self.digit_cache = {}
        self.static = self._static()
        self.counter_center = (W - 220, self.BAR_Y + self.BAR_H / 2)

    def _static(self):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # sfumatura scura in alto e in basso per la leggibilita'
        grad = np.zeros((H, W, 4), np.uint8)
        top = np.clip(1 - np.arange(H) / 480, 0, 1) ** 1.6 * 170
        bot = np.clip((np.arange(H) - (H - 820)) / 820, 0, 1) ** 1.5 * 190
        grad[..., 3] = np.maximum(top, bot)[:, None].astype(np.uint8)
        im = Image.alpha_composite(im, Image.fromarray(grad, "RGBA"))
        # vignettatura
        yy, xx = np.mgrid[0:H, 0:W]
        v = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2)
        vig = np.zeros((H, W, 4), np.uint8)
        vig[..., 3] = (np.clip(v - 0.55, 0, 1) * 200).astype(np.uint8)
        im = Image.alpha_composite(im, Image.fromarray(vig, "RGBA"))

        x0, x1, y0 = 36, W - 36, self.BAR_Y
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 10, x1, y0 + self.BAR_H + 10], radius=58,
                                                 fill=(0, 0, 0, 130))
        im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(16)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([x0, y0, x1, y0 + self.BAR_H], radius=58, fill=(10, 14, 20, 210),
                            outline=(255, 255, 255, 50), width=2)
        # logo + episodio
        fl = font(FONT_BLACK, 48)
        d.text((x0 + 42, y0 + 14), "FROM", font=fl, fill=(255, 255, 255, 255))
        lw = d.textlength("FROM ", font=fl)
        d.text((x0 + 42 + lw, y0 + 14), "ZERO", font=fl, fill=GREEN + (255,))
        fs = font(FONT_BOLD, 26)
        ep_txt = f"EPISODE {self.ep['episode']}"
        day = self.ep.get("day_label", f"DAY {self.ep['episode']}")
        d.text((x0 + 44, y0 + 72), ep_txt, font=fs, fill=GREEN + (255,))
        ew = d.textlength(ep_txt + "  ", font=fs)
        d.text((x0 + 44 + ew, y0 + 72), "·  " + day, font=fs, fill=(200, 205, 212, 255))
        # zona contatore (a destra)
        self.cnt_box = (W - 470, y0 + 12, x1 - 12, y0 + self.BAR_H - 12)
        bx0, by0, bx1, by1 = self.cnt_box
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=46, fill=(4, 30, 16, 235), outline=GREEN + (120,), width=2)
        fn = font(FONT_BOLD, 19)
        lcx = bx0 + 96 + (bx1 - bx0 - 120) / 2
        d.text((lcx - d.textlength("NET WORTH", font=fn) / 2, by0 + 8), "NET WORTH", font=fn,
               fill=(160, 220, 180, 255))
        im.alpha_composite(self.coin_icon, (bx0 + 22, int((by0 + by1) / 2 - 28)))
        return im

    # --- contatore a rulli -------------------------------------------------
    def _glyph(self, ch, size, color):
        key = (ch, size, color)
        if key not in self.digit_cache:
            f = font(FONT_BLACK, size)
            bbox = f.getbbox("0")
            gw = int(f.getlength(ch)) + 2
            gh = int(size * 1.12)
            im = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((1, (gh - (bbox[3] + bbox[1])) / 2), ch, font=f, fill=color)
            self.digit_cache[key] = im
        return self.digit_cache[key]

    def counter_image(self, v, size=66, color=GREEN + (255,)):
        """Disegna il valore v (anche frazionario) come contatore a rulli."""
        vi = int(math.floor(v + 1e-6))
        digits = str(vi + (1 if v - vi > 0.5 else 0))
        n = len(digits)
        f = font(FONT_BLACK, size)
        dw = int(f.getlength("0")) + 2
        cw = int(f.getlength(",")) + 2
        gh = int(size * 1.12)
        dollar = self._glyph("$", size, color)
        commas = sum(1 for p in range(n) if p > 0 and p % 3 == 0)
        im = Image.new("RGBA", (dollar.width + n * dw + commas * cw, gh), (0, 0, 0, 0))
        im.alpha_composite(dollar, (0, 0))
        x = dollar.width
        fracs = {0: v - math.floor(v)}
        for p in range(1, n):
            lower = int(math.floor(v / 10 ** (p - 1))) % 10
            fracs[p] = fracs[p - 1] if lower == 9 else 0.0
        for p in range(n - 1, -1, -1):
            dgt = int(math.floor(v / 10 ** p)) % 10
            off = int(round(fracs[p] * gh))
            g1 = self._glyph(str(dgt), size, color)
            g2 = self._glyph(str((dgt + 1) % 10), size, color)
            cell = Image.new("RGBA", (dw, gh), (0, 0, 0, 0))
            if off < gh:
                cell.alpha_composite(g1, (0, 0), (0, off))
            if off > 0:
                cell.alpha_composite(g2, (0, gh - off), (0, 0, g2.width, off))
            im.alpha_composite(cell, (x, 0))
            x += dw
            if p > 0 and p % 3 == 0:
                im.alpha_composite(self._glyph(",", size, color), (x, 0))
                x += cw
        return im

    def draw(self, ov, value, bump, glow):
        bx0, by0, bx1, by1 = self.cnt_box
        if glow > 0:
            g = Image.new("RGBA", (bx1 - bx0 + 80, by1 - by0 + 80), (0, 0, 0, 0))
            ImageDraw.Draw(g).rounded_rectangle([40, 40, 40 + bx1 - bx0, 40 + by1 - by0], radius=46,
                                                fill=GREEN + (int(170 * glow),))
            ov.alpha_composite(g.filter(ImageFilter.GaussianBlur(16)), (bx0 - 40, by0 - 40))
            ImageDraw.Draw(ov).rounded_rectangle([bx0, by0, bx1, by1], radius=46, outline=GREEN + (int(255 * glow),),
                                                 width=3)
        avail = (bx1 - bx0) - 96 - 30
        num = self.counter_image(value)
        if num.width > avail:  # cifre grandi: rimpicciolisce per stare nel riquadro
            num = self.counter_image(value, size=max(30, int(66 * avail / num.width)))
        s = 1 + 0.16 * bump
        if s != 1:
            num = num.resize((int(num.width * s), int(num.height * s)), Image.BICUBIC)
        cx = bx0 + 96 + (bx1 - bx0 - 96 - 24) / 2
        cy = by0 + 30 + (by1 - by0 - 30) / 2
        ov.alpha_composite(num, (int(cx - num.width / 2), int(cy - num.height / 2)))
        self.counter_center = (cx, cy)

        # elenco episodi (chip): episodi passati, quello attuale, i prossimi bloccati
        cur = self.ep["episode"]
        chips = [(n, m, "past") for n, m in self.history[-3:]]
        chips.append((cur, value, "now"))
        nxt = cur + 1
        while len(chips) < 5:
            chips.append((nxt, None, "lock"))
            nxt += 1
        gap, cw, chh = 10, 194, 54
        x = (W - (len(chips) * cw + (len(chips) - 1) * gap)) / 2
        d = ImageDraw.Draw(ov)
        fe = font(FONT_BLACK, 23)
        fm = font(FONT_BOLD, 23)
        y = self.STRIP_Y
        for n, m, kind in chips:
            if kind == "now":
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=27, fill=GREEN + (255,))
                tc, mc = (8, 30, 14, 255), (8, 30, 14, 255)
            elif kind == "past":
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=27, fill=(14, 20, 26, 215),
                                    outline=GREEN + (170,), width=2)
                tc, mc = (230, 235, 240, 255), GREEN + (255,)
            else:
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=27, fill=(14, 18, 24, 170),
                                    outline=(255, 255, 255, 45), width=2)
                tc, mc = (140, 146, 156, 255), (140, 146, 156, 255)
            et = f"EP {n}"
            ew = d.textlength(et, font=fe)
            if m is None:
                inner = ew + 12 + 20
                sx = x + (cw - inner) / 2
                d.text((sx, y + 15), et, font=fe, fill=tc)
                padlock(d, sx + ew + 12, y + 15, 20, mc)
            else:
                mt = money(math.floor(m + 0.5))
                fe2, fm2 = fe, fm
                sz = 23
                while sz > 14 and d.textlength(et, font=fe2) + 12 + d.textlength(mt, font=fm2) > cw - 24:
                    sz -= 1
                    fe2, fm2 = font(FONT_BLACK, sz), font(FONT_BOLD, sz)
                ew = d.textlength(et, font=fe2)
                mw = d.textlength(mt, font=fm2)
                sx = x + (cw - (ew + 12 + mw)) / 2
                yy = y + 15 + (23 - sz) * 0.5
                d.text((sx, yy), et, font=fe2, fill=tc)
                d.text((sx + ew + 12, yy), mt, font=fm2, fill=mc)
            x += cw + gap


def highlight_tokens(text):
    out = []
    for w in text.replace("\n", " \n ").split(" "):
        out.append(w)
    return out


def caption_sprite(text, size=70, max_w=W - 150):
    """Didascalia bianca con bordo; gli importi in $ e le parole in MAIUSCOLO diventano colorati."""
    f = font(FONT_BLACK, size)
    dummy = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = []
    for para in text.split("\n"):
        words = para.split()
        if dummy.textlength(para, font=f) <= max_w or len(words) < 2:
            lines.append(words)
            continue
        # due righe bilanciate (o a capo normale se serve di piu')
        best = None
        for cut in range(1, len(words)):
            a, b = " ".join(words[:cut]), " ".join(words[cut:])
            wmax = max(dummy.textlength(a, font=f), dummy.textlength(b, font=f))
            if wmax <= max_w and (best is None or wmax < best[0]):
                best = (wmax, cut)
        if best:
            lines += [words[:best[1]], words[best[1]:]]
        else:
            line = []
            for w in words:
                if line and dummy.textlength(" ".join(line + [w]), font=f) > max_w:
                    lines.append(line)
                    line = [w]
                else:
                    line.append(w)
            lines.append(line)
    lh = int(size * 1.22)
    pad = 30
    width = int(max(dummy.textlength(" ".join(l), font=f) for l in lines)) + pad * 2
    im = Image.new("RGBA", (width, lh * len(lines) + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    sw = dummy.textlength(" ", font=f)
    for i, l in enumerate(lines):
        lw = dummy.textlength(" ".join(l), font=f)
        x = (width - lw) / 2
        y = pad + i * lh
        for w in l:
            core = w.strip(".,!?")
            color = (255, 255, 255, 255)
            if "$" in w:
                color = GREEN + (255,)
            elif len(core) > 1 and core.isupper():
                color = GOLD + (255,)
            d.text((x, y), w, font=f, fill=color, stroke_width=8, stroke_fill=(0, 0, 0, 255))
            x += dummy.textlength(w, font=f) + sw
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.alpha_composite(im)
    sh = np.array(shadow)
    sh[..., :3] = 0
    sh[..., 3] = (sh[..., 3] * 0.6).astype(np.uint8)
    shadow = Image.fromarray(sh).filter(ImageFilter.GaussianBlur(10))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, 6))
    out.alpha_composite(im)
    return out


def cta_sprite(title, options, t):
    im = Image.new("RGBA", (W, 760), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ft = font(FONT_BLACK, 96)
    tw = d.textlength(title, font=ft)
    d.text(((W - tw) / 2, 20), title, font=ft, fill=(255, 255, 255, 255), stroke_width=9, stroke_fill=(0, 0, 0, 255))
    fs = font(FONT_BOLD, 40)
    sub = "COMMENT YOUR VOTE"
    sw = d.textlength(sub, font=fs)
    d.text(((W - sw) / 2, 150), sub, font=fs, fill=(230, 235, 240, 255), stroke_width=5, stroke_fill=(0, 0, 0, 255))
    fo = font(FONT_BLACK, 84)
    n = len(options)
    bw, bh, gap = 370, 170, 130
    x = (W - (n * bw + (n - 1) * gap)) / 2
    for i, opt in enumerate(options):
        ph = 2 * math.pi * (t / 1.1) + i * math.pi
        s = 1 + 0.05 * (0.5 + 0.5 * math.sin(ph))
        col = GREEN if i == 0 else GOLD
        card = Image.new("RGBA", (bw + 80, bh + 80), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        g = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle([40, 40, 40 + bw, 40 + bh], radius=50,
                                            fill=col + (int(110 + 80 * (0.5 + 0.5 * math.sin(ph))),))
        card.alpha_composite(g.filter(ImageFilter.GaussianBlur(20)))
        cd.rounded_rectangle([40, 40, 40 + bw, 40 + bh], radius=50, fill=col + (255,))
        ow = cd.textlength(opt, font=fo)
        cd.text((40 + (bw - ow) / 2, 40 + (bh - 100) / 2), opt, font=fo, fill=(10, 18, 12, 255))
        card = card.resize((int(card.width * s), int(card.height * s)), Image.BICUBIC)
        im.alpha_composite(card, (int(x + bw / 2 - card.width / 2), int(300 + bh / 2 + 40 - card.height / 2)))
        x += bw + gap
    fo2 = font(FONT_BLACK, 70)
    vs = "or"
    vw = d.textlength(vs, font=fo2)
    d.text(((W - vw) / 2, 380), vs, font=fo2, fill=(255, 255, 255, 255), stroke_width=7, stroke_fill=(0, 0, 0, 255))
    rule = "1 FOLLOWER = $10   ·   1 LIKE = $1"
    fr = font(FONT_BOLD, 36)
    rw = d.textlength(rule, font=fr)
    d.rounded_rectangle([(W - rw) / 2 - 30, 620, (W + rw) / 2 + 30, 690], radius=35, fill=(10, 14, 20, 200))
    d.text(((W - rw) / 2, 636), rule, font=fr, fill=GREEN + (255,))
    return im


# ----------------------------------------------------------------------------
# Effetti particellari
# ----------------------------------------------------------------------------
class Fx:
    def __init__(self, kinds, seed, src_point=None):
        self.kinds = kinds if isinstance(kinds, list) else ([kinds] if kinds else [])
        self.rng = random.Random(seed)
        self.src_point = src_point
        r = self.rng
        self.rain = [(r.uniform(-200, W + 200), r.uniform(0, H), r.uniform(900, 1500), r.uniform(28, 60),
                      r.randint(25, 70)) for _ in range(150)]
        self.bokeh_sprites = [soft_circle(rr, (255, 220, 150, 150), rr * 0.5) for rr in (4, 6, 9, 13)]
        self.dust = [(r.uniform(0, W), r.uniform(0, H), r.uniform(-14, 14), r.uniform(-40, -12), r.randint(0, 3),
                      r.uniform(0, 6.28)) for _ in range(45)]
        self.ember_sprites = [soft_circle(rr, (255, 170, 70, 220), rr * 0.6) for rr in (3, 5, 7)]
        self.embers = [(r.uniform(0, W), r.uniform(0, H), r.uniform(-20, 30), r.uniform(-60, -20), r.randint(0, 2),
                        r.uniform(0, 6.28)) for _ in range(40)]
        self.bubbles = []
        for i in range(26):
            self.bubbles.append((r.uniform(0, 6), r.uniform(-120, 120), r.uniform(-80, 40), r.uniform(12, 34),
                                 r.uniform(-60, 60), r.uniform(110, 220)))
        self.bubble_cache = {}
        self.sparks = [(r.uniform(0, 8), r.uniform(-30, 30), r.uniform(-30, 30), r.uniform(8, 18)) for _ in range(30)]

    def bubble(self, rad):
        rad = int(rad)
        if rad not in self.bubble_cache:
            s = rad * 2 + 6
            im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.ellipse([3, 3, s - 3, s - 3], fill=(220, 240, 255, 40), outline=(255, 255, 255, 190), width=2)
            d.ellipse([s * 0.28, s * 0.22, s * 0.45, s * 0.36], fill=(255, 255, 255, 220))
            self.bubble_cache[rad] = im
        return self.bubble_cache[rad]

    def draw(self, ov, t, to_out):
        d = ImageDraw.Draw(ov)
        for k in self.kinds:
            if k == "rain":
                for x, y, sp, ln, a in self.rain:
                    yy = (y + sp * t) % (H + 200) - 100
                    xx = x + (yy - y) * 0.12
                    d.line([(xx, yy), (xx + ln * 0.12, yy + ln)], fill=(190, 210, 255, a), width=2)
            elif k == "dust":
                for x, y, vx, vy, si, ph in self.dust:
                    xx = (x + vx * t + 18 * math.sin(t * 0.8 + ph)) % W
                    yy = (y + vy * t) % H
                    sp = self.bokeh_sprites[si]
                    ov.alpha_composite(sp, (int(xx), int(yy)))
            elif k == "embers":
                for x, y, vx, vy, si, ph in self.embers:
                    xx = (x + vx * t + 25 * math.sin(t * 1.3 + ph)) % W
                    yy = (y + vy * t) % H
                    sp = self.ember_sprites[si]
                    tw = 0.5 + 0.5 * math.sin(t * 5 + ph)
                    if tw > 0.25:
                        ov.alpha_composite(sp, (int(xx), int(yy)))
            elif k == "bubbles" and self.src_point:
                sx, sy = to_out(*self.src_point)
                for t0, ox, oy, rad, vx, vy in self.bubbles:
                    life = (t - t0) % 6.0
                    if life > 3.2:
                        continue
                    xx = sx + ox + vx * life + 14 * math.sin(life * 3 + rad)
                    yy = sy + oy - vy * life
                    b = self.bubble(rad * (0.7 + 0.3 * min(life * 2, 1)))
                    ov.alpha_composite(b, (int(xx), int(yy)))
            elif k == "sparks" and self.src_point:
                sx, sy = to_out(*self.src_point)
                for t0, ox, oy, sz in self.sparks:
                    life = (t - t0) % 1.6
                    if life > 0.45:
                        continue
                    a = math.sin(math.pi * life / 0.45)
                    xx, yy = sx + ox + ox * life * 2, sy + oy - 40 * life
                    s = sz * a
                    col = (255, 240, 180, int(255 * a))
                    d.line([(xx - s, yy), (xx + s, yy)], fill=col, width=3)
                    d.line([(xx, yy - s), (xx, yy + s)], fill=col, width=3)
                    d.line([(xx - s * 0.5, yy - s * 0.5), (xx + s * 0.5, yy + s * 0.5)], fill=col, width=2)



# ----------------------------------------------------------------------------
# Scena dei fan: nametag Instagram dei migliori sostenitori
# ----------------------------------------------------------------------------
IG_GRAD = [(254, 218, 117), (250, 126, 30), (214, 41, 118), (150, 47, 191), (79, 91, 213)]
MEDALS = [((255, 205, 60), "1"), ((205, 214, 224), "2"), ((222, 150, 90), "3")]


def ig_ring(size):
    """Anello con il gradiente di Instagram."""
    s = size * 2
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    tpos = np.clip((xx + (s - yy)) / (2 * s), 0, 1)
    stops = np.linspace(0, 1, len(IG_GRAD))
    rgb = np.zeros((s, s, 3), np.float32)
    for c in range(3):
        rgb[..., c] = np.interp(tpos, stops, [g[c] for g in IG_GRAD])
    r = np.hypot(xx - s / 2, yy - s / 2)
    a = ((r <= s / 2 - 1) & (r >= s / 2 - s * 0.09)).astype(np.float32) * 255
    im = Image.fromarray(np.dstack([rgb, a]).astype(np.uint8), "RGBA")
    return im.resize((size, size), Image.LANCZOS)


class SupporterAssets:
    def __init__(self, supporters):
        self.cards = []
        for i, sp in enumerate(supporters[:5]):
            self.cards.append(self._card(i, sp))

    def _card(self, i, sp):
        w, h = 900, 168
        im = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([20, 28, 20 + w, 28 + h], radius=46, fill=(0, 0, 0, 150))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
        d = ImageDraw.Draw(im)
        top = i == 0
        d.rounded_rectangle([20, 20, 20 + w, 20 + h], radius=46, fill=(255, 255, 255, 245) if top else (18, 22, 30, 235),
                            outline=(255, 205, 60, 255) if top else (255, 255, 255, 60), width=4 if top else 2)
        # avatar: anello Instagram + iniziale
        av = 116
        ax, ay = 20 + 34, 20 + (h - av) // 2
        im.alpha_composite(ig_ring(av), (ax, ay))
        inner = Image.new("RGBA", (av, av), (0, 0, 0, 0))
        idr = ImageDraw.Draw(inner)
        idr.ellipse([14, 14, av - 14, av - 14], fill=(36, 40, 52, 255))
        name = sp["username"].lstrip("@")
        ini = (name[:1] or "?").upper()
        fi = font(FONT_BLACK, 50)
        iw = idr.textlength(ini, font=fi)
        idr.text(((av - iw) / 2, av / 2 - 32), ini, font=fi, fill=(255, 255, 255, 255))
        im.alpha_composite(inner, (ax, ay))
        # medaglia
        if i < 3:
            col, num = MEDALS[i]
            mx, my = ax + av - 40, ay + av - 44
            d.ellipse([mx, my, mx + 46, my + 46], fill=col + (255,), outline=(255, 255, 255, 255), width=3)
            fm = font(FONT_BLACK, 26)
            d.text((mx + 23 - d.textlength(num, font=fm) / 2, my + 8), num, font=fm, fill=(40, 30, 10, 255))
        # nome utente
        fn = font(FONT_BLACK, 54 if len(name) <= 16 else (44 if len(name) <= 22 else 36))
        tx = ax + av + 34
        tc = (18, 20, 26, 255) if top else (255, 255, 255, 255)
        d.text((tx, 20 + 34), "@" + name, font=fn, fill=tc)
        detail = sp.get("detail", "")
        if detail:
            fd = font(FONT_BOLD, 30)
            d.text((tx, 20 + 102), detail, font=fd, fill=(214, 41, 118, 255) if top else GREEN + (255,))
        return im

    def draw(self, ov, t, title, note):
        d = ImageDraw.Draw(ov)
        a = ease_out((t - 0.1) / 0.4)
        if a > 0:
            ft = font(FONT_BLACK, 84)
            tw = d.textlength(title, font=ft)
            y = 360 - 30 * (1 - a)
            lay = Image.new("RGBA", (W, 130), (0, 0, 0, 0))
            ImageDraw.Draw(lay).text(((W - tw) / 2, 10), title, font=ft, fill=GOLD + (255,), stroke_width=8,
                                     stroke_fill=(0, 0, 0, 255))
            la = np.array(lay)
            la[..., 3] = (la[..., 3] * a).astype(np.uint8)
            ov.alpha_composite(Image.fromarray(la), (0, int(y)))
        y0 = 520
        for i, c in enumerate(self.cards):
            x = (t - 0.45 - i * 0.28) / 0.45
            if x <= 0:
                continue
            s = 0.75 + 0.25 * ease_back(x)
            dx = (1 - ease_out(x)) * 300
            sp = c if abs(s - 1) < 1e-3 else c.resize((int(c.width * s), int(c.height * s)), Image.BICUBIC)
            # il primo pulsa leggermente
            if i == 0 and x >= 1:
                p = 1 + 0.03 * math.sin(2 * math.pi * t / 1.2)
                sp = c.resize((int(c.width * p), int(c.height * p)), Image.BICUBIC)
            ov.alpha_composite(sp, (int((W - sp.width) / 2 + dx), int(y0 + i * 200 + (c.height - sp.height) / 2)))
        x = (t - 0.6 - len(self.cards) * 0.28) / 0.4
        if x > 0 and note:
            fnote = font(FONT_BOLD, 38)
            nw = d.textlength(note, font=fnote)
            ny = y0 + len(self.cards) * 200 + 30
            d.rounded_rectangle([(W - nw) / 2 - 34, ny, (W + nw) / 2 + 34, ny + 76], radius=38,
                                fill=(214, 41, 118, int(235 * min(1, x))))
            d.text(((W - nw) / 2, ny + 16), note, font=fnote, fill=(255, 255, 255, int(255 * min(1, x))))


# ----------------------------------------------------------------------------
# Scena
# ----------------------------------------------------------------------------
def scene_job(args):
    i, scene, ep, ep_dir, tmp, dur, v0, events, history, preview = args
    src = cv2.cvtColor(cv2.imread(os.path.join(ep_dir, scene["image"])), cv2.COLOR_BGR2RGB)
    ih, iw = src.shape[:2]
    k = max(W / iw, H / ih)
    anim = scene.get("anim", {})
    movers = anim.get("movers", [])
    eyes = anim.get("blink", [])
    skins = [np.array(e[4], np.float32) if len(e) > 4 else lid_color(src, e) for e in eyes]
    eyes = [e[:4] for e in eyes]
    blinks = anim.get("blink_times", [0.9 + 3.1 * j + (0.4 if j % 2 else 0) for j in range(6)])
    cam = scene.get("camera", {})
    z0, z1 = cam.get("zoom", [1.06, 1.14] if i % 2 == 0 else [1.14, 1.07])
    fx, fy = cam.get("focus", [0.5, 0.5])
    pan = cam.get("pan", [0.0, 0.0])
    darken = scene.get("darken", 0.0)
    blur = scene.get("blur", 0)
    sup = scene.get("supporters")
    sup_assets = SupporterAssets(sup) if sup else None

    hud = Hud(ep, history)
    fxs = Fx(anim.get("fx"), seed=i * 17 + 3, src_point=anim.get("fx_point"))
    cap = scene.get("caption", "")
    cta = scene.get("cta")
    cap_im = caption_sprite(cap) if cap and not cta and not sup else None
    cap_pos = scene.get("caption_position", "bottom")
    glows = anim.get("glow", [])
    glow_sprites = [soft_circle(g[2], tuple(g[3]) + (255,), g[2] * 0.45) for g in glows]

    # griglia a bassa risoluzione per le deformazioni
    gs = 4
    gu, gv = np.meshgrid(np.arange(0, W + gs, gs, dtype=np.float32), np.arange(0, H + gs, gs, dtype=np.float32))
    nframes = int(round(dur * FPS))

    def camera(t):
        p = ease_in_out(t / dur)
        z = z0 + (z1 - z0) * p
        scale = k * z
        hw, hh = W / (2 * scale), H / (2 * scale)
        cx = fx * iw + pan[0] * iw * (p - 0.5)
        cy = fy * ih + pan[1] * ih * (p - 0.5)
        cx = min(max(cx, hw), iw - hw)
        cy = min(max(cy, hh), ih - hh)
        return scale, cx, cy

    out_path = os.path.join(tmp, f"s{i:02d}.tmp.mp4")
    proc = None
    if not preview:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-r", str(FPS), out_path], stdin=subprocess.PIPE)
    frames_to_do = range(nframes) if not preview else [min(int(pt * FPS), nframes - 1) for pt in preview]

    for f in frames_to_do:
        t = f / FPS
        scale, cx, cy = camera(t)
        # mappa uscita -> immagine
        X = cx + (gu - W / 2) / scale
        Y = cy + (gv - H / 2) / scale
        DX = np.zeros_like(X)
        DY = np.zeros_like(Y)
        for m in movers:
            dx, dy = mover_disp(m, X, Y, t)
            DX += dx
            DY += dy
        mx = cv2.resize(X - DX, (W, H), interpolation=cv2.INTER_LINEAR)
        my = cv2.resize(Y - DY, (W, H), interpolation=cv2.INTER_LINEAR)
        img = src
        c = blink_amount(t, blinks) if eyes else 0
        if c > 0.01:
            img = src.copy()
            for e, sk in zip(eyes, skins):
                apply_blink(img, e, c, sk)
        frame = cv2.remap(img, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        if blur:
            frame = cv2.GaussianBlur(frame, (0, 0), blur)
        if darken:
            frame = (frame * (1 - darken)).astype(np.uint8)

        def to_out(x, y):
            return (x - cx) * scale + W / 2, (y - cy) * scale + H / 2

        ov = hud.static.copy()
        # bagliori pulsanti (luci, sole)
        for g, sp in zip(glows, glow_sprites):
            a = g[4] * (0.65 + 0.35 * math.sin(2 * math.pi * t / g[5]))
            spa = sp.copy()
            arr = np.array(spa)
            arr[..., 3] = (arr[..., 3] * a).astype(np.uint8)
            ox, oy = to_out(g[0], g[1])
            layer = Image.fromarray(arr)
            ov.alpha_composite(layer, (int(ox - layer.width / 2), int(oy - layer.height / 2)))
        fxs.draw(ov, t, to_out)

        # soldi
        value = v0
        bump, glow = 0.0, 0.0
        for ev in events:
            arr_t = ev["t"] + ev["fly"]
            value += ev["amount"] * ease_out((t - arr_t) / ev["roll"])
            if t >= arr_t:
                x = t - arr_t
                bump = max(bump, math.exp(-x * 9) * math.sin(min(x * 14, math.pi)) if x < 0.5 else 0)
                glow = max(glow, max(0, 1 - x / 0.8))
        hud.draw(ov, value, bump, glow)
        tx, ty = hud.counter_center
        for ev in events:
            # moneta che vola dal punto della scena al contatore
            x = (t - ev["t"]) / ev["fly"]
            if 0 <= x <= 1 and ev.get("from"):
                sx, sy = to_out(*ev["from"])
                e = ease_in_out(x)
                px = sx + (tx - sx) * e
                py = sy + (ty - sy) * e - math.sin(math.pi * e) * 160
                cs = hud.coin_big if abs(ev["amount"]) >= 10 else hud.coin
                s = 1.0 - 0.35 * e
                sp = cs.resize((int(cs.width * s), int(cs.height * s)), Image.LANCZOS)
                ov.alpha_composite(sp, (int(px - sp.width / 2), int(py - sp.height / 2)))
            # "+$X" che sale dal contatore
            y = t - ev["t"] - ev["fly"]
            if 0 <= y <= 1.1:
                a = min(1, y * 6) * (1 - max(0, (y - 0.7) / 0.4))
                txt = ("+" if ev["amount"] >= 0 else "-") + money(abs(ev["amount"]))
                if ev.get("label"):
                    txt += "  " + ev["label"]
                fpl = font(FONT_BLACK, 56 if abs(ev["amount"]) < 10 else 66)
                pcol = GREEN if ev["amount"] >= 0 else (255, 80, 80)
                dd = ImageDraw.Draw(ov)
                tw = dd.textlength(txt, font=fpl)
                yy = Hud.POP_Y + 20 - 50 * ease_out(y)
                lay = Image.new("RGBA", (int(tw) + 40, 100), (0, 0, 0, 0))
                ImageDraw.Draw(lay).text((20, 10), txt, font=fpl, fill=pcol + (255,), stroke_width=7,
                                         stroke_fill=(0, 0, 0, 255))
                la = np.array(lay)
                la[..., 3] = (la[..., 3] * a).astype(np.uint8)
                px0 = min(max(tx - lay.width / 2, 20), W - 20 - lay.width)
                ov.alpha_composite(Image.fromarray(la), (int(px0), int(yy)))

        # didascalia animata
        if cap_im is not None:
            x = (t - 0.25) / 0.35
            if x > 0:
                s = 0.6 + 0.4 * ease_back(x)
                a = min(1, x * 2.5)
                sp = cap_im if abs(s - 1) < 1e-3 else cap_im.resize(
                    (max(1, int(cap_im.width * s)), max(1, int(cap_im.height * s))), Image.BICUBIC)
                if a < 1:
                    arr = np.array(sp)
                    arr[..., 3] = (arr[..., 3] * a).astype(np.uint8)
                    sp = Image.fromarray(arr)
                cyc = {"top": 640, "middle": H // 2}.get(cap_pos, H - 560)
                ov.alpha_composite(sp, (int((W - sp.width) / 2), int(cyc - sp.height / 2)))
        if sup_assets:
            sup_assets.draw(ov, t, scene.get("supporters_title", "TOP SUPPORTERS"),
                            scene.get("supporters_note", "TAG YOUR FRIENDS TO BE HERE NEXT TIME"))
        if cta:
            x = (t - 0.15) / 0.4
            if x > 0:
                sp = cta_sprite(cta.get("title", "What's next?"), cta.get("options", ["A", "B"]), t)
                s = 0.7 + 0.3 * ease_back(x)
                sp = sp.resize((int(W * s), int(sp.height * s)), Image.BICUBIC)
                ov.alpha_composite(sp, (int((W - sp.width) / 2), int(H * 0.56 - sp.height / 2)))

        o = np.asarray(ov)
        a = o[..., 3:4].astype(np.float32) / 255.0
        frame = (frame.astype(np.float32) * (1 - a) + o[..., :3].astype(np.float32) * a)
        # dissolvenza in apertura/chiusura
        fade = min(1.0, t / 0.18, (dur - t) / 0.12 if i == ep["_nscenes"] - 1 else 1.0)
        if fade < 1:
            frame *= fade
        frame = frame.astype(np.uint8)
        if preview:
            Image.fromarray(frame).save(os.path.join(tmp, f"preview_s{i + 1}_{t:.2f}.png"))
        else:
            proc.stdin.write(frame.tobytes())
    if proc:
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit(f"ffmpeg fallito nella scena {i + 1}")
        final = os.path.join(tmp, f"s{i:02d}.mp4")
        os.replace(out_path, final)
        print(f"  scena {i + 1}: {scene['image']} ({dur:.1f}s)", flush=True)
        return final
    return None


def episode_history(ep_dir, number):
    """Soldi finali degli episodi precedenti (per l'elenco nella barra)."""
    hist = []
    for jf in sorted(glob.glob(os.path.join(os.path.dirname(ep_dir), "*", "episode.json"))):
        try:
            e = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        if e.get("episode", 0) < number:
            hist.append((e["episode"], final_worth(e)))
    return sorted(hist)


def final_worth(ep):
    v = ep.get("net_worth", 0)
    for s in ep["scenes"]:
        for m in s.get("money", []):
            v += m["amount"]
    return v


def render_episode(ep_dir, preview=None):
    ep_dir = os.path.abspath(ep_dir)
    name = os.path.basename(ep_dir.rstrip("/"))
    with open(os.path.join(ep_dir, "episode.json"), encoding="utf-8") as f:
        ep = json.load(f)
    ep["_nscenes"] = len(ep["scenes"])
    print(f"Episodio {ep['episode']} ({name})")
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp()
    history = episode_history(ep_dir, ep["episode"])
    try:
        # 1) voci e durate
        voices, durs = [], []
        for i, s in enumerate(ep["scenes"]):
            v = None
            if not preview:
                v = make_voice(s.get("narration", ""), ep, os.path.join(tmp, f"v{i}"), ep_dir, i)
            d = float(s.get("duration", 3.5))
            if v is not None:
                d = max(d, len(v) / SR + ep.get("voice_delay", 0.2) + ep.get("scene_pad", 0.5))
            elif preview and s.get("narration"):
                d = max(d, len(s["narration"].split()) * 0.36 + 0.9)
            voices.append(v)
            durs.append(round(d * FPS) / FPS)

        # 2) soldi: valore di partenza per scena + eventi
        start = ep.get("net_worth", 0)
        prev_final = history[-1][1] if history else start
        values, all_events = [], []
        v = prev_final
        for i, s in enumerate(ep["scenes"]):
            evs = []
            if i == 0 and start != prev_final:
                evs.append({"t": 0.6, "amount": start - prev_final, "fly": 0.01, "roll": 1.4,
                            "label": "FROM YOU"})
            for m in s.get("money", []):
                amt = m["amount"]
                fly = m.get("fly", 0.55 if amt < 10 else 0.7) if amt > 0 else 0.01
                roll = m.get("roll", 0.35 if abs(amt) < 10 else 0.9)
                t = m.get("t")
                if t is None:  # posizione relativa nella scena (0 = inizio, 1 = fine)
                    t = m.get("at", 0.6) * durs[i] - fly
                t = max(0.3, min(t, durs[i] - fly - roll - 0.15))
                evs.append({"t": t, "amount": amt, "from": m.get("from") if amt > 0 else None,
                            "fly": fly, "roll": roll, "label": m.get("label")})
            values.append(v)
            all_events.append(evs)
            v += sum(e["amount"] for e in evs)

        jobs = []
        for i, s in enumerate(ep["scenes"]):
            pv = None
            if preview:
                pv = [pt for (si, pt) in preview if si == i + 1]
                if not pv:
                    continue
            jobs.append((i, s, ep, ep_dir, tmp, durs[i], values[i], all_events[i], history, pv))

        with Pool(min(2, os.cpu_count() or 1)) as pool:
            parts = pool.map(scene_job, jobs, chunksize=1)

        if preview:
            os.makedirs(os.path.join(OUT_DIR, "preview"), exist_ok=True)
            for p in glob.glob(os.path.join(tmp, "preview_*.png")):
                shutil.copy(p, os.path.join(OUT_DIR, "preview"))
            print("Anteprime in output/preview/")
            return

        # 3) colonna sonora unica
        total = sum(durs)
        audio = np.zeros(int(total * SR) + SR, np.float32)
        coin, cash, whoosh = sfx_coin(), sfx_cash(), sfx_whoosh()

        def add(clip, at, gain=1.0):
            s0 = int(at * SR)
            e = min(len(audio), s0 + len(clip))
            audio[s0:e] += clip[:e - s0] * gain

        off = 0.0
        for i, d in enumerate(durs):
            if i > 0:
                add(whoosh, max(off - 0.15, 0))
            if voices[i] is not None:
                add(voices[i], off + ep.get("voice_delay", 0.2))
            for e in all_events[i]:
                if e["amount"] > 0:
                    add(cash if e["amount"] >= 10 else coin, off + e["t"] + e["fly"])
            off += d
        audio = audio[:int(total * SR)]
        peak = np.max(np.abs(audio)) or 1
        if peak > 0.98:
            audio *= 0.98 / peak
        wav = os.path.join(tmp, "audio.f32")
        audio.astype(np.float32).tofile(wav)

        # 4) unione scene + audio (+ musica)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        joined = os.path.join(tmp, "joined.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
        final_tmp = os.path.join(OUT_DIR, f"{name}.tmp.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-f", "f32le", "-ar", str(SR), "-ac", "1",
               "-i", wav]
        if os.path.exists(MUSIC):
            vol = ep.get("music_volume", 0.16)
            cmd += ["-stream_loop", "-1", "-i", MUSIC, "-filter_complex",
                    f"[2:a]volume={vol},afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[m];"
                    f"[1:a][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-15:TP=-1.5:LRA=11[a]",
                    "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", "[1:a]loudnorm=I=-15:TP=-1.5:LRA=11[a]", "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-shortest",
                "-movflags", "+faststart", final_tmp]
        run(cmd)
        final = os.path.join(OUT_DIR, f"{name}.mp4")
        os.replace(final_tmp, final)

        cap = ep.get("instagram_caption")
        if cap:
            with open(os.path.join(OUT_DIR, f"{name}_caption.txt"), "w", encoding="utf-8") as f:
                f.write(cap + "\n")
        print(f"Fatto: {final}  ({total:.1f}s, net worth finale {money(final_worth(ep))})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    if "--preview" in args:
        j = args.index("--preview")
        pts = [(int(a.split(":")[0]), float(a.split(":")[1])) for a in args[j + 1:]]
        render_episode(args[0], preview=pts)
    elif args[0] == "--all":
        base = os.path.join(ROOT, "episodes")
        for d in sorted(os.listdir(base)):
            ep_dir = os.path.join(base, d)
            done = os.path.join(OUT_DIR, f"{d}.mp4")
            if os.path.isfile(os.path.join(ep_dir, "episode.json")) and not os.path.exists(done):
                render_episode(ep_dir)
    else:
        for d in args:
            render_episode(d)
