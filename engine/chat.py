"""Reels Engine, chat-demo style: a generic phone chat screen where a prompt is typed,
the AI answer streams in and key phrases get highlighted. 9:16, burned-in captions,
music and sound effects. Used by the everyday-AI account (@efficiencymaxing).

Usage
  python3 engine/chat.py STORYBOARD.json OUT.mp4 --audio VO.mp3 [--preview auto|1.5,8] [--cover out.png]

See CHAT_GUIDE.md for the storyboard format.
"""
import json, math, os, sys, subprocess, shutil, re
from multiprocessing import Pool
import numpy as np
import cairo
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import (W, H, FPS, SR, ROOT, clamp, ease_io, ease_out, ease_back, ffprobe_dur, load_audio,
                    scene_bounds_from_audio, make_captions, bandnoise, Renderer as MapRenderer)

# ---------------------------------------------------------------- look
CARD = dict(x=60, y=420, w=960, h=1250, r=56)       # the phone screen
HEADER_H, INPUT_H = 128, 150
FONT, BODY, LINE = "Inter", 40, 56
THEMES = {
    "light": dict(screen=(0.975, 0.976, 0.982), header=(1, 1, 1), text=(0.10, 0.11, 0.14), muted=(0.45, 0.47, 0.52),
                  user=(0.17, 0.42, 1.0), user_text=(1, 1, 1), ai=(0.925, 0.932, 0.948), input=(1, 1, 1),
                  input_border=(0.85, 0.86, 0.89), mark=(1.0, 0.86, 0.20)),
    "dark": dict(screen=(0.09, 0.10, 0.12), header=(0.12, 0.13, 0.16), text=(0.93, 0.94, 0.96), muted=(0.60, 0.62, 0.67),
                 user=(0.20, 0.45, 1.0), user_text=(1, 1, 1), ai=(0.17, 0.18, 0.22), input=(0.15, 0.16, 0.19),
                 input_border=(0.26, 0.27, 0.31), mark=(1.0, 0.78, 0.10)),
}
BG = [((0.10, 0.07, 0.25), (0.38, 0.16, 0.55)), ((0.04, 0.17, 0.30), (0.10, 0.45, 0.55))]
ACCENT = (1.0, 0.84, 0.16)
COLORS = {"accent": ACCENT, "white": (1, 1, 1), "red": (0.92, 0.26, 0.24), "green": (0.20, 0.78, 0.45), "blue": (0.35, 0.62, 1.0)}


def col(c, default=ACCENT):
    if c is None:
        return default
    if isinstance(c, str) and c.startswith("#"):
        return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return COLORS.get(c, default)


def rrect(ctx, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0); ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi); ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    ctx.close_path()


_scratch = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))


def measure(s, size, bold=False, font=FONT):
    _scratch.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    _scratch.set_font_size(size)
    return _scratch.text_extents(s).x_advance


# ---------------------------------------------------------------- text layout
def layout(text, maxw, size=BODY, line=LINE):
    """Word-wrap with **bold**, bullet lines ("- ", "• ", "1. ") and blank-line paragraphs.
    Returns (words, height); each word: dict(s, x, y, w, bold, c0, c1) with c0..c1 its char span in
    the plain (markup-free) text, used for streaming and highlight lookup."""
    words, y, plain = [], 0, ""
    space = measure(" ", size)
    for para in text.split("\n"):
        if not para.strip():
            y += line * 0.45; plain += "\n"; continue
        indent, lead = 0, None
        st = para.lstrip()
        for b in ("- ", "• "):
            if st.startswith(b):
                lead, st, indent = "•", st[len(b):], 40
        if lead is None and len(st) > 2 and st[0].isdigit() and st[1:3] in (". ", ") "):
            lead, st, indent = st[:2], st[3:], 48
        x = indent
        if lead:
            words.append(dict(s=lead, x=0, y=y, w=measure(lead, size, True), bold=True, c0=len(plain), c1=len(plain) + len(lead) + 1))
            plain += lead + " "
        bold = False
        for tok in st.split(" "):
            if not tok:
                continue
            b0 = tok.startswith("**")
            core = tok.replace("**", "")
            wb = bold or b0
            toggles = tok.count("**")
            ww = measure(core, size, wb)
            if x > indent and x + ww > maxw:
                x, y = indent, y + line
            words.append(dict(s=core, x=x, y=y, w=ww, bold=wb, c0=len(plain), c1=len(plain) + len(core)))
            plain += core + " "
            x += ww + space
            if toggles % 2:
                bold = not bold
        y += line; plain += "\n"
    return words, max(y, line), plain


# ---------------------------------------------------------------- timeline
class ChatTimeline:
    def __init__(self, sb, starts, ends, speech):
        self.sb, self.scenes = sb, sb["scenes"]
        self.starts, self.ends, self.speech = starts, ends, speech
        self.total = ends[-1] + sb.get("outro_hold", 0.0)
        self.msgs, self.marks, self.words, self.cards, self.clears, self.typing, self.flows = [], [], [], [], [], [], []
        last_end = 0.0
        for i, s in enumerate(self.scenes):
            t0 = starts[i]
            if s.get("clear"):
                self.clears.append(t0)
            u = s.get("user")
            if u or s.get("image"):
                ty0 = max(t0 + s.get("user_delay", 0.25), last_end)
                text = u or ""
                dur = len(text) / s.get("type_cps", 26) if text else 0.4
                dur = min(dur, s.get("type_max", max(0.6, (ends[i] - ty0) * 0.75)))
                send = ty0 + dur + 0.25
                self.typing.append(dict(t0=ty0, t1=ty0 + dur, text=text))
                self.msgs.append(dict(role="user", text=text, image=s.get("image"), t=send, scene=i))
                last_end = send
            a = s.get("ai")
            if a:
                think = max(t0 + s.get("ai_delay", 0.2), last_end + 0.35)
                st0 = think + s.get("think", 0.8)
                _, _, plain = layout(a, 700)
                n = len(plain)
                sd = s.get("stream", min(n / s.get("stream_cps", 70), max(1.0, ends[i] - st0 - 0.4)))
                self.msgs.append(dict(role="ai", text=a, t=think, s0=st0, s1=st0 + sd, scene=i))
                last_end = st0 + sd
            for h in s.get("highlight", []) or []:
                self.marks.append(dict(text=h["text"], t=t0 + h.get("delay", 1.0), scene=i))
            w = s.get("word")
            if w:
                self.words.append(dict(item=w, t_in=t0 + w.get("delay", 0.4), t_out=ends[i] if not w.get("hold") else self.total))
            f = s.get("flow")
            if f:
                span = f.get("scenes", 1)
                self.flows.append(dict(item=f, scene=i, t_in=t0 + f.get("delay", 0.0),
                                       t_out=ends[min(len(ends) - 1, i + span - 1)], t0=t0))
            c = s.get("card")
            if c:
                self.cards.append(dict(item=c, t_in=t0 + c.get("delay", 0.0), t_out=t0 + c["dur"] if c.get("dur") else ends[i]))
        # attach each highlight to the latest AI message (at its time) containing the phrase
        for m in self.marks:
            m["msg"] = None
            for k in range(len(self.msgs) - 1, -1, -1):
                mm = self.msgs[k]
                if mm["role"] == "ai" and mm["t"] <= m["t"] + 0.01 and m["text"].lower() in mm["text"].replace("**", "").lower():
                    m["msg"] = k; break
            if m["msg"] is None:
                print(f"WARNING: highlight '{m['text']}' not found in any AI message before it", flush=True)

    def scene_at(self, t):
        for i in range(len(self.scenes) - 1, -1, -1):
            if t >= self.starts[i]:
                return i
        return 0


