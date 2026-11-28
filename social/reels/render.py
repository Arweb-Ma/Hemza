"""Reeklam reels: 1080x1920, 30 fps, one encoded file per scene, joined with the concat demuxer.
Usage: python3 render.py [reel-id ...] [--frames]   (--frames: only draw check frames into check/)
Product photos: put them in img/ (see fetch_images.py); a scene whose photo is missing falls back to a price chart."""
import json, math, os, shutil, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
BG, INK, MUTED, LINE = "#fbf6ef", "#1e1b18", "#6f665d", "#eadfd2"
ACCENT, ACCENT_SOFT, GREEN, GREEN_SOFT, SURFACE = "#c2562b", "#f6e2d7", "#1f5e4b", "#ddede6", "#ffffff"
# Inter (OFL) is bundled in fonts/ so the script also runs on Windows; system copy as fallback
INTER = os.path.join(HERE, "fonts", "Inter-{}.otf")
INTER_SYS = "/usr/share/fonts/opentype/inter/Inter-{}.otf"

def ffmpeg_bin():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # pip install imageio-ffmpeg  (ships its own ffmpeg, handy on Windows)
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("ffmpeg introuvable : installe ffmpeg ou `pip install imageio-ffmpeg`")
FFMPEG = None

_cache = {}
def serif(size, weight=800, opsz=None):
    key = ("s", size, weight)
    if key not in _cache:
        f = ImageFont.truetype(os.path.join(HERE, "fonts/Fraunces.ttf"), size)
        f.set_variation_by_axes([min(144, opsz or size), weight, 50, 0])
        _cache[key] = f
    return _cache[key]

def sans(size, weight="SemiBold"):
    key = ("i", size, weight)
    if key not in _cache:
        path = INTER.format(weight)
        _cache[key] = ImageFont.truetype(path if os.path.exists(path) else INTER_SYS.format(weight), size)
    return _cache[key]

# ------------------------------------------------------------------ easing
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_back(x):
    x = clamp(x); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
def appear(t, start, dur=0.45): return ease_out((t - start) / dur)

def blend(c1, c2, a):
    c1 = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5)); c2 = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * a) for i in range(3))

# ------------------------------------------------------------------ drawing helpers
def wrap(d, text, font, width):
    lines, cur = [], ""
    for word in text.split(" "):  # split on plain spaces only, so "-29\u00a0%" stays together
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur); cur = word
    return lines + ([cur] if cur else [])

def text_in(img, xy, text, font, fill, a, dy=40, anchor="la"):
    """Text that fades and rises into place (a = 0..1)."""
    if a <= 0: return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0], xy[1] + (1 - a) * dy), text, font=font, fill=fill, anchor=anchor)
    if a < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    img.alpha_composite(layer)

def header(img, d, t):
    """Logo mark + wordmark, top of frame (below Instagram's top bar)."""
    x, y = 90, 210
    d.rounded_rectangle((x, y, x + 84, y + 84), 20, fill=INK)
    d.text((x + 40, y + 46), "r", font=serif(64, 800), fill=BG, anchor="mm")
    d.ellipse((x + 58, y + 52, x + 72, y + 66), fill=ACCENT)
    d.text((x + 108, y + 44), "reeklam", font=serif(52, 700), fill=INK, anchor="lm")
    d.text((W - 90, y + 44), "@reeklam.ma", font=sans(32, "Medium"), fill=MUTED, anchor="rm")

