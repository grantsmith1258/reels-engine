"""Countryballs: flag-coloured balls with cartoon eyes, drawn with cairo (no image files, no credits).

Storyboard: a `pictures` item with "ball": "FRA" (ISO-3 code from FLAGS below) or a custom flag
{"h": ["#hex", ...]} (horizontal stripes, top to bottom) / {"v": [...]} (vertical stripes, left to right).
Options: "mood": happy | angry | sad | shock | smug, "look": left | right | ahead,
"say": "MINE!" (speech bubble), "say_delay": seconds after appearing, plus the usual size / motion / at / x,y / label.
"""
import math
import cairo

R, WH, BL, YE, GR, OR, BK = "#d52b1e", "#ffffff", "#0b3fa8", "#ffce00", "#009b3a", "#ff8c00", "#111111"

FLAGS = {
    "FRA": {"v": ["#0b3fa8", WH, "#e1251b"]}, "NLD": {"h": ["#c8102e", WH, "#1d3f94"]},
    "DEU": {"h": [BK, "#dd0000", YE]}, "ITA": {"v": ["#009246", WH, "#ce2b37"]},
    "ESP": {"h": ["#c60b1e", YE, YE, "#c60b1e"]}, "BEL": {"v": [BK, "#fdda24", "#ef3340"]},
    "IRL": {"v": ["#169b62", WH, "#ff883e"]}, "RUS": {"h": [WH, "#0039a6", "#d52b1e"]},
    "POL": {"h": [WH, "#dc143c"]}, "AUT": {"h": ["#ed2939", WH, "#ed2939"]},
    "UKR": {"h": ["#0057b7", "#ffd700"]}, "MEX": {"v": ["#006847", WH, "#ce1126"]},
    "PRT": {"v": ["#006600", "#006600", "#ff0000", "#ff0000", "#ff0000"]},
    "HUN": {"h": ["#ce2939", WH, "#477050"]}, "BGR": {"h": [WH, "#00966e", "#d62612"]},
    "ROU": {"v": ["#002b7f", "#fcd116", "#ce1126"]}, "LTU": {"h": ["#fdb913", "#006a44", "#c1272d"]},
    "EST": {"h": ["#0072ce", BK, WH]}, "COL": {"h": ["#fcd116", "#fcd116", "#003893", "#ce1126"]},
    "PER": {"v": ["#d91023", WH, "#d91023"]}, "NGA": {"v": ["#008751", WH, "#008751"]},
    "IND": {"h": ["#ff9933", WH, "#138808"], "disc": ("#000080", 0.17, (0.5, 0.5))},
    "JPN": {"h": [WH], "disc": ("#bc002d", 0.32, (0.5, 0.5))},
    "BGD": {"h": ["#006a4e"], "disc": ("#f42a41", 0.3, (0.45, 0.5))},
    "CHN": {"h": ["#de2910"], "star": (YE, 0.16, (0.3, 0.33))},
    "VNM": {"h": ["#da251d"], "star": (YE, 0.26, (0.5, 0.5))},
    "MAR": {"h": ["#c1272d"], "star": ("#006233", 0.26, (0.5, 0.5))},
    "TUR": {"h": ["#e30a17"], "crescent": True},
    "CAN": {"v": ["#d80621", WH, WH, "#d80621"], "leaf": True},
    "DNK": {"h": ["#c8102e"], "nordic": WH}, "SWE": {"h": ["#006aa7"], "nordic": "#fecc00"},
    "NOR": {"h": ["#ba0c2f"], "nordic": WH, "nordic_in": "#00205b"}, "FIN": {"h": [WH], "nordic": "#002f6c"},
    "ISL": {"h": ["#02529c"], "nordic": WH, "nordic_in": "#dc1e35"},
    "CHE": {"h": ["#da291c"], "swiss": True},
    "GBR": {"h": ["#012169"], "union": True}, "USA": {"usa": True},
    "GRC": {"greece": True}, "BRA": {"brazil": True},
}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _star(ctx, cx, cy, r):
    for k in range(10):
        ang = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.4
        (ctx.move_to if k == 0 else ctx.line_to)(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
    ctx.close_path()


def _flag(ctx, spec, x0, y0, s):
    """Paint the flag into the square (x0, y0, s, s); the caller clips to the ball."""
    if isinstance(spec, str):
        spec = FLAGS.get(spec.upper(), {"h": ["#888888"]})
    if spec.get("usa"):
        for k in range(13):
            ctx.rectangle(x0, y0 + k * s / 13, s, s / 13 + 1)
            ctx.set_source_rgb(*_rgb("#b22234" if k % 2 == 0 else WH)); ctx.fill()
        ctx.rectangle(x0, y0, s * 0.5, s * 7 / 13); ctx.set_source_rgb(*_rgb("#3c3b6e")); ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        for i in range(4):
            for j in range(3):
                _star(ctx, x0 + s * (0.08 + 0.12 * i), y0 + s * (0.1 + 0.15 * j), s * 0.035); ctx.fill()
        return
    if spec.get("greece"):
        for k in range(9):
            ctx.rectangle(x0, y0 + k * s / 9, s, s / 9 + 1)
            ctx.set_source_rgb(*_rgb("#0d5eaf" if k % 2 == 0 else WH)); ctx.fill()
        ctx.rectangle(x0, y0, s * 0.45, s * 5 / 9); ctx.set_source_rgb(*_rgb("#0d5eaf")); ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.rectangle(x0 + s * 0.18, y0, s * 0.09, s * 5 / 9); ctx.fill()
        ctx.rectangle(x0, y0 + s * 0.23, s * 0.45, s * 0.1); ctx.fill()
        return
    if spec.get("brazil"):
        ctx.rectangle(x0, y0, s, s); ctx.set_source_rgb(*_rgb("#009c3b")); ctx.fill()
        ctx.move_to(x0 + s * 0.5, y0 + s * 0.12); ctx.line_to(x0 + s * 0.95, y0 + s * 0.5)
        ctx.line_to(x0 + s * 0.5, y0 + s * 0.88); ctx.line_to(x0 + s * 0.05, y0 + s * 0.5); ctx.close_path()
        ctx.set_source_rgb(*_rgb("#ffdf00")); ctx.fill()
        ctx.arc(x0 + s * 0.5, y0 + s * 0.5, s * 0.22, 0, 2 * math.pi); ctx.set_source_rgb(*_rgb("#002776")); ctx.fill()
        return
    cols = spec.get("h") or spec.get("v")
    n = len(cols)
    for k, c in enumerate(cols):
        if spec.get("h"):
            ctx.rectangle(x0, y0 + k * s / n, s, s / n + 1)
        else:
            ctx.rectangle(x0 + k * s / n, y0, s / n + 1, s)
        ctx.set_source_rgb(*_rgb(c)); ctx.fill()
    if spec.get("disc"):
        c, r, (cx, cy) = spec["disc"]
        ctx.arc(x0 + s * cx, y0 + s * cy, s * r, 0, 2 * math.pi); ctx.set_source_rgb(*_rgb(c)); ctx.fill()
    if spec.get("star"):
        c, r, (cx, cy) = spec["star"]
        _star(ctx, x0 + s * cx, y0 + s * cy, s * r); ctx.set_source_rgb(*_rgb(c)); ctx.fill()
    if spec.get("crescent"):
        ctx.arc(x0 + s * 0.42, y0 + s * 0.5, s * 0.22, 0, 2 * math.pi); ctx.set_source_rgb(1, 1, 1); ctx.fill()
        ctx.arc(x0 + s * 0.47, y0 + s * 0.5, s * 0.18, 0, 2 * math.pi); ctx.set_source_rgb(*_rgb("#e30a17")); ctx.fill()
        _star(ctx, x0 + s * 0.66, y0 + s * 0.5, s * 0.09); ctx.set_source_rgb(1, 1, 1); ctx.fill()
    if spec.get("leaf"):
        _star(ctx, x0 + s * 0.5, y0 + s * 0.5, s * 0.17); ctx.set_source_rgb(*_rgb("#d80621")); ctx.fill()
    if spec.get("nordic"):
        ctx.set_source_rgb(*_rgb(spec["nordic"]))
        ctx.rectangle(x0 + s * 0.28, y0, s * 0.16, s); ctx.fill(); ctx.rectangle(x0, y0 + s * 0.42, s, s * 0.16); ctx.fill()
        if spec.get("nordic_in"):
            ctx.set_source_rgb(*_rgb(spec["nordic_in"]))
            ctx.rectangle(x0 + s * 0.32, y0, s * 0.08, s); ctx.fill(); ctx.rectangle(x0, y0 + s * 0.46, s, s * 0.08); ctx.fill()
    if spec.get("swiss"):
        ctx.set_source_rgb(1, 1, 1)
        ctx.rectangle(x0 + s * 0.42, y0 + s * 0.2, s * 0.16, s * 0.6); ctx.fill()
        ctx.rectangle(x0 + s * 0.2, y0 + s * 0.42, s * 0.6, s * 0.16); ctx.fill()
    if spec.get("union"):
        ctx.set_line_cap(cairo.LINE_CAP_SQUARE)
        for w, c in ((s * 0.2, WH), (s * 0.07, "#c8102e")):
            ctx.set_source_rgb(*_rgb(c)); ctx.set_line_width(w)
            ctx.move_to(x0, y0); ctx.line_to(x0 + s, y0 + s); ctx.move_to(x0 + s, y0); ctx.line_to(x0, y0 + s); ctx.stroke()
        for w, c in ((s * 0.3, WH), (s * 0.18, "#c8102e")):
            ctx.set_source_rgb(*_rgb(c))
            ctx.rectangle(x0 + s / 2 - w / 2, y0, w, s); ctx.fill(); ctx.rectangle(x0, y0 + s / 2 - w / 2, s, w); ctx.fill()


def draw_ball(ctx, flag, size, mood="happy", look="ahead", blink=False):
    """Draw a countryball centred at the origin, `size` px across."""
    r = size / 2
    # shadow
    ctx.save(); ctx.translate(0, r * 0.95); ctx.scale(1, 0.18)
    ctx.arc(0, 0, r * 0.85, 0, 2 * math.pi); ctx.restore()
    ctx.set_source_rgba(0, 0, 0, 0.35); ctx.fill()
    # body with flag
    ctx.save(); ctx.arc(0, 0, r, 0, 2 * math.pi); ctx.clip()
    _flag(ctx, flag, -r, -r, size)
    g = cairo.RadialGradient(-r * 0.35, -r * 0.45, r * 0.1, 0, 0, r * 1.05)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.28); g.add_color_stop_rgba(0.55, 1, 1, 1, 0); g.add_color_stop_rgba(1, 0, 0, 0, 0.28)
    ctx.set_source(g); ctx.paint()
    ctx.restore()
    ctx.arc(0, 0, r, 0, 2 * math.pi); ctx.set_source_rgb(0.05, 0.05, 0.05); ctx.set_line_width(max(3, size * 0.035)); ctx.stroke()
    # eyes: classic countryball white eyes, no pupils
    dx = {"left": -0.16, "right": 0.16}.get(look, 0.0) * r
    ew, eh = r * 0.2, r * (0.04 if blink else (0.33 if mood == "shock" else 0.27))
    for side in (-1, 1):
        ex, ey = dx + side * r * 0.3, -r * 0.08
        ctx.save(); ctx.translate(ex, ey); ctx.scale(ew, eh); ctx.arc(0, 0, 1, 0, 2 * math.pi); ctx.restore()
        ctx.set_source_rgb(1, 1, 1); ctx.fill_preserve()
        ctx.set_source_rgb(0.05, 0.05, 0.05); ctx.set_line_width(max(2.5, size * 0.025)); ctx.stroke()
        if mood in ("angry", "sad", "smug") and not blink:      # eyelid cut gives the expression
            y_in, y_out = {"angry": (ey + 0.35 * eh, ey - 0.85 * eh), "sad": (ey - 0.85 * eh, ey + 0.1 * eh),
                           "smug": (ey - 0.02 * eh, ey - 0.02 * eh)}[mood]
            xi, xo = ex - side * ew * 1.3, ex + side * ew * 1.3
            body = _flag_color_at(flag, ex, ey - eh * 0.6, size)
            lw = max(2.5, size * 0.025)
            ctx.save()
            ctx.new_path()
            ctx.save(); ctx.translate(ex, ey); ctx.scale(ew, eh); ctx.arc(0, 0, 1.0, 0, 2 * math.pi); ctx.restore()
            ctx.clip()
            ctx.new_path()
            ctx.move_to(xi, ey - eh * 2); ctx.line_to(xo, ey - eh * 2); ctx.line_to(xo, y_out); ctx.line_to(xi, y_in); ctx.close_path()
            ctx.set_source_rgb(*body); ctx.fill()
            ctx.move_to(xi, y_in); ctx.line_to(xo, y_out)
            ctx.set_source_rgb(0.05, 0.05, 0.05); ctx.set_line_width(lw * 1.6); ctx.stroke()
            ctx.restore()
            ctx.new_path()
            ctx.save(); ctx.translate(ex, ey); ctx.scale(ew, eh); ctx.arc(0, 0, 1, 0, 2 * math.pi); ctx.restore()
            ctx.set_source_rgb(0.05, 0.05, 0.05); ctx.set_line_width(lw); ctx.stroke()


