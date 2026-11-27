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


def sfx_pop():
    n = int(SR * 0.09)
    t = np.arange(n) / SR
    f = 520 + 900 * t / 0.09
    a = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38)
    return (a * 0.12).astype(np.float32)


def sfx_ding():
    t = np.arange(int(SR * 0.5)) / SR
    a = np.zeros_like(t)
    for st, fr in ((0.0, 1318.5), (0.11, 1760.0)):
        tt = np.clip(t - st, 0, None)
        a += np.where(t >= st, np.exp(-tt * 9), 0) * np.sin(2 * np.pi * fr * tt)
    return (a * 0.11).astype(np.float32)


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


LABELS_DEFAULT = {"ep": "EP", "cash": "CASH", "home": "HOME", "job": "JOB", "no": "NO"}


class Hud:
    """Barra in alto: episodio, soldi (contatore a rulli), casa, lavoro + timer + elenco episodi."""
    BAR_Y, BAR_H = 84, 96
    TIMER_Y = 194
    STRIP_H = 50

    def __init__(self, ep, history):
        self.ep = ep
        self.history = history  # [(numero, soldi_finali)] degli episodi precedenti
        self.lab = dict(LABELS_DEFAULT, **ep.get("hud_labels", {}))
        self.coin = coin_sprite(58)
        self.coin_big = coin_sprite(78)
        self.digit_cache = {}
        self.segs = None
        self.static = self._static()
        x0, x1 = self.segs[1]
        self.counter_center = ((x0 + x1) / 2, self.BAR_Y + 58)

    def _static(self):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        grad = np.zeros((H, W, 4), np.uint8)
        top = np.clip(1 - np.arange(H) / 420, 0, 1) ** 1.6 * 150
        bot = np.clip((np.arange(H) - (H - 760)) / 760, 0, 1) ** 1.5 * 200
        grad[..., 3] = np.maximum(top, bot)[:, None].astype(np.uint8)
        im = Image.alpha_composite(im, Image.fromarray(grad, "RGBA"))
        yy, xx = np.mgrid[0:H, 0:W]
        v = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2)
        vig = np.zeros((H, W, 4), np.uint8)
        vig[..., 3] = (np.clip(v - 0.55, 0, 1) * 200).astype(np.uint8)
        im = Image.alpha_composite(im, Image.fromarray(vig, "RGBA"))

        x0, x1, y0, h = 70, W - 70, self.BAR_Y, self.BAR_H
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 8, x1, y0 + h + 8], radius=28, fill=(0, 0, 0, 120))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(14)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([x0, y0, x1, y0 + h], radius=28, fill=(8, 10, 14, 220), outline=(255, 255, 255, 40),
                            width=2)
        # segmenti: episodio | soldi | casa | lavoro
        wsz = [170, 330, 170, 170]
        scale = (x1 - x0) / sum(wsz)
        xs = [x0]
        for wv in wsz:
            xs.append(xs[-1] + wv * scale)
        self.segs = [(xs[i], xs[i + 1]) for i in range(4)]
        fl = font(FONT_BOLD, 19)
        labels = ["FROM ZERO", self.lab["cash"], self.lab["home"], self.lab["job"]]
        for i, (a, b) in enumerate(self.segs):
            if i:
                d.line([(a, y0 + 18), (a, y0 + h - 18)], fill=(255, 255, 255, 50), width=2)
            lw = d.textlength(labels[i], font=fl)
            col = GREEN + (255,) if i == 1 else (170, 176, 186, 255)
            d.text(((a + b) / 2 - lw / 2, y0 + 12), labels[i], font=fl, fill=col)
        fe = font(FONT_BLACK, 40)
        et = f"{self.lab['ep']} {self.ep['episode']}"
        a, b = self.segs[0]
        d.text(((a + b) / 2 - d.textlength(et, font=fe) / 2, y0 + 38), et, font=fe, fill=(255, 255, 255, 255))
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
        v = max(0.0, v)
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

    def _value_text(self, d, txt, a, b, y, flash):
        txt = (txt or self.lab["no"]).upper()
        size = 40
        f = font(FONT_BLACK, size)
        while d.textlength(txt, font=f) > (b - a) - 20 and size > 18:
            size -= 2
            f = font(FONT_BLACK, size)
        is_no = txt == self.lab["no"].upper()
        col = (235, 238, 242, 255) if is_no else GREEN + (255,)
        if flash > 0:
            col = tuple(int(c + (255 - c) * flash) for c in col[:3]) + (255,)
        d.text(((a + b) / 2 - d.textlength(txt, font=f) / 2, y + (40 - size) * 0.6), txt, font=f, fill=col)

    def draw(self, ov, value, bump, glow, stats=None, stat_flash=0.0, timer=None, show_strip=False):
        stats = stats or {}
        d = ImageDraw.Draw(ov)
        y0 = self.BAR_Y
        a, b = self.segs[1]
        if glow > 0:
            g = Image.new("RGBA", (int(b - a) + 60, self.BAR_H + 60), (0, 0, 0, 0))
            ImageDraw.Draw(g).rounded_rectangle([30, 30, 30 + b - a, 30 + self.BAR_H], radius=24,
                                                fill=GREEN + (int(150 * glow),))
            ov.alpha_composite(g.filter(ImageFilter.GaussianBlur(14)), (int(a) - 30, y0 - 30))
        avail = (b - a) - 30
        num = self.counter_image(value, size=54)
        if num.width > avail:
            num = self.counter_image(value, size=max(24, int(54 * avail / num.width)))
        s = 1 + 0.16 * bump
        if s != 1:
            num = num.resize((int(num.width * s), int(num.height * s)), Image.BICUBIC)
        cx, cy = (a + b) / 2, y0 + 60
        ov.alpha_composite(num, (int(cx - num.width / 2), int(cy - num.height / 2)))
        self.counter_center = (cx, cy)
        for key, (sa, sb) in (("home", self.segs[2]), ("job", self.segs[3])):
            self._value_text(d, stats.get(key), sa, sb, y0 + 38, stat_flash if key in stats.get("_changed", ()) else 0)

        y = y0 + self.BAR_H + 14
        if timer:
            label, secs, red = timer
            secs = max(0, int(math.ceil(secs)))
            hh, mm, ss = secs // 3600, (secs % 3600) // 60, secs % 60
            tt = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            ft, fl = font(FONT_BLACK, 50), font(FONT_BOLD, 18)
            tw = max(d.textlength(tt, font=ft), d.textlength(label, font=fl)) + 56
            x0 = W / 2 - tw / 2
            d.rounded_rectangle([x0, y, x0 + tw, y + 92], radius=20, fill=(8, 10, 14, 225),
                                outline=((255, 70, 70, 200) if red else (255, 255, 255, 40)), width=2)
            d.text((W / 2 - d.textlength(label, font=fl) / 2, y + 8), label, font=fl,
                   fill=(255, 120, 110, 255) if red else (255, 196, 70, 255))
            d.text((W / 2 - d.textlength(tt, font=ft) / 2, y + 30), tt, font=ft,
                   fill=(255, 70, 70, 255) if red else (255, 255, 255, 255))
            y += 92 + 12
        if show_strip:
            y = self._strip(d, value, y)
        self.content_top = y + 8

    def _strip(self, d, value, y):
        cur = self.ep["episode"]
        chips = [(n, m, "past") for n, m in self.history[-3:]]
        chips.append((cur, value, "now"))
        nxt = cur + 1
        while len(chips) < 5:
            chips.append((nxt, None, "lock"))
            nxt += 1
        gap, cw, chh = 10, 186, self.STRIP_H
        x = (W - (len(chips) * cw + (len(chips) - 1) * gap)) / 2
        for n, m, kind in chips:
            if kind == "now":
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=25, fill=GREEN + (255,))
                tc = mc = (8, 30, 14, 255)
            elif kind == "past":
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=25, fill=(14, 20, 26, 215),
                                    outline=GREEN + (170,), width=2)
                tc, mc = (230, 235, 240, 255), GREEN + (255,)
            else:
                d.rounded_rectangle([x, y, x + cw, y + chh], radius=25, fill=(14, 18, 24, 170),
                                    outline=(255, 255, 255, 45), width=2)
                tc = mc = (140, 146, 156, 255)
            et = f"{self.lab['ep']} {n}"
            sz = 22
            fe, fm = font(FONT_BLACK, sz), font(FONT_BOLD, sz)
            if m is None:
                ew = d.textlength(et, font=fe)
                sx = x + (cw - (ew + 30)) / 2
                d.text((sx, y + 13), et, font=fe, fill=tc)
                padlock(d, sx + ew + 10, y + 13, 20, mc)
            else:
                mt = money(math.floor(max(0, m) + 0.5))
                while sz > 13 and d.textlength(et, font=fe) + 10 + d.textlength(mt, font=fm) > cw - 22:
                    sz -= 1
                    fe, fm = font(FONT_BLACK, sz), font(FONT_BOLD, sz)
                ew, mw = d.textlength(et, font=fe), d.textlength(mt, font=fm)
                sx = x + (cw - (ew + 10 + mw)) / 2
                yy = y + 13 + (22 - sz) * 0.5
                d.text((sx, yy), et, font=fe, fill=tc)
                d.text((sx + ew + 10, yy), mt, font=fm, fill=mc)
            x += cw + gap
        return y + chh + 10


