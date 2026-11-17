
/*
 * Inkslate tablet-side extension for the Weylus web client.
 *
 * Inkslate appends this file to the lib.js shipped with Weylus and serves the result through
 * `weylus --custom-lib-js`, so it runs in the same script scope as Weylus' own code. It works with
 * Weylus 0.11.4 and with Weylus Community Edition / Weylus master: instead of replacing Weylus'
 * pointer handling it wraps it, and it rewrites the PointerEvent messages on their way to the
 * websocket (pressure curve, eraser, screen area, right/middle-click strokes).
 *
 * Copyright (C) 2026 Inkslate contributors
 * Licensed under the GNU Affero General Public License v3.0 or later, like Weylus itself.
 */
(function () {
    "use strict";

    const CONTROL_PORT = "@CONTROL_PORT@";
    const VERSION = "@VERSION@";
    const ICONS = /*@ICONS@*/ {};
    const SVG_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
    const BUTTON = { PRIMARY: 1, SECONDARY: 2, AUXILARY: 4, ERASER: 32 };   // Weylus' protocol
    const STORAGE_KEY = "inkslate";
    const FULL = { x: 0, y: 0, w: 1, h: 1 };
    const HOLD_MS = 300;

    const DEFAULTS = {
        curve: { in_min: 0, in_max: 1, gamma: 1 },
        smoothing: 0,
        tap: { enabled: true, radius: 8, ms: 300 },
        eraser: false,
        palm: { enabled: true, ms: 500 },
        gestures: { enabled: true, two: "undo", three: "redo" },
        toolbar: {
            enabled: true,
            side: "left",
            size: 54,
            opacity: 0.6,
            theme: "dark",
            accent: "#3584e4",
            labels: false,
            fade: true,
            autohide: false,
            buttons: [
                { action: "undo", kind: "key", icon: "undo", title: "Undo" },
                { action: "redo", kind: "key", icon: "redo", title: "Redo" },
                { action: "eraser", kind: "toggle", icon: "eraser", title: "Eraser" },
            ],
        },
        keys: {
            undo: { code: "KeyZ", key: "z", ctrl: true },
            redo: { code: "KeyZ", key: "z", ctrl: true, shift: true },
        },
        screen: { background: "#000000", show_area: true },
        area: { display: FULL, aspect: 0 },
        wake_lock: true,
        fullscreen_on_tap: false,
        hide_menu: true,
    };

    let cfg = loadConfig();
    const state = {
        accessCode: "",
        ctrl: null,
        ctrlBackoff: 500,
        monitor: false,
        lastPressureReport: 0,
        lastPenSeen: -1e9,
        domEvent: null,               // the DOM event Weylus is currently turning into a message
        rejectedTouches: new Set(),
        forwardedTouches: new Map(),  // pointer_id -> last PointerEvent sent to Weylus
        smooth: null,
        tap: null,                    // pen-down point while a tap is held steady
        gesture: { active: new Map(), max: 0, start: 0, moved: false, cancelled: false },
        tempEraser: false,
        penMode: null,                // "right" | "middle": strokes become mouse-button drags
        penModeOnce: false,
        penModeStroke: false,
        wakeLock: null,
        stateTimer: null,
        fadeTimer: null,
        tuckTimer: null,
        revealedBy: null,             // "pen" | "touch" while the auto-hidden bar is out
        pressing: 0,                  // toolbar buttons currently held down
        started: false,
        menuHandled: false,
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
    run = function (...args) {
        // Weylus 0.11: run(access_code, websocket_port, level); newer: run(level), with the
        // access code in the page URL.
        state.accessCode = args.length >= 3 ? args[0] :
            (new URLSearchParams(location.search).get("access_code") || "");
        weylusRun.apply(this, args);
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

    // Weylus clears the canvas to black when energy saving is switched on: a global function in
    // 0.11, a Settings method in newer versions.
    if (typeof toggle_energysaving === "function") {
        const weylusEnergySaving = toggle_energysaving;
        // eslint-disable-next-line no-global-assign
        toggle_energysaving = function (on) {
            weylusEnergySaving(on);
            if (on)
                drawScreen();
        };
    }
    if (typeof Settings.prototype.toggle_energysaving === "function") {
        const weylusEnergySavingMethod = Settings.prototype.toggle_energysaving;
        Settings.prototype.toggle_energysaving = function (on) {
            weylusEnergySavingMethod.call(this, on);
            if (on)
                drawScreen();
        };
    }

    // Weylus reports errors with alert(), which freezes the page, pen included, until someone
    // taps OK. Show them without blocking and tell the PC.
    window.alert = (msg) => {
        console.warn("weylus:", msg);
        toast(String(msg), 6000);
        sendControl({ type: "weylus_error", message: String(msg) });
    };

    const weylusOnEvent = PointerHandler.prototype.onEvent;
    PointerHandler.prototype.onEvent = function (event, event_type) {
        hookSocket(this.webSocket);
        // A tap on the screen closes Weylus' menu, also when the tap itself is not forwarded.
        if (event_type === "pointerdown" && settings && settings.visible)
            settings.toggle();
        if (!admitPointer(this, event, event_type))
            return;
        // Weylus closes its menu on every pointer event over the screen, a hovering pen included
        // (CE also when the pen leaves the screen for the menu). Only the tap above closes it.
        const keepMenu = settings && settings.visible;
        if (keepMenu)
            settings.visible = false;
        state.domEvent = event;
        try {
            weylusOnEvent.call(this, event, event_type);
        } finally {
            state.domEvent = null;
            if (keepMenu)
                settings.visible = true;
        }
    };

    // Rewrite PointerEvent messages on their way out; everything else passes unchanged.
    function hookSocket(ws) {
        if (!ws || ws.__inkslateRaw)
            return;
        const send = ws.send.bind(ws);
        ws.__inkslateRaw = send;
        ws.send = function (data) {
            if (typeof data === "string" && data.startsWith('{"PointerEvent"')) {
                let pe;
                try {
                    pe = JSON.parse(data).PointerEvent;
                } catch {
                    return send(data);
                }
                for (const out of transformPointer(pe, state.domEvent))
                    send(JSON.stringify({ "PointerEvent": out }));
                return undefined;
            }
            return send(data);
        };
    }

    function rawSend(obj) {
        const ws = settings && settings.webSocket;
        if (!ws || ws.readyState !== WebSocket.OPEN)
            return;
        (ws.__inkslateRaw || ws.send.bind(ws))(JSON.stringify(obj));
    }

    // ---------------------------------------------------------------- pointer pipeline

    const ENDS = ["pointerup", "pointercancel"];
    const HOVER = ["pointerover", "pointerenter", "pointerout", "pointerleave"];

    // Decides whether Weylus gets to see a DOM pointer event at all.
    function admitPointer(handler, event, type) {
        const pt = event.pointerType;
        const now = performance.now();
        if (pt === "pen") {
            state.lastPenSeen = now;
            if (type === "pointerdown" || (event.buttons & BUTTON.PRIMARY))
                fadeBar();
            if (cfg.palm.enabled)
                cancelForwardedTouches();
            return true;
        }
        if (pt !== "touch")
            return true;
        // Weylus CE also sends over/enter/out/leave. A finger has no hover: these only come right
        // before pointerdown and after pointerup, and CE starts a touch contact on "over", before
        // palm rejection has seen the touch go down. Weylus 0.11 never sends them.
        if (HOVER.includes(type))
            return false;
        const forwarding = handler.pointerTypes.includes("touch");
        if (!forwarding) {
            if (cfg.gestures.enabled)
                feedGesture(event, type, now);
            return false;
        }
        const id = event.pointerId;
        if (state.rejectedTouches.has(id)) {
            if (ENDS.includes(type))
                state.rejectedTouches.delete(id);
            return false;
        }
        if (cfg.palm.enabled && type === "pointerdown" && now - state.lastPenSeen < cfg.palm.ms) {
            state.rejectedTouches.add(id);
            return false;
        }
        return true;
    }

    // Returns the PointerEvent messages to send instead of `pe` (usually just `pe`, changed).
    function transformPointer(pe, event) {
        const type = pe.pointer_type;
        const rect = event && event.target && event.target.getBoundingClientRect ?
            event.target.getBoundingClientRect() : null;
        const aspect = rect && rect.height > 0 ? rect.width / rect.height : window.innerWidth / window.innerHeight;
        if (type === "touch") {
            // Touches Weylus has seen go down.
            const t = pe.event_type;
            if (ENDS.includes(t))
                state.forwardedTouches.delete(pe.pointer_id);
            else if (t === "pointerdown" || (t === "pointermove" && state.forwardedTouches.has(pe.pointer_id)))
                state.forwardedTouches.set(pe.pointer_id, pe);
        }
        if (type === "pen")
            steadyTap(pe, rect);
        mapArea(pe, aspect);
        if (type !== "pen")
            return [pe];

        const raw = event ? event.pressure : pe.pressure;
        transformPen(pe);
        if (state.monitor)
            reportPressure(raw, pe);
        const converted = penModeStroke(pe);
        return converted ? [converted] : [pe];
    }

    // A pen tap should be a click, but the tip slips a little when it presses the glass (more so
    // when the pen is tilted), and the PC then sees a short drag. Hold the position still after
    // the tip goes down until it really moves (more than `radius` CSS px) or `ms` have passed.
    function steadyTap(pe, rect) {
        const tap = cfg.tap;
        if (!tap || !tap.enabled || !(tap.radius > 0))
            return;
        const t = pe.event_type;
        if (t === "pointerdown") {
            state.tap = { x: pe.x, y: pe.y, at: performance.now(), held: true };
            return;
        }
        const s = state.tap;
        if (!s)
            return;
        if (s.held) {
            const w = rect && rect.width ? rect.width : window.innerWidth;
            const h = rect && rect.height ? rect.height : window.innerHeight;
            const moved = Math.hypot((pe.x - s.x) * w, (pe.y - s.y) * h);
            if (moved > tap.radius || performance.now() - s.at > tap.ms) {
                s.held = false;
            } else {
                pe.x = s.x;
                pe.y = s.y;
            }
        }
        if (ENDS.includes(t))
            state.tap = null;
    }

    // A palm resting on the screen when the pen shows up: lift the touches Weylus already knows about.
    function cancelForwardedTouches() {
        if (state.forwardedTouches.size === 0)
            return;
        for (const [id, last] of state.forwardedTouches) {
            rawSend({
                "PointerEvent": Object.assign({}, last, {
                    event_type: "pointercancel", button: 0, buttons: 0, pressure: 0,
                    timestamp: Math.round(performance.now() * 1000),
                })
            });
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

    function transformPen(pe) {
        if (pe.pressure > 0)
            pe.pressure = applyCurve(Math.min(1, pe.pressure));

        const contact = (pe.buttons & BUTTON.PRIMARY) !== 0 && !ENDS.includes(pe.event_type);
        if (cfg.smoothing > 0 && contact) {
            if (!state.smooth || pe.event_type === "pointerdown") {
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
            pe.button = BUTTON.ERASER;
            pe.buttons = pe.buttons | BUTTON.ERASER;
        }
    }

    // While a right/middle-click mode is armed, a pen stroke is sent as a mouse-button drag.
    function penModeStroke(pe) {
        const t = pe.event_type;
        if (t === "pointerdown" && state.penMode)
            state.penModeStroke = true;
        if (!state.penModeStroke)
            return null;
        const b = state.penMode === "middle" ? BUTTON.AUXILARY : BUTTON.SECONDARY;
        const end = ENDS.includes(t);
        const m = Object.assign({}, pe, {
            pointer_type: "mouse",
            button: t === "pointerdown" || end ? b : 0,
            buttons: end ? 0 : b,
        });
        if (end) {
            state.penModeStroke = false;
            if (state.penModeOnce) {
                state.penMode = null;
                state.penModeOnce = false;
                refreshToolbar();
            }
        }
        return m;
    }

    // ---------------------------------------------------------------- screen area

    // The part of the tablet that is used: everything, or the largest centred rectangle with
    // the shape of the PC area, so circles stay circles.
    function tabletArea(aspect) {
        const want = cfg.area && cfg.area.aspect;
        if (!want || !aspect)
            return FULL;
        if (aspect > want) {
            const w = want / aspect;
            return { x: (1 - w) / 2, y: 0, w, h: 1 };
        }
        const h = aspect / want;
        return { x: 0, y: (1 - h) / 2, w: 1, h };
    }

    function isFull(r) {
        return Math.abs(r.x) < 1e-6 && Math.abs(r.y) < 1e-6 && Math.abs(r.w - 1) < 1e-6 && Math.abs(r.h - 1) < 1e-6;
    }

    function mapArea(pe, aspect) {
        const d = (cfg.area && cfg.area.display) || FULL;
        const t = tabletArea(aspect);
        if (isFull(d) && isFull(t))
            return;
        const u = Math.min(1, Math.max(0, (pe.x - t.x) / t.w));
        const v = Math.min(1, Math.max(0, (pe.y - t.y) / t.h));
        pe.x = d.x + u * d.w;
        pe.y = d.y + v * d.h;
    }

    // ---------------------------------------------------------------- pressure monitor

    function reportPressure(raw, pe) {
        const now = performance.now();
        if (now - state.lastPressureReport < 33)
            return;
        state.lastPressureReport = now;
        sendControl({
            type: "pressure",
            raw: raw,
            out: pe.pressure,
            contact: (pe.buttons & BUTTON.PRIMARY) !== 0,
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
        } else if (ENDS.includes(type) && g.active.has(id)) {
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

    // ---------------------------------------------------------------- keys & actions

    function keyMessages(combo, event_type) {
        const base = {
            code: combo.code,
            key: combo.key || "",
            location: 0,
            alt: !!combo.alt,
            ctrl: !!combo.ctrl,
            shift: !!combo.shift,
            meta: !!combo.meta,
        };
        const main = { "KeyboardEvent": Object.assign({ event_type }, base) };
        if (!combo.altgr)
            return [main];
        // Characters behind AltGr (e.g. "[" on many European layouts).
        const altgr = {
            "KeyboardEvent": {
                event_type, code: "AltRight", key: "AltGraph", location: 2,
                alt: false, ctrl: false, shift: false, meta: false,
            }
        };
        return event_type === "down" ? [altgr, main] : [main, altgr];
    }

    function keyDown(combo) {
        if (combo && combo.code)
            for (const m of keyMessages(combo, "down"))
                rawSend(m);
    }

    function keyUp(combo) {
        if (combo && combo.code)
            for (const m of keyMessages(combo, "up"))
                rawSend(m);
    }

    function sendCombo(combo) {
        keyDown(combo);
        keyUp(combo);
    }

    function setPenMode(mode, once) {
        state.penMode = mode;
        state.penModeOnce = !!(mode && once);
        refreshToolbar();
    }

    function runAction(action, spec) {
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
                if (document.fullscreenElement)
                    document.exitFullscreen();
                else if (document.body.requestFullscreen)
                    document.body.requestFullscreen({ navigationUI: "hide" }).catch(() => { });
                else    // iPad Safari has no fullscreen for pages
                    toast("This browser has no fullscreen mode", 3000);
                return;
            case "right_click":
                setPenMode(state.penMode === "right" ? null : "right", true);
                return;
            case "middle_drag":
                setPenMode(state.penMode === "middle" ? null : "middle", true);
                return;
            default:
                sendCombo((spec && spec.keys) || cfg.keys[action]);
        }
    }

    // ---------------------------------------------------------------- toolbar

    function injectStyle() {
        const style = document.createElement("style");
        style.textContent = `
#inkslate-bar { --s: 54px; --o: 0.6; --accent: #3584e4; --bg: rgba(36, 36, 36, 0.86); --fg: #eee;
  --btn: rgba(255, 255, 255, 0.08); --btn-press: rgba(255, 255, 255, 0.25);
  position: fixed; z-index: 50; display: flex; gap: calc(var(--s) * 0.11); padding: calc(var(--s) * 0.11);
  border-radius: calc(var(--s) * 0.26); background: var(--bg); box-shadow: 0 2px 12px rgba(0, 0, 0, 0.35);
  opacity: var(--o); transition: opacity 0.25s, transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
  touch-action: none; user-select: none; -webkit-user-select: none; --peek: 14px; }
#inkslate-bar.light { --bg: rgba(246, 246, 246, 0.92); --fg: #222; --btn: rgba(0, 0, 0, 0.06); --btn-press: rgba(0, 0, 0, 0.16); }
#inkslate-bar.left { left: 6px; top: 50%; transform: translateY(-50%); flex-direction: column; }
#inkslate-bar.right { right: 6px; top: 50%; transform: translateY(-50%); flex-direction: column; }
#inkslate-bar.top { top: 6px; left: 50%; transform: translateX(-50%); flex-direction: row; }
#inkslate-bar.bottom { bottom: 6px; left: 50%; transform: translateX(-50%); flex-direction: row; }
#inkslate-bar.faded { opacity: 0.12; }
#inkslate-bar.left.tucked { transform: translateY(-50%) translateX(calc(-100% - 6px + var(--peek))); }
#inkslate-bar.right.tucked { transform: translateY(-50%) translateX(calc(100% + 6px - var(--peek))); }
#inkslate-bar.top.tucked { transform: translateX(-50%) translateY(calc(-100% - 6px + var(--peek))); }
#inkslate-bar.bottom.tucked { transform: translateX(-50%) translateY(calc(100% + 6px - var(--peek))); }
#inkslate-bar button { transition: opacity 0.2s; }
#inkslate-bar.tucked button { pointer-events: none; opacity: 0; }
#inkslate-bar::after { content: ""; position: absolute; border-radius: 3px; background: var(--fg); opacity: 0;
  transition: opacity 0.2s; pointer-events: none; }
#inkslate-bar.left::after { right: 5px; top: 50%; width: 4px; height: min(40%, 64px); transform: translateY(-50%); }
#inkslate-bar.right::after { left: 5px; top: 50%; width: 4px; height: min(40%, 64px); transform: translateY(-50%); }
#inkslate-bar.top::after { bottom: 5px; left: 50%; height: 4px; width: min(40%, 64px); transform: translateX(-50%); }
#inkslate-bar.bottom::after { top: 5px; left: 50%; height: 4px; width: min(40%, 64px); transform: translateX(-50%); }
#inkslate-bar.tucked::after { opacity: 0.6; }
#inkslate-bar.tucked.faded { opacity: var(--o); }
#inkslate-bar:not(.tucked):hover, #inkslate-bar:active, #inkslate-bar.awake { opacity: 1; }
#inkslate-bar button { width: var(--s); height: var(--s); border: none; border-radius: calc(var(--s) * 0.19);
  background: var(--btn); color: var(--fg); padding: 0; touch-action: none; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 1px; font: 600 calc(var(--s) * 0.2) system-ui, sans-serif; overflow: hidden; }
#inkslate-bar button svg { width: 48%; height: 48%; flex: none; }
#inkslate-bar.labels button svg { width: 40%; height: 40%; }
#inkslate-bar button .lbl { max-width: 94%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.1; }
#inkslate-bar button:active { background: var(--btn-press); }
#inkslate-bar button.on { background: var(--accent); color: #fff; }
#settings { z-index: 60; }
#inkslate-toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 60;
  background: rgba(40, 40, 40, 0.9); color: #eee; padding: 8px 14px; border-radius: 10px;
  font: 15px system-ui, sans-serif; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
#inkslate-toast.show { opacity: 1; }

/* ---- Weylus' settings menu: a drawer docked to the screen edge ---- */
#settings.ink-menu { --ink-accent: #3584e4; --ink-bg: #1d1d1f; --ink-fg: #ebebeb; --ink-dim: #8e8e93;
  --ink-line: rgba(255, 255, 255, 0.08); --ink-edge: rgba(255, 255, 255, 0.16); --ink-field: rgba(255, 255, 255, 0.04);
  --ink-off: rgba(255, 255, 255, 0.3); --ink-mono: ui-monospace, "SF Mono", "Roboto Mono", "DejaVu Sans Mono", monospace;
  position: fixed !important; top: 0; bottom: 0; right: 0; left: auto; z-index: 60; height: 100%;
  width: min(312px, calc(100vw - 48px)); box-sizing: border-box; display: flex !important; flex-direction: column;
  padding: 0 !important; border-radius: 0; border-left: 1px solid var(--ink-edge);
  background: var(--ink-bg) !important; color: var(--ink-fg) !important; box-shadow: -6px 0 18px rgba(0, 0, 0, 0.22);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif !important; opacity: 1 !important;
  transition: transform 0.26s cubic-bezier(0.3, 0.7, 0.2, 1) !important; }
#settings.ink-menu.light { --ink-bg: #f7f7f5; --ink-fg: #1f1f1f; --ink-dim: #6e6e73; --ink-line: rgba(0, 0, 0, 0.07);
  --ink-edge: rgba(0, 0, 0, 0.15); --ink-field: rgba(0, 0, 0, 0.03); --ink-off: rgba(0, 0, 0, 0.3); }
#settings.ink-menu.lefty { left: 0; right: auto; border-left: none; border-right: 1px solid var(--ink-edge);
  box-shadow: 6px 0 18px rgba(0, 0, 0, 0.22); }
#settings.ink-menu.hide, #settings.ink-menu.vanish { transform: translateX(100%); box-shadow: none; }
#settings.ink-menu.hide.lefty, #settings.ink-menu.vanish.lefty { transform: translateX(-100%); }
#settings.ink-menu #handle { position: absolute; top: 14px; left: -31px; right: auto; width: 30px; height: 46px;
  padding: 0; box-sizing: border-box; display: flex; align-items: center; justify-content: center; font-size: 0;
  border: 1px solid var(--ink-edge); border-right: none; border-radius: 8px 0 0 8px;
  background: var(--ink-bg) !important; color: var(--ink-dim) !important; }
#settings.ink-menu.lefty #handle { left: auto; right: -31px; border-right: 1px solid var(--ink-edge); border-left: none;
  border-radius: 0 8px 8px 0; }
#settings.ink-menu #handle svg { width: 18px; height: 18px; transition: transform 0.26s; }
#settings.ink-menu.hide #handle svg, #settings.ink-menu.lefty:not(.hide) #handle svg { transform: rotate(180deg); }
#settings.ink-menu #settings_scroll { box-sizing: border-box; height: auto !important; flex: 1 1 auto;
  overflow-y: auto; padding: 0 18px 14px; overscroll-behavior: contain; scrollbar-width: thin; }
#settings.ink-menu h2 { font-size: 16px; font-weight: 600; text-align: left; margin: 0 -18px 4px; padding: 16px 18px 14px;
  border-bottom: 1px solid var(--ink-line); }
#settings.ink-menu h3 { display: flex; align-items: center; gap: 10px; margin: 16px 0 2px; font-size: 12px;
  font-weight: 600; color: var(--ink-dim); text-align: left; text-transform: none; letter-spacing: 0.02em; }
#settings.ink-menu h3::after { content: ""; flex: 1; height: 1px; background: var(--ink-line); }
#settings.ink-menu section { background: none; border: none; border-radius: 0; padding: 0; margin: 0; }
#settings.ink-menu section label { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  min-height: 40px; margin: 0; padding: 4px 0; box-sizing: border-box; border: none; }
#settings.ink-menu .hide { display: none !important; }
#settings.ink-menu br { display: none; }
#settings.ink-menu label.ink-check { flex-direction: row-reverse; }
#settings.ink-menu label > span, #settings.ink-menu .ink-text { flex: 1 1 auto; }
#settings.ink-menu label input:disabled + span { color: var(--ink-dim); }
#settings.ink-menu input[type=checkbox] { -webkit-appearance: none; appearance: none; flex: none; position: relative;
  width: 38px; height: 22px; margin: 0; box-sizing: border-box; border: 1.5px solid var(--ink-off); border-radius: 6px;
  background: transparent; transition: background 0.15s, border-color 0.15s; cursor: pointer; }
#settings.ink-menu input[type=checkbox]::before { content: ""; position: absolute; top: 3px; left: 3px; width: 13px;
  height: 13px; border-radius: 3px; background: var(--ink-off); transition: transform 0.15s, background 0.15s; }
#settings.ink-menu input[type=checkbox]:checked { background: var(--ink-accent); border-color: var(--ink-accent); }
#settings.ink-menu input[type=checkbox]:checked::before { transform: translateX(16px); background: #fff; }
#settings.ink-menu input[type=checkbox]:disabled { opacity: 0.35; }
#settings.ink-menu label.ink-range { flex-wrap: wrap; row-gap: 0; padding-top: 8px; }
#settings.ink-menu label.ink-range .ink-text { flex: 1 1 0; min-width: 0; }
#settings.ink-menu label.ink-range output { order: 1; margin-left: auto; color: var(--ink-dim);
  font: 12px var(--ink-mono); }
#settings.ink-menu .ink-unit { order: 2; flex: none; margin-left: -9px; color: var(--ink-dim); font: 12px var(--ink-mono); }
#settings.ink-menu input[type=range] { order: 3; flex: 1 1 100%; width: 100%; height: 28px; margin: 0;
  -webkit-appearance: none; appearance: none; background: transparent; }
#settings.ink-menu input[type=range]::-webkit-slider-runnable-track { height: 2px; border-radius: 1px;
  background: linear-gradient(to right, var(--ink-accent) var(--p, 0%), var(--ink-off) var(--p, 0%)); }
#settings.ink-menu input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 6px; height: 20px;
  margin-top: -9px; border: none; border-radius: 3px; background: var(--ink-fg); box-shadow: 0 0 0 3px var(--ink-bg); }
#settings.ink-menu input[type=range]::-moz-range-track { height: 2px; background: var(--ink-off); }
#settings.ink-menu input[type=range]::-moz-range-progress { height: 2px; background: var(--ink-accent); }
#settings.ink-menu input[type=range]::-moz-range-thumb { width: 6px; height: 20px; border: none; border-radius: 3px;
  background: var(--ink-fg); }
#settings.ink-menu select, #settings.ink-menu input[type=text] { -webkit-appearance: none; appearance: none;
  width: 100%; box-sizing: border-box; min-height: 36px; margin: 4px 0; padding: 6px 10px; border: 1px solid var(--ink-edge);
  border-radius: 6px; background-color: var(--ink-field); color: inherit; font: inherit; }
#settings.ink-menu select { padding-right: 30px; background-repeat: no-repeat; background-position: right 10px center;
  background-size: 12px; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23999' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E"); }
#settings.ink-menu select option { background: #2a2a2c; color: #ebebeb; }
#settings.ink-menu.light select option { background: #fff; color: #1f1f1f; }
#settings.ink-menu button { -webkit-appearance: none; appearance: none; display: block; width: 100%; min-height: 36px;
  margin: 6px 0; padding: 6px 12px; border: 1px solid var(--ink-edge); border-radius: 6px; background: transparent;
  color: inherit; font: inherit; font-weight: 500; }
#settings.ink-menu button:active, #settings.ink-menu #displayoptions label:active { background: var(--ink-field);
  border-color: var(--ink-accent); }
#settings.ink-menu label[for=window] { min-height: 0; padding: 10px 0 0; font-size: 12px; color: var(--ink-dim); }
#settings.ink-menu label.ink-textfield { flex-direction: column; align-items: stretch; gap: 0; }
#settings.ink-menu label.ink-textfield > span:last-child { font-size: 12px; color: var(--ink-dim); }
#settings.ink-menu #displayoptions { display: flex; gap: 8px; margin-top: 16px; padding-top: 14px;
  border-top: 1px solid var(--ink-line); }
#settings.ink-menu #displayoptions label { flex: 1; justify-content: center; min-height: 36px; padding: 6px 8px;
  border: 1px solid var(--ink-edge); border-radius: 6px; font-weight: 500; text-align: center; }
#settings.ink-menu #displayoptions #leftylabel { flex-direction: row; gap: 6px; }
#settings.ink-menu #displayoptions #leftylabel .ink-text { flex: none; }
#settings.ink-menu #stats_section label { min-height: 0; padding: 10px 0 0; flex-direction: row-reverse;
  justify-content: flex-end; gap: 5px;
  font: 11px var(--ink-mono); color: var(--ink-dim); }
#settings.ink-menu #stats_section label > span { flex: none; }
#settings.ink-menu #stats_section output { font: inherit; }
#settings.ink-menu #log { max-width: 100%; font: 11px var(--ink-mono); }
`;
        document.head.appendChild(style);
    }

    function buildToolbar() {
        let bar = document.getElementById("inkslate-bar");
        if (bar)
            bar.remove();
        const tb = cfg.toolbar;
        if (!tb.enabled || !tb.buttons.length)
            return;
        bar = document.createElement("div");
        bar.id = "inkslate-bar";
        const side = ["left", "right", "top", "bottom"].includes(tb.side) ? tb.side : "left";
        bar.className = side + (tb.theme === "light" ? " light" : "") + (tb.labels ? " labels" : "") +
            (tb.autohide ? " tucked" : "");
        state.revealedBy = null;
        state.pressing = 0;
        bar.style.setProperty("--s", Math.max(32, Math.min(120, Number(tb.size) || 54)) + "px");
        bar.style.setProperty("--o", String(Math.max(0.1, Math.min(1, Number(tb.opacity) || 0.6))));
        if (tb.accent)
            bar.style.setProperty("--accent", tb.accent);
        for (const spec of tb.buttons) {
            const btn = document.createElement("button");
            btn.title = spec.title || spec.action;
            btn.dataset.action = spec.action;
            const icon = ICONS[spec.icon || spec.action];
            if (icon)
                btn.insertAdjacentHTML("beforeend", SVG_OPEN + icon + "</svg>");
            if (tb.labels || spec.label || !icon) {
                const lbl = document.createElement("span");
                lbl.className = "lbl";
                lbl.textContent = spec.label || spec.short || spec.title || spec.action;
                btn.appendChild(lbl);
            }
            attachButton(btn, spec);
            bar.appendChild(btn);
        }
        // Keep the bar's own taps away from Weylus' settings panel and pointer handling.
        for (const t of ["pointerdown", "pointerup", "pointermove", "click"])
            bar.addEventListener(t, (e) => e.stopPropagation());
        bar.addEventListener("pointerenter", () => wakeBar());
        // Tucked away: a tap on the visible strip only brings the bar out (the buttons ignore
        // pointers while tucked, so the tap lands here).
        bar.addEventListener("pointerdown", (e) => {
            if (bar.classList.contains("tucked")) {
                e.preventDefault();
                revealBar(e.pointerType === "pen" ? "pen" : "touch");
            } else if (cfg.toolbar.autohide) {
                keepBarOut();
            }
        });
        document.body.appendChild(bar);
        refreshToolbar();
    }

    // Tap vs. hold for one button. Without hold callbacks every release is a tap. With them,
    // holding longer than HOLD_MS (or at once with holdNow) runs onHoldStart/onHoldEnd instead.
    function pressable(btn, { onTap, onHoldStart, onHoldEnd, holdNow }) {
        let holding = false;
        let armed = false;      // the press started on this button
        let timer = null;
        const startHold = () => {
            holding = true;
            onHoldStart();
        };
        btn.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            btn.setPointerCapture(e.pointerId);
            holding = false;
            if (!armed)
                state.pressing++;
            armed = true;
            if (!onHoldStart)
                return;
            if (holdNow)
                startHold();
            else
                timer = setTimeout(startHold, HOLD_MS);
        });
        const finish = (cancelled) => {
            clearTimeout(timer);
            timer = null;
            if (!armed)
                return;
            armed = false;
            state.pressing = Math.max(0, state.pressing - 1);
            if (holding) {
                holding = false;
                if (onHoldEnd)
                    onHoldEnd();
            } else if (!cancelled && onTap) {
                onTap();
            }
        };
        btn.addEventListener("pointerup", () => finish(false));
        btn.addEventListener("pointercancel", () => finish(true));
    }

    function attachButton(btn, spec) {
        switch (spec.kind) {
            case "hold":
                pressable(btn, {
                    holdNow: true,
                    onHoldStart: () => { keyDown(spec.keys); btn.classList.add("on"); },
                    onHoldEnd: () => { keyUp(spec.keys); btn.classList.remove("on"); },
                });
                break;
            case "toggle":   // eraser
                pressable(btn, {
                    onTap: () => runAction(spec.action, spec),
                    onHoldStart: () => { state.tempEraser = true; refreshToolbar(); },
                    onHoldEnd: () => { state.tempEraser = false; refreshToolbar(); },
                });
                break;
            case "penmode": {
                const mode = spec.action === "middle_drag" ? "middle" : "right";
                pressable(btn, {
                    onTap: () => setPenMode(state.penMode === mode ? null : mode, true),
                    onHoldStart: () => setPenMode(mode, false),
                    onHoldEnd: () => { if (state.penMode === mode && !state.penModeOnce) setPenMode(null); },
                });
                break;
            }
            default:
                pressable(btn, { onTap: () => runAction(spec.action, spec) });
        }
    }

    function refreshToolbar() {
        const bar = document.getElementById("inkslate-bar");
        if (!bar)
            return;
        for (const btn of bar.querySelectorAll("button")) {
            const a = btn.dataset.action;
            if (a === "eraser")
                btn.classList.toggle("on", !!(cfg.eraser || state.tempEraser));
            else if (a === "touch")
                btn.classList.toggle("on", settings.checks.get("enable_touch").checked);
            else if (a === "right_click")
                btn.classList.toggle("on", state.penMode === "right");
            else if (a === "middle_drag")
                btn.classList.toggle("on", state.penMode === "middle");
        }
    }

    // ---- auto-hide: the bar waits at the edge as a thin strip

    const NEAR = 36;       // px around the strip / the bar where a hovering pen counts as "near"

    // Where the bar sits when it is out (the tucked bar is only moved along one axis).
    function barHome(bar) {
        const r = bar.getBoundingClientRect();
        const W = window.innerWidth, H = window.innerHeight;
        if (bar.classList.contains("left"))
            return { x0: 6, x1: 6 + r.width, y0: r.top, y1: r.bottom };
        if (bar.classList.contains("right"))
            return { x0: W - 6 - r.width, x1: W - 6, y0: r.top, y1: r.bottom };
        if (bar.classList.contains("top"))
            return { x0: r.left, x1: r.right, y0: 6, y1: 6 + r.height };
        return { x0: r.left, x1: r.right, y0: H - 6 - r.height, y1: H - 6 };
    }

    function penNearBar(bar, x, y) {
        const h = barHome(bar);
        if (!bar.classList.contains("tucked"))
            return x > h.x0 - NEAR && x < h.x1 + NEAR && y > h.y0 - NEAR && y < h.y1 + NEAR;
        // tucked: only the strip along the edge
        const W = window.innerWidth, H = window.innerHeight, band = 14 + NEAR;
        if (bar.classList.contains("left"))
            return x < band && y > h.y0 - NEAR && y < h.y1 + NEAR;
        if (bar.classList.contains("right"))
            return x > W - band && y > h.y0 - NEAR && y < h.y1 + NEAR;
        if (bar.classList.contains("top"))
            return y < band && x > h.x0 - NEAR && x < h.x1 + NEAR;
        return y > H - band && x > h.x0 - NEAR && x < h.x1 + NEAR;
    }

    function revealBar(by) {
        const bar = document.getElementById("inkslate-bar");
        if (!bar)
            return;
        bar.classList.remove("tucked", "faded");
        state.revealedBy = by;
        keepBarOut();
    }

    function tuckBar() {
        clearTimeout(state.tuckTimer);
        const bar = document.getElementById("inkslate-bar");
        if (!bar || !cfg.toolbar.autohide)
            return;
        // a held button (pan, eraser…) keeps the bar out
        if (state.pressing > 0)
            return keepBarOut();
        bar.classList.add("tucked");
        state.revealedBy = null;
    }

    // Out for a while after the last touch; a hovering pen keeps it out by itself.
    function keepBarOut(ms) {
        clearTimeout(state.tuckTimer);
        state.tuckTimer = setTimeout(tuckBar, ms || (state.revealedBy === "pen" ? 1500 : 3000));
    }

    function watchPenForBar(e) {
        if (e.pointerType !== "pen" || !cfg.toolbar.autohide)
            return;
        const bar = document.getElementById("inkslate-bar");
        if (!bar)
            return;
        const hovering = (e.buttons & BUTTON.PRIMARY) === 0;
        const near = penNearBar(bar, e.clientX, e.clientY);
        const tucked = bar.classList.contains("tucked");
        if (near && hovering) {
            if (tucked)
                revealBar("pen");
            else
                clearTimeout(state.tuckTimer);
        } else if (!tucked) {
            if (!near && !hovering)
                tuckBar();               // drawing elsewhere
            else if (!near)
                keepBarOut(600);         // the pen moved away
        }
    }

    // Almost hide the bar while the pen is in use; it comes back when the pen leaves.
    function fadeBar() {
        const bar = document.getElementById("inkslate-bar");
        if (!bar || !cfg.toolbar.fade)
            return;
        bar.classList.add("faded");
        clearTimeout(state.fadeTimer);
        state.fadeTimer = setTimeout(() => bar.classList.remove("faded"), 1200);
    }

    function wakeBar() {
        const bar = document.getElementById("inkslate-bar");
        if (!bar)
            return;
        clearTimeout(state.fadeTimer);
        bar.classList.remove("faded");
    }

    function toast(text, ms) {
        let el = document.getElementById("inkslate-toast");
        if (!el) {
            el = document.createElement("div");
            el.id = "inkslate-toast";
            document.body.appendChild(el);
        }
        el.textContent = text;
        el.classList.add("show");
        clearTimeout(el._timer);
        el._timer = setTimeout(() => el.classList.remove("show"), ms || 1800);
    }


    // ---------------------------------------------------------------- Weylus' settings menu

    const MENU_ICON = '<path d="M9.5 6l6 6-6 6"/>';

    // Shorter labels for Weylus' menu, by element id.
    const LABELS = {
        enable_stylus: "Pen", enable_touch: "Finger input", enable_mouse: "Mouse", uinput_support: "uinput",
        min_pressure: "Minimum pressure", enable_video: "Video", energysaving: "Energy saving",
        stretch: "Fill the screen", capture_cursor: "Show the cursor in the video", aggressive_seeking: "Low latency",
        scale_video: "Maximum resolution", frame_update_limit: "Frame interval", frame_rate: "Frame rate",
        refresh: "Refresh the list", fullscreen: "Fullscreen", custom_input_areas: "Custom input area",
        enable_custom_input_areas: "Use the custom input area", enable_debug_overlay: "Debug overlay",
    };

    // Plain text inside the menu's labels becomes spans, so it can be styled and replaced.
    // The structure Weylus relies on (ids, input -> output sibling) stays as it is.
    function wrapLabelText(label) {
        let afterControl = false;
        for (const n of Array.from(label.childNodes)) {
            if (n.nodeType === 1 && (n.tagName === "OUTPUT" || (n.tagName === "INPUT" && n.type === "range")))
                afterControl = true;
            if (n.nodeType !== 3 || !n.textContent.trim())
                continue;
            const span = document.createElement("span");
            span.className = afterControl ? "ink-unit" : "ink-text";
            span.textContent = n.textContent.trim();
            n.replaceWith(span);
        }
    }

    function heading(text) {
        const h = document.createElement("h3");
        h.textContent = text;
        return h;
    }

    // Input (pen, touch) first: it matters most for a pen tablet. The captured screen goes with
    // the video settings, the device name gets a heading of its own.
    function reorderMenu() {
        const heads = Array.from(document.querySelectorAll("#settings_scroll > h3"));
        const videoHead = heads.find((h) => h.nextElementSibling && h.nextElementSibling.querySelector("#enable_video"));
        const inputHead = heads.find((h) => h.nextElementSibling && h.nextElementSibling.querySelector("#enable_stylus"));
        if (!videoHead || !inputHead)
            return;
        const videoSection = videoHead.nextElementSibling;
        videoHead.before(inputHead, inputHead.nextElementSibling);
        inputHead.textContent = "Input";
        videoHead.textContent = "Video";
        const select = document.getElementById("window");
        const capture = select && select.closest("section");
        if (capture && capture !== videoSection)
            videoSection.after(capture);
        const client = document.getElementById("client_name");
        const clientSection = client && client.closest("section");
        if (clientSection)
            clientSection.before(heading("This device"));
    }

    function rangeFill(r) {
        const min = Number(r.min || 0), max = Number(r.max || 100);
        const p = max > min ? (r.valueAsNumber - min) / (max - min) * 100 : 0;
        r.style.setProperty("--p", Math.max(0, Math.min(100, p)) + "%");
    }

    function refreshRanges() {
        for (const r of document.querySelectorAll("#settings input[type=range]"))
            rangeFill(r);
    }

    function setText(el, text) {
        if (el && text)
            el.textContent = text;
    }

    function relabelMenu() {
        setText(document.querySelector("#settings h2"), "Inkslate");
        for (const [id, text] of Object.entries(LABELS)) {
            const el = document.getElementById(id);
            if (!el)
                continue;
            if (el.tagName === "BUTTON")
                setText(el, text);
            else if (el.type === "range")
                setText(el.closest("label") && el.closest("label").querySelector(".ink-text"), text);
            else if (el.parentElement)
                setText(el.parentElement.querySelector("span"), text);
        }
        setText(document.querySelector('#settings label[for="window"]'), "Captured screen");
        const lefty = document.getElementById("leftylabel");
        setText(lefty && lefty.querySelector(".ink-text"), "Swap side");
        const vanish = document.getElementById("vanish");
        setText(vanish && vanish.querySelector(".ink-text"), "Hide until reload");
        const client = document.getElementById("client_name");
        const clientLabel = client && client.closest("label");
        if (clientLabel) {
            const spans = clientLabel.querySelectorAll("span");
            setText(spans[0], "Name");
            if (spans.length > 1)
                setText(spans[spans.length - 1], "Optional, tells several tablets apart");
        }
        const fps = document.getElementById("fps");
        setText(fps && fps.closest("label") && fps.closest("label").querySelector("span"), "fps");
    }

    function styleMenu() {
        const menu = document.getElementById("settings");
        if (!menu)
            return;
        try {
            if (!menu.classList.contains("ink-menu")) {
                const handle = document.getElementById("handle");
                if (handle)
                    handle.innerHTML = SVG_OPEN + MENU_ICON + "</svg>";
                for (const label of menu.querySelectorAll("label")) {
                    wrapLabelText(label);
                    // classes instead of :has(), which older tablet browsers don't know
                    const kinds = Array.from(label.children).map((c) => c.tagName === "INPUT" ? c.type : "");
                    label.classList.toggle("ink-check", kinds.includes("checkbox"));
                    label.classList.toggle("ink-range", kinds.includes("range"));
                    label.classList.toggle("ink-textfield", kinds.includes("text"));
                }
                reorderMenu();
                relabelMenu();
                for (const r of menu.querySelectorAll("input[type=range]")) {
                    r.addEventListener("input", () => rangeFill(r));
                    r.addEventListener("change", () => rangeFill(r));
                }
                menu.classList.add("ink-menu");
            }
            refreshRanges();
            menu.classList.toggle("light", cfg.toolbar.theme === "light");
            menu.style.setProperty("--ink-accent", cfg.toolbar.accent || "#3584e4");
        } catch (err) {
            console.warn("inkslate: menu styling failed", err);
        }
    }

    // ---------------------------------------------------------------- screen

    function luminance(hex) {
        const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
        if (!m)
            return 0;
        const n = parseInt(m[1], 16);
        return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    }

    // The energy-saving screen: background colour and, when only part of the tablet is used,
    // the unused bands. Weylus clears the canvas on resize (fullscreen, rotation), so this
    // runs again after those.
    function drawScreen() {
        if (!settings || !settings.checks.get("energysaving").checked)
            return;
        const canvas = document.getElementById("canvas");
        const ctx = canvas && canvas.getContext("2d");
        if (!ctx)
            return;
        const W = canvas.width, H = canvas.height;
        const bg = cfg.screen.background || "#000000";
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, W, H);
        const r = tabletArea(H > 0 ? W / H : 0);
        if (!cfg.screen.show_area || isFull(r))
            return;
        const light = luminance(bg) > 0.5;
        ctx.fillStyle = light ? "rgba(0, 0, 0, 0.07)" : "rgba(255, 255, 255, 0.07)";
        const x0 = r.x * W, y0 = r.y * H, x1 = (r.x + r.w) * W, y1 = (r.y + r.h) * H;
        ctx.fillRect(0, 0, W, y0);
        ctx.fillRect(0, y1, W, H - y1);
        ctx.fillRect(0, y0, x0, y1 - y0);
        ctx.fillRect(x1, y0, W - x1, y1 - y0);
        ctx.strokeStyle = light ? "rgba(0, 0, 0, 0.25)" : "rgba(255, 255, 255, 0.25)";
        ctx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
        ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
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

    // Weylus 0.11.4 has a minimum frame interval in ms, Community Edition / master a frame rate.
    function frameRate() {
        const s = settings;
        if (s.frame_update_limit_input && typeof frame_update_scale === "function") {
            const ms = frame_update_scale(s.frame_update_limit_input.valueAsNumber);
            return ms > 0 ? Math.round(1000 / ms) : 0;
        }
        if (s.frame_rate_input && typeof frame_rate_scale === "function")
            return Math.round(frame_rate_scale(s.frame_rate_input.valueAsNumber));
        return null;
    }

    function setFrameRate(fps) {
        const s = settings;
        fps = Math.max(1, Math.min(120, Number(fps) || 30));
        let el = null;
        if (s.frame_update_limit_input && typeof frame_update_scale_inv === "function") {
            el = s.frame_update_limit_input;
            el.value = String(frame_update_scale_inv(1000 / fps));
        } else if (s.frame_rate_input && typeof frame_rate_scale_inv === "function") {
            el = s.frame_rate_input;
            el.value = String(frame_rate_scale_inv(fps));
        }
        if (el)
            fire(el, "input", "change");
    }

    function nativeState() {
        const s = settings;
        const out = {};
        for (const [key, el] of s.checks.entries())
            out[key] = el.checked;
        out.frame_rate = frameRate();
        out.flavor = s.frame_update_limit_input ? "weylus-0.11" : "weylus-ce";
        out.scale_video = s.scale_video_input.valueAsNumber;
        out.min_pressure = s.range_min_pressure.valueAsNumber;
        out.capturable = s.capturable_select.value;
        out.capturables = Array.from(s.capturable_select.options).map((o) => o.textContent);
        out.client_name = s.client_name_input.value;
        if (typeof calc_max_video_resolution === "function")
            out.video_resolution = calc_max_video_resolution(s.scale_video_input.valueAsNumber);
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
        } else if (key === "frame_rate") {
            setFrameRate(value);
        } else if (key === "frame_update_limit") {   // older Inkslate versions sent milliseconds
            setFrameRate(1000 / Math.max(1, Number(value)));
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
            inkslate: { eraser: !!cfg.eraser, pen_mode: state.penMode },
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
                styleMenu();
                drawScreen();
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
                    rawSend("GetCapturableList");
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

    // Everything in the Weylus menu can be changed from the PC, so start with it closed.
    // Only once per page load: if the user opens it with the handle, it stays open.
    function hideMenuOnce() {
        if (state.menuHandled)
            return;
        state.menuHandled = true;
        if (cfg.hide_menu && settings.visible)
            settings.toggle();
    }

    function start() {
        if (state.started || !settings)
            return;
        state.started = true;
        hookSocket(settings.webSocket);
        injectStyle();
        buildToolbar();
        window.addEventListener("resize", () => setTimeout(drawScreen, 50));
        for (const t of ["pointermove", "pointerdown"])
            window.addEventListener(t, watchPenForBar, { capture: true, passive: true });
        document.addEventListener("fullscreenchange", () => {
            setTimeout(drawScreen, 50);
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
        styleMenu();
        hideMenuOnce();   // uses the last config the PC sent (kept in localStorage)
        drawScreen();
        watchWeylusConnection();
        connectControl();
        window.inkslate = { version: VERSION, config: () => cfg, state, runAction };
    }
})();
