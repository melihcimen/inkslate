"""Pressure curve preview with the live pen pressure drawn on top."""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402


def apply_curve(p, curve):
    t = (p - curve["in_min"]) / max(1e-6, curve["in_max"] - curve["in_min"])
    t = min(1.0, max(0.0, t))
    return t ** max(0.05, curve["gamma"])


class CurveView(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.curve = {"in_min": 0.0, "in_max": 1.0, "gamma": 1.0}
        self.sample = None          # (raw, out, contact, eraser)
        self.sample_time = 0.0
        self.peak_raw = 0.0
        self.set_content_height(220)
        self.set_hexpand(True)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_draw_func(self._draw)
        self._fade = None

    def set_curve(self, curve):
        self.curve = dict(curve)
        self.queue_draw()

    def push_sample(self, raw, out, contact, eraser):
        self.sample = (raw, out, contact, eraser)
        self.sample_time = time.monotonic()
        if contact:
            self.peak_raw = max(self.peak_raw, raw)
        self.queue_draw()
        if self._fade is None:
            self._fade = GLib.timeout_add(250, self._fade_tick)

    def reset_peak(self):
        self.peak_raw = 0.0
        self.queue_draw()

    def _fade_tick(self):
        if self.sample and time.monotonic() - self.sample_time > 1.5:
            self.sample = None
            self.queue_draw()
            self._fade = None
            return False
        return True

    def _draw(self, area, cr, width, height):
        fg = self.get_color()
        try:
            accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        except AttributeError:  # libadwaita < 1.6 has no accent color API
            accent = Gdk.RGBA()
            accent.parse("#3584e4")
        pad = 24
        w, h = width - 2 * pad, height - 2 * pad
        side = min(w, h * 1.6)
        x0 = pad + (w - side) / 2
        y0 = pad
        h_ = h

        def to_xy(px, py):
            return x0 + px * side, y0 + (1 - py) * h_

        # frame and grid
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        cr.set_line_width(1)
        for i in range(5):
            gx, _ = to_xy(i / 4, 0)
            cr.move_to(gx, y0)
            cr.line_to(gx, y0 + h_)
            _, gy = to_xy(0, i / 4)
            cr.move_to(x0, gy)
            cr.line_to(x0 + side, gy)
        cr.stroke()

        # identity (no change) as dashed reference
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
        cr.set_dash([4, 4])
        cr.move_to(*to_xy(0, 0))
        cr.line_to(*to_xy(1, 1))
        cr.stroke()
        cr.set_dash([])

        # peak marker: hardest press seen while this view was open
        if self.peak_raw > 0:
            px, _ = to_xy(self.peak_raw, 0)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.4)
            cr.move_to(px, y0)
            cr.line_to(px, y0 + h_)
            cr.stroke()

        # the curve
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
        cr.set_line_width(2.5)
        steps = 200
        for i in range(steps + 1):
            p = i / steps
            x, y = to_xy(p, apply_curve(p, self.curve))
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        # live sample
        if self.sample:
            raw, out, contact, eraser = self.sample
            x, y = to_xy(raw, out)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.25)
            cr.arc(x, y, 11, 0, 6.2832)
            cr.fill()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1 if contact else 0.4)
            cr.arc(x, y, 5, 0, 6.2832)
            cr.fill()

        # axis labels
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
        cr.select_font_face("sans")
        cr.set_font_size(11)
        cr.move_to(x0, y0 + h_ + 16)
        cr.show_text(self._label_in)
        ext = cr.text_extents(self._label_out)
        cr.move_to(x0 - 6 - ext.width, y0 + 10)
        cr.show_text(self._label_out)

    _label_in = "→"
    _label_out = "↑"

    def set_axis_labels(self, label_in, label_out):
        self._label_in = label_in
        self._label_out = label_out
