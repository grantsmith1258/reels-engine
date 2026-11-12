"""Reels Engine: animated map-explainer renderer.

Usage
  python3 engine/render.py STORYBOARD.json OUT.mp4 [--audio VO.mp3] [--preview 1.5,8,20]

Without --audio, each scene's line is voiced by the free Kokoro model.
With --audio (one voiceover file for the whole script, e.g. vidIQ's Liam voice),
scene boundaries are found from the pauses between paragraphs.
See STORYBOARD_GUIDE.md for the storyboard format.
"""
import json, math, os, sys, subprocess, re, hashlib
from multiprocessing import Pool
import numpy as np
import cairo
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
W, H, FPS = 1080, 1920, 30
SR = 44100
MAP_CY = H * 0.53
LEAD, TAIL = 0.20, 0.30   # Kokoro mode: silence around each line

PALETTE = {
    "ocean_top": (0.035, 0.07, 0.11), "ocean_bot": (0.055, 0.11, 0.16),
    "land": (0.17, 0.23, 0.30), "border": (0.27, 0.34, 0.42), "admin": (0.30, 0.38, 0.47),
    "accent": (1.0, 0.80, 0.18), "white": (1, 1, 1),
    "red": (0.75, 0.22, 0.17), "green": (0.18, 0.62, 0.36), "orange": (0.90, 0.49, 0.13),
    "blue": (0.23, 0.56, 0.85), "purple": (0.56, 0.35, 0.80), "teal": (0.10, 0.65, 0.65),
}


def hexc(h):
    if isinstance(h, (list, tuple)):
        return tuple(h)
    if h in PALETTE:
        return PALETTE[h]
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ---------------------------------------------------------------- geometry
def merc(lon, lat):
    lat = max(-85, min(85, lat))
    return lon, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def merc_arr(a):
    lat = np.clip(a[:, 1], -85, 85)
    return np.c_[a[:, 0], np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))]


def decimate(a, tol):
    if tol <= 0 or len(a) < 8:
        return a
    keep = [0]
    last = a[0]
    for i in range(1, len(a) - 1):
        if abs(a[i, 0] - last[0]) + abs(a[i, 1] - last[1]) >= tol:
            keep.append(i); last = a[i]
    keep.append(len(a) - 1)
    return a[keep]


def ring(a):
    return (a, a[:, 0].min(), a[:, 0].max(), a[:, 1].min(), a[:, 1].max())


def load_layer(fname, key, tol=0.0, only=None):
    gj = json.load(open(os.path.join(DATA, fname)))
    out = {}
    for f in gj["features"]:
        k = f["properties"].get(key)
        if only is not None and k not in only:
            continue
        g = f["geometry"]
        if not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            a = merc_arr(np.array(poly[0], dtype=float))
            a = decimate(a, tol)
            out.setdefault(k, []).append(ring(a))
    return out


def shape_rings(points):
    return [ring(merc_arr(np.array(points, dtype=float)))]


def path_points(points, straight=False, n=240):
    """Mercator polyline through lon/lat points: Catmull-Rom curve, or straight segments."""
    P = [np.array(merc(*p)) for p in points]
    if straight:
        out = []
        for a, b in zip(P[:-1], P[1:]):
            for t in np.linspace(0, 1, max(2, n // max(1, len(P) - 1)), endpoint=False):
                out.append(a + (b - a) * t)
        out.append(P[-1])
    else:
        if len(P) == 2:
            mid = (P[0] + P[1]) / 2
            d = P[1] - P[0]
            P = [P[0], mid + np.array([-d[1], d[0]]) * 0.18, P[1]]
        P = [P[0]] + P + [P[-1]]
        out = []
        segs = len(P) - 3
        per = max(8, n // segs)
        for i in range(segs):
            p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
            for k in range(per):
                t = k / per
                out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
        out.append(P[-2])
    a = np.array(out)
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(a, axis=0).T))]
    return a, d / max(d[-1], 1e-9)


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_io(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out(x):
    return 1 - (1 - clamp(x)) ** 3


def ease_back(x):
    x = clamp(x)
    return 1 + 2.9 * (x - 1) ** 3 + 1.9 * (x - 1) ** 2


def window(t, t_in, t_out, fin=0.35, fout=0.35):
    if t < t_in or t > t_out + fout:
        return 0.0
    a = ease_out((t - t_in) / fin)
    if t > t_out:
        a *= 1 - ease_out((t - t_out) / fout)
    return a


# ---------------------------------------------------------------- audio timing
def ffprobe_dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).decode().strip())


