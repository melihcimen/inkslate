"""Main window: status, the tablet page's Weylus settings, pen, shortcuts and connection."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .curve_widget import CurveView  # noqa: E402
from .i18n import LANGUAGES, _  # noqa: E402
from .profiles import ACTIONS, PROFILES, action_name, profile_name  # noqa: E402

MAX_TOOLBAR_BUTTONS = 6


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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Inkslate")
        self.app = app
        self.cfg = app.config
        self._updating = False
        self.set_default_size(920, 820)
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

        self.stack.add_titled_with_icon(self._build_status(), "status", _("Status"),
                                        "computer-symbolic")
        self.stack.add_titled_with_icon(self._build_tablet(), "tablet", _("Tablet"),
                                        "input-tablet-symbolic")
        self.stack.add_titled_with_icon(self._build_pen(), "pen", _("Pen"),
                                        "document-edit-symbolic")
        self.stack.add_titled_with_icon(self._build_shortcuts(), "shortcuts", _("Shortcuts"),
                                        "input-keyboard-symbolic")
        self.stack.add_titled_with_icon(self._build_connection(), "connection", _("Connection"),
                                        "network-wired-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_a: self._update_monitor())
        self.connect("notify::visible", lambda *_a: self._update_monitor())

        # Narrow windows: icons only, switcher moves to the bottom.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
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

        g = Adw.PreferencesGroup(title=_("Screen"))
        self._native_switch(g, "energysaving", _("Energy saving"),
                            _("No video, black screen (saves the tablet's battery)"))
        self._native_switch(g, "enable_video", _("Video"))
        self._native_switch(g, "stretch", _("Fill the screen"))
        self._native_switch(g, "capture_cursor", _("Show the cursor in the video"))
        self._native_switch(g, "aggressive_seeking", _("Low latency"),
                            _("Skip ahead instead of buffering the video"))

        self.spin_interval = Adw.SpinRow.new_with_range(1, 1000, 1)
        self.spin_interval.set_title(_("Frame interval (ms)"))
        self.spin_interval.set_subtitle(_("16 ms ≈ 60 FPS, 33 ms ≈ 30 FPS"))
        self.spin_interval.connect(
            "notify::value", lambda r, _p: self._native_changed("frame_update_limit", int(r.get_value())))
        g.add(self.spin_interval)

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

    # ================================================================== shortcuts page

    def _string_combo(self, title, items, subtitle=None):
        row = Adw.ComboRow(title=title, subtitle=subtitle or "",
                           model=Gtk.StringList.new([label for _k, label in items]))
        row.keys = [k for k, _label in items]
        return row

    def _build_shortcuts(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=_("Tablet toolbar"),
                                 description=_("Buttons on the edge of the tablet screen. Tap the eraser "
                                               "to toggle it, hold it to erase only while holding."))
        self.sw_toolbar = Adw.SwitchRow(title=_("Show the toolbar"))
        self.sw_toolbar.connect("notify::active", lambda r, _p: self._set_toolbar("enabled", r.get_active()))
        g.add(self.sw_toolbar)
        self.combo_side = self._string_combo(_("Side"), [("left", _("Left")), ("right", _("Right"))])
        self.combo_side.connect("notify::selected", lambda r, _p: self._set_toolbar(
            "side", r.keys[r.get_selected()]))
        g.add(self.combo_side)
        self.combo_profile = self._string_combo(
            _("App profile"), [(k, profile_name(k)) for k in PROFILES],
            _("Which key combos undo, redo and save send"))
        self.combo_profile.connect("notify::selected", self._profile_changed)
        g.add(self.combo_profile)
        self.combo_buttons = []
        action_items = [(a, action_name(a)) for a in ACTIONS]
        for i in range(MAX_TOOLBAR_BUTTONS):
            row = self._string_combo(_("Button {n}").format(n=i + 1), action_items)
            row.connect("notify::selected", lambda *_a: self._buttons_changed())
            g.add(row)
            self.combo_buttons.append(row)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Finger gestures"),
                                 description=_("Work while finger input is off, so your hand can't draw."))
        self.sw_gestures = Adw.SwitchRow(title=_("Gestures"))
        self.sw_gestures.connect("notify::active", lambda r, _p: self._set_gesture("enabled", r.get_active()))
        g.add(self.sw_gestures)
        gesture_items = [(a, action_name(a)) for a in ACTIONS if a != "fullscreen"]
        self.combo_two = self._string_combo(_("Two-finger tap"), gesture_items)
        self.combo_two.connect("notify::selected", lambda r, _p: self._set_gesture("two", r.keys[r.get_selected()]))
        g.add(self.combo_two)
        self.combo_three = self._string_combo(_("Three-finger tap"), gesture_items)
        self.combo_three.connect("notify::selected",
                                 lambda r, _p: self._set_gesture("three", r.keys[r.get_selected()]))
        g.add(self.combo_three)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Palm rejection"))
        self.sw_palm = Adw.SwitchRow(title=_("Ignore touches near the pen"),
                                     subtitle=_("When finger input is on, a palm resting while the pen is "
                                                "close won't draw or click"))
        self.sw_palm.connect("notify::active", lambda r, _p: self._set_palm("enabled", r.get_active()))
        g.add(self.sw_palm)
        self.spin_palm = Adw.SpinRow.new_with_range(100, 3000, 50)
        self.spin_palm.set_title(_("Pen grace period (ms)"))
        self.spin_palm.connect("notify::value", lambda r, _p: self._set_palm("ms", int(r.get_value())))
        g.add(self.spin_palm)
        page.add(g)
        return page

    def _profile_changed(self, row, _pspec):
        if self._updating:
            return
        self.cfg["profile"] = row.keys[row.get_selected()]
        self.app.tablet_config_changed()

    def _buttons_changed(self):
        if self._updating:
            return
        buttons = []
        for row in self.combo_buttons:
            action = row.keys[row.get_selected()]
            if action != "none":
                buttons.append({"action": action})
        self.cfg["tablet"]["toolbar"]["buttons"] = buttons
        self.app.tablet_config_changed()

    def _set_toolbar(self, key, value):
        if not self._updating:
            self.cfg["tablet"]["toolbar"][key] = value
            self.app.tablet_config_changed()

    def _set_gesture(self, key, value):
        if not self._updating:
            self.cfg["tablet"]["gestures"][key] = value
            self.app.tablet_config_changed()

    def _set_palm(self, key, value):
        if not self._updating:
            self.cfg["tablet"]["palm"][key] = value
            self.app.tablet_config_changed()

    def _set_tablet(self, key, value):
        if not self._updating:
            self.cfg["tablet"][key] = value
            self.app.tablet_config_changed()
            self.refresh_config()

    # ================================================================== connection page

    def _build_connection(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=_("Connection"))
        self.combo_mode = self._string_combo(
            _("Mode"), [("usb", _("USB")), ("wifi", _("Wi-Fi"))],
            _("USB (recommended) needs USB debugging on the tablet; Wi-Fi needs the access code"))
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
            self.row_weylus.set_subtitle(_("Running"))
        else:
            msg = app.weylus_message
            self.row_weylus.set_subtitle({
                "weylus-missing": _("Weylus not found. Install it or set its path in config.json"),
                "crashed": _("Keeps stopping; see the log (is another Weylus using the ports?)"),
                "restarting": _("Restarting…"),
            }.get(msg, msg or _("Stopped")))
        self.row_url.set_subtitle(app.page_url())
        self.row_weylus_path.set_subtitle(app.weylus_path() or _("not found"))

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
            for w in (self.spin_interval, self.row_scale_video, self.combo_capture, self.row_min_pressure):
                w.set_sensitive(bool(native))
            if "frame_update_limit" in native:
                self.spin_interval.set_value(native["frame_update_limit"])
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
            self.sw_toolbar.set_active(t["toolbar"]["enabled"])
            self._select_key(self.combo_side, t["toolbar"]["side"])
            self._select_key(self.combo_profile, self.cfg["profile"])
            actions = [b.get("action", "none") for b in t["toolbar"]["buttons"]][:MAX_TOOLBAR_BUTTONS]
            for i, row in enumerate(self.combo_buttons):
                self._select_key(row, actions[i] if i < len(actions) else "none")
            self.sw_gestures.set_active(t["gestures"]["enabled"])
            self._select_key(self.combo_two, t["gestures"]["two"])
            self._select_key(self.combo_three, t["gestures"]["three"])
            self.sw_palm.set_active(t["palm"]["enabled"])
            self.spin_palm.set_value(t["palm"]["ms"])
            self._select_key(self.combo_mode, self.cfg["mode"])
            self.row_code.set_text(self.cfg["access_code"])
            self.row_code.set_visible(self.cfg["mode"] == "wifi")
            u = self.cfg["usb"]
            self.sw_auto_open.set_active(u["auto_open"])
            self.sw_auto_fs.set_active(u["auto_fullscreen"])
            self.sw_dim.set_active(u["dim_on_energysaving"])
            self.sw_wake.set_active(t["wake_lock"])
            self.sw_tap_fs.set_active(t["fullscreen_on_tap"])
            v = self.cfg["video"]
            self.sw_wayland.set_active(v["wayland_support"])
            self.sw_vaapi.set_active(v["try_vaapi"])
            self.sw_nvenc.set_active(v["try_nvenc"])
            self.sw_autostart.set_active(self.app.autostart_enabled())
            self._select_key(self.combo_lang, self.cfg["language"])
        finally:
            self._updating = False

    # ================================================================== helpers

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def _copy(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
        self.toast(_("Copied"))
