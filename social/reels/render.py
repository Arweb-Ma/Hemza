"""Reeklam reels: 1080x1920, 30 fps, one encoded file per scene, joined with the concat demuxer.
Usage: python3 render.py [--frames]   (--frames: only draw check frames into check/)"""
import json, math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
BG, INK, MUTED, LINE = "#fbf6ef", "#1e1b18", "#6f665d", "#eadfd2"
ACCENT, ACCENT_SOFT, GREEN, GREEN_SOFT, SURFACE = "#c2562b", "#f6e2d7", "#1f5e4b", "#ddede6", "#ffffff"
INTER = "/usr/share/fonts/opentype/inter/Inter-{}.otf"

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
        _cache[key] = ImageFont.truetype(INTER.format(weight), size)
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
    for word in text.split():
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
    return (f"{v:,.0f}" if v == int(v) else f"{v:,.2f}").replace(",", " ") + " DH"

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
    d.rectangle((0, 0, int(W * clamp(t / dur)), 10), fill=ACCENT)

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

SCENES = {"hook": scene_hook, "deal": scene_deal, "versus": scene_versus, "tips": scene_tips, "end": scene_end}

# ------------------------------------------------------------------ output
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-g", "60", "-profile:v", "high", "-movflags", "+faststart"]

def render_scene(draw, dur, path, fade_in=0.25):
    tmp = path[:-4] + ".part.mp4"
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
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
    for k, sc in enumerate(reel["scenes"]):
        draw = SCENES[sc["type"]](sc)
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
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)
    print("wrote", out)

if __name__ == "__main__":
    reels = json.load(open(os.path.join(HERE, "reels.json"), encoding="utf-8"))
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    for r in reels:
        if not only or r["id"] in only:
            build(r, frames_only="--frames" in sys.argv)
