
/*
 * Inkslate tablet-side extension for the Weylus web client.
 *
 * Inkslate appends this file to the lib.js shipped with Weylus and serves the result through
 * `weylus --custom-lib-js`, so it runs in the same script scope as Weylus' own code and can
 * patch its classes (Settings, PEvent, PointerHandler) and wrap `run`.
 *
 * Copyright (C) 2026 Inkslate contributors
 * Licensed under the GNU Affero General Public License v3.0 or later, like Weylus itself.
 */
(function () {
    "use strict";

    const CONTROL_PORT = "@CONTROL_PORT@";
    const VERSION = "@VERSION@";
    const ERASER = 32;              // Button::ERASER in Weylus' protocol
    const STORAGE_KEY = "inkslate";

    const DEFAULTS = {
        curve: { in_min: 0, in_max: 1, gamma: 1 },
        smoothing: 0,
        eraser: false,
        palm: { enabled: true, ms: 500 },
        gestures: { enabled: true, two: "undo", three: "redo" },
        toolbar: {
            enabled: true,
            side: "left",
            buttons: [
                { action: "undo" },
                { action: "redo" },
                { action: "eraser" },
            ],
        },
        keys: {
            undo: { code: "KeyZ", key: "z", ctrl: true },
            redo: { code: "KeyZ", key: "Z", ctrl: true, shift: true },
        },
        wake_lock: true,
        fullscreen_on_tap: false,
    };

    const LABELS = {
        undo: { icon: "↶", en: "Undo", tr: "Geri al" },
        redo: { icon: "↷", en: "Redo", tr: "Yinele" },
        eraser: { icon: "⌫", en: "Eraser (tap: toggle, hold: momentary)", tr: "Silgi (dokun: aç/kapa, basılı tut: geçici)" },
        save: { icon: "💾", en: "Save", tr: "Kaydet" },
        touch: { icon: "☝", en: "Finger input on/off", tr: "Parmak girdisi aç/kapa" },
        fullscreen: { icon: "⛶", en: "Fullscreen", tr: "Tam ekran" },
    };
    const LANG = (navigator.language || "en").toLowerCase().startsWith("tr") ? "tr" : "en";

    let cfg = loadConfig();
    const state = {
        accessCode: "",
        ctrl: null,
        ctrlBackoff: 500,
        monitor: false,
        lastPressureReport: 0,
        lastPenSeen: -1e9,
        rejectedTouches: new Set(),
        forwardedTouches: new Map(),   // pointerId -> last PEvent sent to Weylus
        smooth: null,
        gesture: { active: new Map(), max: 0, start: 0, moved: false, cancelled: false },
        tempEraser: false,
        wakeLock: null,
        stateTimer: null,
        started: false,
    };

    function merge(base, over) {
        const out = Array.isArray(base) ? base.slice() : Object.assign({}, base);
        if (!over || typeof over !== "object")
            return out;
        for (const [k, v] of Object.entries(over)) {
            if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object" && !Array.isArray(base[k]))
                out[k] = merge(base[k], v);
            else
                out[k] = v;
        }
        return out;
    }

    function loadConfig() {
        try {
            return merge(DEFAULTS, JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"));
        } catch {
            return merge(DEFAULTS, {});
        }
    }

    function saveConfig() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
        } catch { /* storage may be unavailable, the PC resends the config anyway */ }
    }

    // ---------------------------------------------------------------- hooks into Weylus

    const weylusRun = run;
    // eslint-disable-next-line no-global-assign
    run = function (access_code, websocket_port, level) {
        state.accessCode = access_code;
        weylusRun(access_code, websocket_port, level);
        const weylusOnload = window.onload;
        window.onload = (e) => {
            if (weylusOnload)
                weylusOnload.call(window, e);
            start();
        };
    };

    const weylusSave = Settings.prototype.save_settings;
    Settings.prototype.save_settings = function () {
        weylusSave.call(this);
        scheduleState();
    };

    const weylusCapturables = Settings.prototype.onCapturableList;
    Settings.prototype.onCapturableList = function (names) {
        weylusCapturables.call(this, names);
        scheduleState();
    };

    PointerHandler.prototype.onEvent = function (event, event_type) {
        handlePointer(this, event, event_type);
    };

    // ---------------------------------------------------------------- pointer pipeline

    function handlePointer(handler, event, type) {
        const pt = event.pointerType;
        const now = performance.now();
        if (pt === "pen") {
            state.lastPenSeen = now;
            if (cfg.palm.enabled)
                cancelForwardedTouches(handler);
        }
        if (pt === "touch") {
            const forwarding = handler.pointerTypes.includes("touch");
            if (!forwarding && cfg.gestures.enabled)
                feedGesture(event, type, now);
            if (!forwarding)
                return;
            const id = event.pointerId;
            if (state.rejectedTouches.has(id)) {
                if (type === "pointerup" || type === "pointercancel")
                    state.rejectedTouches.delete(id);
                return;
            }
            if (cfg.palm.enabled && type === "pointerdown" && now - state.lastPenSeen < cfg.palm.ms) {
                state.rejectedTouches.add(id);
                return;
            }
        }
        else if (!handler.pointerTypes.includes(pt)) {
            return;
        }

        const pe = new PEvent(type, event, event.target);
        if (pt === "pen")
            transformPen(pe, event, type);
        if (pt === "touch") {
            if (type === "pointerup" || type === "pointercancel")
                state.forwardedTouches.delete(event.pointerId);
            else
                state.forwardedTouches.set(event.pointerId, pe);
        }
        handler.webSocket.send(JSON.stringify({ "PointerEvent": pe }));
        if (settings.visible)
            settings.toggle();
        if (pt === "pen" && state.monitor)
            reportPressure(event, pe, now);
    }

    // A palm resting on the screen when the pen shows up: lift the touches Weylus already knows about.
    function cancelForwardedTouches(handler) {
        if (state.forwardedTouches.size === 0)
            return;
        for (const [id, last] of state.forwardedTouches) {
            const cancel = Object.assign({}, last, {
                event_type: "pointercancel", button: 0, buttons: 0, pressure: 0,
                timestamp: Math.round(performance.now() * 1000),
            });
            handler.webSocket.send(JSON.stringify({ "PointerEvent": cancel }));
            state.rejectedTouches.add(id);
        }
        state.forwardedTouches.clear();
    }

    function applyCurve(p) {
        const c = cfg.curve;
        let t = (p - c.in_min) / Math.max(1e-6, c.in_max - c.in_min);
        t = Math.min(1, Math.max(0, t));
        return Math.pow(t, Math.max(0.05, c.gamma));
    }

    function transformPen(pe, event, type) {
        if (pe.pressure > 0)
            pe.pressure = applyCurve(Math.min(1, pe.pressure));

        const contact = (event.buttons & 1) !== 0 && type !== "pointerup" && type !== "pointercancel";
        if (cfg.smoothing > 0 && contact) {
            if (!state.smooth || type === "pointerdown") {
                state.smooth = { x: pe.x, y: pe.y };
            } else {
                const a = 1 - Math.min(0.95, cfg.smoothing);
                state.smooth.x += a * (pe.x - state.smooth.x);
                state.smooth.y += a * (pe.y - state.smooth.y);
                pe.x = state.smooth.x;
                pe.y = state.smooth.y;
            }
        } else {
            state.smooth = null;
        }

        if (cfg.eraser || state.tempEraser) {
            pe.button = ERASER;
            pe.buttons = pe.buttons | ERASER;
        }
    }

    function reportPressure(event, pe, now) {
        if (now - state.lastPressureReport < 33)
            return;
        state.lastPressureReport = now;
        sendControl({
            type: "pressure",
            raw: event.pressure,
            out: pe.pressure,
            contact: (event.buttons & 1) !== 0,
            eraser: !!(cfg.eraser || state.tempEraser),
        });
    }

    // ---------------------------------------------------------------- gestures

    function feedGesture(event, type, now) {
        const g = state.gesture;
        const id = event.pointerId;
        if (type === "pointerdown") {
            if (g.active.size === 0) {
                g.start = now;
                g.max = 0;
                g.moved = false;
                g.cancelled = false;
            }
            g.active.set(id, { x: event.clientX, y: event.clientY });
            g.max = Math.max(g.max, g.active.size);
            // A tap while the pen is near is a resting palm, not a gesture.
            if (now - state.lastPenSeen < cfg.palm.ms)
                g.cancelled = true;
        } else if (type === "pointermove") {
            const p = g.active.get(id);
            if (p && Math.hypot(event.clientX - p.x, event.clientY - p.y) > 25)
                g.moved = true;
        } else if (g.active.has(id)) {
            g.active.delete(id);
            if (type === "pointercancel")
                g.cancelled = true;
            if (g.active.size === 0 && !g.moved && !g.cancelled && now - g.start < 400) {
                if (g.max === 2)
                    runAction(cfg.gestures.two);
                else if (g.max === 3)
                    runAction(cfg.gestures.three);
            }
        }
    }

    // ---------------------------------------------------------------- actions & keys

    function sendKey(combo) {
        if (!combo || !combo.code || !settings || !settings.webSocket)
            return;
        const base = {
            code: combo.code,
            key: combo.key || "",
            location: 0,
            alt: !!combo.alt,
            ctrl: !!combo.ctrl,
            shift: !!combo.shift,
            meta: !!combo.meta,
        };
        for (const event_type of ["down", "up"])
            settings.webSocket.send(JSON.stringify({ "KeyboardEvent": Object.assign({ event_type }, base) }));
    }

    function runAction(action) {
        if (!action || action === "none")
            return;
        switch (action) {
            case "eraser":
                cfg.eraser = !cfg.eraser;
                saveConfig();
                refreshToolbar();
                scheduleState();
                return;
            case "touch": {
                const el = settings.checks.get("enable_touch");
                el.checked = !el.checked;
                el.dispatchEvent(new Event("change"));
                refreshToolbar();
                return;
            }
            case "fullscreen":
                if (!document.fullscreenElement)
                    document.body.requestFullscreen({ navigationUI: "hide" }).catch(() => { });
                else
                    document.exitFullscreen();
                return;
            default:
                sendKey(cfg.keys[action]);
        }
    }

    // ---------------------------------------------------------------- toolbar

    function injectStyle() {
        const style = document.createElement("style");
        style.textContent = `
#inkslate-bar { position: fixed; top: 50%; transform: translateY(-50%); z-index: 50;
  display: flex; flex-direction: column; gap: 6px; padding: 6px; border-radius: 14px;
  background: rgba(40, 40, 40, 0.82); box-shadow: 0 2px 10px rgba(0, 0, 0, 0.4);
  opacity: 0.6; transition: opacity 0.2s; touch-action: none; user-select: none; -webkit-user-select: none; }
#inkslate-bar:hover, #inkslate-bar:active { opacity: 1; }
#inkslate-bar.left { left: 6px; }
#inkslate-bar.right { right: 6px; }
#inkslate-bar button { width: 54px; height: 54px; border: none; border-radius: 10px;
  background: rgba(255, 255, 255, 0.08); color: #eee; font-size: 26px; line-height: 1;
  font-family: system-ui, sans-serif; padding: 0; touch-action: none; }
#inkslate-bar button:active { background: rgba(255, 255, 255, 0.25); }
#inkslate-bar button.on { background: #e95420; color: #fff; }
#inkslate-bar button.text { font-size: 15px; font-weight: 600; }
#settings { z-index: 60; }
#inkslate-toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 60;
  background: rgba(40, 40, 40, 0.9); color: #eee; padding: 8px 14px; border-radius: 10px;
  font: 15px system-ui, sans-serif; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
#inkslate-toast.show { opacity: 1; }
`;
        document.head.appendChild(style);
    }

    function buttonInfo(spec) {
        const known = LABELS[spec.action];
        const label = spec.label || (known ? known.icon : spec.action.slice(0, 3));
        const title = spec.title || (known ? known[LANG] : spec.action);
        return { label, title };
    }

    function buildToolbar() {
        let bar = document.getElementById("inkslate-bar");
        if (bar)
            bar.remove();
        if (!cfg.toolbar.enabled)
            return;
        bar = document.createElement("div");
        bar.id = "inkslate-bar";
        bar.className = cfg.toolbar.side === "right" ? "right" : "left";
        for (const spec of cfg.toolbar.buttons) {
            const { label, title } = buttonInfo(spec);
            const btn = document.createElement("button");
            btn.textContent = label;
            btn.title = title;
            btn.dataset.action = spec.action;
            if ([...label].length > 2)
                btn.classList.add("text");
            if (spec.action === "eraser")
                attachEraserButton(btn);
            else
                btn.addEventListener("click", (e) => { e.stopPropagation(); runAction(spec.action); });
            bar.appendChild(btn);
        }
        // Keep the bar's own taps away from Weylus' settings panel and pointer handling.
        for (const t of ["pointerdown", "pointerup", "pointermove", "click"])
            bar.addEventListener(t, (e) => e.stopPropagation());
        document.body.appendChild(bar);
        refreshToolbar();
    }

    // Tap toggles the eraser, holding the button keeps it active only while held.
    function attachEraserButton(btn) {
        let downAt = 0;
        let holdTimer = null;
        btn.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            btn.setPointerCapture(e.pointerId);
            downAt = performance.now();
            holdTimer = setTimeout(() => {
                state.tempEraser = true;
                refreshToolbar();
            }, 300);
        });
        const release = () => {
            clearTimeout(holdTimer);
            if (state.tempEraser) {
                state.tempEraser = false;
                refreshToolbar();
            } else if (performance.now() - downAt < 300) {
                runAction("eraser");
            }
        };
        btn.addEventListener("pointerup", release);
        btn.addEventListener("pointercancel", () => {
            clearTimeout(holdTimer);
            state.tempEraser = false;
            refreshToolbar();
        });
    }

    function refreshToolbar() {
        const bar = document.getElementById("inkslate-bar");
        if (!bar)
            return;
        for (const btn of bar.querySelectorAll("button")) {
            if (btn.dataset.action === "eraser")
                btn.classList.toggle("on", !!(cfg.eraser || state.tempEraser));
            if (btn.dataset.action === "touch")
                btn.classList.toggle("on", settings.checks.get("enable_touch").checked);
        }
    }

    function toast(text) {
        let el = document.getElementById("inkslate-toast");
        if (!el) {
            el = document.createElement("div");
            el.id = "inkslate-toast";
            document.body.appendChild(el);
        }
        el.textContent = text;
        el.classList.add("show");
        clearTimeout(el._timer);
        el._timer = setTimeout(() => el.classList.remove("show"), 1800);
    }

    // ---------------------------------------------------------------- screen

    // Weylus' resize handler resizes (and thereby clears) the canvas that shows the black
    // energy-saving screen, e.g. when going fullscreen or rotating. Paint it black again.
    function redrawBlack() {
        if (!settings || !settings.checks.get("energysaving").checked)
            return;
        const canvas = document.getElementById("canvas");
        const ctx = canvas.getContext("2d");
        if (!ctx)
            return;
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    async function updateWakeLock() {
        if (!cfg.wake_lock) {
            if (state.wakeLock) {
                state.wakeLock.release().catch(() => { });
                state.wakeLock = null;
            }
            return;
        }
        if (state.wakeLock || !("wakeLock" in navigator) || document.visibilityState !== "visible")
            return;
        try {
            state.wakeLock = await navigator.wakeLock.request("screen");
            state.wakeLock.addEventListener("release", () => { state.wakeLock = null; });
        } catch { /* needs a secure context (http://127.0.0.1 over USB is one) */ }
    }

    // When Weylus restarts or the cable comes back, reload instead of showing Weylus' confirm dialog.
    function watchWeylusConnection() {
        const ws = settings.webSocket;
        const onClose = () => {
            const retry = () => {
                fetch("/lib.js", { cache: "no-store" })
                    .then((r) => { if (r.ok) location.reload(); else setTimeout(retry, 1500); })
                    .catch(() => setTimeout(retry, 1500));
            };
            setTimeout(retry, 1000);
        };
        ws.addEventListener("close", onClose);
        ws.addEventListener("error", onClose, { once: true });
    }

    // ---------------------------------------------------------------- native Weylus settings

    function nativeState() {
        const s = settings;
        const out = {};
        for (const [key, el] of s.checks.entries())
            out[key] = el.checked;
        out.frame_update_limit = Math.round(frame_update_scale(s.frame_update_limit_input.valueAsNumber));
        out.scale_video = s.scale_video_input.valueAsNumber;
        out.min_pressure = s.range_min_pressure.valueAsNumber;
        out.capturable = s.capturable_select.value;
        out.capturables = Array.from(s.capturable_select.options).map((o) => o.textContent);
        out.client_name = s.client_name_input.value;
        const [w, h] = calc_max_video_resolution(s.scale_video_input.valueAsNumber);
        out.video_resolution = [w, h];
        return out;
    }

    function fire(el, ...types) {
        for (const t of types)
            el.dispatchEvent(new Event(t));
    }

    function applyNative(key, value) {
        const s = settings;
        if (s.checks.has(key)) {
            const el = s.checks.get(key);
            if (el.disabled || el.checked === !!value)
                return;
            el.checked = !!value;
            fire(el, "change");
            refreshToolbar();
        } else if (key === "frame_update_limit") {
            s.frame_update_limit_input.value = String(frame_update_scale_inv(Math.max(1, Number(value))));
            fire(s.frame_update_limit_input, "input", "change");
        } else if (key === "scale_video") {
            s.scale_video_input.value = String(value);
            fire(s.scale_video_input, "input", "change");
        } else if (key === "min_pressure") {
            s.range_min_pressure.value = String(value);
            fire(s.range_min_pressure, "input", "change");
        } else if (key === "capturable") {
            s.capturable_select.value = String(value);
            fire(s.capturable_select, "change");
        } else if (key === "client_name") {
            s.client_name_input.value = String(value);
            fire(s.client_name_input, "change");
        }
    }

    // ---------------------------------------------------------------- control channel to the PC

    function sendControl(msg) {
        if (state.ctrl && state.ctrl.readyState === WebSocket.OPEN)
            state.ctrl.send(JSON.stringify(msg));
    }

    function fullState() {
        return {
            type: "state",
            native: nativeState(),
            inkslate: { eraser: !!cfg.eraser },
            fullscreen: !!document.fullscreenElement,
            visible: document.visibilityState === "visible",
            screen: { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio },
            wake_lock: !!state.wakeLock,
        };
    }

    function scheduleState() {
        if (!state.started)
            return;
        clearTimeout(state.stateTimer);
        state.stateTimer = setTimeout(() => sendControl(fullState()), 80);
    }

    function connectControl() {
        const proto = location.protocol === "https:" ? "wss://" : "ws://";
        let ws;
        try {
            ws = new WebSocket(proto + location.hostname + ":" + CONTROL_PORT + "/control");
        } catch {
            setTimeout(connectControl, 5000);
            return;
        }
        ws.onopen = () => {
            state.ctrl = ws;
            state.ctrlBackoff = 500;
            ws.send(JSON.stringify({
                type: "hello",
                version: VERSION,
                code: state.accessCode,
                user_agent: navigator.userAgent,
            }));
            ws.send(JSON.stringify(fullState()));
        };
        ws.onmessage = (e) => {
            try {
                handleControl(JSON.parse(e.data));
            } catch (err) {
                console.warn("inkslate: bad control message", err);
            }
        };
        ws.onclose = () => {
            if (state.ctrl === ws)
                state.ctrl = null;
            state.monitor = false;
            setTimeout(connectControl, state.ctrlBackoff);
            state.ctrlBackoff = Math.min(10000, state.ctrlBackoff * 2);
        };
    }

    function handleControl(msg) {
        switch (msg.type) {
            case "config":
                cfg = merge(DEFAULTS, msg.config);
                saveConfig();
                buildToolbar();
                updateWakeLock();
                scheduleState();
                break;
            case "set_native":
                for (const [k, v] of Object.entries(msg.values || {}))
                    applyNative(k, v);
                scheduleState();
                break;
            case "monitor":
                state.monitor = !!msg.on;
                break;
            case "action":
                if (msg.action === "reload")
                    location.reload();
                else if (msg.action === "refresh_capturables")
                    settings.webSocket.send('"GetCapturableList"');
                else if (msg.action === "toast")
                    toast(msg.text || "");
                else
                    runAction(msg.action);
                break;
            case "get_state":
                sendControl(fullState());
                break;
        }
    }

    // ---------------------------------------------------------------- startup

    function start() {
        if (state.started || !settings)
            return;
        state.started = true;
        injectStyle();
        buildToolbar();
        window.addEventListener("resize", () => setTimeout(redrawBlack, 50));
        document.addEventListener("fullscreenchange", () => {
            setTimeout(redrawBlack, 50);
            scheduleState();
        });
        document.addEventListener("visibilitychange", () => {
            updateWakeLock();
            scheduleState();
        });
        document.addEventListener("pointerup", () => {
            if (cfg.fullscreen_on_tap && !document.fullscreenElement && document.body.requestFullscreen)
                document.body.requestFullscreen({ navigationUI: "hide" }).catch(() => { });
        }, true);
        updateWakeLock();
        watchWeylusConnection();
        connectControl();
        window.inkslate = { version: VERSION, config: () => cfg, state, runAction };
    }
})();