# ---------------------------------------------------------------- renderer
class ChatRenderer:
    def __init__(self, tl):
        self.tl, self.sb = tl, tl.sb
        self.th = THEMES[self.sb.get("theme", "light")]
        self.bg = BG[self.sb.get("background", 0) % len(BG)]
        self.cache = {}
        self.inner_w = CARD["w"] - 80
        for k, m in enumerate(tl.msgs):
            maxw = (self.inner_w - 220) if m["role"] == "user" else (self.inner_w - 150)
            if m["role"] == "user" and m["text"]:
                # shrink-wrap user bubbles
                wds, hh, plain = layout(m["text"], maxw)
                m["bw"] = min(maxw, max(w_["x"] + w_["w"] for w_ in wds)) + 56
                m["words"], m["th"], m["plain"] = wds, hh, plain
            elif m["role"] == "ai":
                wds, hh, plain = layout(m["text"], maxw)
                m["words"], m["th"], m["plain"] = wds, hh, plain
                m["bw"] = maxw + 56
            else:
                m["words"], m["th"], m["plain"], m["bw"] = [], 0, "", 0
            m["img_h"] = 330 if m.get("image") else 0
        for mk in tl.marks:
            mk["span"] = None
            if mk["msg"] is not None:
                m = tl.msgs[mk["msg"]]
                i0 = m["plain"].lower().find(mk["text"].lower())
                if i0 < 0:  # the phrase may cross a line break; compare without newlines
                    i0 = m["plain"].replace("\n", " ").lower().find(mk["text"].lower())
                if i0 >= 0:
                    mk["span"] = (i0, i0 + len(mk["text"]))

    # --- message geometry at time t
    def visible(self, t):
        start = 0
        for c in self.tl.clears:
            if c <= t:
                start = c
        return [(k, m) for k, m in enumerate(self.tl.msgs) if start <= m["t"] <= t]

    def reveal(self, m, t):
        """chars revealed for an AI message (None = still thinking)."""
        if m["role"] != "ai":
            return 10 ** 9
        if t < m["s0"]:
            return None
        return int(len(m["plain"]) * ease_out(clamp((t - m["s0"]) / max(0.05, m["s1"] - m["s0"])) ** 0.9)) if t < m["s1"] else 10 ** 9

    def msg_height(self, m, t):
        if m["role"] == "user":
            return (m["th"] + 40 if m["text"] else 0) + (m["img_h"] + (14 if m["text"] else 0) if m["img_h"] else 0)
        n = self.reveal(m, t)
        if n is None:
            return 96
        ws = [w for w in m["words"] if w["c0"] < n]
        return (ws[-1]["y"] + LINE if ws else LINE) + 40

    def stack(self, t):
        """[(k, m, y_top, h)] in content coordinates, plus total height."""
        out, y = [], 0
        for k, m in self.visible(t):
            h = self.msg_height(m, t)
            out.append((k, m, y, h)); y += h + 34
        return out, y

    def scroll(self, t):
        top = CARD["y"] + HEADER_H + 24
        bottom = CARD["y"] + CARD["h"] - INPUT_H - 20
        view = bottom - top

        def target(tt):
            st, total = self.stack(tt)
            s = max(0.0, total - 34 - view)
            # an active highlight must stay in view: centre it if it sits above the top
            for mk in self.tl.marks:
                if mk["span"] is None or not (mk["t"] - 0.6 <= tt):
                    continue
                if self.tl.scene_at(tt) != mk["scene"]:
                    continue
                for k, m, y0, h in st:
                    if k == mk["msg"]:
                        wy = [w["y"] for w in m["words"] if w["c1"] > mk["span"][0] and w["c0"] < mk["span"][1]]
                        if wy:
                            hy = y0 + 20 + min(wy)
                            if hy - s < view * 0.2 or hy - s > view * 0.85:
                                s = clamp(hy - view * 0.4, 0, max(0.0, total - 34 - view * 0.5))
            return s
        samples = [target(max(0, t - d)) for d in (0, 0.08, 0.16, 0.24, 0.32, 0.4)]
        return sum(samples) / len(samples), top, bottom

    # --- drawing
    def text_at(self, ctx, s, x, y, size, rgb, bold=False, font=FONT, alpha=1.0):
        ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size); ctx.set_source_rgba(*rgb, alpha); ctx.move_to(x, y); ctx.show_text(s)

    def draw_bg(self, ctx, t):
        (a, b) = self.bg
        g = cairo.LinearGradient(0, 0, W * 0.4, H)
        g.add_color_stop_rgb(0, *a); g.add_color_stop_rgb(1, *b)
        ctx.set_source(g); ctx.paint()
        for k, (cx, cy, r, c) in enumerate([(200, 300, 520, (0.95, 0.45, 0.85)), (900, 1500, 600, (0.25, 0.65, 1.0)), (700, 700, 380, (1.0, 0.75, 0.3))]):
            x = cx + 60 * math.sin(t * 0.35 + k * 2); y = cy + 50 * math.cos(t * 0.3 + k)
            rg = cairo.RadialGradient(x, y, 0, x, y, r)
            rg.add_color_stop_rgba(0, *c, 0.22); rg.add_color_stop_rgba(1, *c, 0)
            ctx.set_source(rg); ctx.paint()

    def draw_header(self, ctx):
        th = self.th
        x, y, w = CARD["x"], CARD["y"], CARD["w"]
        ctx.save(); rrect(ctx, x, y, w, CARD["h"], CARD["r"]); ctx.clip()
        ctx.rectangle(x, y, w, HEADER_H); ctx.set_source_rgb(*th["header"]); ctx.fill()
        ctx.rectangle(x, y + HEADER_H - 2, w, 2); ctx.set_source_rgba(*th["input_border"], 1); ctx.fill()
        ctx.restore()
        cx, cy = x + 82, y + HEADER_H / 2
        g = cairo.LinearGradient(cx - 34, cy - 34, cx + 34, cy + 34)
        g.add_color_stop_rgb(0, 0.55, 0.35, 1.0); g.add_color_stop_rgb(1, 0.15, 0.75, 0.95)
        ctx.arc(cx, cy, 36, 0, 2 * math.pi); ctx.set_source(g); ctx.fill()
        self.text_at(ctx, "✦", cx - 15, cy + 13, 38, (1, 1, 1), True, "DejaVu Sans")
        self.text_at(ctx, self.sb.get("app_name", "AI Assistant"), cx + 56, cy + 2, 40, th["text"], True)
        ctx.arc(cx + 66, cy + 28, 7, 0, 2 * math.pi); ctx.set_source_rgb(0.2, 0.78, 0.4); ctx.fill()
        self.text_at(ctx, "online", cx + 82, cy + 38, 28, th["muted"])

    def draw_input(self, ctx, t):
        th = self.th
        x, y, w = CARD["x"] + 30, CARD["y"] + CARD["h"] - INPUT_H + 28, CARD["w"] - 60
        h = 96
        rrect(ctx, x, y, w, h, 48); ctx.set_source_rgb(*th["input"]); ctx.fill_preserve()
        ctx.set_line_width(2.5); ctx.set_source_rgb(*th["input_border"]); ctx.stroke()
        typing = [ty for ty in self.tl.typing if ty["t0"] <= t <= ty["t1"] + 0.25]
        sx, sy = x + w - 56, y + h / 2
        active = bool(typing)
        ctx.arc(sx, sy, 34, 0, 2 * math.pi)
        ctx.set_source_rgb(*(th["user"] if active else th["input_border"])); ctx.fill()
        ctx.set_source_rgb(1, 1, 1); ctx.set_line_width(5); ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.move_to(sx, sy + 15); ctx.line_to(sx, sy - 14); ctx.stroke()
        ctx.move_to(sx - 12, sy - 3); ctx.line_to(sx, sy - 15); ctx.line_to(sx + 12, sy - 3); ctx.stroke()
        ctx.save(); ctx.rectangle(x + 30, y, w - 130, h); ctx.clip()
        if typing:
            ty = typing[0]
            n = len(ty["text"]) if t >= ty["t1"] else int(len(ty["text"]) * clamp((t - ty["t0"]) / max(0.05, ty["t1"] - ty["t0"])))
            s = ty["text"][:n].replace("\n", " ")
            size = 38
            tw = measure(s, size)
            off = max(0, tw - (w - 150))
            self.text_at(ctx, s, x + 40 - off, y + h / 2 + 13, size, th["text"])
            if (t * 2.2) % 1 < 0.6:
                ctx.rectangle(x + 44 + tw - off, y + 26, 3, 46); ctx.set_source_rgb(*th["user"]); ctx.fill()
        else:
            self.text_at(ctx, "Ask anything…", x + 40, y + h / 2 + 13, 38, th["muted"])
        ctx.restore()

    def draw_words(self, ctx, m, x, y, n, rgb, marks, t):
        th = self.th
        for mk in marks:          # marker sweep behind highlighted phrases
            a0, a1 = mk["span"]
            k = clamp((t - mk["t"]) / 0.55)
            if k <= 0:
                continue
            ws = [w for w in m["words"] if w["c1"] > a0 and w["c0"] < a1 and w["c0"] < n]
            rows = {}
            for w in ws:
                rows.setdefault(w["y"], []).append(w)
            total = sum(max(r_["x"] + r_["w"] for r_ in r) - min(r_["x"] for r_ in r) for r in rows.values()) or 1
            budget = total * ease_io(k)
            for ry in sorted(rows):
                r = rows[ry]; rx0 = min(w["x"] for w in r); rx1 = max(w["x"] + w["w"] for w in r)
                seg = min(rx1 - rx0, budget); budget -= seg
                if seg <= 0:
                    break
                rrect(ctx, x + rx0 - 8, y + ry + 6, seg + 16, LINE - 4, 10)
                ctx.set_source_rgba(*th["mark"], 0.75); ctx.fill()
        for w in m["words"]:
            if w["c0"] >= n:
                break
            a = 1.0
            if n < 10 ** 8:
                a = clamp((n - w["c0"]) / 6.0)
            self.text_at(ctx, w["s"], x + w["x"], y + w["y"] + 40, BODY, rgb, w["bold"], alpha=a)

    def draw_image(self, ctx, img, x, y, w, h, t, t0):
        k = ease_back(clamp((t - t0) / 0.35))
        ctx.save()
        ctx.translate(x + w / 2, y + h / 2); ctx.scale(0.85 + 0.15 * k, 0.85 + 0.15 * k); ctx.translate(-x - w / 2, -y - h / 2)
        rrect(ctx, x, y, w, h, 30)
        g = cairo.LinearGradient(x, y, x + w, y + h)
        c0, c1 = col(img.get("from", "#9BA7B8")), col(img.get("to", "#5B6676"))
        g.add_color_stop_rgb(0, *c0); g.add_color_stop_rgb(1, *c1)
        ctx.set_source(g); ctx.fill()
        if img.get("emoji"):
            surf = MapRenderer.emoji_surface(img["emoji"])
            sc = (h * 0.55) / surf.get_height()
            ctx.save(); ctx.translate(x + w / 2 - surf.get_width() * sc / 2, y + h * 0.42 - surf.get_height() * sc / 2)
            ctx.scale(sc, sc); ctx.set_source_surface(surf, 0, 0); ctx.paint(); ctx.restore()
        if img.get("label"):
            self.text_at(ctx, "📷 " if False else "", x, y, 1, (1, 1, 1))
            lw = measure(img["label"], 28, True)
            rrect(ctx, x + 20, y + h - 64, lw + 36, 46, 23); ctx.set_source_rgba(0, 0, 0, 0.45); ctx.fill()
            self.text_at(ctx, img["label"], x + 38, y + h - 31, 28, (1, 1, 1), True)
        ctx.restore()

    def ai_avatar(self, ctx, ax, ay):
        g = cairo.LinearGradient(ax - 26, ay - 28, ax + 26, ay + 24)
        g.add_color_stop_rgb(0, 0.55, 0.35, 1.0); g.add_color_stop_rgb(1, 0.15, 0.75, 0.95)
        ctx.arc(ax, ay, 26, 0, 2 * math.pi); ctx.set_source(g); ctx.fill()
        self.text_at(ctx, "✦", ax - 11, ay + 10, 28, (1, 1, 1), True, "DejaVu Sans")

    def draw_messages(self, ctx, t):
        th = self.th
        s, top, bottom = self.scroll(t)
        st, _ = self.stack(t)
        ctx.save()
        ctx.rectangle(CARD["x"], top - 20, CARD["w"], bottom - top + 20); ctx.clip()
        left = CARD["x"] + 40
        right = CARD["x"] + CARD["w"] - 40
        for k, m, y0, h in st:
            y = top + y0 - s
            if y > bottom + 20 or y + h < top - 40:
                continue
            app = ease_back(clamp((t - m["t"]) / 0.3))
            ctx.save()
            if m["role"] == "user":
                bw = max(m["bw"], 380 if m.get("image") else 0) if m["text"] else 400
                bx = right - bw
                ctx.translate(right, y + h); ctx.scale(0.8 + 0.2 * app, 0.8 + 0.2 * app); ctx.translate(-right, -(y + h))
                yy = y
                if m.get("image"):
                    self.draw_image(ctx, m["image"], right - 400, yy, 400, m["img_h"], t, m["t"])
                    yy += m["img_h"] + 14
                if m["text"]:
                    bh = m["th"] + 40
                    rrect(ctx, bx, yy, bw, bh, 34); ctx.set_source_rgb(*th["user"]); ctx.fill()
                    self.draw_words(ctx, m, bx + 28, yy + 14, 10 ** 9, th["user_text"], [], t)
            else:
                ax = left + 30
                self.ai_avatar(ctx, ax, y + 28)
                bx = left + 76
                n = self.reveal(m, t)
                bw = m["bw"] if n is not None else 170
                ctx.translate(bx, y); ctx.scale(0.85 + 0.15 * app, 0.85 + 0.15 * app); ctx.translate(-bx, -y)
                rrect(ctx, bx, y, bw, h, 34); ctx.set_source_rgb(*th["ai"]); ctx.fill()
                if n is None:
                    for d in range(3):
                        ph = (t * 3 - d * 0.35) % 1
                        r = 9 + 4 * max(0, math.sin(ph * math.pi))
                        ctx.arc(bx + 50 + d * 36, y + 48, r, 0, 2 * math.pi); ctx.set_source_rgb(*th["muted"]); ctx.fill()
                else:
                    marks = [mk for mk in self.tl.marks if mk["msg"] == k and mk["span"] and mk["t"] <= t]
                    self.draw_words(ctx, m, bx + 28, y + 14, n, th["text"], marks, t)
            ctx.restore()
        ctx.restore()
        # soft fade at the top edge of the message area
        g = cairo.LinearGradient(0, top - 20, 0, top + 40)
        g.add_color_stop_rgba(0, *th["screen"], 1); g.add_color_stop_rgba(1, *th["screen"], 0)
        ctx.rectangle(CARD["x"], top - 20, CARD["w"], 60); ctx.set_source(g); ctx.fill()

    def zoom(self, t):
        """(scale, focus_y) for the whole phone: per-scene 'zoom', eased; gentle drift otherwise."""
        i = self.tl.scene_at(t)
        sc = self.tl.scenes[i]
        prev = self.tl.scenes[i - 1].get("zoom", 1.0) if i > 0 else 1.0
        z = sc.get("zoom", 1.0)
        k = ease_io(clamp((t - self.tl.starts[i] - sc.get("zoom_delay", 0.0)) / 0.8))
        zz = prev + (z - prev) * k
        frac = clamp((t - self.tl.starts[i]) / max(0.1, self.tl.ends[i] - self.tl.starts[i]))
        zz *= 1 + 0.02 * frac
        fy = sc.get("focus_y", 1150)
        pfy = self.tl.scenes[i - 1].get("focus_y", 1150) if i > 0 else 1150
        return zz, pfy + (fy - pfy) * k

    def draw_phone(self, ctx, t):
        th = self.th
        z, fy = self.zoom(t)
        ctx.save()
        ctx.translate(W / 2, fy); ctx.scale(z, z); ctx.translate(-W / 2, -fy)
        x, y, w, h, r = CARD["x"], CARD["y"], CARD["w"], CARD["h"], CARD["r"]
        for k in range(14, 0, -1):     # shadow
            rrect(ctx, x - k * 2, y - k * 2 + 18, w + k * 4, h + k * 4, r + k * 2)
            ctx.set_source_rgba(0, 0, 0, 0.018); ctx.fill()
        rrect(ctx, x - 14, y - 14, w + 28, h + 28, r + 14); ctx.set_source_rgb(0.06, 0.06, 0.08); ctx.fill()
        rrect(ctx, x, y, w, h, r); ctx.set_source_rgb(*th["screen"]); ctx.fill()
        ctx.save(); rrect(ctx, x, y, w, h, r); ctx.clip()
        self.draw_messages(ctx, t)
        ctx.restore()
        self.draw_header(ctx)
        self.draw_input(ctx, t)
        ctx.restore()

    def big_text(self, ctx, s, x, y, size, rgb, alpha=1.0, outline=True, font="Anton", maxw=980):
        ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(size)
        e = ctx.text_extents(s)
        if e.x_advance > maxw:
            size *= maxw / e.x_advance; ctx.set_font_size(size); e = ctx.text_extents(s)
        ctx.move_to(x - e.x_advance / 2, y); ctx.text_path(s)
        if outline:
            ctx.set_source_rgba(0, 0, 0, 0.9 * alpha); ctx.set_line_width(size * 0.12); ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.stroke_preserve()
        ctx.set_source_rgba(*rgb, alpha); ctx.fill()
        return size

    def draw_cards(self, ctx, t):
        for c in self.tl.cards:
            if not (c["t_in"] <= t <= c["t_out"] + 0.3):
                continue
            a = clamp((t - c["t_in"]) / 0.15) * (1 - clamp((t - c["t_out"]) / 0.3))
            it = c["item"]
            ctx.set_source_rgba(0.03, 0.02, 0.08, 0.8 * a); ctx.paint()
            y = it.get("y", 860)
            lines = it.get("lines", [])
            tot = sum(l.get("size", 140) * 1.08 for l in lines)
            yy = y - tot / 2
            for j, l in enumerate(lines):
                sz = l.get("size", 140)
                k = ease_back(clamp((t - c["t_in"] - 0.08 * j) / 0.3))
                ctx.save(); ctx.translate(W / 2, yy + sz); ctx.scale(0.6 + 0.4 * k, 0.6 + 0.4 * k); ctx.translate(-W / 2, -(yy + sz))
                self.big_text(ctx, l["text"], W / 2, yy + sz, sz, col(l.get("color"), (1, 1, 1)), a * clamp(k * 3))
                ctx.restore()
                yy += sz * 1.08
            if it.get("emoji"):
                surf = MapRenderer.emoji_surface(it["emoji"])
                sz = it.get("emoji_size", 220); sc = sz / surf.get_height()
                k = ease_back(clamp((t - c["t_in"] - 0.2) / 0.35))
                bob = 10 * math.sin(t * 3)
                ex, ey = W / 2, yy + 40 + sz / 2 + bob
                k = max(k, 0.02)
                ctx.save(); ctx.translate(ex, ey); ctx.scale(sc * k, sc * k)
                ctx.set_source_surface(surf, -surf.get_width() / 2, -surf.get_height() / 2); ctx.paint_with_alpha(a); ctx.restore()

    def draw_flows(self, ctx, t):
        """Animated connection diagram: nodes light up one by one, joined by arrows with a travelling dot.
        Step delays are seconds after the flow's scene starts (they may run into later scenes)."""
        for fl in self.tl.flows:
            if not (fl["t_in"] <= t <= fl["t_out"] + 0.3):
                continue
            it = fl["item"]
            a = clamp((t - fl["t_in"]) / 0.3) * (1 - clamp((t - fl["t_out"]) / 0.3))
            g = cairo.LinearGradient(0, 0, 0, H)
            g.add_color_stop_rgba(0, 0.06, 0.05, 0.16, a); g.add_color_stop_rgba(1, 0.04, 0.12, 0.22, a)
            ctx.set_source(g); ctx.paint()
            if it.get("title"):
                k = ease_back(clamp((t - fl["t_in"]) / 0.35))
                ctx.save(); ctx.translate(W / 2, 520); ctx.scale(0.7 + 0.3 * k, 0.7 + 0.3 * k)
                self.big_text(ctx, it["title"], 0, 0, it.get("title_size", 96), col(it.get("title_color")), a)
                ctx.restore()
            if it.get("subtitle"):
                self.big_text(ctx, it["subtitle"], W / 2, 590, 40, (0.85, 0.87, 0.95), a * clamp((t - fl["t_in"] - 0.2) / 0.3), outline=False, font=FONT)
            steps = it["steps"]
            n = len(steps)
            y0 = it.get("y", 660)
            gap = min(270, (it.get("bottom", 1580) - y0 - 170) / max(1, n - 1))
            nh, nw = 170, 800
            times = [fl["t0"] + st.get("delay", 0.6 + 0.9 * j) for j, st in enumerate(steps)]
            shown = [j for j in range(n) if t >= times[j]]
            latest = shown[-1] if shown else -1
            for j in range(1, n):          # arrows
                ya, yb = y0 + (j - 1) * gap + nh, y0 + j * gap
                k = clamp((t - (times[j] - 0.45)) / 0.4)
                if k <= 0:
                    continue
                ctx.set_source_rgba(*ACCENT, 0.9 * a); ctx.set_line_width(7); ctx.set_line_cap(cairo.LINE_CAP_ROUND)
                ctx.set_dash([2, 16]); ctx.move_to(W / 2, ya + 8); ctx.line_to(W / 2, ya + 8 + (yb - ya - 16) * ease_io(k)); ctx.stroke(); ctx.set_dash([])
                if k >= 1:
                    ctx.move_to(W / 2 - 18, yb - 26); ctx.line_to(W / 2, yb - 8); ctx.line_to(W / 2 + 18, yb - 26); ctx.stroke()
                    ph = ((t - times[j]) * 0.9 + j * 0.3) % 1
                    ctx.arc(W / 2, ya + 8 + (yb - ya - 16) * ph, 9, 0, 2 * math.pi); ctx.set_source_rgba(1, 1, 1, a); ctx.fill()
            for j, st in enumerate(steps):   # nodes
                if t < times[j]:
                    continue
                k = ease_back(clamp((t - times[j]) / 0.35))
                y = y0 + j * gap; x = W / 2 - nw / 2
                ctx.save(); ctx.translate(W / 2, y + nh / 2); ctx.scale(0.7 + 0.3 * k, 0.7 + 0.3 * k); ctx.translate(-W / 2, -(y + nh / 2))
                if j == latest:
                    glow = 0.5 + 0.5 * math.sin((t - times[j]) * 5)
                    for r_ in range(4, 0, -1):
                        rrect(ctx, x - r_ * 5, y - r_ * 5, nw + r_ * 10, nh + r_ * 10, 40 + r_ * 5)
                        ctx.set_source_rgba(*ACCENT, (0.06 + 0.05 * glow) * a); ctx.fill()
                rrect(ctx, x, y, nw, nh, 40); ctx.set_source_rgba(1, 1, 1, 0.97 * a); ctx.fill()
                rrect(ctx, x + 26, y + 25, 120, 120, 30)
                ctx.set_source_rgba(*col(st.get("color"), (0.93, 0.94, 0.98)), a); ctx.fill()
                if st.get("emoji"):
                    surf = MapRenderer.emoji_surface(st["emoji"]); sc = 84 / surf.get_height()
                    ctx.save(); ctx.translate(x + 86, y + 85); ctx.scale(sc, sc)
                    ctx.set_source_surface(surf, -surf.get_width() / 2, -surf.get_height() / 2); ctx.paint_with_alpha(a); ctx.restore()
                lx = x + 176
                ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); ctx.set_font_size(46)
                lab = st["label"]
                while measure(lab, 46, True) > nw - 210 and len(lab) > 4:
                    lab = lab[:-2]
                self.text_at(ctx, lab, lx, y + (78 if st.get("sub") else 100), 46, (0.08, 0.09, 0.13), True, alpha=a)
                if st.get("sub"):
                    self.text_at(ctx, st["sub"], lx, y + 126, 32, (0.40, 0.42, 0.48), alpha=a)
                num = str(j + 1)
                ctx.arc(x + nw - 50, y + 50, 26, 0, 2 * math.pi); ctx.set_source_rgba(*ACCENT, a); ctx.fill()
                self.text_at(ctx, num, x + nw - 50 - measure(num, 32, True) / 2, y + 61, 32, (0.1, 0.08, 0.02), True, alpha=a)
                ctx.restore()
            nt = it.get("notify")
            if nt:
                tn = fl["t0"] + nt.get("delay", 3.0)
                if t >= tn:
                    k = ease_out(clamp((t - tn) / 0.4))
                    by = nt.get("y", 1430) + (1 - k) * 60
                    bx, bw, bh = 110, 860, 150
                    rrect(ctx, bx, by, bw, bh, 36); ctx.set_source_rgba(0.97, 0.97, 0.99, 0.98 * a * k); ctx.fill()
                    rrect(ctx, bx + 24, by + 30, 90, 90, 22); ctx.set_source_rgba(0.2, 0.78, 0.4, a * k); ctx.fill()
                    surf = MapRenderer.emoji_surface(nt.get("emoji", "💬")); sc = 56 / surf.get_height()
                    ctx.save(); ctx.translate(bx + 69, by + 75); ctx.scale(sc, sc)
                    ctx.set_source_surface(surf, -surf.get_width() / 2, -surf.get_height() / 2); ctx.paint_with_alpha(a * k); ctx.restore()
                    self.text_at(ctx, nt.get("app", "Messages") + "  ·  now", bx + 136, by + 58, 30, (0.45, 0.47, 0.52), alpha=a * k)
                    txt = nt["text"]
                    while measure(txt, 38, True) > bw - 170 and len(txt) > 4:
                        txt = txt[:-2]
                    self.text_at(ctx, txt, bx + 136, by + 108, 38, (0.08, 0.09, 0.13), True, alpha=a * k)

    def draw_word(self, ctx, t):
        for w in self.tl.words:
            if not (w["t_in"] <= t <= w["t_out"] + 0.25):
                continue
            it = w["item"]
            a = 1 - clamp((t - w["t_out"]) / 0.25)
            k = ease_back(clamp((t - w["t_in"]) / 0.3))
            y = it.get("y", 1300)
            sz = it.get("size", 120)
            ctx.save(); ctx.translate(W / 2, y); ctx.rotate(math.radians(it.get("tilt", -3))); ctx.scale(0.5 + 0.5 * k, 0.5 + 0.5 * k)
            ctx.select_font_face("Anton"); ctx.set_font_size(sz)
            e = ctx.text_extents(it["text"])
            if e.x_advance > 860:
                sz *= 860 / e.x_advance; ctx.set_font_size(sz); e = ctx.text_extents(it["text"])
            pw, ph = e.x_advance + 80, sz * 1.25 + (60 if it.get("sub") else 0)
            rrect(ctx, -pw / 2 + 8, -ph / 2 + 12, pw, ph, 28); ctx.set_source_rgba(0, 0, 0, 0.35 * a); ctx.fill()
            rrect(ctx, -pw / 2, -ph / 2, pw, ph, 28); ctx.set_source_rgba(*col(it.get("color")), a); ctx.fill()
            oy = 0
            if it.get("sub"):
                ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); ctx.set_font_size(36)
                se = ctx.text_extents(it["sub"]); ctx.move_to(-se.x_advance / 2, -ph / 2 + 56)
                ctx.set_source_rgba(0.1, 0.08, 0.02, a); ctx.show_text(it["sub"]); oy = 30
            ctx.select_font_face("Anton"); ctx.set_font_size(sz)
            ctx.move_to(-e.x_advance / 2, oy + sz * 0.36); ctx.set_source_rgba(0.08, 0.06, 0.02, a); ctx.show_text(it["text"])
            ctx.restore()

    def draw_captions(self, ctx, t):
        i = self.tl.scene_at(t)
        for c0, c1, txt in self.tl.captions[i]:
            if c0 <= t < c1:
                pop = 0.92 + 0.08 * ease_back((t - c0) / 0.18)
                ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); ctx.set_font_size(62)
                lines = [txt]
                if ctx.text_extents(txt).x_advance > 940:
                    ws = txt.split()
                    best = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:]))))
                    lines = [" ".join(ws[:best]), " ".join(ws[best:])]
                y = 290 if len(lines) == 1 else 250
                for ln in lines:
                    ctx.save(); ctx.translate(W / 2, y); ctx.scale(pop, pop)
                    self.big_text(ctx, ln, 0, 0, 62, (1, 1, 1), font=FONT, maxw=960)
                    ctx.restore()
                    y += 76

    def frame(self, t, captions=True):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        ctx = cairo.Context(surf)
        self.draw_bg(ctx, t)
        self.draw_phone(ctx, t)
        self.draw_flows(ctx, t)
        self.draw_word(ctx, t)
        self.draw_cards(ctx, t)
        if captions:
            self.draw_captions(ctx, t)
        return surf

    def cover(self):
        cv = self.sb["cover"]
        t = cv.get("t", self.tl.msgs[-1].get("s1", self.tl.msgs[-1]["t"]) + 0.5 if self.tl.msgs else 2.0)
        surf = self.frame(min(t, self.tl.total - 0.1), captions=False)
        ctx = cairo.Context(surf)
        g = cairo.LinearGradient(0, 0, 0, H)
        g.add_color_stop_rgba(0, 0.02, 0.01, 0.06, 0.82); g.add_color_stop_rgba(0.55, 0.02, 0.01, 0.06, 0.6); g.add_color_stop_rgba(1, 0.02, 0.01, 0.06, 0.25)
        ctx.set_source(g); ctx.paint()
        lines = cv["lines"]
        tot = sum(l.get("size", 150) * 1.06 for l in lines)
        yy = cv.get("y", 860) - tot / 2
        for l in lines:
            sz = l.get("size", 150)
            self.big_text(ctx, l["text"], W / 2, yy + sz, sz, col(l.get("color"), (1, 1, 1)))
            yy += sz * 1.06
        if cv.get("emoji"):
            surf_e = MapRenderer.emoji_surface(cv["emoji"]); sz = cv.get("emoji_size", 200); sc = sz / surf_e.get_height()
            ctx.save(); ctx.translate(W / 2, yy + 30 + sz / 2); ctx.scale(sc, sc)
            ctx.set_source_surface(surf_e, -surf_e.get_width() / 2, -surf_e.get_height() / 2); ctx.paint(); ctx.restore()
            yy += sz + 40
        if cv.get("badge"):
            ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); ctx.set_font_size(50)
            e = ctx.text_extents(cv["badge"])
            bw = min(1000, e.x_advance + 70); by = yy + 40
            rrect(ctx, W / 2 - bw / 2, by, bw, 92, 18); ctx.set_source_rgb(0.86, 0.18, 0.2); ctx.fill()
            self.big_text(ctx, cv["badge"], W / 2, by + 64, 50, (1, 1, 1), outline=False, font=FONT, maxw=bw - 50)
        return surf