def footer_note(d, text):
    d.text((W // 2, 1500), text, font=sans(28, "Medium"), fill=MUTED, anchor="ma")

def dh(v):
    v = float(v)
    s = f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return s.replace(",", " ").replace(".", ",") + " DH"  # French: 1 439,50 DH

def strike(d, x1, x2, y, a, width=7, fill=ACCENT):
    if a > 0:
        d.line((x1 - 6, y, x1 - 6 + (x2 - x1 + 12) * a, y), fill=fill, width=width)

def badge(img, center, text, a, fill=ACCENT, size=180):
    if a <= 0: return
    s = max(0.01, ease_back(a))
    r = int(size / 2 * s)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cx, cy = center
    ld.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    if s > 0.5:
        ld.text((cx, cy + 4), text, font=serif(int(64 * s), 900), fill=BG, anchor="mm")
    img.alpha_composite(layer)

def chip(d, x, y, text, fill=INK, ink=BG, font=None):
    font = font or sans(34, "Bold")
    w = d.textlength(text, font=font)
    d.rounded_rectangle((x, y, x + w + 56, y + 68), 34, fill=fill)
    d.text((x + 28, y + 34), text, font=font, fill=ink, anchor="lm")
    return x + w + 56

def progress(d, t, dur):
    """Replaced by segments() drawn in build(); kept so the older scene code stays unchanged."""

# ------------------------------------------------------------------ scenes
def scene_hook(spec):
    lines, kicker, sub = spec["lines"], spec.get("kicker"), spec.get("sub")
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        y = 560
        if kicker:
            a = appear(t, 0.05)
            if a > 0:
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); chip(ImageDraw.Draw(layer), 90, y + (1 - a) * 30, kicker, ACCENT, BG)
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
            y += 120
        font = serif(112, 850)
        for i, line in enumerate(lines):
            colour = ACCENT if line.startswith("*") else INK
            text_in(img, (90, y), line.lstrip("*"), font, colour, appear(t, 0.25 + 0.18 * i))
            y += 132
        if sub:
            y += 30
            for j, line in enumerate(wrap(d, sub, sans(44, "Medium"), W - 180)):
                text_in(img, (90, y), line, sans(44, "Medium"), MUTED, appear(t, 0.35 + 0.18 * len(lines)))
                y += 60
        return img
    return draw

def scene_deal(spec):
    """One product: usual price struck through, new price counting down, real discount badge."""
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        # card
        a_card = appear(t, 0.0, 0.4)
        top = 470 + (1 - a_card) * 80
        d.rounded_rectangle((60, top, W - 60, top + 960), 48, fill=SURFACE, outline=LINE, width=3)
        x = 120
        if spec.get("count"):
            d.text((W - 120, top + 84), spec["count"], font=sans(34, "Medium"), fill=MUTED, anchor="rm")
        chip(d, x, top + 50, spec["brand"])
        y = top + 170
        for line in wrap(d, spec["title"], serif(64, 650, 72), W - 240)[:2]:
            text_in(img, (x, y), line, serif(64, 650, 72), INK, appear(t, 0.15))
            y += 80
        if spec.get("who"):
            text_in(img, (x, y + 6), spec["who"], sans(36, "Medium"), MUTED, appear(t, 0.2))
        # usual price
        y = top + 430
        label = spec.get("old_label", "Prix habituel")
        text_in(img, (x, y), label, sans(36, "Medium"), MUTED, appear(t, 0.35))
        old = dh(spec["old"])
        f_old = serif(84, 600, 84)
        a_old = appear(t, 0.4)
        text_in(img, (x, y + 52), old, f_old, MUTED, a_old)
        if a_old >= 1:
            w = d.textlength(old, font=f_old)
            strike(d, x, x + w, y + 52 + 52, ease_out((t - 0.95) / 0.35))
        # new price, counting down from old to new
        y2 = y + 185
        text_in(img, (x, y2), "Aujourd'hui", sans(36, "SemiBold"), ACCENT, appear(t, 1.05))
        k = ease_out((t - 1.15) / 0.9)
        if t >= 1.15:
            val = float(spec["old"]) + (float(spec["new"]) - float(spec["old"])) * k
            val = float(spec["new"]) if k >= 1 else round(val)
            d.text((x, y2 + 48), dh(val), font=serif(150, 900, 144), fill=INK)
        badge(img, (W - 230, y - 10 + 40), f"-{spec['pct']}%", (t - 1.9) / 0.45)
        # proof line
        if spec.get("proof"):
            a = appear(t, 2.2)
            if a > 0:
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
                yy = top + 862 + (1 - a) * 20
                f = sans(32, "SemiBold"); txt = spec["proof"]
                tw = ld.textlength(txt, font=f)
                ld.rounded_rectangle((x, yy, x + tw + 48, yy + 62), 31, fill=GREEN_SOFT)
                ld.text((x + 24, yy + 31), txt, font=f, fill=GREEN, anchor="lm")
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
        if spec.get("note"):
            for i, line in enumerate(wrap(d, spec["note"], sans(34, "Medium"), W - 180)):
                text_in(img, (90, top + 1000 + i * 48), line, sans(34, "Medium"), MUTED, appear(t, 2.5))
        progress(d, t, total)
        return img
    return draw

def scene_versus(spec):
    """Black Friday example: the announced discount vs what our price history shows."""
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        top = 470
        d.rounded_rectangle((60, top, W - 60, top + 430), 48, fill=SURFACE, outline=LINE, width=3)
        text_in(img, (120, top + 50), "Ce que la boutique affiche", sans(36, "SemiBold"), MUTED, appear(t, 0.05))
        text_in(img, (120, top + 110), spec["item"], serif(56, 650, 60), INK, appear(t, 0.15))
        f = serif(84, 600, 84); old = dh(spec["shown_old"])
        text_in(img, (120, top + 220), old, f, MUTED, appear(t, 0.3))
        w = d.textlength(old, font=f)
        if t > 0.75: strike(d, 120, 120 + w, top + 272, ease_out((t - 0.75) / 0.3))
        text_in(img, (120 + w + 50, top + 205), dh(spec["price"]), serif(110, 900, 110), INK, appear(t, 0.9))
        badge(img, (W - 190, top + 120), f"-{spec['shown_pct']}%", (t - 1.2) / 0.45, ACCENT, 170)
        # history
        top2 = top + 480
        a = appear(t, 2.0)
        d.rounded_rectangle((60, top2, W - 60, top2 + 520), 48, fill=INK if a > 0 else BG)
        text_in(img, (120, top2 + 50), spec["history_label"], sans(36, "SemiBold"), BG, a)
        n = spec["checks"]; bw = 120; gap = (W - 240 - n * bw) // (n - 1)
        for i in range(n):
            ai = appear(t, 2.3 + 0.2 * i, 0.35)
            if ai <= 0: continue
            bx = 120 + i * (bw + gap); hgt = 190 * ai
            d.rounded_rectangle((bx, top2 + 330 - hgt, bx + bw, top2 + 330), 14, fill=ACCENT)
            d.text((bx + bw / 2, top2 + 360), dh(spec["price"]).replace(" DH", ""), font=sans(34, "Bold"), fill=BG, anchor="ma")
        text_in(img, (120, top2 + 420), spec["verdict"], serif(62, 850, 64), "#f6c9b4", appear(t, 3.3))
        progress(d, t, total)
        return img
    return draw

def scene_tips(spec):
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        text_in(img, (90, 470), spec["title"], serif(92, 850, 96), INK, appear(t, 0.05))
        y = 640
        for i, tip in enumerate(spec["tips"]):
            a = appear(t, 0.4 + 0.9 * i)
            if a > 0:
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
                yy = y + (1 - a) * 40
                ld.ellipse((90, yy, 170, yy + 80), fill=ACCENT)
                ld.text((130, yy + 42), str(i + 1), font=serif(48, 900), fill=BG, anchor="mm")
                for j, line in enumerate(wrap(ld, tip, sans(46, "SemiBold"), W - 300)):
                    ld.text((210, yy + 4 + j * 60), line, font=sans(46, "SemiBold"), fill=INK)
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
            y += 230
        progress(d, t, total)
        return img
    return draw

def scene_end(spec):
    def draw(t, total):
        img = Image.new("RGBA", (W, H), INK); d = ImageDraw.Draw(img)
        cx, cy = W // 2, 640
        s = ease_back(clamp(t / 0.5))
        r = int(130 * s)
        if r > 2:
            d.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), int(r * 0.3), fill=BG)
            if s > 0.5:
                d.text((cx - 8 * s, cy + 12 * s), "r", font=serif(max(8, int(190 * s)), 850), fill=INK, anchor="mm")
                d.ellipse((cx + 50 * s, cy + 40 * s, cx + 90 * s, cy + 80 * s), fill=ACCENT)
        text_in(img, (cx, 860), "Les vraies promos", serif(96, 850, 96), BG, appear(t, 0.3), anchor="ma")
        text_in(img, (cx, 975), "des grandes marques au Maroc", serif(60, 500, 60), "#e9dccd", appear(t, 0.45), anchor="ma")
        y = 1140
        for i, line in enumerate(spec.get("cta", ["Enregistre ce reel", "Suis @reeklam.ma"])):
            a = appear(t, 0.8 + 0.25 * i)
            if a <= 0: continue
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
            f = sans(44, "Bold"); w = ld.textlength(line, font=f)
            fill, ink = (ACCENT, BG) if i == 1 else ("#2c2824", BG)
            yy = y + (1 - a) * 30
            ld.rounded_rectangle((cx - w / 2 - 44, yy, cx + w / 2 + 44, yy + 96), 48, fill=fill)
            ld.text((cx, yy + 48), line, font=f, fill=ink, anchor="mm")
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
            y += 128
        if spec.get("small"):
            text_in(img, (cx, 1440), spec["small"], sans(28, "Medium"), "#a99c8e", appear(t, 1.2), anchor="ma")
        return img
    return draw

