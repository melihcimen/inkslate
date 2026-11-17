"""Toolbar icons, shared by the tablet page and the preview in the PC app (24×24, stroke-based)."""

ICONS = {
    "undo": '<path d="M10 5 5 10l5 5"/><path d="M5 10h9a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m14 5 5 5-5 5"/><path d="M19 10h-9a5 5 0 0 0 0 10h3"/>',
    "eraser": '<path d="M8 20h12"/><path d="m5.6 14.4 8.5-8.5a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8'
              'l-8.6 8.6a1.7 1.7 0 0 1-1.2.5H8.4a1.7 1.7 0 0 1-1.2-.5l-1.6-1.6a2.5 2.5 0 0 1 0-3.5Z"/>'
              '<path d="m9.5 10.5 5 5"/>',
    "save": '<path d="M6 4h10l4 4v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/>'
            '<path d="M8 4v5h7V4"/><path d="M8 20v-6h8v6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    "paste": '<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/>',
    "zoom_in": '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/><path d="M11 8.5v5M8.5 11h5"/>',
    "zoom_out": '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/><path d="M8.5 11h5"/>',
    "brush_bigger": '<circle cx="9" cy="15" r="5" fill="currentColor" stroke="none"/><path d="M18 3v6M15 6h6"/>',
    "brush_smaller": '<circle cx="9" cy="15" r="2.5" fill="currentColor" stroke="none"/><path d="M15 6h6"/>',
    "pan": '<path d="M12 3v18M3 12h18"/><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    "right_click": '<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 3v7"/>'
                   '<path d="M12 3.2a6 6 0 0 1 5.8 5.8V10H12Z" fill="currentColor"/>',
    "middle_drag": '<rect x="6" y="3" width="12" height="18" rx="6"/>'
                   '<rect x="10.75" y="6" width="2.5" height="5" rx="1.25" fill="currentColor"/>',
    "touch": '<circle cx="12" cy="11" r="2.5"/><path d="M7.8 15.2a6 6 0 1 1 8.4 0"/><path d="M12 13.5V21"/>',
    "fullscreen": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "custom": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10"/>',
}
ICONS["custom_hold"] = ICONS["custom"]

SVG_OPEN = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">')


def svg(name, color=None, size=None):
    body = ICONS.get(name, "")
    head = SVG_OPEN
    if size:
        head = head.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
    out = head + body + "</svg>"
    return out.replace("currentColor", color) if color else out
