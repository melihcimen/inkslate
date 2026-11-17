"""Main window: status, the tablet page's Weylus settings, pen, screen area, toolbar and connection."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import keys as keymod  # noqa: E402
from . import qr  # noqa: E402
from .curve_widget import CurveView  # noqa: E402
from .i18n import LANGUAGES, _  # noqa: E402
from .payload import FULL  # noqa: E402
from .previews import AreaView, ToolbarPreview  # noqa: E402
from .profiles import (ACTIONS, GESTURE_ACTIONS, MAX_BUTTONS, PROFILES, action_hint,  # noqa: E402
                       action_name, profile_keys, profile_name)

REGIONS = {
    "full": dict(FULL),
    "left": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
    "right": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
    "top": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5},
    "bottom": {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5},
}


def _scale(lo, hi, step, digits):
    s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    s.set_digits(digits)
    s.set_draw_value(True)
    s.set_value_pos(Gtk.PositionType.LEFT)
    s.set_hexpand(True)
    s.set_size_request(240, -1)
    s.set_valign(Gtk.Align.CENTER)
    return s


def _scale_row(title, subtitle, scale):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    row.add_suffix(scale)
    return row


def _button(label, callback, css=None, tooltip=None):
    b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if css:
        b.add_css_class(css)
    if tooltip:
        b.set_tooltip_text(tooltip)
    b.connect("clicked", lambda *_a: callback())
    return b


def _icon_button(icon, callback, tooltip):
    b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_a: callback())
    return b


def _hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def _rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec or "#000000")
    return c


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Inkslate")
        self.app = app
        self.cfg = app.config
        self._updating = False
        self._qr_text = None
        self.button_rows = []
        self.set_default_size(960, 840)
        self.set_hide_on_close(True)

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        menu = Gio.Menu()
        menu.append(_("About Inkslate"), "app.about")
        menu.append(_("Quit (stops Weylus)"), "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text=_("Menu")))
        view.add_top_bar(header)
        bottom = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(bottom)
        view.set_content(self.stack)

        for builder, name, title, icon in (
                (self._build_status, "status", _("Status"), "computer-symbolic"),
                (self._build_tablet, "tablet", _("Tablet"), "input-tablet-symbolic"),
                (self._build_pen, "pen", _("Pen"), "document-edit-symbolic"),
                (self._build_area, "area", _("Area"), "view-fullscreen-symbolic"),
                (self._build_toolbar, "toolbar", _("Buttons"), "input-keyboard-symbolic"),
                (self._build_connection, "connection", _("Connection"), "network-wired-symbolic")):
            self.stack.add_titled_with_icon(builder(), name, title, icon)
        self.stack.connect("notify::visible-child-name", lambda *_a: self._update_monitor())
        self.connect("notify::visible", lambda *_a: self._update_monitor())

        # Narrow windows: icons only, switcher moves to the bottom.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.add_setter(bottom, "reveal", True)
        bp.add_setter(header, "show-title", False)
        self.add_breakpoint(bp)

        self.refresh_status()
        self.refresh_native()
        self.refresh_config()

    # ================================================================== status page

    def _build_status(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=_("Tablet"))
        self.row_tablet = Adw.ActionRow(title=_("Connection"))
        self.row_tablet.add_prefix(Gtk.Image(icon_name="input-tablet-symbolic"))
        g.add(self.row_tablet)

        self.row_page = Adw.ActionRow(title=_("Tablet page"))
        self.btn_open = _button(_("Open"), self.app.open_page, tooltip=_("Open the page on the tablet"))
        self.btn_fullscreen = _button(_("Fullscreen"), self.app.fullscreen)
        self.btn_reload = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text=_("Reload the tablet page"))
        self.btn_reload.connect("clicked", lambda *_a: self.app.send_action("reload"))
        for b in (self.btn_open, self.btn_fullscreen, self.btn_reload):
            self.row_page.add_suffix(b)
        g.add(self.row_page)

        self.row_battery = Adw.ActionRow(title=_("Tablet battery"))
        g.add(self.row_battery)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Server"))
        self.row_weylus = Adw.ActionRow(title="Weylus")
        self.row_weylus.add_suffix(_button(_("Restart"), self.app.restart_weylus))
        log_btn = Gtk.Button(icon_name="text-x-generic-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text=_("Open the Weylus log"))
        log_btn.connect("clicked", lambda *_a: self.app.open_log())
        self.row_weylus.add_suffix(log_btn)
        g.add(self.row_weylus)

        self.row_url = Adw.ActionRow(title=_("Address for the tablet"))
        self.row_url.set_subtitle_selectable(True)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text=_("Copy"))
        copy.connect("clicked", lambda *_a: self._copy(self.app.page_url()))
        self.row_url.add_suffix(copy)
        g.add(self.row_url)
        page.add(g)

        self.grp_qr = Adw.PreferencesGroup(
            title=_("Connect over Wi-Fi"),
            description=_("Scan with the tablet's camera. The code includes the access code."))
        self.qr_picture = Gtk.Picture(can_shrink=False, halign=Gtk.Align.CENTER,
                                      margin_top=6, margin_bottom=6)
        self.grp_qr.add(self.qr_picture)
        page.add(self.grp_qr)

        g = Adw.PreferencesGroup(title=_("Quick settings"))
        self.sw_quick_energy = Adw.SwitchRow(title=_("Energy saving"),
                                             subtitle=_("No video, black tablet screen; pen still works"))
        self.sw_quick_energy.connect("notify::active",
                                     lambda r, _p: self._native_changed("energysaving", r.get_active()))
        g.add(self.sw_quick_energy)
        self.sw_quick_eraser = Adw.SwitchRow(title=_("Eraser mode"))
        self.sw_quick_eraser.connect("notify::active", lambda r, _p: self._set_tablet("eraser", r.get_active()))
        g.add(self.sw_quick_eraser)
        page.add(g)
        return page

    # ================================================================== tablet page (Weylus' own settings)

    def _native_switch(self, group, key, title, subtitle=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or "")
        row.connect("notify::active", lambda r, _p: self._native_changed(key, r.get_active()))
        group.add(row)
        self.native_rows[key] = row
        return row

    def _build_tablet(self):
        page = Adw.PreferencesPage()
        self.native_rows = {}
        self.tablet_banner = Adw.Banner(title=_("The tablet page is not connected. These are the "
                                                "settings of the Weylus menu on the tablet."))
        self.tablet_banner.set_revealed(True)
        bgroup = Adw.PreferencesGroup()
        bgroup.add(self.tablet_banner)
        page.add(bgroup)

        g = Adw.PreferencesGroup(title=_("Input"))
        self._native_switch(g, "enable_stylus", _("Pen"))
        self._native_switch(g, "enable_touch", _("Finger input"),
                            _("Off: fingers only do gestures and can't draw by accident"))
        self._native_switch(g, "enable_mouse", _("Mouse"))
        self._native_switch(g, "uinput_support", _("uinput"),
                            _("Needed for pen pressure and multi-touch"))
        self._native_switch(g, "lefty", _("Left-handed"), _("Puts the Weylus menu on the right"))
        self.scale_min_pressure = _scale(0, 1, 0.01, 2)
        self.scale_min_pressure.connect(
            "value-changed", lambda s: self._native_changed("min_pressure", round(s.get_value(), 2)))
        self.row_min_pressure = _scale_row(_("Minimum pressure"),
                                           _("Weylus' own floor, applied before the curve"),
                                           self.scale_min_pressure)
        g.add(self.row_min_pressure)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Finger gestures"),
                                 description=_("Work while finger input is off, so your hand can't draw."))
        self.sw_gestures = Adw.SwitchRow(title=_("Gestures"))
        self.sw_gestures.connect("notify::active", lambda r, _p: self._set_sub("gestures", "enabled", r.get_active()))
        g.add(self.sw_gestures)
        gesture_items = [(a, action_name(a)) for a in GESTURE_ACTIONS]
        self.combo_two = self._string_combo(_("Two-finger tap"), gesture_items)
        self.combo_two.connect("notify::selected", lambda r, _p: self._set_sub("gestures", "two", r.keys[r.get_selected()]))
        g.add(self.combo_two)
        self.combo_three = self._string_combo(_("Three-finger tap"), gesture_items)
        self.combo_three.connect("notify::selected",
                                 lambda r, _p: self._set_sub("gestures", "three", r.keys[r.get_selected()]))
        g.add(self.combo_three)
        self.sw_palm = Adw.SwitchRow(title=_("Palm rejection"),
                                     subtitle=_("When finger input is on, a palm resting while the pen is "
                                                "close won't draw or click"))
        self.sw_palm.connect("notify::active", lambda r, _p: self._set_sub("palm", "enabled", r.get_active()))
        g.add(self.sw_palm)
        self.spin_palm = Adw.SpinRow.new_with_range(100, 3000, 50)
        self.spin_palm.set_title(_("Pen grace period (ms)"))
        self.spin_palm.connect("notify::value", lambda r, _p: self._set_sub("palm", "ms", int(r.get_value())))
        g.add(self.spin_palm)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Screen"))
        self._native_switch(g, "energysaving", _("Energy saving"),
                            _("No video, black screen (saves the tablet's battery)"))
        self._native_switch(g, "enable_video", _("Video"))
        self._native_switch(g, "stretch", _("Fill the screen"))
        self._native_switch(g, "capture_cursor", _("Show the cursor in the video"))
        self._native_switch(g, "aggressive_seeking", _("Low latency"),
                            _("Skip ahead instead of buffering the video"))

        self.spin_fps = Adw.SpinRow.new_with_range(1, 120, 1)
        self.spin_fps.set_title(_("Maximum frame rate (FPS)"))
        self.spin_fps.connect("notify::value", lambda r, _p: self._native_changed("frame_rate", int(r.get_value())))
        g.add(self.spin_fps)

        self.scale_video = _scale(0.1, 2, 0.01, 2)
        self.scale_video.connect(
            "value-changed", lambda s: self._native_changed("scale_video", round(s.get_value(), 2)))
        self.row_scale_video = _scale_row(_("Maximum video resolution"), "", self.scale_video)
        g.add(self.row_scale_video)

        self.capturables = Gtk.StringList()
        self.combo_capture = Adw.ComboRow(title=_("Captured screen"), model=self.capturables)
        self.combo_capture.connect("notify::selected", self._capture_changed)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text=_("Refresh the list"))
        refresh.connect("clicked", lambda *_a: self.app.send_action("refresh_capturables"))
        self.combo_capture.add_suffix(refresh)
        g.add(self.combo_capture)
        page.add(g)
        return page

    def _capture_changed(self, row, _pspec):
        if self._updating:
            return
        idx = row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION:
            self.app.set_native("capturable", str(idx))

    def _native_changed(self, key, value):
        if not self._updating:
            self.app.set_native(key, value)

    # ================================================================== pen page

    def _build_pen(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=_("Pressure curve"),
                                 description=_("Draw on the tablet to see the pressure live. "
                                               "The vertical line marks your hardest press."))
        reset = Gtk.Button(label=_("Reset curve"), valign=Gtk.Align.CENTER)
        reset.add_css_class("flat")
        reset.connect("clicked", lambda *_a: self._reset_curve())
        g.set_header_suffix(reset)
        self.curve_view = CurveView()
        self.curve_view.set_axis_labels(_("pen pressure"), _("sent to apps"))
        frame = Gtk.Frame(child=self.curve_view)
        frame.set_margin_bottom(12)
        g.add(frame)
        self.lbl_live = Gtk.Label(xalign=0)
        self.lbl_live.add_css_class("dim-label")
        self.lbl_live.add_css_class("numeric")
        self.lbl_live.set_label(_("Waiting for the pen…"))
        g.add(self.lbl_live)
        page.add(g)

        g = Adw.PreferencesGroup()
        self.scale_in_max = _scale(0.2, 1, 0.01, 2)
        self.scale_in_max.connect("value-changed", lambda s: self._curve_changed("in_max", s.get_value()))
        g.add(_scale_row(_("Full pressure at"), _("Lower = less force for a thick line"),
                         self.scale_in_max))
        self.scale_in_min = _scale(0, 0.3, 0.01, 2)
        self.scale_in_min.connect("value-changed", lambda s: self._curve_changed("in_min", s.get_value()))
        g.add(_scale_row(_("Threshold"), _("Pressure below this counts as zero"), self.scale_in_min))
        self.scale_gamma = _scale(0.3, 3, 0.05, 2)
        self.scale_gamma.add_mark(1, Gtk.PositionType.BOTTOM, None)
        self.scale_gamma.connect("value-changed", lambda s: self._curve_changed("gamma", s.get_value()))
        g.add(_scale_row(_("Curve"), _("Below 1: soft (light strokes get thicker), above 1: firm"),
                         self.scale_gamma))
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Pen"))
        self.sw_eraser = Adw.SwitchRow(title=_("Eraser mode"),
                                       subtitle=_("The pen acts as an eraser tip (also on the tablet toolbar)"))
        self.sw_eraser.connect("notify::active", lambda r, _p: self._set_tablet("eraser", r.get_active()))
        g.add(self.sw_eraser)
        self.scale_smooth = _scale(0, 0.9, 0.05, 2)
        self.scale_smooth.connect("value-changed",
                                  lambda s: self._set_tablet("smoothing", round(s.get_value(), 2)))
        g.add(_scale_row(_("Smoothing"), _("Steadies shaky lines, adds a little lag"), self.scale_smooth))
        page.add(g)
        return page

    def _curve_changed(self, key, value):
        if self._updating:
            return
        curve = self.cfg["tablet"]["curve"]
        curve[key] = round(value, 3)
        if curve["in_min"] >= curve["in_max"] - 0.05:
            curve["in_min"] = max(0.0, round(curve["in_max"] - 0.05, 3))
        self.curve_view.set_curve(curve)
        self.app.tablet_config_changed()

    def _reset_curve(self):
        self.cfg["tablet"]["curve"] = {"in_min": 0.0, "in_max": 1.0, "gamma": 1.0}
        self.curve_view.reset_peak()
        self.app.tablet_config_changed()
        self.refresh_config()

    def push_pressure(self, msg):
        raw, out = float(msg.get("raw", 0)), float(msg.get("out", 0))
        self.curve_view.push_sample(raw, out, bool(msg.get("contact")), bool(msg.get("eraser")))
        self.lbl_live.set_label(_("Pen: {raw:.2f} → sent: {out:.2f}").format(raw=raw, out=out)
                                + ("  · " + _("eraser") if msg.get("eraser") else ""))

    def _update_monitor(self):
        on = self.get_visible() and self.stack.get_visible_child_name() == "pen"
        self.app.set_monitor(on)

    # ================================================================== area page

    def _build_area(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title=_("Screen area"),
            description=_("The part of the PC screen the tablet controls. Drag across the screens "
                          "below to draw your own area."))
        self.area_view = AreaView(self._area_drawn)
        g.add(Gtk.Frame(child=self.area_view))
        page.add(g)

        g = Adw.PreferencesGroup()
        self.monitor_list = Gtk.StringList()
        self.monitor_keys = []
        self.combo_monitor = Adw.ComboRow(title=_("Screen"), model=self.monitor_list)
        self.combo_monitor.connect("notify::selected", self._monitor_changed)
        g.add(self.combo_monitor)
        self.combo_region = self._string_combo(_("Area"), [
            ("full", _("Whole screen")), ("left", _("Left half")), ("right", _("Right half")),
            ("top", _("Top half")), ("bottom", _("Bottom half")), ("custom", _("Custom (drawn above)"))])
        self.combo_region.connect("notify::selected", self._region_changed)
        g.add(self.combo_region)
        self.sw_keep_aspect = Adw.SwitchRow(
            title=_("Keep proportions"),
            subtitle=_("Use only the part of the tablet with the same shape as the area, so "
                       "circles stay round"))
        self.sw_keep_aspect.connect("notify::active",
                                    lambda r, _p: self._set_sub("area", "keep_aspect", r.get_active()))
        g.add(self.sw_keep_aspect)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Tablet screen"),
                                 description=_("Shown while energy saving is on."))
        self.color_bg = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                              valign=Gtk.Align.CENTER)
        self.color_bg.connect("notify::rgba", lambda b, _p: self._set_sub(
            "screen", "background", _hex(b.get_rgba())))
        row = Adw.ActionRow(title=_("Background"))
        row.add_suffix(self.color_bg)
        g.add(row)
        self.sw_show_area = Adw.SwitchRow(title=_("Mark the unused part"),
                                          subtitle=_("When proportions are kept, the rest of the tablet "
                                                     "is shaded and does nothing"))
        self.sw_show_area.connect("notify::active",
                                  lambda r, _p: self._set_sub("screen", "show_area", r.get_active()))
        g.add(self.sw_show_area)
        page.add(g)
        return page

    def _monitor_changed(self, row, _pspec):
        if self._updating:
            return
        idx = row.get_selected()
        if 0 <= idx < len(self.monitor_keys):
            self._set_sub("area", "monitor", self.monitor_keys[idx])

    def _region_changed(self, row, _pspec):
        if self._updating:
            return
        key = row.keys[row.get_selected()]
        if key in REGIONS:
            self._set_sub("area", "region", dict(REGIONS[key]))

    def _area_drawn(self, region):
        self._set_sub("area", "region", dict(region))

    # ================================================================== toolbar page

    def _string_combo(self, title, items, subtitle=None):
        row = Adw.ComboRow(title=title, subtitle=subtitle or "",
                           model=Gtk.StringList.new([label for _k, label in items]))
        row.keys = [k for k, _label in items]
        return row

    def _build_toolbar(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=_("Tablet toolbar"),
                                 description=_("Buttons on the edge of the tablet screen. This is how "
                                               "they will look."))
        self.preview = ToolbarPreview()
        g.add(Gtk.Frame(child=self.preview))
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Look"))
        self.sw_toolbar = Adw.SwitchRow(title=_("Show the toolbar"))
        self.sw_toolbar.connect("notify::active", lambda r, _p: self._set_sub("toolbar", "enabled", r.get_active()))
        g.add(self.sw_toolbar)
        self.combo_side = self._string_combo(_("Position"), [
            ("left", _("Left")), ("right", _("Right")), ("top", _("Top")), ("bottom", _("Bottom"))])
        self.combo_side.connect("notify::selected", lambda r, _p: self._set_sub(
            "toolbar", "side", r.keys[r.get_selected()]))
        g.add(self.combo_side)
        self.scale_size = _scale(36, 96, 2, 0)
        self.scale_size.connect("value-changed", lambda s: self._set_sub("toolbar", "size", int(s.get_value())))
        g.add(_scale_row(_("Button size"), _("In the tablet's pixels"), self.scale_size))
        self.scale_opacity = _scale(0.2, 1, 0.05, 2)
        self.scale_opacity.connect("value-changed",
                                   lambda s: self._set_sub("toolbar", "opacity", round(s.get_value(), 2)))
        g.add(_scale_row(_("Opacity"), _("While you're not touching it"), self.scale_opacity))
        self.combo_bar_theme = self._string_combo(_("Style"), [("dark", _("Dark")), ("light", _("Light"))])
        self.combo_bar_theme.connect("notify::selected", lambda r, _p: self._set_sub(
            "toolbar", "theme", r.keys[r.get_selected()]))
        g.add(self.combo_bar_theme)
        self.color_accent = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                  valign=Gtk.Align.CENTER)
        self.color_accent.connect("notify::rgba", lambda b, _p: self._set_sub(
            "toolbar", "accent", _hex(b.get_rgba())))
        row = Adw.ActionRow(title=_("Highlight colour"), subtitle=_("For buttons that are on, like the eraser"))
        row.add_suffix(_icon_button("edit-undo-symbolic", lambda: self._set_sub("toolbar", "accent", ""),
                                    _("Use the desktop's accent colour")))
        row.add_suffix(self.color_accent)
        g.add(row)
        self.sw_labels = Adw.SwitchRow(title=_("Show labels"))
        self.sw_labels.connect("notify::active", lambda r, _p: self._set_sub("toolbar", "labels", r.get_active()))
        g.add(self.sw_labels)
        self.sw_fade = Adw.SwitchRow(title=_("Fade while drawing"),
                                     subtitle=_("Almost hidden while the pen is on the screen"))
        self.sw_fade.connect("notify::active", lambda r, _p: self._set_sub("toolbar", "fade", r.get_active()))
        g.add(self.sw_fade)
        page.add(g)

        self.grp_buttons = Adw.PreferencesGroup(
            title=_("Buttons"),
            description=_("Shortcuts are sent for your keyboard layout. Pick an app preset, or set "
                          "your own shortcut on any button."))
        self.btn_add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text=_("Add a button"))
        self.btn_add.add_css_class("flat")
        self.btn_add.connect("clicked", lambda *_a: self._add_button())
        self.grp_buttons.set_header_suffix(self.btn_add)
        self.combo_profile = self._string_combo(
            _("App preset"), [(k, profile_name(k)) for k in PROFILES],
            _("Which shortcuts undo, redo, zoom and the others send"))
        self.combo_profile.connect("notify::selected", self._profile_changed)
        self.grp_buttons.add(self.combo_profile)
        page.add(self.grp_buttons)
        return page

    def _button_actions(self):
        return [a for a in ACTIONS if a != "none"]

    def _rebuild_button_rows(self):
        for row in self.button_rows:
            self.grp_buttons.remove(row)
        self.button_rows = []
        buttons = self.cfg["tablet"]["toolbar"]["buttons"]
        actions = self._button_actions()
        prof = profile_keys(self.cfg["profile"])
        was = self._updating
        self._updating = True
        try:
            for i, b in enumerate(buttons):
                action = b.get("action", "undo")
                row = Adw.ComboRow(title=_("Button {n}").format(n=i + 1),
                                   model=Gtk.StringList.new([action_name(a) for a in actions]))
                row.keys = actions
                if action in actions:
                    row.set_selected(actions.index(action))
                if action in ("custom", "custom_hold"):
                    spec = b.get("keys")
                    rec = Gtk.Button(label=keymod.label(spec) if spec else _("Set shortcut…"),
                                     valign=Gtk.Align.CENTER, tooltip_text=_("Record a shortcut"))
                    rec.connect("clicked", lambda *_a, i=i: self._record_shortcut(i))
                    row.add_suffix(rec)
                    row.set_subtitle(action_hint(action))
                else:
                    spec = prof.get(action) if ACTIONS.get(action) in ("key", "hold") else None
                    shortcut = keymod.label(spec) if spec else ""
                    row.set_subtitle(" · ".join(x for x in (shortcut, action_hint(action)) if x))
                up = _icon_button("go-up-symbolic", lambda i=i: self._move_button(i, -1), _("Move up"))
                up.set_sensitive(i > 0)
                down = _icon_button("go-down-symbolic", lambda i=i: self._move_button(i, 1), _("Move down"))
                down.set_sensitive(i < len(buttons) - 1)
                rm = _icon_button("user-trash-symbolic", lambda i=i: self._remove_button(i), _("Remove"))
                for w in (up, down, rm):
                    row.add_suffix(w)
                row.connect("notify::selected", lambda r, _p, i=i: self._button_action_changed(i, r))
                self.grp_buttons.add(row)
                self.button_rows.append(row)
        finally:
            self._updating = was
        self.btn_add.set_sensitive(len(buttons) < MAX_BUTTONS)

    def _buttons_changed(self):
        self.app.tablet_config_changed()
        # Rebuilding inside the row's own signal handler would destroy it mid-emission.
        GLib.idle_add(self._after_buttons_changed)

    def _after_buttons_changed(self):
        self._rebuild_button_rows()
        self._update_previews()
        return False

    def _button_action_changed(self, index, row):
        if self._updating:
            return
        buttons = self.cfg["tablet"]["toolbar"]["buttons"]
        action = row.keys[row.get_selected()]
        new = {"action": action}
        if action in ("custom", "custom_hold") and buttons[index].get("keys"):
            new["keys"] = buttons[index]["keys"]
        buttons[index] = new
        self._buttons_changed()
        if action in ("custom", "custom_hold") and "keys" not in new:
            GLib.idle_add(lambda: self._record_shortcut(index) and False)

    def _add_button(self):
        buttons = self.cfg["tablet"]["toolbar"]["buttons"]
        if len(buttons) >= MAX_BUTTONS:
            return
        used = {b.get("action") for b in buttons}
        nxt = next((a for a in ("undo", "redo", "eraser", "pan", "zoom_in", "zoom_out", "save")
                    if a not in used), "custom")
        buttons.append({"action": nxt})
        self._buttons_changed()

    def _move_button(self, index, delta):
        buttons = self.cfg["tablet"]["toolbar"]["buttons"]
        j = index + delta
        if 0 <= j < len(buttons):
            buttons[index], buttons[j] = buttons[j], buttons[index]
            self._buttons_changed()

    def _remove_button(self, index):
        buttons = self.cfg["tablet"]["toolbar"]["buttons"]
        if 0 <= index < len(buttons):
            del buttons[index]
            self._buttons_changed()

    def _record_shortcut(self, index):
        dialog = Adw.AlertDialog(heading=_("Press the shortcut"),
                                 body=_("Press the key combination this button should send, "
                                        "for example Ctrl+B. For a button that holds a key, a "
                                        "single Ctrl, Shift or Alt also works. Escape cancels."))
        dialog.add_response("cancel", _("Cancel"))
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        lone = {"keyval": None}     # a modifier pressed with nothing else (so far)

        def store(spec):
            buttons = self.cfg["tablet"]["toolbar"]["buttons"]
            if 0 <= index < len(buttons):
                buttons[index]["keys"] = spec
                if buttons[index].get("action") not in ("custom", "custom_hold"):
                    buttons[index]["action"] = "custom"
                self._buttons_changed()
            dialog.close()

        def pressed(_c, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape:
                dialog.close()
                return True
            spec = keymod.from_key_event(keyval, state)
            if spec is None:        # a modifier: wait for the real key, or its release
                lone["keyval"] = keyval if lone["keyval"] is None else -1
                return True
            store(spec)
            return True

        def released(_c, keyval, _keycode, _state):
            # Ctrl, Shift, Alt or Super pressed and released alone: hold buttons can hold it.
            if lone["keyval"] == keyval:
                spec = keymod.modifier_combo(keyval)
                if spec:
                    store(spec)
            lone["keyval"] = None

        ctrl.connect("key-pressed", pressed)
        ctrl.connect("key-released", released)
        dialog.add_controller(ctrl)
        dialog.present(self)
        return False

    def _profile_changed(self, row, _pspec):
        if self._updating:
            return
        self.cfg["profile"] = row.keys[row.get_selected()]
        self._buttons_changed()

    def _set_sub(self, section, key, value):
        """Change cfg["tablet"][section][key] and push it to the tablet."""
        if self._updating:
            return
        self.cfg["tablet"][section][key] = value
        self.app.tablet_config_changed()
        self.refresh_config()

    def _set_tablet(self, key, value):
        if not self._updating:
            self.cfg["tablet"][key] = value
            self.app.tablet_config_changed()
            self.refresh_config()

    # ================================================================== connection page

    def _build_connection(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(
            title=_("Connection"),
            description=_("USB (recommended for Android) needs USB debugging on the tablet. Wi-Fi works "
                          "with any tablet's browser, iPads included, and needs the access code."))
        self.combo_mode = self._string_combo(_("Mode"), [("usb", _("USB")), ("wifi", _("Wi-Fi"))])
        self.combo_mode.connect("notify::selected", lambda r, _p: None if self._updating
                                else self.app.set_mode(r.keys[r.get_selected()]))
        g.add(self.combo_mode)
        self.row_code = Adw.EntryRow(title=_("Access code (Wi-Fi)"), show_apply_button=True)
        self.row_code.connect("apply", lambda r: self.app.set_access_code(r.get_text().strip()))
        g.add(self.row_code)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("USB"))
        self.sw_auto_open = Adw.SwitchRow(title=_("Open the page when the tablet is plugged in"))
        self.sw_auto_open.connect("notify::active", lambda r, _p: self._set_usb("auto_open", r.get_active()))
        g.add(self.sw_auto_open)
        self.sw_auto_fs = Adw.SwitchRow(title=_("Switch the page to fullscreen"),
                                        subtitle=_("Through Chrome's USB debugging connection"))
        self.sw_auto_fs.connect("notify::active", lambda r, _p: self._set_usb("auto_fullscreen", r.get_active()))
        g.add(self.sw_auto_fs)
        self.sw_dim = Adw.SwitchRow(
            title=_("Dim the tablet in energy saving"),
            subtitle=_("Restored when energy saving is turned off or the page closes. If you unplug "
                       "the cable first, it is restored the next time you plug it in."))
        self.sw_dim.connect("notify::active", lambda r, _p: self._set_usb("dim_on_energysaving", r.get_active()))
        g.add(self.sw_dim)
        self.sw_wake = Adw.SwitchRow(title=_("Keep the tablet screen on"),
                                     subtitle=_("While the page is open"))
        self.sw_wake.connect("notify::active", lambda r, _p: self._set_tablet("wake_lock", r.get_active()))
        g.add(self.sw_wake)
        self.sw_tap_fs = Adw.SwitchRow(title=_("Fullscreen on first tap"),
                                       subtitle=_("For Wi-Fi, where automatic fullscreen isn't possible"))
        self.sw_tap_fs.connect("notify::active", lambda r, _p: self._set_tablet("fullscreen_on_tap", r.get_active()))
        g.add(self.sw_tap_fs)
        self.sw_hide_menu = Adw.SwitchRow(title=_("Start with the Weylus menu closed"),
                                          subtitle=_("It can still be opened with the ⠿ handle on the tablet"))
        self.sw_hide_menu.connect("notify::active", lambda r, _p: self._set_tablet("hide_menu", r.get_active()))
        g.add(self.sw_hide_menu)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Video"),
                                 description=_("Only matters if you want to see the PC screen on the "
                                               "tablet. Changing these restarts Weylus."))
        self.sw_wayland = Adw.SwitchRow(title=_("Wayland screen capture (PipeWire)"),
                                        subtitle=_("Without it the video is black on Wayland; it asks for "
                                                   "screen-sharing permission"))
        self.sw_wayland.connect("notify::active", lambda r, _p: self._set_video("wayland_support", r.get_active()))
        g.add(self.sw_wayland)
        self.sw_vaapi = Adw.SwitchRow(title=_("Hardware encoding (VA-API)"),
                                      subtitle=_("Keep the maximum video resolution at the right end"))
        self.sw_vaapi.connect("notify::active", lambda r, _p: self._set_video("try_vaapi", r.get_active()))
        g.add(self.sw_vaapi)
        self.sw_nvenc = Adw.SwitchRow(title=_("NVIDIA encoding (NVENC)"),
                                      subtitle=_("Wakes the discrete GPU and costs battery"))
        self.sw_nvenc.connect("notify::active", lambda r, _p: self._set_video("try_nvenc", r.get_active()))
        g.add(self.sw_nvenc)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("System"))
        self.sw_autostart = Adw.SwitchRow(title=_("Start at login"),
                                          subtitle=_("Runs in the background without opening this window"))
        self.sw_autostart.connect("notify::active", lambda r, _p: None if self._updating
                                  else self.app.set_autostart(r.get_active()))
        g.add(self.sw_autostart)
        self.combo_theme = self._string_combo(
            _("Appearance"), [("system", _("Follow the system")), ("light", _("Light")), ("dark", _("Dark"))])
        self.combo_theme.connect("notify::selected", self._theme_changed)
        g.add(self.combo_theme)
        self.combo_lang = self._string_combo(
            _("Language"), [(k, v or _("System default")) for k, v in LANGUAGES.items()],
            _("Applies after restarting Inkslate"))
        self.combo_lang.connect("notify::selected", self._language_changed)
        g.add(self.combo_lang)
        self.row_weylus_path = Adw.ActionRow(title=_("Weylus program"))
        self.row_weylus_path.set_subtitle_selectable(True)
        g.add(self.row_weylus_path)
        page.add(g)
        return page

    def _theme_changed(self, row, _pspec):
        if not self._updating:
            self.cfg["appearance"]["theme"] = row.keys[row.get_selected()]
            self.cfg.save()
            self.app.apply_theme()

    def _language_changed(self, row, _pspec):
        if not self._updating:
            self.cfg["language"] = row.keys[row.get_selected()]
            self.cfg.save()
            self.toast(_("Restart Inkslate to change the language"))

    def _set_usb(self, key, value):
        if not self._updating:
            self.cfg["usb"][key] = value
            self.app.usb_config_changed()

    def _set_video(self, key, value):
        if not self._updating:
            self.cfg["video"][key] = value
            self.cfg.save()
            self.app.restart_weylus()
            self.toast(_("Weylus restarted"))

    # ================================================================== refresh from app state

    def _select_key(self, row, key):
        if key in row.keys:
            row.set_selected(row.keys.index(key))

    def refresh_status(self):
        app = self.app
        st = app.usb.status
        if not st["adb"]:
            tablet = _("adb is not installed (sudo apt install adb)")
        elif st["state"] == "connected":
            tablet = _("{model} · USB").format(model=st["model"] or st["serial"])
        elif st["state"] == "unauthorized":
            tablet = _("Allow USB debugging on the tablet")
        elif st["state"] == "offline":
            tablet = _("Tablet is not responding (replug the cable)")
        elif self.cfg["mode"] == "wifi" and app.client():
            tablet = _("Connected over Wi-Fi ({ip})").format(ip=app.client().remote)
        else:
            tablet = _("Not connected · plug in the cable")
        self.row_tablet.set_subtitle(tablet)

        client = app.client()
        if client:
            s = client.state
            parts = [_("Connected")]
            if s.get("fullscreen"):
                parts.append(_("fullscreen"))
            if not s.get("visible", True):
                parts.append(_("in the background"))
            self.row_page.set_subtitle(" · ".join(parts))
        else:
            self.row_page.set_subtitle(_("Not open"))
        usb_ok = st["state"] == "connected" and self.cfg["mode"] == "usb"
        self.btn_open.set_sensitive(usb_ok)
        self.btn_fullscreen.set_sensitive(usb_ok and client is not None)
        self.btn_reload.set_sensitive(client is not None)

        if st["battery"] is not None:
            self.row_battery.set_subtitle(f"%{st['battery']}" + (" · " + _("charging") if st["charging"] else ""))
            self.row_battery.set_visible(True)
        else:
            self.row_battery.set_visible(False)

        if app.weylus.running:
            flavor = app.weylus_flavor_name()
            self.row_weylus.set_subtitle(_("Running") + (f" · {flavor}" if flavor else ""))
        else:
            msg = app.weylus_message
            self.row_weylus.set_subtitle({
                "weylus-missing": _("Weylus not found. Install it or set its path in config.json"),
                "crashed": _("Keeps stopping; see the log (is another Weylus using the ports?)"),
                "restarting": _("Restarting…"),
            }.get(msg, msg or _("Stopped")))
        url = app.page_url()
        self.row_url.set_subtitle(url)
        self.row_weylus_path.set_subtitle(app.weylus_description() or _("not found"))

        wifi = self.cfg["mode"] == "wifi"
        self.grp_qr.set_visible(wifi)
        if wifi and url != self._qr_text:
            self._qr_text = url
            data = qr.to_svg(qr.encode(url), module=5).encode()
            self.qr_picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))

    def refresh_native(self):
        client = self.app.client()
        native = (client.state.get("native") if client else None) or {}
        self.tablet_banner.set_revealed(not native)
        self._updating = True
        try:
            for key, row in self.native_rows.items():
                row.set_sensitive(bool(native))
                if key in native:
                    row.set_active(bool(native[key]))
            self.sw_quick_energy.set_sensitive(bool(native))
            if "energysaving" in native:
                self.sw_quick_energy.set_active(bool(native["energysaving"]))
            if "enable_video" in self.native_rows and native.get("energysaving"):
                self.native_rows["enable_video"].set_sensitive(False)
            for w in (self.spin_fps, self.row_scale_video, self.combo_capture, self.row_min_pressure):
                w.set_sensitive(bool(native))
            if native.get("frame_rate"):
                self.spin_fps.set_value(native["frame_rate"])
            if "scale_video" in native:
                self.scale_video.set_value(native["scale_video"])
                w, h = native.get("video_resolution") or (0, 0)
                self.row_scale_video.set_subtitle(_("At most {w}×{h}").format(w=w, h=h))
            if "min_pressure" in native:
                self.scale_min_pressure.set_value(native["min_pressure"])
            names = native.get("capturables") or []
            if [self.capturables.get_string(i) for i in range(self.capturables.get_n_items())] != names:
                self.capturables.splice(0, self.capturables.get_n_items(), names)
            cap = native.get("capturable", "")
            if isinstance(cap, str) and cap.isdigit() and int(cap) < len(names):
                self.combo_capture.set_selected(int(cap))
        finally:
            self._updating = False
        self._update_previews()

    def _refresh_monitors(self):
        monitors = self.app.monitors()
        keys = [""] + [m["connector"] for m in monitors]
        labels = [_("All screens")]
        for m in monitors:
            size = f"{m['w']}×{m['h']}"
            labels.append(f"{m['name']} ({m['connector']}, {size})" if m.get("name")
                          else f"{m['connector']} ({size})")
        if keys != self.monitor_keys:
            self.monitor_keys = keys
            self.monitor_list.splice(0, self.monitor_list.get_n_items(), labels)
        area = self.cfg["tablet"]["area"]
        self.combo_monitor.set_selected(keys.index(area["monitor"]) if area["monitor"] in keys else 0)
        region = area.get("region") or FULL
        preset = next((k for k, r in REGIONS.items()
                       if all(abs(r[c] - region.get(c, 0)) < 1e-3 for c in "xywh")), "custom")
        self._select_key(self.combo_region, preset)

    def _update_previews(self):
        t = self.cfg["tablet"]
        payload = self.app.tablet_payload()
        size = self.app.tablet_screen_size()
        active = set()
        if t["eraser"]:
            active.add("eraser")
        client = self.app.client()
        if client and (client.state.get("native") or {}).get("enable_touch"):
            active.add("touch")
        preview_cfg = dict(t, toolbar=payload["toolbar"], _area_aspect=payload["area"]["aspect"])
        self.preview.update(preview_cfg, payload["toolbar"]["buttons"], size, active)
        self.area_view.update(self.app.monitors(), t["area"], size[0] / size[1] if size[1] else 1.6,
                              t["screen"]["background"], t["screen"]["show_area"],
                              payload["toolbar"]["accent"])

    def refresh_config(self):
        t = self.cfg["tablet"]
        self._updating = True
        try:
            self.curve_view.set_curve(t["curve"])
            self.scale_in_max.set_value(t["curve"]["in_max"])
            self.scale_in_min.set_value(t["curve"]["in_min"])
            self.scale_gamma.set_value(t["curve"]["gamma"])
            self.sw_eraser.set_active(t["eraser"])
            self.sw_quick_eraser.set_active(t["eraser"])
            self.scale_smooth.set_value(t["smoothing"])

            tb = t["toolbar"]
            self.sw_toolbar.set_active(tb["enabled"])
            self._select_key(self.combo_side, tb["side"])
            self.scale_size.set_value(tb["size"])
            self.scale_opacity.set_value(tb["opacity"])
            self._select_key(self.combo_bar_theme, tb["theme"])
            self.color_accent.set_rgba(_rgba(tb["accent"] or self.app.accent_hex()))
            self.sw_labels.set_active(tb["labels"])
            self.sw_fade.set_active(tb["fade"])
            self._select_key(self.combo_profile, self.cfg["profile"])

            self.sw_gestures.set_active(t["gestures"]["enabled"])
            self._select_key(self.combo_two, t["gestures"]["two"])
            self._select_key(self.combo_three, t["gestures"]["three"])
            self.sw_palm.set_active(t["palm"]["enabled"])
            self.spin_palm.set_value(t["palm"]["ms"])

            self._refresh_monitors()
            self.sw_keep_aspect.set_active(t["area"]["keep_aspect"])
            self.color_bg.set_rgba(_rgba(t["screen"]["background"]))
            self.sw_show_area.set_active(t["screen"]["show_area"])

            self._select_key(self.combo_mode, self.cfg["mode"])
            self.row_code.set_text(self.cfg["access_code"])
            self.row_code.set_visible(self.cfg["mode"] == "wifi")
            u = self.cfg["usb"]
            self.sw_auto_open.set_active(u["auto_open"])
            self.sw_auto_fs.set_active(u["auto_fullscreen"])
            self.sw_dim.set_active(u["dim_on_energysaving"])
            self.sw_wake.set_active(t["wake_lock"])
            self.sw_tap_fs.set_active(t["fullscreen_on_tap"])
            self.sw_hide_menu.set_active(t["hide_menu"])
            v = self.cfg["video"]
            self.sw_wayland.set_active(v["wayland_support"])
            self.sw_vaapi.set_active(v["try_vaapi"])
            self.sw_nvenc.set_active(v["try_nvenc"])
            self.sw_autostart.set_active(self.app.autostart_enabled())
            self._select_key(self.combo_theme, self.cfg["appearance"]["theme"])
            self._select_key(self.combo_lang, self.cfg["language"])
        finally:
            self._updating = False
        if len(self.button_rows) != len(t["toolbar"]["buttons"]) or self._rows_stale():
            self._rebuild_button_rows()
        self._update_previews()

    def _rows_stale(self):
        for row, b in zip(self.button_rows, self.cfg["tablet"]["toolbar"]["buttons"]):
            if row.keys[row.get_selected()] != b.get("action"):
                return True
        return False

    # ================================================================== helpers

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def _copy(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
        self.toast(_("Copied"))
