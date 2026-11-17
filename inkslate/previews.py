"""Live previews in the PC app: the tablet toolbar as it will look, and the screen area editor."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .icons import svg  # noqa: E402
from .payload import FULL  # noqa: E402


def _rgba(spec, alpha=1.0):
    c = Gdk.RGBA()
    if not c.parse(spec or "#000000"):
        c.parse("#000000")
    c.alpha = alpha
    return c


def _luminance(c):
    return 0.299 * c.red + 0.587 * c.green + 0.114 * c.blue


def tablet_area(aspect, want):
    """Same as tabletArea() in inkslate.js: the used part of the tablet, normalized."""
    if not want or not aspect:
        return dict(FULL)
    if aspect > want:
        w = want / aspect
        return {"x": (1 - w) / 2, "y": 0.0, "w": w, "h": 1.0}
    h = aspect / want
    return {"x": 0.0, "y": (1 - h) / 2, "w": 1.0, "h": h}


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def _bezel(w):
    return max(6, round(w * 0.03))


def _draw_tablet_screen(cr, x, y, w, h, background, area_aspect, show_area):
    """The tablet's screen in energy saving: background, unused bands, area outline."""
    bezel = _bezel(w)
    cr.set_source_rgba(0.12, 0.12, 0.13, 1)
    _rounded(cr, x - bezel, y - bezel, w + 2 * bezel, h + 2 * bezel, bezel * 1.8)
    cr.fill()
    bg = _rgba(background)
    cr.set_source_rgba(bg.red, bg.green, bg.blue, 1)
    cr.rectangle(x, y, w, h)
    cr.fill()
    r = tablet_area(w / h if h else 0, area_aspect)
    if show_area and (r["w"] < 0.999 or r["h"] < 0.999):
        light = _luminance(bg) > 0.5
        shade = (0, 0, 0, 0.07) if light else (1, 1, 1, 0.07)
        cr.set_source_rgba(*shade)
        x0, y0 = x + r["x"] * w, y + r["y"] * h
        x1, y1 = x0 + r["w"] * w, y0 + r["h"] * h
        cr.rectangle(x, y, w, y0 - y)
        cr.rectangle(x, y1, w, y + h - y1)
        cr.rectangle(x, y0, x0 - x, y1 - y0)
        cr.rectangle(x1, y0, x + w - x1, y1 - y0)
        cr.fill()
        cr.set_source_rgba(*shade[:3], 0.3)
        cr.set_line_width(1)
        cr.rectangle(x0 + 0.5, y0 + 0.5, x1 - x0 - 1, y1 - y0 - 1)
        cr.stroke()


