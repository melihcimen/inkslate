#!/usr/bin/env bash
# Removes the user install made by install.sh. Settings stay unless --purge is given.
# Weylus itself and the uinput udev rule are left alone (other tools may use them).
set -euo pipefail

APP_ID=io.github.melihcimen.Inkslate
PREFIX="${HOME}/.local"

"${PREFIX}/bin/inkslate" --quit 2>/dev/null || true
rm -rf "${PREFIX}/share/inkslate"
rm -f "${PREFIX}/bin/inkslate" \
      "${PREFIX}/share/applications/${APP_ID}.desktop" \
      "${PREFIX}/share/icons/hicolor/scalable/apps/${APP_ID}.svg" \
      "${HOME}/.config/autostart/${APP_ID}.desktop"
update-desktop-database "${PREFIX}/share/applications" 2>/dev/null || true

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "${HOME}/.config/inkslate" "${HOME}/.local/state/inkslate" "${HOME}/.cache/inkslate"
    echo "Inkslate and its settings were removed."
else
    echo "Inkslate was removed. Settings are kept in ~/.config/inkslate (use --purge to delete)."
fi
