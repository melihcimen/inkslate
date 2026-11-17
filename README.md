# Inkslate

**Use an Android tablet as a pen tablet on Linux, over a USB cable.**

Inkslate is built on [Weylus](https://github.com/H-M-H/Weylus) and keeps Weylus doing what it does
well: turning the tablet's browser into a pen, touch and video client. Inkslate adds the parts that
make it pleasant to use every day:

- **Plug in and draw.** When the cable goes in, Inkslate forwards the ports over adb, wakes the
  tablet, opens the page in Chrome and switches it to fullscreen. No URLs to type, no taps.
- **Control everything from the PC.** All settings of the Weylus menu on the tablet (pen, touch,
  energy saving, video, frame rate, resolution, captured screen…) are mirrored in a GTK window and
  apply live.
- **Pressure curve with a live preview.** Make light strokes count more (or less) without root, udev
  hwdb files or restarts. Draw on the tablet and watch the point move on the curve.
- **Eraser.** Browsers never see the S Pen side button, so Inkslate puts an eraser button on the
  tablet: tap to toggle, hold to erase only while holding. The pen then shows up as a real eraser
  tip (`BTN_TOOL_RUBBER`), which apps like Rnote, Xournal++ and Krita understand.
- **Toolbar on the tablet** with undo/redo/save buttons and per-app profiles (Rnote, Xournal++,
  Krita, GIMP).
- **Finger gestures**: two-finger tap undoes, three-finger tap redoes, while finger drawing stays off.
- **Palm rejection**: with finger input on, touches right after the pen was near are ignored.
- **Energy saving that stays black** (fixes the Weylus page going grey after fullscreen/rotation),
  keeps the tablet screen awake, optionally dims the tablet's backlight.
- Runs in the **background** (autostart), Weylus is restarted if it crashes.

![Inkslate's pen page](docs/pen.png)

## How it works

```
 tablet (Chrome)                         PC
┌──────────────────────┐   adb reverse   ┌──────────────────────────────────────────┐
│ Weylus web client     │ ───────────────▶│ weylus --no-gui   (uinput pen/touch/keys)│
│  + inkslate.js        │  1701 / 9001    │                                          │
│    curve, eraser,     │                 │ Inkslate (GTK 4 / libadwaita)            │
│    toolbar, gestures  │ ───────────────▶│  · control channel (WebSocket, 9002)     │
│                       │      9002       │  · adb: ports, open page, fullscreen     │
└──────────────────────┘                 └──────────────────────────────────────────┘
```

Inkslate does not fork Weylus. It runs the stock binary headless and serves its own build of the
tablet page through `weylus --custom-lib-js`: Weylus' `lib.js` with `inkslate/web/inkslate.js`
appended. Pen input still goes through Weylus' uinput devices, so every app sees a normal tablet.

## Install

Requirements: Python 3, GTK 4, libadwaita ≥ 1.5, libsoup 3 (all on current Ubuntu/Fedora/Arch
GNOME desktops), `adb` for USB mode and Weylus 0.11.4.

```sh
git clone https://github.com/melihcimen/inkslate
cd inkslate
./install.sh --with-weylus --autostart
```

`--with-weylus` downloads the official Weylus 0.11.4 release (checksum verified) if Weylus isn't
installed. The script installs to `~/.local`, and only asks for sudo once to make `/dev/uinput`
writable (needed for pen pressure). On Debian/Ubuntu the dependencies are:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-soup-3.0 adb gettext
```

### Tablet setup (once)

1. Settings → About tablet → Software information → tap *Build number* 7 times.
2. Developer options → enable **USB debugging**.
3. Plug in the cable and allow the PC when the tablet asks.

Inkslate opens the page in **Chrome** (needed for automatic fullscreen). Other browsers work, but
then use the tablet toolbar or the Weylus menu for fullscreen.

## Use

Start *Inkslate* from the app menu (or `inkslate`), plug in the tablet, draw. Closing the window
keeps it running; *Quit* in the menu stops it.

Recommended: turn **Energy saving** on (black tablet screen, pen only) and **Finger input** off
(your hand can't draw, gestures still work).

### Command line

The running instance can be scripted, which is handy for shortcuts:

```sh
inkslate --set tablet.eraser=true            # any key of ~/.config/inkslate/config.json
inkslate --set tablet.curve.in_max=0.6
inkslate --set profile=xournalpp
inkslate --native energysaving=true          # a setting of the Weylus menu on the tablet
inkslate --action undo                       # undo, redo, eraser, reload, open-page, fullscreen
inkslate --background                        # start without the window
inkslate --quit
```

### Wi-Fi

Switch *Connection → Mode* to Wi-Fi. Weylus then listens on the network and requires the access
code; the address to open on the tablet is shown on the status page. Automatic page opening and
fullscreen need USB; enable *Fullscreen on first tap* instead.

## Notes and limitations

- **S Pen button**: Chrome on Android does not report the S Pen side button to web pages (and
  Samsung's system grabs button + drag), so it can't be mapped. Use the eraser button.
- **Key combos** are sent as physical key positions (US layout positions), as Weylus does.
- **Video on Wayland** needs *Wayland screen capture* on (and asks for screen-sharing permission).
  With VA-API encoding keep the tablet's *maximum video resolution* at the right end; odd sizes
  produce a garbled picture.
- **Dimming**: if you unplug the cable while energy saving dimmed the tablet, the brightness is
  restored the next time you plug it in.
- Tested with a Samsung Galaxy Tab S7 FE on Ubuntu (GNOME, Wayland). Reports from other tablets are
  very welcome.

## Security

In USB mode Weylus and the control channel only listen on `127.0.0.1` and are reached through
`adb reverse`. The control channel accepts only connections from the Weylus page's origin. In Wi-Fi
mode Weylus requires the access code and the control channel checks it too; the traffic is not
encrypted, so use Wi-Fi mode on networks you trust.

## Development

```sh
python3 -m inkslate                     # run from the checkout
msgfmt -o inkslate/locale/tr/LC_MESSAGES/inkslate.mo po/tr.po   # translations for local runs
python3 tests/e2e_test.py               # end-to-end test (headless Chrome + evtest --grab)
python3 tests/screenshots.py OUTDIR     # render every page of the window
```

The end-to-end test starts its own instance on spare ports, drives the tablet page in headless
Chrome with synthetic pen/touch events and checks what Weylus emits on its uinput devices. The
devices are grabbed exclusively, so nothing reaches your desktop; it needs passwordless
`sudo evtest`.

Translations live in `po/` (`xgettext` → `msgmerge`). Turkish is included.

## License

AGPL-3.0-or-later, the same as Weylus. Inkslate's tablet page is a derivative of Weylus' web client.
Weylus is © H-M-H and contributors.