# ------------------------------------------------------------------ v2: photos, charts, split stats
_photos = {}
def load_photo(rel):
    """Product photo from img/ (or any path relative to this folder); None when missing."""
    if not rel:
        return None
    if rel not in _photos:
        path = os.path.join(HERE, rel)
        try:
            im = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
            _photos[rel] = im
        except (OSError, ValueError):
            _photos[rel] = None
    return _photos[rel]

def rounded_paste(img, tile, xy, radius):
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile.size[0] - 1, tile.size[1] - 1), radius, fill=255)
    if tile.mode == "RGBA":
        mask = Image.composite(tile.getchannel("A"), Image.new("L", tile.size, 0), mask)
    img.paste(tile.convert("RGB"), (int(xy[0]), int(xy[1])), mask)

def photo_tile(photo, size, zoom=1.0, pad=0.06, bg=SURFACE):
    """Product photo 'contained' on a white tile (never cropped), with a gentle zoom."""
    tw, th = size
    tile = Image.new("RGBA", size, bg)
    iw, ih = photo.size
    s = min(tw * (1 - 2 * pad) / iw, th * (1 - 2 * pad) / ih) * zoom
    pw, ph = max(1, int(iw * s)), max(1, int(ih * s))
    pic = photo.resize((pw, ph), Image.LANCZOS)
    tile.alpha_composite(pic, ((tw - pw) // 2, (th - ph) // 2))
    return tile

def shadow(img, box, radius, a=1.0, blur=28, offset=18, strength=60):
    if a <= 0: return
    x1, y1, x2, y2 = box
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((x1, y1 + offset, x2, y2 + offset), radius, fill=(60, 40, 20, int(strength * a)))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))