class ToolbarPreview(Gtk.Overlay):
    """A scaled-down tablet with the toolbar built from the same settings and icons."""

    WIDTH = 600

    def __init__(self):
        super().__init__(halign=Gtk.Align.CENTER, margin_top=18, margin_bottom=18)
        self.screen = Gtk.DrawingArea()
        self.screen.set_draw_func(self._draw)
        self.set_child(self.screen)
        self.bar = None
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.tablet = {}
        self.toolbar = {}
        self.active = set()
        self.tablet_size = (1280, 800)

    def update(self, tablet_cfg, buttons, tablet_size=None, active=()):
        """buttons: [{"action", "icon", "title", "label"}] as built by payload.build()."""
        self.tablet = tablet_cfg
        self.toolbar = tablet_cfg["toolbar"]
        self.buttons = buttons
        self.active = set(active)
        if tablet_size and tablet_size[0] and tablet_size[1]:
            self.tablet_size = tablet_size
        tw, th = self.tablet_size
        self.scale = self.WIDTH / tw
        b = _bezel(self.WIDTH)
        self.screen.set_size_request(self.WIDTH + 2 * b, round(self.WIDTH * th / tw) + 2 * b)
        self._rebuild()
        self.screen.queue_draw()

    def _draw(self, _area, cr, width, height):
        b = _bezel(self.WIDTH)
        _draw_tablet_screen(cr, b, b, width - 2 * b, height - 2 * b,
                            self.tablet.get("screen", {}).get("background"),
                            self.tablet.get("_area_aspect", 0),
                            self.tablet.get("screen", {}).get("show_area", True))

    def _rebuild(self):
        if self.bar is not None:
            self.remove_overlay(self.bar)
            self.bar = None
        tb = self.toolbar
        if not tb.get("enabled", True) or not self.buttons:
            return
        side = tb.get("side", "left")
        vertical = side in ("left", "right")
        s = max(14, round(max(32, min(120, tb.get("size", 54))) * self.scale))
        gap = max(2, round(s * 0.11))
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                      spacing=gap)
        bar.set_name("inkslate-preview-bar")
        bar.set_opacity(max(0.1, min(1.0, tb.get("opacity", 0.6))))
        margin = max(2, round(6 * self.scale)) + _bezel(self.WIDTH)
        if side == "left":
            bar.set_halign(Gtk.Align.START)
            bar.set_valign(Gtk.Align.CENTER)
            bar.set_margin_start(margin)
        elif side == "right":
            bar.set_halign(Gtk.Align.END)
            bar.set_valign(Gtk.Align.CENTER)
            bar.set_margin_end(margin)
        elif side == "top":
            bar.set_halign(Gtk.Align.CENTER)
            bar.set_valign(Gtk.Align.START)
            bar.set_margin_top(margin)
        else:
            bar.set_halign(Gtk.Align.CENTER)
            bar.set_valign(Gtk.Align.END)
            bar.set_margin_bottom(margin)

        light = tb.get("theme") == "light"
        fg = "#222222" if light else "#eeeeee"
        accent = tb.get("accent") or "#3584e4"
        labels = tb.get("labels", False)
        for spec in self.buttons:
            on = spec["action"] in self.active
            btn = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                          halign=Gtk.Align.CENTER)
            btn.add_css_class("ink-btn")
            if on:
                btn.add_css_class("ink-on")
            btn.set_size_request(s, s)
            btn.set_tooltip_text(spec.get("title", ""))
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                            vexpand=True, spacing=1)
            color = "#ffffff" if on else fg
            px = max(8, round(s * (0.4 if labels else 0.48)))
            try:
                tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(
                    svg(spec.get("icon", spec["action"]), color, px * 2).encode()))
                img = Gtk.Image.new_from_paintable(tex)
                img.set_pixel_size(px)
                inner.append(img)
            except GLib.Error:
                pass
            text = spec.get("label") or ((spec.get("short") or spec.get("title")) if labels else "")
            if text:
                lbl = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END, max_width_chars=6)
                lbl.add_css_class("ink-lbl")
                inner.append(lbl)
            btn.append(inner)
            bar.append(btn)

        radius = round(s * 0.26)
        self.css.load_from_string(f"""
#inkslate-preview-bar {{ background: {"rgba(246,246,246,0.92)" if light else "rgba(36,36,36,0.86)"};
  border-radius: {radius}px; padding: {gap}px; box-shadow: 0 1px 6px rgba(0,0,0,0.35); }}
#inkslate-preview-bar .ink-btn {{ background: {"rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.08)"};
  border-radius: {round(s * 0.19)}px; color: {fg}; }}
#inkslate-preview-bar .ink-on {{ background: {accent}; color: #fff; }}
#inkslate-preview-bar .ink-lbl {{ font-size: {max(6, round(s * 0.2))}px; font-weight: 600; }}
""")
        self.add_overlay(bar)
        self.bar = bar