# ---------------------------------------------------------------- audio
def mix_chat_audio(tl, voice, path):
    n = int((tl.total + 0.3) * SR)
    v = np.zeros(n, np.float32); v[:min(n, len(voice))] = voice[:n]
    voice = v / max(1e-6, np.abs(v).max()) * 0.9
    rng = np.random.default_rng(3)
    t = np.arange(n) / SR
    # bright lo-fi loop: soft chords + gentle beat
    chords = [[261.63, 329.63, 392.0, 493.88], [220.0, 261.63, 329.63, 392.0], [174.61, 220.0, 261.63, 329.63], [196.0, 246.94, 293.66, 392.0]]
    pad = np.zeros(n, np.float32)
    seg = 2.4
    for ci in range(int(tl.total / seg) + 2):
        a0, a1 = int(ci * seg * SR), min(n, int((ci + 1) * seg * SR + 0.6 * SR))
        if a0 >= n:
            break
        tt = t[a0:a1] - ci * seg
        env = np.minimum(1, tt / 0.05) * np.exp(-tt / 1.6)
        for f in chords[ci % 4]:
            pad[a0:a1] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)) * env * 0.025
    beat = 0.6
    kick = np.zeros(n, np.float32)
    for bi in range(int(tl.total / beat) + 1):
        s = int(bi * beat * SR); L = min(n - s, int(0.25 * SR))
        if L <= 0:
            break
        tt = np.arange(L) / SR
        if bi % 2 == 0:
            kick[s:s + L] += np.sin(2 * np.pi * (50 + 60 * np.exp(-tt / 0.04)) * tt) * np.exp(-tt / 0.09) * 0.35
        else:
            kick[s:s + L] += bandnoise(L, 2000, 8000, rng) * np.exp(-tt / 0.03) * 0.06
    music = pad + kick
    env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
    music *= 1 - 0.55 * np.clip(env / 0.03, 0, 1)
    sfx = np.zeros(n, np.float32)

    def add(sig, at):
        s = int(at * SR)
        if 0 <= s < n:
            sfx[s:s + len(sig)] += sig[: n - s]
    for ty in tl.typing:          # key clicks
        k = ty["t0"]
        while k < ty["t1"]:
            L = int(0.025 * SR); tt = np.arange(L) / SR
            add(bandnoise(L, 2500, 9000, rng) * np.exp(-tt / 0.004) * (0.10 + 0.05 * rng.random()), k)
            k += 1 / 18 * (0.6 + 0.8 * rng.random())
    for m in tl.msgs:
        if m["role"] == "user":   # send whoosh
            L = int(0.25 * SR); tt = np.arange(L) / SR
            add(np.sin(2 * np.pi * np.cumsum(400 + 1400 * tt / 0.25) / SR) * np.sin(np.pi * tt / 0.25) * 0.12, m["t"] - 0.05)
        else:                     # reply pop
            L = int(0.18 * SR); tt = np.arange(L) / SR
            add(np.sin(2 * np.pi * 880 * tt) * np.exp(-tt / 0.05) * 0.12 + np.sin(2 * np.pi * 1320 * tt) * np.exp(-tt / 0.04) * 0.06, m["s0"])
    for mk in tl.marks:           # highlighter swipe
        L = int(0.45 * SR); tt = np.arange(L) / SR
        add(bandnoise(L, 1500, 6000, rng) * np.sin(np.pi * tt / 0.45) * 0.05, mk["t"])
    for c in tl.cards:            # hook boom
        L = int(0.6 * SR); tt = np.arange(L) / SR
        add(np.sin(2 * np.pi * 55 * tt) * np.exp(-tt / 0.18) * 0.5 + bandnoise(L, 1000, 6000, rng) * np.exp(-tt / 0.03) * 0.15, c["t_in"])
    for fl in tl.flows:           # node pops + notification ding
        for j, st in enumerate(fl["item"]["steps"]):
            L = int(0.15 * SR); tt = np.arange(L) / SR
            add(np.sin(2 * np.pi * (660 + 110 * j) * tt) * np.exp(-tt / 0.05) * 0.14, fl["t0"] + st.get("delay", 0.6 + 0.9 * j))
        nt = fl["item"].get("notify")
        if nt:
            L = int(0.5 * SR); tt = np.arange(L) / SR
            add((np.sin(2 * np.pi * 1318 * tt) * np.exp(-tt / 0.15) + 0.6 * np.sin(2 * np.pi * 1760 * np.maximum(tt - 0.12, 0)) * np.exp(-np.maximum(tt - 0.12, 0) / 0.15) * (tt > 0.12)) * 0.12,
                fl["t0"] + nt.get("delay", 3.0))
    for w in tl.words:            # word pop
        L = int(0.12 * SR); tt = np.arange(L) / SR
        add(np.sin(2 * np.pi * np.cumsum(600 + 900 * tt / 0.12) / SR) * np.exp(-tt / 0.04) * 0.2, w["t_in"])
    out = voice + music * 0.5 + sfx
    out /= max(1.0, np.abs(out).max() / 0.95)
    sf.write(path, out, SR)