def price_bars(img, box, history, t, start, big=False, on_dark=False, dates=True):
    """One bar per price check; bars at the old price in a muted tone, after the drop in accent."""
    x1, y1, x2, y2 = box
    d = ImageDraw.Draw(img)
    prices = [float(p) for _, p in history]
    hi, new = max(prices), prices[-1]
    n = len(prices)
    gap = 18 if big else 12
    bw = (x2 - x1 - gap * (n - 1)) / n
    base = y2 - (64 if big and dates else 0)
    top_room = 70 if big else 4
    full = base - y1 - top_room
    muted = "#cdbfae" if not on_dark else "#6b625a"
    for i, p in enumerate(prices):
        a = ease_out((t - start - 0.07 * i) / 0.35)
        if a <= 0: continue
        bx = x1 + i * (bw + gap)
        h = max(6, full * p / hi * a)
        fill = ACCENT if p < hi else muted
        d.rounded_rectangle((bx, base - h, bx + bw, base), 10 if big else 6, fill=fill)
    if big:
        f = sans(34, "Bold")
        for i in (0, n - 1):
            a = ease_out((t - start - 0.07 * i - 0.25) / 0.3)
            if a <= 0: continue
            bx = x1 + i * (bw + gap) + bw / 2
            h = full * prices[i] / hi
            text_in(img, (bx, base - h - 52), dh(prices[i]).replace(" DH", ""), f, INK if i == 0 else ACCENT, a, dy=12, anchor="ma")
            if dates:
                text_in(img, (bx, base + 18), history[i][0], sans(30, "Medium"), MUTED, a, dy=8, anchor="ma")