def load_audio(p):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"])
    return np.frombuffer(raw, np.float32).copy()


def scene_bounds_from_audio(mp3, texts, tail=0.6):
    """Split one voiceover into scenes at the pauses nearest each paragraph boundary."""
    D = ffprobe_dur(mp3)
    log = subprocess.run(["ffmpeg", "-i", mp3, "-af", "silencedetect=noise=-35dB:d=0.25", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    sil = [(s, e) for s, e in zip(starts, ends) if 0.3 < s < D - 0.3]
    lens = np.array([len(t) + 4 for t in texts], float)
    est = np.cumsum(lens)[:-1] / lens.sum() * D
    bounds, prev = [], 0.0
    for b in est:
        cands = [(abs((s + e) / 2 - b), s, e) for s, e in sil if (s + e) / 2 > prev + 0.8]
        cands = [c for c in cands if c[0] < 3.0]
        if cands:
            _, s, e = min(cands)
            cut = (s + e) / 2
        else:
            cut = max(b, prev + 0.8)
        bounds.append(cut); prev = cut
    scene_starts = [0.0] + bounds
    scene_ends = bounds + [D + tail]
    return scene_starts, scene_ends, D


# ---------------------------------------------------------------- timeline
class Timeline:
    def __init__(self, sb, starts, ends, speech):
        self.sb = sb
        self.scenes = sb["scenes"]
        self.starts, self.ends = starts, ends
        self.speech = speech          # (t0, t1) of speech per scene, for captions
        self.total = ends[-1] + sb.get("outro_hold", 0.0)
        self._merge()
        self.routes = []
        for i, s in enumerate(self.scenes):
            for kind in ("routes", "lines"):
                for r in s.get(kind, []):
                    pts, frac = path_points(r["points"], straight=(kind == "lines" or r.get("straight", False)))
                    self.routes.append(dict(pts=pts, frac=frac, color=hexc(r.get("color", "accent" if kind == "routes" else "white")),
                                            t0=self.starts[i] + r.get("delay", 0.5), dur=r.get("draw", 1.6 if kind == "routes" else 1.2),
                                            head=(kind == "routes"), width=r.get("width", 5 if kind == "routes" else 4),
                                            label=r.get("label"), label_at=r.get("label_at"),
                                            t_end=self.ends[i] if r.get("scene_only", kind == "lines") else None))

    def _merge(self):
        def spans(key):
            res = []
            for i, s in enumerate(self.scenes):
                v = s.get(key)
                items = v if isinstance(v, list) else ([v] if v else [])
                for item in items:
                    sig = json.dumps(item, sort_keys=True)
                    found = [r for r in res if r["sig"] == sig and r["last"] == i - 1]
                    if found:
                        found[0]["last"] = i; found[0]["t_out"] = self.ends[i]
                    else:
                        res.append(dict(sig=sig, item=item, first=i, last=i,
                                        t_in=self.starts[i] + item.get("delay", 0.25), t_out=self.ends[i]))
            return res
        self.highlights = spans("highlight")
        self.pins = spans("pins")
        self.labels = spans("labels")
        self.headlines = spans("headline")
        self.stamps = spans("stamp")
        self.words = spans("word")

    def scene_at(self, t):
        for i in range(len(self.scenes) - 1, -1, -1):
            if t >= self.starts[i]:
                return i
        return 0

    def camera(self, t):
        i = self.scene_at(t)
        cur = self.scenes[i]["camera"]
        prev = self.scenes[i - 1]["camera"] if i > 0 else cur
        move = self.scenes[i].get("camera_move", 1.1)
        k = ease_io((t - self.starts[i]) / move)
        (x0, y0), (x1, y1) = merc(*prev["center"]), merc(*cur["center"])
        z = math.exp(math.log(prev["zoom"]) * (1 - k) + math.log(cur["zoom"]) * k)
        z *= 1 + 0.035 * clamp((t - self.starts[i]) / max(0.1, self.ends[i] - self.starts[i]))
        return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, z


def make_captions(tl):
    """4-7 word ALL-CAPS phrases, broken at punctuation, timed by characters within each line's speech."""
    caps = []
    for i, s in enumerate(tl.scenes):
        words = s["vo"].split()
        chunks, cur = [], []
        for j, w in enumerate(words):
            cur.append(w)
            rest = len(words) - j - 1
            punct = re.search(r"[,.?!:;]$", w)
            if (len(cur) >= 4 and punct and rest != 1) or len(cur) >= 7 or (len(cur) >= 5 and rest in (0,)):
                chunks.append(cur); cur = []
        if cur:
            if chunks and len(cur) < 3 and len(chunks[-1]) + len(cur) <= 8:
                chunks[-1] += cur
            else:
                chunks.append(cur)
        texts = [" ".join(c) for c in chunks]
        t0, t1 = tl.speech[i]
        total = sum(len(c) + 3 for c in texts)
        out, t = [], t0
        for k, c in enumerate(texts):
            d = (t1 - t0) * (len(c) + 3) / total
            end = t + d if k < len(texts) - 1 else max(t + d, tl.ends[i] - 0.05)
            out.append((t, end, c.upper().rstrip(",;:")))
            t += d
        caps.append(out)
    tl.captions = caps


# ---------------------------------------------------------------- drawing
class Renderer:
    def __init__(self, tl):
        self.tl = tl
        sb = tl.sb
        self.lo = load_layer("ne_50m_admin_0_countries.geojson", "ADM0_A3")
        self.hi = load_layer("ne_10m_admin_0_countries.geojson", "ADM0_A3", tol=0.003)
        need = set()
        for s in sb["scenes"]:
            for h in s.get("highlight", []) or []:
                need.update(h.get("admin1", []))
        border_of = set(sb.get("admin1_borders", []))
        self.adm = {}
        self.adm_lines = []
        if need or border_of:
            gj = json.load(open(os.path.join(DATA, "ne_10m_admin_1_states_provinces.geojson")))
            for f in gj["features"]:
                p = f["properties"]; g = f["geometry"]
                if not g:
                    continue
                code = p.get("iso_3166_2")
                if code not in need and p.get("adm0_a3") not in border_of:
                    continue
                polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
                for poly in polys:
                    r = ring(decimate(merc_arr(np.array(poly[0], dtype=float)), 0.01))
                    if code in need:
                        self.adm.setdefault(code, []).append(r)
                    if p.get("adm0_a3") in border_of:
                        self.adm_lines.append(r)
        self.shapes = {}
        for s in sb["scenes"]:
            for h in s.get("highlight", []) or []:
                if h.get("shape"):
                    self.shapes[json.dumps(h["shape"])] = shape_rings(h["shape"])
        rng = np.random.default_rng(7)
        self.grain = []
        for _ in range(4):
            n = rng.random((H // 2, W // 2))
            a = np.zeros((H // 2, W // 2, 4), np.uint8)
            v = (n * 11).astype(np.uint8)
            a[..., 0] = a[..., 1] = a[..., 2] = v
            a[..., 3] = 11
            self.grain.append((cairo.ImageSurface.create_for_data(memoryview(a), cairo.FORMAT_ARGB32, W // 2, H // 2), a))

    def layer(self, cam):
        return self.hi if cam[2] >= 18 else self.lo

    def to_screen(self, cam, x, y):
        cx, cy, z = cam
        return W / 2 + (x - cx) * z, MAP_CY - (y - cy) * z

    def ring_path(self, ctx, cam, r):
        a, x0, x1, y0, y1 = r
        cx, cy, z = cam
        hw, hh = W / 2 / z + 1, H / z + 1
        if x1 < cx - hw or x0 > cx + hw or y1 < cy - hh or y0 > cy + hh:
            return
        if (x1 - x0) * z < 1.2 and (y1 - y0) * z < 1.2:
            return
        sx = W / 2 + (a[:, 0] - cx) * z
        sy = MAP_CY - (a[:, 1] - cy) * z
        ctx.move_to(sx[0], sy[0])
        for X, Y in zip(sx[1:].tolist(), sy[1:].tolist()):
            ctx.line_to(X, Y)
        ctx.close_path()

    def rings_for(self, it, cam):
        L = self.layer(cam)
        for iso in it.get("iso", []):
            yield from L.get(iso, [])
        for code in it.get("admin1", []):
            yield from self.adm.get(code, [])
        if it.get("shape"):
            yield from self.shapes[json.dumps(it["shape"])]

    def frame(self, t):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        ctx = cairo.Context(surf)
        cam = self.tl.camera(t)
        self.draw_base(ctx, cam)
        self.draw_highlights(ctx, cam, t)
        self.draw_routes(ctx, cam, t)
        self.draw_pins(ctx, cam, t)
        self.draw_labels(ctx, cam, t)
        self.draw_vignette(ctx)
        self.draw_headlines(ctx, t)
        self.draw_stamps(ctx, t)
        self.draw_words(ctx, t)
        self.draw_captions(ctx, t)
        ctx.save(); ctx.scale(2, 2); ctx.set_source_surface(self.grain[int(t * FPS) % 4][0], 0, 0); ctx.paint(); ctx.restore()
        return surf

    def draw_base(self, ctx, cam):
        g = cairo.LinearGradient(0, 0, 0, H)
        g.add_color_stop_rgb(0, *PALETTE["ocean_top"]); g.add_color_stop_rgb(1, *PALETTE["ocean_bot"])
        ctx.set_source(g); ctx.paint()
        cx, cy, z = cam
        step = 10 if z < 40 else 5 if z < 120 else 1
        ctx.set_line_width(1); ctx.set_source_rgba(1, 1, 1, 0.045)
        lo = int((cx - W / 2 / z) // step * step)
        for lon in range(lo, int(cx + W / 2 / z) + step + 1, step):
            X = W / 2 + (lon - cx) * z
            ctx.move_to(X, 0); ctx.line_to(X, H)
        for lat in range(-80, 81, step):
            Y = MAP_CY - (merc(0, lat)[1] - cy) * z
            if -5 < Y < H + 5:
                ctx.move_to(0, Y); ctx.line_to(W, Y)
        ctx.stroke()
        L = self.layer(cam)
        ctx.new_path()
        for rings in L.values():
            for r in rings:
                self.ring_path(ctx, cam, r)
        path = ctx.copy_path()
        ctx.save(); ctx.translate(0, 6); ctx.new_path(); ctx.append_path(path)
        ctx.set_source_rgba(0, 0, 0, 0.35); ctx.fill(); ctx.restore()
        ctx.new_path(); ctx.append_path(path)
        ctx.set_source_rgb(*PALETTE["land"]); ctx.fill_preserve()
        ctx.set_source_rgb(*PALETTE["border"]); ctx.set_line_width(1.4); ctx.set_line_join(cairo.LINE_JOIN_ROUND); ctx.stroke()
        if self.adm_lines and z >= 8:
            ctx.new_path()
            for r in self.adm_lines:
                self.ring_path(ctx, cam, r)
            ctx.set_source_rgba(*PALETTE["admin"], 0.7); ctx.set_line_width(1.0); ctx.set_dash([4, 4]); ctx.stroke(); ctx.set_dash([])

    def draw_highlights(self, ctx, cam, t):
        for h in self.tl.highlights:
            it = h["item"]
            a = window(t, h["t_in"], h["t_out"], 0.5, 0.5)
            residual = it.get("residual", 0.0)
            if t > h["t_out"] and residual:
                a = max(a, residual * ease_out((t - h["t_out"]) / 0.5))
            if a <= 0.001:
                continue
            col = hexc(it.get("color", "accent"))
            ctx.new_path()
            for r in self.rings_for(it, cam):
                self.ring_path(ctx, cam, r)
            path = ctx.copy_path()
            pulse = 1.0
            if it.get("pulse"):
                pulse = 0.75 + 0.25 * math.sin((t - h["t_in"]) * 4)
            for w_, al in ((26, 0.05), (16, 0.08), (9, 0.12)):
                ctx.new_path(); ctx.append_path(path)
                ctx.set_source_rgba(*col, al * a * pulse); ctx.set_line_width(w_); ctx.stroke()
            ctx.new_path(); ctx.append_path(path)
            ctx.set_source_rgba(*col, it.get("fill", 0.78) * a); ctx.fill_preserve()
            ctx.set_source_rgba(*[min(1, c * 1.3 + 0.15) for c in col], 0.95 * a); ctx.set_line_width(2.4); ctx.stroke()
            if it.get("label") and t <= h["t_out"] + 0.5:
                la = window(t, h["t_in"] + 0.35, h["t_out"], 0.35, 0.35)
                X, Y = self.to_screen(cam, *merc(*it["label_at"]))
                self.text(ctx, it["label"], X, Y, 46 * it.get("label_scale", 1), "Anton", (1, 1, 1), la,
                          spacing=4, outline=(0, 0, 0, 0.6), pop=ease_back((t - h["t_in"] - 0.35) / 0.4))

    def draw_routes(self, ctx, cam, t):
        for r in self.tl.routes:
            if t < r["t0"]:
                continue
            fade = 1.0
            if r["t_end"] is not None:
                fade = 1 - ease_out((t - r["t_end"]) / 0.4) if t > r["t_end"] else 1.0
                if fade <= 0:
                    continue
            p = ease_io((t - r["t0"]) / r["dur"])
            n = max(2, int(np.searchsorted(r["frac"], p)))
            pts = r["pts"][:n]
            sx = W / 2 + (pts[:, 0] - cam[0]) * cam[2]
            sy = MAP_CY - (pts[:, 1] - cam[1]) * cam[2]
            ctx.new_path(); ctx.move_to(sx[0], sy[0])
            for X, Y in zip(sx[1:].tolist(), sy[1:].tolist()):
                ctx.line_to(X, Y)
            path = ctx.copy_path()
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            for w_, al in ((r["width"] * 3.6, 0.07), (r["width"] * 2, 0.12)):
                ctx.new_path(); ctx.append_path(path); ctx.set_source_rgba(*r["color"], al * fade); ctx.set_line_width(w_); ctx.stroke()
            ctx.new_path(); ctx.append_path(path)
            ctx.set_dash([14, 10], -t * 40); ctx.set_line_width(r["width"]); ctx.set_source_rgba(*r["color"], fade); ctx.stroke()
            ctx.set_dash([])
            if r["head"] and p < 1:
                X, Y = sx[-1], sy[-1]
                for rad, al in ((22, 0.15), (13, 0.35)):
                    ctx.arc(X, Y, rad, 0, 2 * math.pi); ctx.set_source_rgba(*r["color"], al); ctx.fill()
                ctx.arc(X, Y, 7, 0, 2 * math.pi); ctx.set_source_rgb(1, 1, 1); ctx.fill()
            if r["label"] and p > 0.6:
                X, Y = self.to_screen(cam, *merc(*r["label_at"]))
                self.text(ctx, r["label"], X, Y, 38, "Anton", r["color"], ease_out((p - 0.6) / 0.4) * fade, spacing=3, outline=(0, 0, 0, 0.6))

    def draw_pins(self, ctx, cam, t):
        for h in self.tl.pins:
            it = h["item"]
            a = window(t, h["t_in"], h["t_out"])
            if a <= 0:
                continue
            X, Y = self.to_screen(cam, *merc(*it["at"]))
            col = hexc(it.get("color", "white"))
            ph = ((t - h["t_in"]) % 1.4) / 1.4
            ctx.arc(X, Y, 12 + 34 * ph, 0, 2 * math.pi); ctx.set_source_rgba(*col, 0.5 * (1 - ph) * a); ctx.set_line_width(3); ctx.stroke()
            s = ease_back((t - h["t_in"]) / 0.4)
            ctx.arc(X, Y, 11 * s, 0, 2 * math.pi); ctx.set_source_rgba(*col, a); ctx.fill()
            ctx.arc(X, Y, 5 * s, 0, 2 * math.pi); ctx.set_source_rgba(0.05, 0.08, 0.12, a); ctx.fill()
            if it.get("label"):
                side = it.get("side", "right")
                dx = {"right": 30, "left": -30}.get(side, 0)
                dy = {"above": -42, "below": 42}.get(side, 0)
                self.text(ctx, it["label"], X + dx, Y + dy, it.get("size", 40), "Anton", (1, 1, 1), a,
                          align="left" if side == "right" else "right" if side == "left" else "center", spacing=3, outline=(0, 0, 0, 0.6))

    def draw_labels(self, ctx, cam, t):
        for h in self.tl.labels:
            it = h["item"]
            a = window(t, h["t_in"], h["t_out"])
            if a <= 0:
                continue
            X, Y = self.to_screen(cam, *merc(*it["at"]))
            self.text(ctx, it["text"], X, Y, it.get("size", 40), "Anton", hexc(it.get("color", "white")), a * it.get("alpha", 1),
                      spacing=it.get("spacing", 4), outline=(0, 0, 0, 0.6), pop=0.9 + 0.1 * ease_back((t - h["t_in"]) / 0.35))

    def draw_vignette(self, ctx):
        g = cairo.RadialGradient(W / 2, H * 0.52, H * 0.25, W / 2, H * 0.52, H * 0.75)
        g.add_color_stop_rgba(0, 0, 0, 0, 0); g.add_color_stop_rgba(1, 0, 0, 0, 0.6)
        ctx.set_source(g); ctx.paint()
        for y0, y1, flip in ((0, 560, False), (1240, H, True)):
            g = cairo.LinearGradient(0, y0, 0, y1)
            g.add_color_stop_rgba(1 if flip else 0, 0.02, 0.04, 0.07, 0.85)
            g.add_color_stop_rgba(0 if flip else 1, 0.02, 0.04, 0.07, 0)
            ctx.rectangle(0, y0, W, y1 - y0); ctx.set_source(g); ctx.fill()

    def text(self, ctx, s, x, y, size, font, col, alpha, align="center", spacing=0, outline=None, pop=1.0, maxw=None, weight=cairo.FONT_WEIGHT_NORMAL, stroke_k=0.12):
        if alpha <= 0:
            return
        ctx.save()
        ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, weight)
        ctx.set_font_size(size)
        widths = [ctx.text_extents(ch).x_advance for ch in s]
        tw = sum(widths) + spacing * (len(s) - 1)
        if maxw and tw > maxw:
            k = maxw / tw; size *= k; ctx.set_font_size(size)
            widths = [w * k for w in widths]; spacing *= k; tw = maxw
        asc = ctx.font_extents()[0] * 0.72
        x0 = x - tw / 2 if align == "center" else (x if align == "left" else x - tw)
        ctx.translate(x, y); ctx.scale(pop, pop); ctx.translate(-x, -y)
        cx = x0
        for ch, w in zip(s, widths):
            ctx.move_to(cx, y + asc / 2); ctx.text_path(ch); cx += w + spacing
        if outline:
            ctx.set_source_rgba(*outline[:3], outline[3] * alpha); ctx.set_line_width(size * stroke_k)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND); ctx.stroke_preserve()
        ctx.set_source_rgba(*col, alpha); ctx.fill()
        ctx.restore()

    def draw_headlines(self, ctx, t):
        for h in self.tl.headlines:
            y = h["item"].get("y", 520)
            for j, ln in enumerate(h["item"]["lines"]):
                ti = h["t_in"] + j * 0.22 + ln.get("delay", 0)
                a = window(t, ti, h["t_out"], 0.3, 0.3)
                size = ln.get("size", 84)
                self.text(ctx, ln["text"], W / 2, y, size, "Anton", hexc(ln.get("color", "white")), a,
                          spacing=2, outline=(0, 0, 0, 0.5), pop=0.85 + 0.15 * ease_back((t - ti) / 0.35), maxw=980)
                y += size * 1.12

    def draw_stamps(self, ctx, t):
        for h in self.tl.stamps:
            it = h["item"]
            ti = h["t_in"]
            a = window(t, ti, h["t_out"], 0.12, 0.3)
            if a <= 0:
                continue
            s = 1.7 - 0.7 * ease_out((t - ti) / 0.22)
            x, y = it.get("x", 830), it.get("y", 640)
            ctx.save(); ctx.translate(x, y); ctx.rotate(math.radians(-9)); ctx.scale(s, s)
            col = hexc(it.get("color", "#e8483b"))
            ctx.select_font_face("Anton"); ctx.set_font_size(64)
            tw = ctx.text_extents(it["text"]).x_advance
            bw, bh = tw + 60, 104
            ctx.rectangle(-bw / 2, -bh / 2, bw, bh); ctx.set_source_rgba(*col, 0.92 * a); ctx.set_line_width(7); ctx.stroke()
            ctx.rectangle(-bw / 2 + 10, -bh / 2 + 10, bw - 20, bh - 20); ctx.set_source_rgba(*col, 0.18 * a); ctx.fill()
            ctx.move_to(-tw / 2, 24); ctx.text_path(it["text"]); ctx.set_source_rgba(*col, a); ctx.fill()
            ctx.restore()

    def draw_words(self, ctx, t):
        for h in self.tl.words:
            it = h["item"]
            a = window(t, h["t_in"], h["t_out"], 0.25, 0.3)
            if a <= 0:
                continue
            pop = 0.7 + 0.3 * ease_back((t - h["t_in"]) / 0.4)
            y = it.get("y", 1440)
            if it.get("sub"):
                self.text(ctx, it["sub"], W / 2, y - it.get("size", 150) * 0.7, 40, "Inter", (0.75, 0.82, 0.9), a, spacing=6,
                          weight=cairo.FONT_WEIGHT_BOLD)
            self.text(ctx, it["text"], W / 2, y, it.get("size", 150), "Anton", hexc(it.get("color", "accent")), a,
                      spacing=6, outline=(0, 0, 0, 0.55), pop=pop, maxw=1000)

    def draw_captions(self, ctx, t):
        i = self.tl.scene_at(t)
        for c0, c1, txt in self.tl.captions[i]:
            if c0 <= t < c1:
                pop = 0.92 + 0.08 * ease_back((t - c0) / 0.18)
                ctx.select_font_face("Inter", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); ctx.set_font_size(64)
                lines = [txt]
                if ctx.text_extents(txt).x_advance > 940:
                    ws = txt.split()
                    best = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:]))))
                    lines = [" ".join(ws[:best]), " ".join(ws[best:])]
                y = 300 if len(lines) == 1 else 262
                for ln in lines:
                    self.text(ctx, ln, W / 2, y, 64, "Inter", (1, 1, 1), 1.0, outline=(0, 0, 0, 0.95),
                              pop=pop, maxw=960, weight=cairo.FONT_WEIGHT_BOLD, stroke_k=0.16)
                    y += 78


# ---------------------------------------------------------------- audio
def kokoro_voice(sb, cache_dir):
    from kokoro_onnx import Kokoro
    k = None
    os.makedirs(cache_dir, exist_ok=True)
    clips = []
    for s in sb["scenes"]:
        key = hashlib.md5(f'{sb.get("voice")}|{sb.get("speed")}|{s.get("say", s["vo"])}'.encode()).hexdigest()
        p = os.path.join(cache_dir, key + ".wav")
        if not os.path.exists(p):
            k = k or Kokoro(os.path.join(ROOT, "models", "kokoro-v1.0.int8.onnx"), os.path.join(ROOT, "models", "voices-v1.0.bin"))
            a, sr = k.create(s.get("say", s["vo"]), voice=sb.get("voice", "am_michael"), speed=sb.get("speed", 1.1), lang="en-us")
            nz = np.where(np.abs(a) > 0.01)[0]
            sf.write(p, a[max(0, nz[0] - 600): nz[-1] + 1200], sr)
        clips.append(load_audio(p))
    return clips


def bandnoise(n, lo, hi, rng):
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-9)


def mix_audio(tl, voice, path):
    n = int((tl.total + 0.3) * SR)
    v = np.zeros(n, np.float32); v[:min(n, len(voice))] = voice[:n]
    voice = v / max(1e-6, np.abs(v).max()) * 0.9
    rng = np.random.default_rng(1)
    t = np.arange(n) / SR
    chords = [[110, 130.81, 164.81, 196], [87.31, 130.81, 174.61, 220], [98, 146.83, 174.61, 220], [82.41, 123.47, 164.81, 207.65]]
    pad = np.zeros(n, np.float32)
    seg = 6.0
    for ci in range(int(tl.total / seg) + 2):
        a0, a1 = int(ci * seg * SR), min(n, int((ci + 1) * seg * SR + 1.5 * SR))
        if a0 >= n:
            break
        tt = t[a0:a1] - ci * seg
        env = np.minimum(1, tt / 1.5) * np.minimum(1, np.maximum(0, (seg + 1.5 - tt) / 1.5))
        for f in chords[ci % 4]:
            for det in (-0.6, 0.6):
                pad[a0:a1] += np.sin(2 * np.pi * (f + det) * tt) * env * 0.05
    pad += bandnoise(n, 200, 1200, rng) * 0.01
    music = pad + (np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 1.0) / 0.18)) * 0.12).astype(np.float32)
    env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
    music *= 1 - 0.5 * np.clip(env / 0.03, 0, 1)
    sfx = np.zeros(n, np.float32)

    def add(sig, at):
        s = int(at * SR)
        if 0 <= s < n:
            sfx[s:s + len(sig)] += sig[: n - s]
    wl = int(0.9 * SR)
    for i in range(1, len(tl.scenes)):
        tt = np.arange(wl) / SR
        env_ = np.where(tt < 0.4, (tt / 0.4) ** 2, np.exp(-(tt - 0.4) / 0.18))
        add(bandnoise(wl, 300, 4000, rng) * env_ * 0.16, tl.starts[i] - 0.2)
    for h in tl.stamps:
        tt = np.arange(int(0.5 * SR)) / SR
        add(np.sin(2 * np.pi * 62 * tt) * np.exp(-tt / 0.12) * 0.5 + bandnoise(len(tt), 1500, 6000, rng) * np.exp(-tt / 0.012) * 0.25, h["t_in"] + 0.15)
    for h in tl.words:
        tt = np.arange(int(0.12 * SR)) / SR
        add(np.sin(2 * np.pi * np.cumsum(500 + 700 * tt / 0.12) / SR) * np.exp(-tt / 0.04) * 0.2, h["t_in"])
    for r in tl.routes:
        tt = np.arange(int(r["dur"] * SR)) / SR
        add(bandnoise(len(tt), 2000, 7000, rng) * np.sin(np.pi * tt / r["dur"]) * 0.025, r["t0"])
    out = voice + music * 0.5 + sfx
    out /= max(1.0, np.abs(out).max() / 0.95)
    sf.write(path, out, SR)