# ================================================================ CLEAN LOOK ("look": "clean")
# Apple.com-inspired: black stage, graphite phone, crisp Inter Display type, gradient key words,
# smooth fades and slides (no overshoot), thin line icons instead of emojis, soft ambient sound.
HEAD, HEAD_B, BODY_F = "Inter Display SemiBold", "Inter Display Bold", "Inter"
GRAY = (0.53, 0.53, 0.55)
BLUE = (0.0, 0.44, 0.89)
GRAD = ((0.16, 0.59, 1.0), (0.66, 0.40, 1.0))


def ease3(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def icon(ctx, name, cx, cy, s, rgb, a=1.0, lw=None):
    """Thin line icons drawn in a box of size s centred on (cx, cy)."""
    ctx.save(); ctx.new_path(); ctx.translate(cx, cy); ctx.scale(s / 64, s / 64)
    ctx.set_source_rgba(*rgb, a); ctx.set_line_width(lw or 3.6); ctx.set_line_cap(cairo.LINE_CAP_ROUND); ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    if name == "camera":
        rrect(ctx, -26, -16, 52, 36, 8); ctx.stroke()
        ctx.move_to(-10, -16); ctx.line_to(-6, -23); ctx.line_to(6, -23); ctx.line_to(10, -16); ctx.stroke()
        ctx.new_sub_path(); ctx.arc(0, 2, 10, 0, 2 * math.pi); ctx.stroke()
    elif name == "ai":
        for (x, y, r) in ((0, 0, 20), (19, -19, 7)):
            ctx.move_to(x, y - r); ctx.curve_to(x + r * 0.15, y - r * 0.15, x + r * 0.15, y - r * 0.15, x + r, y)
            ctx.curve_to(x + r * 0.15, y + r * 0.15, x + r * 0.15, y + r * 0.15, x, y + r)
            ctx.curve_to(x - r * 0.15, y + r * 0.15, x - r * 0.15, y + r * 0.15, x - r, y)
            ctx.curve_to(x - r * 0.15, y - r * 0.15, x - r * 0.15, y - r * 0.15, x, y - r); ctx.close_path(); ctx.stroke()
    elif name == "message":
        rrect(ctx, -26, -20, 52, 34, 14); ctx.stroke()
        ctx.move_to(-12, 14); ctx.line_to(-16, 24); ctx.line_to(-2, 14); ctx.stroke()
    elif name == "mail":
        rrect(ctx, -26, -18, 52, 36, 6); ctx.stroke()
        ctx.move_to(-24, -14); ctx.line_to(0, 4); ctx.line_to(24, -14); ctx.stroke()
    elif name == "sheet":
        rrect(ctx, -24, -22, 48, 44, 6); ctx.stroke()
        for y in (-7, 8):
            ctx.move_to(-24, y); ctx.line_to(24, y); ctx.stroke()
        ctx.move_to(-6, -22); ctx.line_to(-6, 22); ctx.stroke()
    elif name == "doc":
        ctx.move_to(-18, -26); ctx.line_to(8, -26); ctx.line_to(20, -14); ctx.line_to(20, 26); ctx.line_to(-18, 26); ctx.close_path(); ctx.stroke()
        for y in (-4, 6, 16):
            ctx.move_to(-9, y); ctx.line_to(11, y); ctx.stroke()
    elif name == "folder":
        ctx.move_to(-26, -14); ctx.line_to(-26, 20); ctx.line_to(26, 20); ctx.line_to(26, -8); ctx.line_to(0, -8); ctx.line_to(-6, -16); ctx.line_to(-24, -16); ctx.close_path(); ctx.stroke()
    elif name == "check":
        ctx.new_sub_path(); ctx.arc(0, 0, 24, 0, 2 * math.pi); ctx.stroke()
        ctx.move_to(-11, 0); ctx.line_to(-3, 8); ctx.line_to(12, -8); ctx.stroke()
    elif name == "bell":
        ctx.move_to(-18, 12); ctx.curve_to(-18, -24, 18, -24, 18, 12); ctx.line_to(22, 16); ctx.line_to(-22, 16); ctx.close_path(); ctx.stroke()
        ctx.new_sub_path(); ctx.arc(0, 21, 5, 0, math.pi); ctx.stroke()
    elif name == "phone":
        rrect(ctx, -15, -27, 30, 54, 7); ctx.stroke()
        ctx.move_to(-4, 21); ctx.line_to(4, 21); ctx.stroke()
    else:  # dot
        ctx.arc(0, 0, 8, 0, 2 * math.pi); ctx.fill()
    ctx.restore()


def clean_captions(tl):
    """Same timing as make_captions, but keeps the script's own capitalisation."""
    caps = []
    for i, s in enumerate(tl.scenes):
        words = s["vo"].split()
        chunks, cur = [], []
        for j, w in enumerate(words):
            cur.append(w)
            rest = len(words) - j - 1
            punct = re.search(r"[,.?!:;]$", w)
            if (len(cur) >= 4 and punct and rest != 1) or len(cur) >= 7 or (len(cur) >= 5 and rest == 0):
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
            c = c.rstrip(",;:")
            out.append((t, end, c[:1].upper() + c[1:]))
            t += d
        caps.append(out)
    tl.captions = caps


class CleanChatRenderer(ChatRenderer):
    def __init__(self, tl):
        super().__init__(tl)
        clean_captions(tl)
        self.th = dict(screen=(1, 1, 1), header=(0.98, 0.98, 0.985), text=(0.07, 0.07, 0.08), muted=(0.56, 0.56, 0.58),
                       user=BLUE, user_text=(1, 1, 1), ai=(0.914, 0.914, 0.922), input=(0.96, 0.96, 0.965),
                       input_border=(0.86, 0.86, 0.88), mark=(0.0, 0.44, 0.89))

    # --- type helpers
    def htext(self, ctx, s, x, y, size, rgb=(1, 1, 1), a=1.0, font=HEAD, align="center", grad=False, maxw=960):
        ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); ctx.set_font_size(size)
        e = ctx.text_extents(s)
        if e.x_advance > maxw:
            size *= maxw / e.x_advance; ctx.set_font_size(size); e = ctx.text_extents(s)
        x0 = x - e.x_advance / 2 if align == "center" else x
        ctx.move_to(x0, y)
        if grad:
            g = cairo.LinearGradient(x0, 0, x0 + e.x_advance, 0)
            g.add_color_stop_rgba(0, *GRAD[0], a); g.add_color_stop_rgba(1, *GRAD[1], a)
            ctx.text_path(s); ctx.set_source(g); ctx.fill()
        else:
            ctx.set_source_rgba(*rgb, a); ctx.show_text(s)
        return size

    # --- stage
    def draw_bg(self, ctx, t):
        ctx.set_source_rgb(0, 0, 0); ctx.paint()
        rg = cairo.RadialGradient(W / 2, 980, 0, W / 2, 980, 900)
        rg.add_color_stop_rgba(0, 0.16, 0.17, 0.20, 0.55); rg.add_color_stop_rgba(1, 0, 0, 0, 0)
        ctx.set_source(rg); ctx.paint()

    def ai_avatar(self, ctx, ax, ay):
        ctx.arc(ax, ay, 26, 0, 2 * math.pi); ctx.set_source_rgb(0.07, 0.07, 0.08); ctx.fill()
        icon(ctx, "ai", ax, ay, 30, (1, 1, 1), lw=4.5)

    def draw_header(self, ctx):
        th = self.th
        x, y, w = CARD["x"], CARD["y"], CARD["w"]
        ctx.save(); rrect(ctx, x, y, w, CARD["h"], CARD["r"]); ctx.clip()
        ctx.rectangle(x, y, w, HEADER_H); ctx.set_source_rgb(*th["header"]); ctx.fill()
        ctx.rectangle(x, y + HEADER_H - 1, w, 1); ctx.set_source_rgb(0.88, 0.88, 0.9); ctx.fill()
        ctx.restore()
        cx, cy = x + 82, y + HEADER_H / 2
        ctx.arc(cx, cy, 34, 0, 2 * math.pi); ctx.set_source_rgb(0.07, 0.07, 0.08); ctx.fill()
        icon(ctx, "ai", cx, cy, 40, (1, 1, 1), lw=4.2)
        self.text_at(ctx, self.sb.get("app_name", "AI Assistant"), cx + 54, cy + 13, 38, th["text"], True)

    def draw_phone(self, ctx, t):
        th = self.th
        z, fy = self.zoom(t)
        ctx.save(); ctx.translate(W / 2, fy); ctx.scale(z, z); ctx.translate(-W / 2, -fy)
        x, y, w, h, r = CARD["x"], CARD["y"], CARD["w"], CARD["h"], CARD["r"]
        rrect(ctx, x - 16, y - 16, w + 32, h + 32, r + 16); ctx.set_source_rgb(0.11, 0.11, 0.12); ctx.fill()
        rrect(ctx, x - 16, y - 16, w + 32, h + 32, r + 16); ctx.set_line_width(2); ctx.set_source_rgba(1, 1, 1, 0.12); ctx.stroke()
        rrect(ctx, x, y, w, h, r); ctx.set_source_rgb(*th["screen"]); ctx.fill()
        ctx.save(); rrect(ctx, x, y, w, h, r); ctx.clip()
        self.draw_messages(ctx, t)
        ctx.restore()
        self.draw_header(ctx)
        self.draw_input(ctx, t)
        ctx.restore()

    def draw_messages(self, ctx, t):
        # same layout, calmer motion: replace pop-in scale with a short fade + rise
        global ease_back
        saved = ease_back
        try:
            globals()["ease_back"] = ease3
            super().draw_messages(ctx, t)
        finally:
            globals()["ease_back"] = saved

    def draw_words(self, ctx, m, x, y, n, rgb, marks, t):
        th = self.th
        for mk in marks:      # soft blue tint, sweeping left to right
            a0, a1 = mk["span"]
            k = clamp((t - mk["t"]) / 0.5)
            if k <= 0:
                continue
            ws = [w for w in m["words"] if w["c1"] > a0 and w["c0"] < a1 and w["c0"] < n]
            rows = {}
            for w in ws:
                rows.setdefault(w["y"], []).append(w)
            total = sum(max(r_["x"] + r_["w"] for r_ in r) - min(r_["x"] for r_ in r) for r in rows.values()) or 1
            budget = total * ease3(k)
            for ry in sorted(rows):
                r = rows[ry]; rx0 = min(w["x"] for w in r); rx1 = max(w["x"] + w["w"] for w in r)
                seg = min(rx1 - rx0, budget); budget -= seg
                if seg <= 0:
                    break
                rrect(ctx, x + rx0 - 6, y + ry + 8, seg + 12, LINE - 8, 8)
                ctx.set_source_rgba(*BLUE, 0.16); ctx.fill()
                ctx.rectangle(x + rx0 - 6, y + ry + LINE - 4, seg + 12, 3); ctx.set_source_rgba(*BLUE, 0.9); ctx.fill()
        for w in m["words"]:
            if w["c0"] >= n:
                break
            a = 1.0 if n >= 10 ** 8 else clamp((n - w["c0"]) / 6.0)
            self.text_at(ctx, w["s"], x + w["x"], y + w["y"] + 40, BODY, rgb, w["bold"], alpha=a)

    def draw_image(self, ctx, img, x, y, w, h, t, t0):
        k = ease3(clamp((t - t0) / 0.35))
        rrect(ctx, x, y, w, h, 26); ctx.set_source_rgba(0.95, 0.95, 0.96, k); ctx.fill()
        rrect(ctx, x, y, w, h, 26); ctx.set_line_width(2); ctx.set_source_rgba(0.85, 0.85, 0.87, k); ctx.stroke()
        icon(ctx, img.get("icon", "doc"), x + w / 2, y + h * 0.42, 110, (0.35, 0.35, 0.38), k, lw=3)
        if img.get("label"):
            self.text_at(ctx, img["label"], x + w / 2 - measure(img["label"], 30, True) / 2, y + h - 40, 30, (0.25, 0.25, 0.27), True, alpha=k)

    # --- full-screen slides (cards)
    def draw_cards(self, ctx, t):
        for c in self.tl.cards:
            if not (c["t_in"] <= t <= c["t_out"] + 0.35):
                continue
            a = ease3((t - c["t_in"]) / 0.35) * (1 - ease3((t - c["t_out"]) / 0.35))
            it = c["item"]
            ctx.set_source_rgba(0, 0, 0, a); ctx.paint()
            y = it.get("y", 900)
            rise = lambda d: 40 * (1 - ease3((t - c["t_in"] - d) / 0.6))
            alpha = lambda d: a * ease3((t - c["t_in"] - d) / 0.5)
            if it.get("icon"):
                icon(ctx, it["icon"], W / 2, y - 250 + rise(0), 120, (1, 1, 1), alpha(0), lw=3)
            if it.get("eyebrow"):
                self.htext(ctx, it["eyebrow"], W / 2, y - 120 + rise(0.0), 46, BLUE if not it.get("eyebrow_gray") else GRAY, alpha(0.0), font=HEAD)
            lines = it.get("title", [])
            if isinstance(lines, str):
                lines = [lines]
            sz = it.get("size", 120)
            yy = y
            for j, ln in enumerate(lines):
                grad = isinstance(ln, dict) and ln.get("grad")
                txt = ln["text"] if isinstance(ln, dict) else ln
                self.htext(ctx, txt, W / 2, yy + rise(0.08 + 0.08 * j), sz, (1, 1, 1), alpha(0.08 + 0.08 * j), font=HEAD_B, grad=grad)
                yy += sz * 1.1
            if it.get("sub"):
                self.htext(ctx, it["sub"], W / 2, yy + 20 + rise(0.3), 44, GRAY, alpha(0.3), font=HEAD)

    # --- small floating label (replaces the yellow sticker)
    def draw_word(self, ctx, t):
        for w in self.tl.words:
            if not (w["t_in"] <= t <= w["t_out"] + 0.3):
                continue
            it = w["item"]
            a = ease3((t - w["t_in"]) / 0.4) * (1 - ease3((t - w["t_out"]) / 0.3))
            y = it.get("y", 560) + 24 * (1 - ease3((t - w["t_in"]) / 0.5))
            txt, sub = it["text"], it.get("sub")
            tw = max(measure(txt, 50, True, HEAD), measure(sub or "", 30, True))
            pw, ph = tw + 90, 130 if sub else 96
            rrect(ctx, W / 2 - pw / 2, y - ph / 2, pw, ph, ph / 2 if not sub else 34)
            ctx.set_source_rgba(0.11, 0.11, 0.12, a); ctx.fill_preserve()
            ctx.set_line_width(1.5); ctx.set_source_rgba(1, 1, 1, 0.14 * a); ctx.stroke()
            if sub:
                self.htext(ctx, sub, W / 2, y - 14, 30, BLUE, a, font=HEAD)
                self.htext(ctx, txt, W / 2, y + 40, 50, (1, 1, 1), a, font=HEAD)
            else:
                self.htext(ctx, txt, W / 2, y + 18, 50, (1, 1, 1), a, font=HEAD)

    def draw_captions(self, ctx, t):
        i = self.tl.scene_at(t)
        for c0, c1, txt in self.tl.captions[i]:
            if c0 <= t < c1:
                a = ease3((t - c0) / 0.15)
                ctx.select_font_face(HEAD_B, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); ctx.set_font_size(58)
                lines = [txt]
                if ctx.text_extents(txt).x_advance > 920:
                    ws = txt.split()
                    best = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:]))))
                    lines = [" ".join(ws[:best]), " ".join(ws[best:])]
                y = 300 if len(lines) == 1 else 262
                for ln in lines:
                    self.htext(ctx, ln, W / 2 + 2, y + 3, 58, (0, 0, 0), a * 0.6, font=HEAD_B)
                    self.htext(ctx, ln, W / 2, y + 6 * (1 - a), 58, (1, 1, 1), a, font=HEAD_B)
                    y += 72

    # --- connection diagram
    def draw_flows(self, ctx, t):
        for fl in self.tl.flows:
            if not (fl["t_in"] <= t <= fl["t_out"] + 0.35):
                continue
            it = fl["item"]
            a = ease3((t - fl["t_in"]) / 0.35) * (1 - ease3((t - fl["t_out"]) / 0.35))
            ctx.set_source_rgba(0, 0, 0, a); ctx.paint()
            rise = lambda t0_, d=40: d * (1 - ease3((t - t0_) / 0.6))
            if it.get("eyebrow"):
                self.htext(ctx, it["eyebrow"], W / 2, 470 + rise(fl["t_in"]), 40, BLUE, a, font=HEAD)
            if it.get("title"):
                self.htext(ctx, it["title"], W / 2, 560 + rise(fl["t_in"] + 0.08), 84, (1, 1, 1), a * ease3((t - fl["t_in"] - 0.08) / 0.5), font=HEAD_B)
            if it.get("subtitle"):
                self.htext(ctx, it["subtitle"], W / 2, 625 + rise(fl["t_in"] + 0.16), 38, GRAY, a * ease3((t - fl["t_in"] - 0.16) / 0.5), font=HEAD)
            steps = it["steps"]; n = len(steps)
            y0 = it.get("y", 720)
            nh, nw = 150, 820
            gap = min(230, (it.get("bottom", 1600) - y0 - nh) / max(1, n - 1))
            times = [fl["t0"] + st.get("delay", 0.6 + 0.9 * j) for j, st in enumerate(steps)]
            shown = [j for j in range(n) if t >= times[j]]
            latest = shown[-1] if shown else -1
            for j in range(1, n):
                ya, yb = y0 + (j - 1) * gap + nh, y0 + j * gap
                k = ease3((t - (times[j] - 0.5)) / 0.5)
                if k <= 0:
                    continue
                ctx.set_line_width(2); ctx.set_source_rgba(1, 1, 1, 0.22 * a)
                ctx.move_to(W / 2, ya); ctx.line_to(W / 2, ya + (yb - ya) * k); ctx.stroke()
                if k >= 1:     # light pulse travelling down the line
                    ph = ((t - times[j]) * 0.8) % 1
                    py = ya + (yb - ya) * ph
                    lg = cairo.LinearGradient(0, py - 40, 0, py + 4)
                    lg.add_color_stop_rgba(0, *GRAD[0], 0); lg.add_color_stop_rgba(1, *GRAD[0], 0.95 * a)
                    ctx.set_line_width(3); ctx.set_source(lg); ctx.move_to(W / 2, max(ya, py - 40)); ctx.line_to(W / 2, py); ctx.stroke()
            for j, st in enumerate(steps):
                if t < times[j]:
                    continue
                k = ease3((t - times[j]) / 0.45)
                y = y0 + j * gap + 30 * (1 - k); x = W / 2 - nw / 2
                active = (j == latest)
                rrect(ctx, x, y, nw, nh, 30); ctx.set_source_rgba(0.11, 0.11, 0.12, a * k); ctx.fill()
                rrect(ctx, x, y, nw, nh, 30); ctx.set_line_width(2)
                if active:
                    g = cairo.LinearGradient(x, y, x + nw, y + nh)
                    g.add_color_stop_rgba(0, *GRAD[0], 0.9 * a * k); g.add_color_stop_rgba(1, *GRAD[1], 0.9 * a * k)
                    ctx.set_source(g)
                else:
                    ctx.set_source_rgba(1, 1, 1, 0.12 * a * k)
                ctx.stroke()
                ctx.arc(x + 86, y + nh / 2, 46, 0, 2 * math.pi); ctx.set_source_rgba(1, 1, 1, 0.07 * a * k); ctx.fill()
                icon(ctx, st.get("icon", "dot"), x + 86, y + nh / 2, 54, (1, 1, 1), a * k, lw=3.4)
                self.text_at(ctx, st["label"], x + 160, y + (68 if st.get("sub") else 88), 44, (1, 1, 1), False, HEAD, alpha=a * k)
                if st.get("sub"):
                    self.text_at(ctx, st["sub"], x + 160, y + 112, 32, GRAY, False, BODY_F, alpha=a * k)
                self.text_at(ctx, f"{j + 1:02d}", x + nw - 82, y + 58, 28, GRAY, False, HEAD, alpha=a * k)
            nt = it.get("notify")
            if nt:
                tn = fl["t0"] + nt.get("delay", 3.0)
                if t >= tn:
                    k = ease3((t - tn) / 0.45)
                    by = nt.get("y", 1440) + 40 * (1 - k)
                    bx, bw, bh = 100, 880, 140
                    rrect(ctx, bx, by, bw, bh, 34); ctx.set_source_rgba(0.17, 0.17, 0.18, 0.96 * a * k); ctx.fill()
                    rrect(ctx, bx + 24, by + 28, 84, 84, 20); ctx.set_source_rgba(0.19, 0.78, 0.35, a * k); ctx.fill()
                    icon(ctx, "message", bx + 66, by + 70, 52, (1, 1, 1), a * k, lw=4)
                    self.text_at(ctx, nt.get("app", "Messages"), bx + 132, by + 58, 30, (1, 1, 1), True, alpha=a * k)
                    self.text_at(ctx, "now", bx + bw - 90, by + 58, 28, GRAY, alpha=a * k)
                    txt = nt["text"]
                    while measure(txt, 36) > bw - 160 and len(txt) > 4:
                        txt = txt[:-2]
                    self.text_at(ctx, txt, bx + 132, by + 104, 36, (0.92, 0.92, 0.94), alpha=a * k)

    def cover(self):
        cv = self.sb["cover"]
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H); ctx = cairo.Context(surf)
        self.draw_bg(ctx, 0)
        y = cv.get("y", 820)
        if cv.get("icon"):
            icon(ctx, cv["icon"], W / 2, y - 300, 150, (1, 1, 1), lw=3)
        if cv.get("eyebrow"):
            self.htext(ctx, cv["eyebrow"], W / 2, y - 140, 52, BLUE, font=HEAD)
        yy = y
        for ln in cv["title"]:
            grad = isinstance(ln, dict) and ln.get("grad")
            txt = ln["text"] if isinstance(ln, dict) else ln
            self.htext(ctx, txt, W / 2, yy, cv.get("size", 150), (1, 1, 1), font=HEAD_B, grad=grad)
            yy += cv.get("size", 150) * 1.1
        if cv.get("sub"):
            self.htext(ctx, cv["sub"], W / 2, yy + 30, 52, GRAY, font=HEAD)
        return surf