def scene_product(spec):
    """Product photo + price drop + mini price history. Without a photo, the history chart takes the photo's place."""
    photo = load_photo(spec.get("image"))
    hist = spec.get("history") or []
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        box = (90, 330, 990, 970)
        a = appear(t, 0.0, 0.5)
        dy = (1 - a) * 90
        b = (box[0], box[1] + dy, box[2], box[3] + dy)
        shadow(img, b, 44, a)
        if photo is not None:
            tile = photo_tile(photo, (box[2] - box[0], box[3] - box[1]), zoom=1.0 + 0.06 * clamp(t / total))
            if a < 1:
                tile.putalpha(tile.getchannel("A").point(lambda v: int(v * a)))
            rounded_paste(img, tile, b[:2], 44)
        else:
            d.rounded_rectangle(b, 44, fill=SURFACE, outline=LINE, width=3)
            if a > 0.6:
                label = spec.get("history_label", "Son prix à chaque relevé")
                if hist:
                    label += f" · {hist[0][0].replace(' nov.', '')} → {hist[-1][0]}"
                text_in(img, (b[0] + 60, b[1] + 130), label, sans(36, "SemiBold"), MUTED, appear(t, 0.2))
                if hist:
                    price_bars(img, (b[0] + 60, b[1] + 220, b[2] - 60, b[3] - 140), hist, t, 0.35, big=True, dates=False)
        d = ImageDraw.Draw(img)
        if a > 0.5:
            chip(d, b[0] + 36, b[1] + 36, spec["brand"])
            if spec.get("count"):
                f = sans(32, "Bold"); w = d.textlength(spec["count"], font=f)
                d.rounded_rectangle((b[2] - 36 - w - 48, b[1] + 36, b[2] - 36, b[1] + 104), 34, fill=(255, 255, 255), outline=LINE, width=2)
                d.text((b[2] - 36 - (w + 48) / 2, b[1] + 70), spec["count"], font=f, fill=INK, anchor="mm")
        badge(img, (box[2] - 110, box[3] - 30), f"-{spec['pct']}%", (t - 1.75) / 0.45, ACCENT, 210)
        # text block (left of Instagram's right-hand buttons)
        x, y = 90, 1005
        for line in wrap(d, spec["title"], serif(60, 700, 64), 860)[:2]:
            text_in(img, (x, y), line, serif(60, 700, 64), INK, appear(t, 0.2)); y += 70
        if spec.get("who"):
            text_in(img, (x, y + 8), spec["who"], sans(34, "Medium"), MUTED, appear(t, 0.25)); y += 56
        y += 18
        old = dh(spec["old"]); f_old = serif(62, 600, 64)
        a_old = appear(t, 0.4)
        text_in(img, (x, y), old, f_old, MUTED, a_old)
        w_old = d.textlength(old, font=f_old)
        if a_old >= 1:
            strike(d, x, x + w_old, y + 40, ease_out((t - 0.9) / 0.3), width=6)
        if spec.get("old_label"):
            text_in(img, (x + w_old + 30, y + 22), spec["old_label"], sans(32, "Medium"), MUTED, appear(t, 0.5))
        k = ease_out((t - 1.0) / 0.85)
        if t >= 1.0:
            val = float(spec["new"]) if k >= 1 else round(float(spec["old"]) + (float(spec["new"]) - float(spec["old"])) * k)
            d.text((x - 4, y + 78), dh(val), font=serif(124, 900, 128), fill=INK)
        # mini history under the price (only when the photo used the space above)
        if photo is not None and hist:
            ya = 1415
            text_in(img, (x, ya), spec.get("history_label", f"Son prix à nos {len(hist)} relevés"), sans(30, "SemiBold"), GREEN, appear(t, 2.05), dy=14)
            price_bars(img, (x, ya + 48, 880, ya + 118), hist, t, 2.1)
        elif spec.get("proof"):
            a = appear(t, 2.1)
            if a > 0:
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
                f = sans(32, "SemiBold"); tw = ld.textlength(spec["proof"], font=f); yy = 1415 + (1 - a) * 20
                ld.rounded_rectangle((x, yy, x + tw + 48, yy + 62), 31, fill=GREEN_SOFT)
                ld.text((x + 24, yy + 31), spec["proof"], font=f, fill=GREEN, anchor="lm")
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
        return img
    return draw