# ---------------------------------------------------------------- main
R = None


def render_range(args):
    a, b, d = args
    for f in range(a, b):
        R.frame(f / FPS).write_to_png(os.path.join(d, f"f{f:05d}.png"))
    return b - a


def build(sbp, audio=None):
    sb = json.load(open(sbp))
    texts = [s["vo"] for s in sb["scenes"]]
    if audio:
        starts, ends, D = scene_bounds_from_audio(audio, texts, min(0.6, max(0.1, sb.get("max_seconds", 75) - 0.05 - ffprobe_dur(audio))))
        voice = load_audio(audio)
        speech = [(s + (0.15 if i else 0.05), e - 0.25) for i, (s, e) in enumerate(zip(starts, ends))]
        speech[-1] = (speech[-1][0], D)
    else:
        clips = kokoro_voice(sb, os.path.join(ROOT, "build", "tts_cache"))
        starts, ends, t, parts = [], [], 0.0, []
        for c in clips:
            starts.append(t)
            parts += [np.zeros(int(LEAD * SR), np.float32), c, np.zeros(int(TAIL * SR), np.float32)]
            t += LEAD + len(c) / SR + TAIL
            ends.append(t)
        voice = np.concatenate(parts)
        speech = [(s + LEAD, e - TAIL) for s, e in zip(starts, ends)]
    tl = Timeline(sb, starts, ends, speech)
    make_captions(tl)
    return sb, tl, voice