def mix_clean_audio(tl, voice, path):
    """Soft ambient bed + restrained UI sounds."""
    n = int((tl.total + 0.3) * SR)
    v = np.zeros(n, np.float32); v[:min(n, len(voice))] = voice[:n]
    voice = v / max(1e-6, np.abs(v).max()) * 0.9
    rng = np.random.default_rng(5)
    t = np.arange(n) / SR
    chords = [[220.0, 277.18, 329.63, 415.30], [196.0, 246.94, 293.66, 369.99], [164.81, 207.65, 246.94, 329.63], [185.0, 233.08, 277.18, 349.23]]
    pad = np.zeros(n, np.float32); seg = 4.0
    for ci in range(int(tl.total / seg) + 2):
        a0, a1 = int(ci * seg * SR), min(n, int((ci + 1) * seg * SR + 2.0 * SR))
        if a0 >= n:
            break
        tt = t[a0:a1] - ci * seg
        env = np.minimum(1, tt / 1.2) * np.minimum(1, np.maximum(0, (seg + 2.0 - tt) / 2.0))
        for f in chords[ci % 4]:
            pad[a0:a1] += (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * 2 * f * tt + 0.3)) * env * 0.022
    pulse = np.zeros(n, np.float32)
    for bi in range(int(tl.total / 0.5) + 1):
        s = int(bi * 0.5 * SR); L = min(n - s, int(0.12 * SR))
        if L <= 0:
            break
        tt = np.arange(L) / SR
        pulse[s:s + L] += np.sin(2 * np.pi * 880 * tt) * np.exp(-tt / 0.02) * (0.018 if bi % 2 else 0.03)
    music = pad + pulse
    env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
    music *= 1 - 0.5 * np.clip(env / 0.03, 0, 1)
    sfx = np.zeros(n, np.float32)

    def add(sig, at):
        s = int(at * SR)
        if 0 <= s < n:
            sfx[s:s + len(sig)] += sig[: n - s]

    def tick(at, f=1200, vol=0.08, d=0.06):
        L = int(d * SR); tt = np.arange(L) / SR
        add(np.sin(2 * np.pi * f * tt) * np.exp(-tt / (d / 4)) * vol, at)
    for ty in tl.typing:
        k = ty["t0"]
        while k < ty["t1"]:
            L = int(0.02 * SR); tt = np.arange(L) / SR
            add(bandnoise(L, 3000, 9000, rng) * np.exp(-tt / 0.003) * 0.05, k)
            k += 1 / 16 * (0.7 + 0.6 * rng.random())
    for m in tl.msgs:
        tick(m["t"] if m["role"] == "user" else m["s0"], 1000 if m["role"] == "user" else 1500, 0.07)
    for mk in tl.marks:
        tick(mk["t"], 1800, 0.05)
    for c in tl.cards:
        L = int(0.7 * SR); tt = np.arange(L) / SR
        add(bandnoise(L, 200, 2500, rng) * np.sin(np.pi * tt / 0.7) ** 2 * 0.05, c["t_in"] - 0.2)
    for fl in tl.flows:
        for j, st in enumerate(fl["item"]["steps"]):
            tick(fl["t0"] + st.get("delay", 0.6 + 0.9 * j), 1100 + 150 * j, 0.07)
        nt = fl["item"].get("notify")
        if nt:
            at = fl["t0"] + nt.get("delay", 3.0)
            tick(at, 1568, 0.09, 0.25); tick(at + 0.12, 2093, 0.07, 0.3)
    for w in tl.words:
        tick(w["t_in"], 1300, 0.05)
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