def scene_split(spec):
    """'Of the N items showing -X %…' : two counters that split the claim into what is real."""
    rows = spec["rows"]
    def draw(t, total):
        img = Image.new("RGBA", (W, H), BG); d = ImageDraw.Draw(img)
        header(img, d, t)
        y = 400
        for i, line in enumerate(spec["title"]):
            colour = ACCENT if line.startswith("*") else INK
            text_in(img, (90, y), line.lstrip("*"), serif(78, 850, 80), colour, appear(t, 0.05 + 0.12 * i)); y += 92
        y += 50
        for i, row in enumerate(rows):
            start = 0.5 + 1.1 * i
            a = appear(t, start, 0.45)
            if a <= 0:
                y += 400; continue
            fill, ink = (GREEN_SOFT, GREEN) if row.get("tone") == "good" else (ACCENT_SOFT, ACCENT)
            yy = y + (1 - a) * 50
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
            ld.rounded_rectangle((60, yy, 900, yy + 360), 44, fill=fill)
            k = ease_out((t - start - 0.1) / 0.8)
            ld.text((110, yy + 36), str(round(row["n"] * k)), font=serif(180, 900, 144), fill=ink)
            fx = sans(42, "Bold"); ty = yy + 70
            for line in wrap(ld, row["label"], fx, 420):
                ld.text((440, ty), line, font=fx, fill=INK); ty += 54
            for j, line in enumerate(wrap(ld, row.get("sub", ""), sans(34, "Medium"), 740)):
                ld.text((110, yy + 250 + j * 46), line, font=sans(34, "Medium"), fill=MUTED)
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a))); img.alpha_composite(layer)
            y += 400
        return img
    return draw

