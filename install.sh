#!/usr/bin/env bash
# Installs Inkslate for the current user (~/.local). Run from the repository root.
#
#   ./install.sh                 install / update
#   ./install.sh --with-weylus   also download Weylus 0.11.4 if it isn't installed
#   ./install.sh --autostart     also start Inkslate in the background at login
#
# The only steps that need sudo are the one-time uinput permissions (asked separately).
set -euo pipefail

APP_ID=io.github.melihcimen.Inkslate
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${HOME}/.local"
SHARE="${PREFIX}/share/inkslate"
WEYLUS_URL=https://github.com/H-M-H/Weylus/releases/download/v0.11.4/linux.zip
WEYLUS_SHA256=8f0a640d37a8aa451eef5831c513c6ebae6cb411170208ef028678ba4b8a99f2

with_weylus=0
autostart=0
for arg in "$@"; do
    case "$arg" in
        --with-weylus) with_weylus=1 ;;
        --autostart) autostart=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- dependencies
say "Checking dependencies"
missing=()
python3 - <<'EOF' || missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-soup-3.0)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Soup", "3.0")
from gi.repository import Adw, Gtk, Soup
assert (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) >= (1, 5), "libadwaita >= 1.5 needed"
EOF
command -v adb >/dev/null || missing+=(adb)
command -v msgfmt >/dev/null || missing+=(gettext)
if ((${#missing[@]})); then
    warn "Missing packages: ${missing[*]}"
    echo "    Debian/Ubuntu: sudo apt install ${missing[*]}"
    echo "    (adb is only needed for USB mode)"
    if ! python3 -c 'import gi; gi.require_version("Adw","1")' 2>/dev/null; then
        exit 1
    fi
fi

# ---------------------------------------------------------------- Weylus
weylus=""
for c in "$(command -v weylus || true)" "${PREFIX}/bin/weylus"; do
    if [[ -n "$c" && -x "$c" ]]; then weylus="$c"; break; fi
done
if [[ -z "$weylus" && $with_weylus == 1 ]]; then
    say "Downloading Weylus 0.11.4"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fL --progress-bar -o "$tmp/linux.zip" "$WEYLUS_URL"
    echo "$WEYLUS_SHA256  $tmp/linux.zip" | sha256sum -c --quiet
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("weylus", sys.argv[2])' \
        "$tmp/linux.zip" "$tmp"
    install -Dm755 "$tmp/weylus" "${PREFIX}/bin/weylus"
    weylus="${PREFIX}/bin/weylus"
fi
if [[ -z "$weylus" ]]; then
    warn "Weylus is not installed. Re-run with --with-weylus, or get it from"
    echo "    https://github.com/H-M-H/Weylus/releases"
else
    say "Using Weylus: $weylus"
fi

# ---------------------------------------------------------------- files
say "Installing to ${PREFIX}"
rm -rf "${SHARE}/inkslate"
mkdir -p "${SHARE}"
cp -r "${SRC}/inkslate" "${SHARE}/inkslate"
find "${SHARE}/inkslate" -name '__pycache__' -prune -exec rm -rf {} +
for po in "${SRC}"/po/*.po; do
    lang="$(basename "$po" .po)"
    mkdir -p "${SHARE}/inkslate/locale/${lang}/LC_MESSAGES"
    msgfmt -o "${SHARE}/inkslate/locale/${lang}/LC_MESSAGES/inkslate.mo" "$po"
done
install -Dm755 "${SRC}/bin/inkslate" "${PREFIX}/bin/inkslate"
# the launcher looks for the package in ../share/inkslate relative to itself
install -Dm644 "${SRC}/data/${APP_ID}.desktop" "${PREFIX}/share/applications/${APP_ID}.desktop"
sed -i "s|^Exec=inkslate|Exec=${PREFIX}/bin/inkslate|" "${PREFIX}/share/applications/${APP_ID}.desktop"
install -Dm644 "${SRC}/data/${APP_ID}.svg" "${PREFIX}/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
update-desktop-database "${PREFIX}/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "${PREFIX}/share/icons/hicolor" 2>/dev/null || true

if [[ $autostart == 1 ]]; then
    mkdir -p "${HOME}/.config/autostart"
    cat > "${HOME}/.config/autostart/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Inkslate
Exec=${PREFIX}/bin/inkslate --background
Icon=${APP_ID}
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    say "Autostart enabled"
fi

# ---------------------------------------------------------------- uinput permissions
if [[ -w /dev/uinput ]]; then
    say "/dev/uinput is writable, pen pressure will work"
else
    warn "/dev/uinput is not writable by you: pen pressure and multi-touch need it."
    read -r -p "    Set up a 'uinput' group and udev rule now (uses sudo)? [y/N] " reply
    if [[ "${reply,,}" == y* ]]; then
        sudo groupadd -f -r uinput
        sudo usermod -aG uinput "$USER"
        echo 'KERNEL=="uinput", MODE="0660", GROUP="uinput", OPTIONS+="static_node=uinput"' \
            | sudo tee /etc/udev/rules.d/60-weylus.rules >/dev/null
        sudo udevadm control --reload
        sudo udevadm trigger
        warn "Log out and back in once so the new group applies."
    fi
fi

cat <<EOF

Done. Start Inkslate from the app menu or run: inkslate
On the tablet: enable Developer options > USB debugging, plug in the cable and allow the PC.
EOF
case ":$PATH:" in *":${PREFIX}/bin:"*) ;; *) warn "${PREFIX}/bin is not in your PATH" ;; esac