class AreaView(Gtk.DrawingArea):
    """Left: the PC's monitors with the part the tablet maps to. Right: the tablet with the
    part that is used. Dragging on the monitors draws a custom area."""

    def __init__(self, on_region):
        super().__init__(hexpand=True, margin_top=12, margin_bottom=12)
        self.set_content_height(250)
        self.set_draw_func(self._draw)
        self.on_region = on_region
        self.monitors = []
        self.monitor = ""
        self.region = dict(FULL)
        self.keep_aspect = False
        self.tablet_aspect = 1.6
        self.background = "#000000"
        self.show_area = True
        self.accent = "#3584e4"
        self._layout = None
        self._drag = None
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

    def update(self, monitors, area_cfg, tablet_aspect, background, show_area, accent):
        self.monitors = monitors or [{"connector": "", "name": "", "x": 0, "y": 0, "w": 1920, "h": 1080}]
        self.monitor = area_cfg.get("monitor", "")
        self.region = dict(FULL, **(area_cfg.get("region") or {}))
        self.keep_aspect = area_cfg.get("keep_aspect", False)
        self.tablet_aspect = tablet_aspect or 1.6
        self.background = background
        self.show_area = show_area
        self.accent = accent or "#3584e4"
        self.queue_draw()

    # geometry -------------------------------------------------------------

    def _bbox(self):
        x0 = min(m["x"] for m in self.monitors)
        y0 = min(m["y"] for m in self.monitors)
        x1 = max(m["x"] + m["w"] for m in self.monitors)
        y1 = max(m["y"] + m["h"] for m in self.monitors)
        return x0, y0, x1 - x0, y1 - y0

    def _target(self):
        for m in self.monitors:
            if self.monitor and m["connector"] == self.monitor:
                return m["x"], m["y"], m["w"], m["h"]
        return self._bbox()

    def _compute_layout(self, width, height):
        pad = 16
        tab_w = min(width * 0.32, (height - 2 * pad) * self.tablet_aspect)
        scr_w = width - tab_w - 4 * pad
        bx, by, bw, bh = self._bbox()
        k = min(scr_w / bw, (height - 2 * pad - 18) / bh)
        sx = pad + (scr_w - bw * k) / 2
        sy = pad + (height - 18 - bh * k) / 2
        tab_h = tab_w / self.tablet_aspect
        tx = width - pad - tab_w
        ty = (height - tab_h) / 2
        return {"k": k, "sx": sx, "sy": sy, "bbox": (bx, by, bw, bh), "tablet": (tx, ty, tab_w, tab_h)}

    def _to_screen(self, L, px, py):
        bx, by, _bw, _bh = L["bbox"]
        return L["sx"] + (px - bx) * L["k"], L["sy"] + (py - by) * L["k"]

    def _from_screen(self, L, x, y):
        bx, by, _bw, _bh = L["bbox"]
        return bx + (x - L["sx"]) / L["k"], by + (y - L["sy"]) / L["k"]

    # drawing --------------------------------------------------------------

    def _draw(self, _area, cr, width, height):
        L = self._compute_layout(width, height)
        self._layout = L
        fg = self.get_color()
        accent = _rgba(self.accent)
        cr.select_font_face("sans")
        cr.set_font_size(11)
        for m in self.monitors:
            x, y = self._to_screen(L, m["x"], m["y"])
            w, h = m["w"] * L["k"], m["h"] * L["k"]
            chosen = not self.monitor or m["connector"] == self.monitor
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10 if chosen else 0.04)
            _rounded(cr, x + 1, y + 1, w - 2, h - 2, 4)
            cr.fill_preserve()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35 if chosen else 0.15)
            cr.set_line_width(1)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(x + 6, y + 15)
            cr.show_text(m.get("connector") or "")

        tx, ty, tw, th = self._target()
        r = self._drag or self.region
        x, y = self._to_screen(L, tx + r["x"] * tw, ty + r["y"] * th)
        w, h = r["w"] * tw * L["k"], r["h"] * th * L["k"]
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.28)
        cr.rectangle(x, y, w, h)
        cr.fill_preserve()
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
        cr.set_line_width(2)
        cr.stroke()

        # the tablet, with the used part in the same colour as the area on the PC
        tx, ty, tw, th = L["tablet"]
        aspect = (r["w"] * self._target()[2]) / (r["h"] * self._target()[3]) if self.keep_aspect else 0
        _draw_tablet_screen(cr, tx, ty, tw, th, self.background, aspect, True)
        ta = tablet_area(tw / th, aspect)
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
        cr.set_line_width(2)
        cr.rectangle(tx + ta["x"] * tw + 1, ty + ta["y"] * th + 1, ta["w"] * tw - 2, ta["h"] * th - 2)
        cr.stroke()

        # arrow from the tablet to the area
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45)
        cr.set_line_width(1.5)
        ax = tx - 22
        cr.move_to(ax + 14, ty + th / 2)
        cr.line_to(ax, ty + th / 2)
        cr.stroke()
        cr.move_to(ax, ty + th / 2)
        cr.line_to(ax + 5, ty + th / 2 - 4)
        cr.line_to(ax + 5, ty + th / 2 + 4)
        cr.close_path()
        cr.fill()

    # dragging -------------------------------------------------------------

    def _norm(self, x, y):
        L = self._layout
        px, py = self._from_screen(L, x, y)
        tx, ty, tw, th = self._target()
        return min(1.0, max(0.0, (px - tx) / tw)), min(1.0, max(0.0, (py - ty) / th))

    def _drag_begin(self, gesture, x, y):
        if self._layout is None:
            return
        self._start = self._norm(x, y)
        self._drag = None

    def _drag_update(self, gesture, dx, dy):
        if self._layout is None:
            return
        ok, sx, sy = gesture.get_start_point()
        u0, v0 = self._start
        u1, v1 = self._norm(sx + dx, sy + dy)
        self._drag = {"x": min(u0, u1), "y": min(v0, v1), "w": abs(u1 - u0), "h": abs(v1 - v0)}
        self.queue_draw()

    def _drag_end(self, gesture, dx, dy):
        r = self._drag
        self._drag = None
        if r and r["w"] > 0.05 and r["h"] > 0.05:
            self.region = {k: round(v, 4) for k, v in r.items()}
            self.on_region(self.region)
        self.queue_draw()