def scene_hook2(spec):
    """Hook with a fan of product photos below the headline (or a giant discount figure when no photo is there)."""
    photos = [p for p in (load_photo(r) for r in spec.get("images", [])) if p is not None]
    base = scene_hook(spec)
    def draw(t, total):
        img = base(t, total)
        if photos:
            n = len(photos[:3]); tw, th = 290, 350
            for i, ph in enumerate(photos[:3]):
                a = ease_back(clamp((t - 0.6 - 0.15 * i) / 0.5))
                if a <= 0: continue
                ang = (-8, 2, 9)[i] if n == 3 else (-6, 6)[i % 2]
                cx = W / 2 + (i - (n - 1) / 2) * 290
                cy = 1450 + (1 - a) * 320 + (0, -30, 10)[i % 3]
                tile = photo_tile(ph, (tw, th), pad=0.08)
                card = Image.new("RGBA", (tw + 24, th + 24), (255, 255, 255, 255))
                card.alpha_composite(tile, (12, 12))
                rot = card.rotate(ang, resample=Image.BICUBIC, expand=True)
                sh = Image.new("RGBA", rot.size, (0, 0, 0, 0))
                sh.putalpha(rot.getchannel("A").point(lambda v: int(v * 0.25)))
                sh = sh.filter(ImageFilter.GaussianBlur(14))
                img.alpha_composite(sh, (int(cx - rot.size[0] / 2), int(cy - rot.size[1] / 2 + 18)))
                img.alpha_composite(rot, (int(cx - rot.size[0] / 2), int(cy - rot.size[1] / 2)))
        elif spec.get("big"):
            a = appear(t, 0.7, 0.6)
            text_in(img, (W // 2, 1150), spec["big"], serif(400, 900, 144), ACCENT_SOFT, a, dy=80, anchor="ma")
        if spec.get("stamp"):
            a = (t - 1.3) / 0.4
            if a > 0:
                badge(img, (W - 200, 860), spec["stamp"], a, INK, 230)
        return img
    return draw

SCENES = {"hook": scene_hook, "deal": scene_deal, "versus": scene_versus, "tips": scene_tips, "end": scene_end,
          "hook2": scene_hook2, "product": scene_product, "split": scene_split}

def segments(img, k, n, t, dur):
    """Story-style progress: one segment per scene (end card excluded)."""
    d = ImageDraw.Draw(img)
    x1, x2, y, gap = 90, W - 90, 150, 12
    sw = (x2 - x1 - gap * (n - 1)) / n
    for i in range(n):
        sx = x1 + i * (sw + gap)
        d.rounded_rectangle((sx, y, sx + sw, y + 8), 4, fill=LINE)
        f = 1 if i < k else (clamp(t / dur) if i == k else 0)
        if f > 0:
            d.rounded_rectangle((sx, y, sx + max(8, sw * f), y + 8), 4, fill=ACCENT)

# ------------------------------------------------------------------ output
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-g", "60", "-profile:v", "high", "-movflags", "+faststart"]

def render_scene(draw, dur, path, fade_in=0.25):
    tmp = path[:-4] + ".part.mp4"
    p = subprocess.Popen([FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *ENC, tmp], stdin=subprocess.PIPE)
    n = int(round(dur * FPS))
    for i in range(n):
        t = i / FPS
        frame = draw(t, dur).convert("RGB")
        if t < fade_in:  # quick fade from the cream background, inside this file
            frame = Image.blend(Image.new("RGB", (W, H), BG), frame, ease_out(t / fade_in))
        p.stdin.write(frame.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit(f"ffmpeg failed on {path}")
    os.replace(tmp, path)

def build(reel, frames_only=False):
    os.makedirs(os.path.join(HERE, "parts"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "check"), exist_ok=True)
    parts = []
    n_seg = sum(1 for sc in reel["scenes"] if sc["type"] != "end")
    for k, sc in enumerate(reel["scenes"]):
        inner = SCENES[sc["type"]](sc)
        if sc["type"] != "end":
            def draw(t, total, inner=inner, k=k):
                img = inner(t, total); segments(img, k, n_seg, t, total); return img
        else:
            draw = inner
        if frames_only:
            t = sc.get("check_at", sc["dur"] - 0.2)
            draw(t, sc["dur"]).convert("RGB").save(os.path.join(HERE, "check", f"{reel['id']}-{k}.jpg"), quality=88)
            continue
        part = os.path.join(HERE, "parts", f"{reel['id']}-{k}.mp4")
        render_scene(draw, sc["dur"], part)
        parts.append(part)
    if frames_only:
        return
    lst = os.path.join(HERE, "parts", f"{reel['id']}.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    out = os.path.join(HERE, "out", f"{reel['id']}.mp4")
    tmp = out[:-4] + ".part.mp4"
    # silent AAC track so Instagram treats it as a normal video; add a trending sound in the app
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)
    print("wrote", out)

if __name__ == "__main__":
    FFMPEG = ffmpeg_bin() if "--frames" not in sys.argv else None
    reels = json.load(open(os.path.join(HERE, "reels.json"), encoding="utf-8"))
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    for r in reels:
        if not only or r["id"] in only:
            build(r, frames_only="--frames" in sys.argv)