def main():
    global R
    args = sys.argv[1:]
    if "--audio" not in args:
        sys.exit("chat style needs --audio VO.mp3 (the vidIQ voiceover)")
    audio = args[args.index("--audio") + 1]
    preview = None
    if "--preview" in args:
        v = args[args.index("--preview") + 1]
        preview = [] if v in ("", "auto") else [float(x) for x in v.split(",")]
    cover_only = args[args.index("--cover") + 1] if "--cover" in args else None
    sbp, outp = args[0], args[1]
    sb = json.load(open(sbp))
    texts = [s["vo"] for s in sb["scenes"]]
    starts, ends, D = scene_bounds_from_audio(audio, texts, 0.6, sb.get("min_pause", 0.25))
    if sb.get("cuts"):
        assert len(sb["cuts"]) == len(texts) - 1, "cuts needs one time per paragraph break"
        starts, ends = [0.0] + list(sb["cuts"]), list(sb["cuts"]) + [ends[-1]]
    voice = load_audio(audio)
    speech = [(s + (0.15 if i else 0.05), e - 0.25) for i, (s, e) in enumerate(zip(starts, ends))]
    speech[-1] = (speech[-1][0], D)
    tl = ChatTimeline(sb, starts, ends, speech)
    make_captions(tl)
    R = CleanChatRenderer(tl) if sb.get("look") == "clean" else ChatRenderer(tl)
    work = os.path.join(ROOT, "build", os.path.splitext(os.path.basename(sbp))[0])
    os.makedirs(work, exist_ok=True)
    print(f"duration {tl.total:.1f}s, {len(sb['scenes'])} scenes", flush=True)
    for i, (s, e) in enumerate(zip(starts, ends)):
        print(f"  scene {i + 1}: {s:5.1f}-{e:5.1f}s  {texts[i][:60]}")
    for m in tl.msgs:
        if m["role"] == "ai":
            print(f"  AI answer {m['t']:.1f}s, streams {m['s0']:.1f}-{m['s1']:.1f}s, {len(m['text'])} chars")
        else:
            print(f"  user message sent at {m['t']:.1f}s")
    if cover_only:
        if not sb.get("cover"):
            sys.exit("storyboard has no \"cover\" block")
        R.cover().write_to_png(cover_only); print("cover:", cover_only); return
    if preview is not None:
        if not preview:
            preview = [min(e - 0.3, s + max(1.4, (e - s) * 0.6)) for s, e in zip(starts, ends)]
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
    shutil.rmtree(fdir, ignore_errors=True); os.makedirs(fdir)
    nf = int(tl.total * FPS)
    jobs = [(a, min(nf, a + 30), fdir) for a in range(0, nf, 30)]
    done = 0
    with Pool(os.cpu_count() or 2) as pool:
        for k in pool.imap_unordered(render_range, jobs):
            done += k
            if done % 300 < 30:
                print(f"  {done}/{nf} frames", flush=True)
    if sb.get("cover"):
        cpath = os.path.splitext(outp)[0] + "_cover.png"
        R.cover().write_to_png(cpath)
        for f in range(min(nf, sb["cover"].get("frames", 2))):
            shutil.copy(cpath, os.path.join(fdir, f"f{f:05d}.png"))
        print("cover:", cpath, flush=True)
    wav = os.path.join(work, "mix.wav")
    (mix_clean_audio if sb.get("look") == "clean" else mix_chat_audio)(tl, voice, wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(fdir, "f%05d.png"),
                    "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "4M", "-bufsize", "8M",
                    "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                    "-shortest", "-movflags", "+faststart", outp], check=True)
    print("wrote", outp, os.path.getsize(outp) // 1_000_000, "MB")


if __name__ == "__main__":
    main()