# ----------------------------------------------------------------------------
# Riquadri sopra l'immagine: info, messaggi, commento vincente, voti, avvisi
# ----------------------------------------------------------------------------
CARD_COLORS = {"green": GREEN, "yellow": (255, 205, 60), "red": (255, 72, 72), "white": (255, 255, 255),
               "blue": (90, 170, 255)}
OPT_COLORS = [(255, 122, 26), (56, 140, 255)]


def wrap_text(d, text, f, max_w):
    lines, line = [], []
    for w in text.split():
        if line and d.textlength(" ".join(line + [w]), font=f) > max_w:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return lines


def _fade(img, a):
    if a >= 0.999:
        return img
    arr = np.array(img)
    arr[..., 3] = (arr[..., 3] * max(0.0, a)).astype(np.uint8)
    return Image.fromarray(arr)


def _box(w, h, fill=(10, 12, 16, 228), outline=None, radius=24):
    im = Image.new("RGBA", (int(w) + 40, int(h) + 40), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([20, 26, 20 + w, 26 + h], radius=radius, fill=(0, 0, 0, 120))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    ImageDraw.Draw(im).rounded_rectangle([20, 20, 20 + w, 20 + h], radius=radius, fill=fill,
                                         outline=outline, width=2 if outline else 0)
    return im


def overlay_sprite(o, tl):
    """Immagine del riquadro o al tempo tl (secondi da quando compare)."""
    kind = o.get("type", "card")
    dd = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    if kind == "card":
        fl, fv = font(FONT_BOLD, 24), font(FONT_BLACK, 56)
        label, value = o.get("label", "").upper(), o.get("value", "").upper()
        vlines = wrap_text(dd, value, fv, 880)
        w = max([dd.textlength(label, font=fl)] + [dd.textlength(x, font=fv) for x in vlines]) + 70
        h = (40 if label else 16) + 66 * len(vlines) + 22
        im = _box(w, h)
        d = ImageDraw.Draw(im)
        col = CARD_COLORS.get(o.get("color", "yellow"), CARD_COLORS["yellow"])
        y = 20 + 14
        if label:
            d.text((20 + w / 2 - d.textlength(label, font=fl) / 2, y), label, font=fl, fill=(225, 228, 235, 255))
            y += 36
        for x in vlines:
            d.text((20 + w / 2 - d.textlength(x, font=fv) / 2, y), x, font=fv, fill=col + (255,))
            y += 66
        return im
    if kind == "tag":
        f = font(FONT_BLACK, 50)
        txt = o.get("text", "").upper()
        tw = dd.textlength(txt, font=f)
        im = Image.new("RGBA", (int(tw) + 60, 100), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((30, 18), txt, font=f, fill=(255, 205, 60, 255), stroke_width=7,
                                stroke_fill=(0, 0, 0, 255))
        return im
    if kind in ("dm", "comment", "say"):
        w = 940
        ft = font(FONT_BOLD, 36)
        full = o.get("text", "")
        n = int(len(full) * min(1.0, tl / max(0.4, min(len(full) * 0.035, o.get("_span", 3) * 0.7))))
        shown = full[:n]
        lines = wrap_text(dd, full, ft, w - 60)
        # righe calcolate sul testo completo, cosi' il riquadro non cambia misura
        out_lines, used = [], 0
        for ln in lines:
            take = max(0, min(len(ln), n - used))
            out_lines.append(ln[:take])
            used += len(ln) + 1
        head = 96 if kind == "comment" else 52
        if kind == "say" and not o.get("from"):
            head = 22
        h = head + 46 * len(lines) + 24
        im = _box(w, h)
        d = ImageDraw.Draw(im)
        if kind in ("dm", "say"):
            fh = font(FONT_BOLD, 22)
            if kind == "say":
                head_txt = o.get("from", "")
            elif o.get("outgoing"):
                head_txt = "YOU → " + o.get("from", "")
            else:
                head_txt = "NEW MESSAGE · " + o.get("from", "")
            if head_txt:
                d.text((20 + 30, 20 + 16), head_txt.upper(), font=fh,
                       fill=(255, 205, 60, 255) if kind == "say" else GREEN + (255,))
        else:
            fh, fu = font(FONT_BOLD, 20), font(FONT_BLACK, 34)
            d.text((50, 34), "MOST LIKED COMMENT", font=fh, fill=GREEN + (255,))
            d.text((50, 60), "@" + o.get("user", "").lstrip("@"), font=fu, fill=(255, 255, 255, 255))
            team = ("TEAM " + o.get("team", "")).upper()
            fp = font(FONT_BLACK, 20)
            pw = d.textlength(team, font=fp) + 28
            d.rounded_rectangle([20 + w - 30 - pw, 32, 20 + w - 30, 66], radius=17, fill=GREEN + (255,))
            d.text((20 + w - 30 - pw + 14, 38), team, font=fp, fill=(8, 30, 14, 255))
            lk = f"♥ {o.get('likes', 0)}"
            fk = font(FONT_BOLD, 26)
            d.text((20 + w - 30 - d.textlength(lk, font=fk), 72), lk, font=fk, fill=(255, 80, 90, 255))
        y = 20 + head
        for ln in out_lines:
            d.text((50, y), ln, font=ft, fill=(255, 255, 255, 255))
            y += 46
        if n < len(full) and int(tl * 3) % 2 == 0:  # cursore
            d.rectangle([50 + d.textlength(out_lines[-1] if out_lines else "", font=ft) + 4, y - 42,
                         50 + d.textlength(out_lines[-1] if out_lines else "", font=ft) + 8, y - 8],
                        fill=(255, 255, 255, 200))
        return im
    if kind == "votes":
        res = o.get("results", [])
        w, h = 900, 66 + 74 * len(res) + 10
        im = _box(w, h)
        d = ImageDraw.Draw(im)
        fh, fo, fp = font(FONT_BOLD, 22), font(FONT_BLACK, 30), font(FONT_BLACK, 30)
        title = o.get("title", "THE VOTES ARE IN").upper()
        d.text((20 + w / 2 - d.textlength(title, font=fh) / 2, 20 + 16), title, font=fh, fill=(255, 205, 60, 255))
        p = ease_out(tl / 1.4)
        y = 20 + 60
        for j, (lab, pct) in enumerate(res):
            col = OPT_COLORS[j % 2]
            d.rounded_rectangle([50, y, 20 + w - 30, y + 58], radius=12, fill=(40, 44, 52, 255))
            fill_w = (w - 60) * pct / 100 * p
            if fill_w > 6:
                d.rounded_rectangle([50, y, 50 + fill_w, y + 58], radius=12, fill=col + (255,))
            d.text((68, y + 11), str(lab).upper(), font=fo, fill=(255, 255, 255, 255))
            pt = f"{int(round(pct * p))}%"
            d.text((20 + w - 50 - d.textlength(pt, font=fp), y + 11), pt, font=fp, fill=(255, 255, 255, 255))
            y += 74
        return im
    if kind == "alert":
        f = font(FONT_BLACK, 62)
        lines = wrap_text(dd, o.get("text", "").upper(), f, 960)
        w = max(dd.textlength(x, font=f) for x in lines) + 40
        im = Image.new("RGBA", (int(w) + 40, 76 * len(lines) + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        y = 20
        for x in lines:
            d.text((20 + (w - d.textlength(x, font=f)) / 2, y), x, font=f, fill=(255, 64, 64, 255), stroke_width=8,
                   stroke_fill=(20, 0, 0, 255))
            y += 76
        return im
    return None


class Overlays:
    def __init__(self, items, dur):
        self.items = []
        for o in items or []:
            a = o.get("at", 0.0)
            u = o.get("until", 1.0)
            t0 = a * dur if a <= 1 else a
            t1 = u * dur if u <= 1 else u
            o = dict(o, _span=max(0.5, t1 - t0))
            self.items.append((t0, t1, o))
        self.cache = {}

    def draw(self, ov, t, y_top):
        y = y_top
        for idx, (t0, t1, o) in enumerate(self.items):
            if not (t0 <= t < t1 + 1e-6):
                continue
            if o.get("type") == "tag":
                sp = self.cache.get(idx) or overlay_sprite(o, 0)
                self.cache[idx] = sp
                tl = t - t0
                sp2 = _fade(sp, min(1.0, tl / 0.15))
                ov.alpha_composite(sp2, (int(W / 2 - sp.width / 2), int(H * 0.5 - 30 * (1 - ease_out(tl / 0.3)))))
                continue
            tl = t - t0
            static = o.get("type", "card") in ("card", "alert")
            if static and idx in self.cache:
                sp = self.cache[idx]
            else:
                sp = overlay_sprite(o, tl)
                if static:
                    self.cache[idx] = sp
            if sp is None:
                continue
            s = 0.82 + 0.18 * ease_back(tl / 0.28)
            a = min(1.0, tl / 0.12, (t1 - t) / 0.12 if t1 - t < 0.12 else 1.0)
            if o.get("type") == "alert":
                s *= 1 + 0.03 * math.sin(t * 18)
            if abs(s - 1) > 1e-3:
                sp = sp.resize((max(1, int(sp.width * s)), max(1, int(sp.height * s))), Image.BICUBIC)
            sp = _fade(sp, a)
            ov.alpha_composite(sp, (int(W / 2 - sp.width / 2), int(y - (sp.height * (s - 1)) / 2)))
            y += int(sp.height / max(s, 0.01)) - 14


def word_sprite(word, hl, size=104):
    f = font(FONT_BLACK, size)
    dd = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    while dd.textlength(word, font=f) > W - 110 and size > 50:
        size -= 6
        f = font(FONT_BLACK, size)
    tw = dd.textlength(word, font=f)
    im = Image.new("RGBA", (int(tw) + 60, int(size * 1.45)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = {"money": GREEN, "alert": (255, 72, 72), True: (255, 205, 60)}.get(hl, (255, 255, 255))
    d.text((30, 14), word, font=f, fill=col + (255,), stroke_width=11, stroke_fill=(0, 0, 0, 255))
    sh = np.array(im)
    sh[..., :3] = 0
    sh[..., 3] = (sh[..., 3] * 0.55).astype(np.uint8)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(Image.fromarray(sh).filter(ImageFilter.GaussianBlur(8)), (0, 6))
    out.alpha_composite(im)
    return out


def cta_full(cta, t, ep):
    """Schermata finale del voto, stile 'tu cosa faresti?'."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    opts = cta.get("options", [])
    opts = [o if isinstance(o, dict) else {"label": o, "sub": ""} for o in opts]
    nxt = ep["episode"] + 1
    lab = [o["label"].upper() for o in opts]
    if cta.get("finale"):
        head = cta.get("header", "THE FINALE").upper()
        sub = cta.get("subheader", "THANK YOU TO EVERYONE WHO VOTED").upper()
    else:
        head = cta.get("header", f"COMMENT {' OR '.join(lab)}").upper()
        sub = cta.get("subheader", f"THE MOST LIKED COMMENT ENTERS PART {nxt}").upper()
    fh, fs = font(FONT_BLACK, 30), font(FONT_BOLD, 20)
    bw = max(d.textlength(head, font=fh), d.textlength(sub, font=fs)) + 60
    y = 470
    d.rounded_rectangle([W / 2 - bw / 2, y, W / 2 + bw / 2, y + 92], radius=18, fill=(0, 0, 0, 215))
    d.text((W / 2 - d.textlength(head, font=fh) / 2, y + 14), head, font=fh, fill=(255, 255, 255, 255))
    d.text((W / 2 - d.textlength(sub, font=fs) / 2, y + 56), sub, font=fs, fill=(255, 205, 60, 255))
    q = cta.get("title", "WHAT WOULD YOU DO?").upper()
    fq = font(FONT_BLACK, 80)
    while d.textlength(q, font=fq) > W - 80:
        fq = font(FONT_BLACK, fq.size - 4)
    y += 130
    d.text((W / 2 - d.textlength(q, font=fq) / 2, y), q, font=fq, fill=(255, 255, 255, 255), stroke_width=9,
           stroke_fill=(0, 0, 0, 255))
    y += 130
    bwid, bh = 860, 150
    fo, fsub, fr = font(FONT_BLACK, 70), font(FONT_BLACK, 21), font(FONT_BLACK, 36)
    for j, o in enumerate(opts[:2]):
        ph = 2 * math.pi * (t / 1.2) + j * math.pi
        s = 1 + 0.035 * (0.5 + 0.5 * math.sin(ph))
        col = OPT_COLORS[j]
        card = Image.new("RGBA", (bwid + 80, bh + 80), (0, 0, 0, 0))
        g = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle([40, 40, 40 + bwid, 40 + bh], radius=22,
                                            fill=col + (int(90 + 90 * (0.5 + 0.5 * math.sin(ph))),))
        card.alpha_composite(g.filter(ImageFilter.GaussianBlur(18)))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle([40, 40, 40 + bwid, 40 + bh], radius=22, fill=col + (255,),
                             outline=(0, 0, 0, 255), width=4)
        L = o["label"].upper()
        f2 = fo
        while cd.textlength(L, font=f2) > bwid - 60:
            f2 = font(FONT_BLACK, f2.size - 4)
        cd.text((40 + bwid / 2 - cd.textlength(L, font=f2) / 2, 40 + 18), L, font=f2, fill=(16, 18, 34, 255))
        sb = o.get("sub", "").upper()
        if sb:
            f3 = fsub
            while cd.textlength(sb, font=f3) > bwid - 40:
                f3 = font(FONT_BLACK, f3.size - 1)
            cd.text((40 + bwid / 2 - cd.textlength(sb, font=f3) / 2, 40 + bh - 40), sb, font=f3,
                    fill=(16, 18, 34, 230))
        card = card.resize((int(card.width * s), int(card.height * s)), Image.BICUBIC)
        im.alpha_composite(card, (int(W / 2 - card.width / 2), int(y + bh / 2 + 40 - card.height / 2 - 40)))
        y += bh + 10
        if j == 0:
            d.text((W / 2 - d.textlength("OR", font=fr) / 2, y), "OR", font=fr, fill=(255, 205, 60, 255),
                   stroke_width=5, stroke_fill=(0, 0, 0, 255))
            y += 50
    y += 14
    foot = cta.get("footer", f"TOMORROW · PART {nxt}").upper()
    ff = font(FONT_BLACK, 30)
    d.text((W / 2 - d.textlength(foot, font=ff) / 2, y), foot, font=ff, fill=(255, 205, 60, 255), stroke_width=5,
           stroke_fill=(0, 0, 0, 255))
    fans = cta.get("fans") or []
    if fans:
        y += 52
        line = "TOP FANS: " + "  ·  ".join("@" + f.lstrip("@") for f in fans[:3])
        fz = font(FONT_BOLD, 26)
        while d.textlength(line, font=fz) > W - 80 and fz.size > 16:
            fz = font(FONT_BOLD, fz.size - 1)
        lw = d.textlength(line, font=fz)
        d.rounded_rectangle([W / 2 - lw / 2 - 22, y - 8, W / 2 + lw / 2 + 22, y + 40], radius=22,
                            fill=(214, 41, 118, 230))
        d.text((W / 2 - lw / 2, y), line, font=fz, fill=(255, 255, 255, 255))
    return im



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
def clean_narration(text):
    return text.replace("*", "")


def word_times(narr, voice, offset, dur):
    """Tempi di ogni parola: distribuiti sulla parte parlata dell'audio, in base alla lunghezza."""
    toks = narr.split()
    if not toks:
        return []
    if voice is not None and len(voice):
        env = np.abs(voice)
        win = int(SR * 0.02)
        n = len(env) // win
        e = env[: n * win].reshape(n, win).max(axis=1) if n else np.array([1.0])
        thr = max(0.02, e.max() * 0.06)
        on = np.where(e > thr)[0]
        s0 = (on[0] * win / SR) if len(on) else 0.0
        s1 = ((on[-1] + 1) * win / SR) if len(on) else len(voice) / SR
    else:
        s0, s1 = 0.0, max(0.5, dur - offset - 0.3)
    weights = []
    for tk in toks:
        c = tk.strip("*")
        wgt = len(c.strip(".,!?;:\"'")) + 2.0
        weights.append(wgt)
    pauses = [4.0 if tk.rstrip("*")[-1:] in ".!?" else (2.2 if tk.rstrip("*")[-1:] in ",;:" else 0.0)
              for tk in toks]
    total = sum(weights) + sum(pauses[:-1])
    out, acc = [], 0.0
    for j, tk in enumerate(toks):
        st = offset + s0 + (s1 - s0) * acc / total
        acc += weights[j]
        en_speech = offset + s0 + (s1 - s0) * acc / total
        acc += pauses[j] if j < len(toks) - 1 else 0
        hl = False
        raw = tk
        if raw.startswith("*") or raw.endswith("*") or raw.rstrip(".,!?;:").endswith("*"):
            hl = True
        word = raw.replace("*", "").upper()
        if "$" in word or any(ch.isdigit() for ch in word):
            hl = "money"
        out.append([st, en_speech, word, hl])
    for j in range(len(out) - 1):
        out[j][1] = out[j + 1][0]
    out[-1][1] = max(out[-1][1] + 0.35, min(dur - 0.05, out[-1][1] + 1.2))
    return out


def scene_job(job):
    i, scene, ep, ep_dir, tmp = job["i"], job["scene"], job["ep"], job["ep_dir"], job["tmp"]
    dur, v0, events, history, preview = job["dur"], job["v0"], job["events"], job["history"], job["preview"]
    src = cv2.cvtColor(cv2.imread(os.path.join(ep_dir, scene["image"])), cv2.COLOR_BGR2RGB)
    ih, iw = src.shape[:2]
    k = max(W / iw, H / ih)
    anim = scene.get("anim", {})
    movers = anim.get("movers", [])
    eyes = anim.get("blink", [])
    skins = [np.array(e[4], np.float32) if len(e) > 4 else lid_color(src, e) for e in eyes]
    eyes = [e[:4] for e in eyes]
    blinks = anim.get("blink_times", [0.9 + 3.1 * j + (0.4 if j % 2 else 0) for j in range(6)])
    blinks = [b - job.get("anim_offset", 0.0) for b in blinks] if job.get("anim_offset") else blinks
    cam = scene.get("camera", {})
    z0, z1 = cam.get("zoom", [1.06, 1.14] if i % 2 == 0 else [1.14, 1.07])
    fx, fy = cam.get("focus", [0.5, 0.5])
    pan = cam.get("pan", [0.0, 0.0])
    darken = scene.get("darken", 0.0)
    blur = scene.get("blur", 0)
    sup = scene.get("supporters")
    sup_assets = SupporterAssets(sup) if sup else None
    words_mode = ep.get("caption_mode") == "words"

    hud = Hud(ep, history)
    fxs = Fx(anim.get("fx"), seed=i * 17 + 3, src_point=anim.get("fx_point"))
    cap = scene.get("caption", "")
    cta = scene.get("cta")
    cap_im = caption_sprite(cap) if cap and not cta and not sup and not words_mode else None
    cap_pos = scene.get("caption_position", "bottom")
    glows = anim.get("glow", [])
    glow_sprites = [soft_circle(g[2], tuple(g[3]) + (255,), g[2] * 0.45) for g in glows]
    overlays = Overlays(scene.get("overlays"), dur)
    words = job.get("words") or []
    word_cache = {}
    word_y = 1500 if cta else 1430
    timer = job.get("timer")
    stats = job.get("stats") or {}
    t_anim0 = job.get("anim_offset", 0.0)

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

    out_path = os.path.join(tmp, f"s{i:03d}.tmp.mp4")
    proc = None
    if not preview:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
             "-pix_fmt", "yuv420p", "-r", str(FPS), out_path], stdin=subprocess.PIPE)
    frames_to_do = range(nframes) if not preview else [min(int(pt * FPS), nframes - 1) for pt in preview]
    last = ep["_nscenes"] - 1

    for f in frames_to_do:
        t = f / FPS
        ta = t + t_anim0  # tempo dell'animazione del personaggio (continua tra inquadrature uguali)
        scale, cx, cy = camera(t)
        X = cx + (gu - W / 2) / scale
        Y = cy + (gv - H / 2) / scale
        DX = np.zeros_like(X)
        DY = np.zeros_like(Y)
        for m in movers:
            dx, dy = mover_disp(m, X, Y, ta)
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
        for g, sp in zip(glows, glow_sprites):
            a = g[4] * (0.65 + 0.35 * math.sin(2 * math.pi * ta / g[5]))
            arr = np.array(sp)
            arr[..., 3] = (arr[..., 3] * a).astype(np.uint8)
            ox, oy = to_out(g[0], g[1])
            layer = Image.fromarray(arr)
            ov.alpha_composite(layer, (int(ox - layer.width / 2), int(oy - layer.height / 2)))
        fxs.draw(ov, ta, to_out)

        value = v0
        bump, glow = 0.0, 0.0
        for ev in events:
            arr_t = ev["t"] + ev["fly"]
            value += ev["amount"] * ease_out((t - arr_t) / ev["roll"])
            if t >= arr_t:
                x = t - arr_t
                bump = max(bump, math.exp(-x * 9) * math.sin(min(x * 14, math.pi)) if x < 0.5 else 0)
                glow = max(glow, max(0, 1 - x / 0.8))
        tmr = (timer[0], timer[1] - t, timer[2]) if timer else None
        flash = max(0.0, 1 - t / 0.9) if stats.get("_changed") else 0.0
        hud.draw(ov, value, bump, glow, stats=stats, stat_flash=flash, timer=tmr,
                 show_strip=job.get("show_strip", False))
        tx, ty = hud.counter_center
        pop_y = hud.content_top + 10
        for ev in events:
            x = (t - ev["t"]) / ev["fly"]
            src_pt = ev.get("from") or ([iw * 0.5, ih * 0.62] if ev["amount"] > 0 else None)
            if 0 <= x <= 1 and src_pt and ev["fly"] > 0.05:
                sx, sy = to_out(*src_pt)
                e = ease_in_out(x)
                px = sx + (tx - sx) * e
                py = sy + (ty - sy) * e - math.sin(math.pi * e) * 160
                cs = hud.coin_big if abs(ev["amount"]) >= 10 else hud.coin
                s = 1.0 - 0.35 * e
                sp = cs.resize((int(cs.width * s), int(cs.height * s)), Image.LANCZOS)
                ov.alpha_composite(sp, (int(px - sp.width / 2), int(py - sp.height / 2)))
            y = t - ev["t"] - ev["fly"]
            if 0 <= y <= 1.3:
                a = min(1, y * 6) * (1 - max(0, (y - 0.9) / 0.4))
                txt = ("+" if ev["amount"] >= 0 else "-") + money(abs(ev["amount"]))
                if ev.get("label"):
                    txt += "  " + ev["label"]
                fpl = font(FONT_BLACK, 52 if abs(ev["amount"]) < 10 else 60)
                pcol = GREEN if ev["amount"] >= 0 else (255, 72, 72)
                dd = ImageDraw.Draw(ov)
                tw = dd.textlength(txt, font=fpl)
                lay = Image.new("RGBA", (int(tw) + 40, 96), (0, 0, 0, 0))
                ImageDraw.Draw(lay).text((20, 10), txt, font=fpl, fill=pcol + (255,), stroke_width=7,
                                         stroke_fill=(0, 0, 0, 255))
                la = np.array(lay)
                la[..., 3] = (la[..., 3] * a).astype(np.uint8)
                px0 = min(max(tx - lay.width / 2, 20), W - 20 - lay.width)
                ov.alpha_composite(Image.fromarray(la), (int(px0), int(pop_y + 30 - 40 * ease_out(y))))
                pop_y += 0  # una per volta di solito

        if not cta and not sup:
            overlays.draw(ov, t, hud.content_top + (90 if any(
                0 <= t - ev["t"] - ev["fly"] <= 1.3 for ev in events) else 0))

        if cap_im is not None:
            x = (t - 0.25) / 0.35
            if x > 0:
                s = 0.6 + 0.4 * ease_back(x)
                a = min(1, x * 2.5)
                sp = cap_im if abs(s - 1) < 1e-3 else cap_im.resize(
                    (max(1, int(cap_im.width * s)), max(1, int(cap_im.height * s))), Image.BICUBIC)
                sp = _fade(sp, a)
                cyc = {"top": 640, "middle": H // 2}.get(cap_pos, H - 560)
                ov.alpha_composite(sp, (int((W - sp.width) / 2), int(cyc - sp.height / 2)))
        if sup_assets:
            sup_assets.draw(ov, t, scene.get("supporters_title", "TOP SUPPORTERS"),
                            scene.get("supporters_note", "TAG YOUR FRIENDS TO BE HERE NEXT TIME"))
        if cta:
            x = (t - 0.1) / 0.35
            if x > 0:
                if words_mode:
                    sp = cta_full(cta, t, ep)
                    ov.alpha_composite(_fade(sp, min(1, x)))
                else:
                    sp = cta_sprite(cta.get("title", "What's next?"),
                                    [o["label"] if isinstance(o, dict) else o for o in cta.get("options", [])], t)
                    s = 0.7 + 0.3 * ease_back(x)
                    sp = sp.resize((int(W * s), int(sp.height * s)), Image.BICUBIC)
                    ov.alpha_composite(sp, (int((W - sp.width) / 2), int(H * 0.56 - sp.height / 2)))

        for w0, w1, wd, hl in words:
            if w0 <= t < w1:
                key = (wd, hl)
                if key not in word_cache:
                    word_cache[key] = word_sprite(wd, hl)
                sp = word_cache[key]
                s = 1.16 - 0.16 * ease_out((t - w0) / 0.12)
                if s > 1.001:
                    sp = sp.resize((int(sp.width * s), int(sp.height * s)), Image.BICUBIC)
                ov.alpha_composite(sp, (int(W / 2 - sp.width / 2), int(word_y - sp.height / 2)))
                break

        o = np.asarray(ov)
        a = o[..., 3:4].astype(np.float32) / 255.0
        frame = (frame.astype(np.float32) * (1 - a) + o[..., :3].astype(np.float32) * a)
        fade = 1.0
        if i == 0:
            fade = min(fade, t / 0.2)
        if i == last:
            fade = min(fade, (dur - t) / 0.2)
        if not words_mode:
            fade = min(1.0, t / 0.18, (dur - t) / 0.12 if i == last else 1.0)
        if fade < 1:
            frame *= max(0.0, fade)
        frame = frame.astype(np.uint8)
        if preview:
            Image.fromarray(frame).save(os.path.join(tmp, f"preview_s{i + 1}_{t:.2f}.png"))
        else:
            proc.stdin.write(frame.tobytes())
    if proc:
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit(f"ffmpeg fallito nella scena {i + 1}")
        final = os.path.join(tmp, f"s{i:03d}.mp4")
        os.replace(out_path, final)
        print(f"  scena {i + 1}/{last + 1}: {scene['image']} ({dur:.1f}s)", flush=True)
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
            narr = clean_narration(s.get("narration", ""))
            if not preview:
                v = make_voice(narr, ep, os.path.join(tmp, f"v{i}"), ep_dir, i)
            d = float(s.get("duration", 1.2 if ep.get("caption_mode") == "words" else 3.5))
            if v is not None:
                d = max(d, len(v) / SR + ep.get("voice_delay", 0.2) + ep.get("scene_pad", 0.5))
            elif preview and narr:
                d = max(d, len(narr.split()) * 0.36 + 0.5)
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

        # 3) parole, timer, casa/lavoro, continuita' dell'animazione
        words_mode = ep.get("caption_mode") == "words"
        vd = ep.get("voice_delay", 0.2)
        timer_state = None
        stats_state = dict(ep.get("stats", {}))
        prev_img, anim_off = None, 0.0
        extra = []
        for i, s in enumerate(ep["scenes"]):
            tc = s.get("timer")
            if tc == "hide":
                timer_state = None
            elif isinstance(tc, dict):
                if "set" in tc:
                    timer_state = [tc.get("label", timer_state[0] if timer_state else "TIME LEFT"),
                                   float(tc["set"]), bool(tc.get("red", False))]
                elif timer_state:
                    timer_state[1] -= float(tc.get("jump", 0))
                    if "red" in tc:
                        timer_state[2] = bool(tc["red"])
                    if "label" in tc:
                        timer_state[0] = tc["label"]
            changed = []
            for kx, vx in (s.get("stats") or {}).items():
                if stats_state.get(kx) != vx:
                    changed.append(kx)
                stats_state[kx] = vx
            st = dict(stats_state, _changed=changed)
            anim_off = anim_off + durs[i - 1] if (i and s["image"] == prev_img) else 0.0
            prev_img = s["image"]
            extra.append({
                "words": word_times(s.get("narration", ""), voices[i], vd, durs[i]) if words_mode else [],
                "timer": tuple(timer_state) if timer_state else None,
                "stats": st, "anim_offset": anim_off,
                "show_strip": bool(s.get("show_episodes", i == 0 or s.get("cta"))),
            })
            if timer_state:
                timer_state[1] -= durs[i]

        jobs = []
        for i, s in enumerate(ep["scenes"]):
            pv = None
            if preview:
                pv = [pt for (si, pt) in preview if si == i + 1]
                if not pv:
                    continue
            jobs.append(dict(i=i, scene=s, ep=ep, ep_dir=ep_dir, tmp=tmp, dur=durs[i], v0=values[i],
                             events=all_events[i], history=history, preview=pv, **extra[i]))

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
        coin, cash, whoosh, pop, ding = sfx_coin(), sfx_cash(), sfx_whoosh(), sfx_pop(), sfx_ding()

        def add(clip, at, gain=1.0):
            s0 = int(at * SR)
            e = min(len(audio), s0 + len(clip))
            audio[s0:e] += clip[:e - s0] * gain

        off = 0.0
        for i, d in enumerate(durs):
            sc = ep["scenes"][i]
            if i > 0 and (not words_mode or sc.get("sfx") == "whoosh" or sc.get("cta")):
                add(whoosh, max(off - 0.15, 0))
            for o in sc.get("overlays") or []:
                at = o.get("at", 0.0)
                at = at * d if at <= 1 else at
                if o.get("type") != "tag":
                    add(ding if o.get("type") in ("dm", "comment", "say") else pop, off + at)
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
        # video finale sotto i 25 MB (limite del caricamento da browser su GitHub), qualita' adatta a Instagram
        cmd += ["-c:v", "libx264", "-preset", "slow", "-b:v", "2700k", "-maxrate", "3200k", "-bufsize", "6000k",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-shortest",
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