def _flag_color_at(flag, x, y, size):
    """Approximate flag colour at a point (for eyelids): render one pixel."""
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1)
    c = cairo.Context(surf); c.translate(-(x + size / 2), -(y + size / 2))
    _flag(c, flag, 0, 0, size)
    surf.flush()
    b = bytes(surf.get_data()[:4])
    return (b[2] / 255, b[1] / 255, b[0] / 255)


def draw_bubble(ctx, text, x, y, a, size=46):
    """White comic speech bubble whose tail points down to (x, y)."""
    ctx.save()
    ctx.select_font_face("Anton", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); ctx.set_font_size(size)
    tw = ctx.text_extents(text).x_advance
    bw, bh = tw + size * 0.9, size * 1.5
    bx, by = x - bw / 2, y - bh - size * 0.55
    r = bh * 0.45
    ctx.new_path()
    ctx.arc(bx + bw - r, by + r, r, -math.pi / 2, 0); ctx.arc(bx + bw - r, by + bh - r, r, 0, math.pi / 2)
    ctx.line_to(x + size * 0.25, by + bh); ctx.line_to(x, y); ctx.line_to(x - size * 0.15, by + bh)
    ctx.arc(bx + r, by + bh - r, r, math.pi / 2, math.pi); ctx.arc(bx + r, by + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()
    ctx.set_source_rgba(1, 1, 1, a); ctx.fill_preserve()
    ctx.set_source_rgba(0.05, 0.05, 0.05, a); ctx.set_line_width(4); ctx.stroke()
    ctx.move_to(x - tw / 2, by + bh / 2 + size * 0.36); ctx.set_source_rgba(0.05, 0.05, 0.05, a); ctx.show_text(text)
    ctx.restore()