def main():
    global R
    args = sys.argv[1:]
    audio = args[args.index("--audio") + 1] if "--audio" in args else None
    preview = None
    if "--preview" in args:
        v = args[args.index("--preview") + 1]
        preview = [] if v in ("", "auto") else [float(x) for x in v.split(",")]
    sbp, outp = args[0], args[1]
    sb, tl, voice = build(sbp, audio)
    work = os.path.join(ROOT, "build", os.path.splitext(os.path.basename(sbp))[0])
    os.makedirs(work, exist_ok=True)
    R = Renderer(tl)
    print(f"duration {tl.total:.1f}s, {len(sb['scenes'])} scenes", flush=True)
    for i, (s, e) in enumerate(zip(tl.starts, tl.ends)):
        print(f"  scene {i + 1}: {s:5.1f}-{e:5.1f}s  {sb['scenes'][i]['vo'][:60]}")
    if preview is not None:
        if not preview:
            preview = [min(e - 0.3, s + max(1.4, (e - s) * 0.6)) for s, e in zip(tl.starts, tl.ends)]
        paths = []
        for ts in preview:
            p = os.path.join(work, f"preview_{ts:05.1f}.png")
            R.frame(ts).write_to_png(p); paths.append(p)
        from PIL import Image
        cols = min(5, len(paths)); rows = math.ceil(len(paths) / cols)
        sheet = Image.new("RGB", (cols * 360, rows * 640))
        for k, p in enumerate(paths):
            sheet.paste(Image.open(p).convert("RGB").resize((360, 640)), ((k % cols) * 360, (k // cols) * 640))
        sheet.save(os.path.join(work, "contact_sheet.png"))
        print("contact sheet:", os.path.join(work, "contact_sheet.png"))
        return
    fdir = os.path.join(work, "frames")
    os.makedirs(fdir, exist_ok=True)
    for f in os.listdir(fdir):
        os.remove(os.path.join(fdir, f))
    nf = int(tl.total * FPS)
    jobs = [(a, min(nf, a + 30), fdir) for a in range(0, nf, 30)]
    done = 0
    with Pool(os.cpu_count() or 2) as pool:
        for k in pool.imap_unordered(render_range, jobs):
            done += k
            if done % 300 < 30:
                print(f"  {done}/{nf} frames", flush=True)
    wav = os.path.join(work, "mix.wav")
    mix_audio(tl, voice, wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(fdir, "f%05d.png"),
                    "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "4M", "-bufsize", "8M",
                    "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                    "-shortest", "-movflags", "+faststart", outp], check=True)
    print("wrote", outp, os.path.getsize(outp) // 1_000_000, "MB")


if __name__ == "__main__":
    main()
