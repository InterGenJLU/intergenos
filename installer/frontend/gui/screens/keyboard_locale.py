# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Keyboard / Locale / Timezone — second page of the 9-screen flow.

2026-05-25 Wave-3 "shock-and-awe" rewrite — bar set against Apple Setup
Assistant, Ubuntu's installer, Calamares, and Pop!_OS. Replaces the
plain Adw.PreferencesGroup stack with:

  * **Hero region card** at the top — big flag emoji, language native
    name, city + country, ticking local clock, UTC offset. Plain
    English everything. Updates live every second.
  * **Promoted world map** as the visual centerpiece — taller (360px),
    full width, click-to-pick, glowing ECG-blue pin on the selected
    timezone with the city name floating beside it.
  * **Three concise selector rows** below the map (Language /
    Keyboard / Timezone), each backed by an Adw.ComboRow whose
    dropdown items are flag + plain English name + raw code, so the
    user sees "🇺🇸  English (United States)  ·  en_US.UTF-8" — not
    just `en_US.utf8`.
  * **Type-to-test entry** retained for keyboard verification.
  * **Language list pulls from `/usr/share/i18n/locales/`** (370
    supported locales) not `locale -a` (only generates what already
    exists, typically just 1-3). The installer's locale-gen phase
    will materialize whichever the user picks.

Data helpers live in `installer/frontend/gui/locale_data.py` — iso-codes
JSON parsing + curated native-script names + zoneinfo offsets.
"""

import os
import subprocess

from gi.repository import Adw, GLib, Gtk

from .. import locale_data as ld
from ._base import _ForgePage


# ──────────────────────────────────────────────────────────────────────
#  Continent silhouettes for the timezone map.
#  Hand-traced approximations — geographically rough but visually
#  recognizable. Coord convention: latitude north positive, longitude
#  east positive. Eurasia handled at antimeridian by the renderer.
# ──────────────────────────────────────────────────────────────────────
CONTINENTS = {
    "north_america": [
        (71, -156), (70, -141), (69, -134), (60, -135), (55, -130),
        (49, -123), (39, -123), (32, -117), (24, -110), (18, -98),
        (18, -88), (15, -83), (8, -78), (10, -64), (25, -80),
        (33, -78), (40, -75), (44, -67), (47, -52), (60, -65),
        (65, -77), (70, -82), (73, -100), (75, -125), (71, -156),
    ],
    "south_america": [
        (12, -73), (8, -60), (5, -52), (0, -50), (-8, -35),
        (-15, -39), (-23, -45), (-30, -52), (-35, -58), (-43, -65),
        (-55, -68), (-52, -75), (-40, -73), (-25, -71), (-15, -76),
        (-5, -81), (5, -78), (10, -75), (12, -73),
    ],
    "africa": [
        (35, -6), (37, 10), (32, 24), (31, 32), (22, 36),
        (12, 43), (1, 41), (-12, 41), (-26, 33), (-35, 20),
        (-26, 14), (-12, 13), (-5, 12), (3, 9), (5, 0),
        (6, -7), (13, -17), (20, -17), (28, -13), (35, -6),
    ],
    "eurasia": [
        (35, -10), (43, -10), (49, -5), (50, 2), (53, 5),
        (58, 8), (63, 6), (71, 28), (77, 70), (75, 100),
        (73, 140), (70, 170), (65, 178), (55, 162), (50, 142),
        (45, 135), (35, 130), (32, 121), (22, 113), (10, 105),
        (5, 102), (10, 95), (8, 78), (22, 70), (25, 60),
        (25, 51), (27, 48), (37, 33), (35, 28), (35, 24),
        (35, 14), (43, 13), (44, 5), (43, 0), (37, -2), (35, -10),
    ],
    "australia": [
        (-11, 142), (-19, 147), (-26, 153), (-34, 151), (-38, 147),
        (-37, 140), (-35, 137), (-34, 122), (-32, 116), (-20, 114),
        (-20, 121), (-15, 124), (-12, 130), (-11, 136), (-15, 140),
        (-11, 142),
    ],
}


# Anchor cities for the map "constellation" — selected to visually
# orient the user without shipping a city database. They render as
# small dim dots with labels.
MAJOR_CITIES = [
    (40.7128, -74.0060, "New York"),
    (34.0522, -118.2437, "Los Angeles"),
    (41.8781, -87.6298, "Chicago"),
    (19.4326, -99.1332, "Mexico City"),
    (-23.5505, -46.6333, "São Paulo"),
    (-34.6037, -58.3816, "Buenos Aires"),
    (51.5074, -0.1278, "London"),
    (48.8566, 2.3522, "Paris"),
    (52.5200, 13.4050, "Berlin"),
    (55.7558, 37.6173, "Moscow"),
    (41.0082, 28.9784, "Istanbul"),
    (30.0444, 31.2357, "Cairo"),
    (6.5244, 3.3792, "Lagos"),
    (-26.2041, 28.0473, "Johannesburg"),
    (25.2048, 55.2708, "Dubai"),
    (28.6139, 77.2090, "New Delhi"),
    (19.0760, 72.8777, "Mumbai"),
    (13.7563, 100.5018, "Bangkok"),
    (1.3521, 103.8198, "Singapore"),
    (22.3193, 114.1694, "Hong Kong"),
    (35.6762, 139.6503, "Tokyo"),
    (37.5665, 126.9780, "Seoul"),
    (-33.8688, 151.2093, "Sydney"),
    (-36.8485, 174.7633, "Auckland"),
    (21.3099, -157.8581, "Honolulu"),
    (61.2181, -149.9003, "Anchorage"),
    (45.4215, -75.6972, "Ottawa"),
    (-15.7975, -47.8919, "Brasília"),
    (-12.0464, -77.0428, "Lima"),
    (39.9042, 116.4074, "Beijing"),
]


# ──────────────────────────────────────────────────────────────────────
#  zone.tab parser — IANA tz canonical lat/long
# ──────────────────────────────────────────────────────────────────────
def _parse_iso6709_coord(coord: str) -> tuple[float, float] | None:
    """'+415100-0873900' or '+4151-08739' -> (41.85, -87.65)."""
    if not coord or coord[0] not in "+-":
        return None
    try:
        for second_sign_idx in range(1, len(coord)):
            if coord[second_sign_idx] in "+-":
                break
        else:
            return None
        lat_str = coord[:second_sign_idx]
        lon_str = coord[second_sign_idx:]

        def _to_decimal(s: str, deg_digits: int) -> float:
            sign = 1.0 if s[0] == "+" else -1.0
            body = s[1:]
            deg = int(body[:deg_digits])
            minute = int(body[deg_digits:deg_digits + 2])
            sec = int(body[deg_digits + 2:deg_digits + 4]) if len(body) >= deg_digits + 4 else 0
            return sign * (deg + minute / 60.0 + sec / 3600.0)

        lat = _to_decimal(lat_str, 2)
        lon = _to_decimal(lon_str, 3)
        return (lat, lon)
    except (ValueError, IndexError):
        return None


def _load_zone_tab() -> dict[str, tuple[float, float]]:
    """Parse /usr/share/zoneinfo/zone.tab into {timezone: (lat, long)}."""
    result: dict[str, tuple[float, float]] = {}
    path = "/usr/share/zoneinfo/zone.tab"
    if not os.path.exists(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                coord = parts[1]
                tz = parts[2]
                latlong = _parse_iso6709_coord(coord)
                if latlong:
                    result[tz] = latlong
    except OSError:
        pass
    return result


# ──────────────────────────────────────────────────────────────────────
#  System detection helpers
# ──────────────────────────────────────────────────────────────────────
def _detect_keymap() -> str:
    try:
        out = subprocess.run(
            ["localectl", "status"], capture_output=True, text=True, timeout=2
        ).stdout
        for line in out.splitlines():
            if "VC Keymap:" in line:
                return line.split(":", 1)[1].strip() or "us"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "us"


def _detect_locale() -> str:
    lang = os.environ.get("LANG", "").strip()
    if lang:
        return lang
    try:
        out = subprocess.run(
            ["localectl", "status"], capture_output=True, text=True, timeout=2
        ).stdout
        for line in out.splitlines():
            if "System Locale:" in line:
                val = line.split(":", 1)[1].strip()
                if "=" in val:
                    return val.split("=", 1)[1].strip() or "en_US.UTF-8"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "en_US.UTF-8"


def _detect_timezone() -> str:
    try:
        out = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "UTC"


def _list_timezones() -> list[str]:
    try:
        out = subprocess.run(
            ["timedatectl", "list-timezones"], capture_output=True, text=True, timeout=2
        ).stdout
        items = [line.strip() for line in out.splitlines() if line.strip()]
        return items or ["UTC"]
    except (OSError, subprocess.TimeoutExpired):
        return ["UTC"]


def _normalize_locale_for_lang(loc: str) -> str:
    """`locale -a` reports 'en_US.utf8'; LANG conventionally uses
    'en_US.UTF-8'. We standardize on the LANG form for state.locale."""
    if loc.endswith(".utf8"):
        return loc[:-len(".utf8")] + ".UTF-8"
    if loc.endswith(".UTF8"):
        return loc[:-len(".UTF8")] + ".UTF-8"
    return loc


# ──────────────────────────────────────────────────────────────────────
#  Display-string formatters — what the user actually reads
# ──────────────────────────────────────────────────────────────────────
def _format_locale_display(locale_id: str) -> str:
    """'🇺🇸  English (United States)  ·  en_US.UTF-8'."""
    h = ld.humanize_locale(locale_id)
    flag = h["flag"] or "🌐"
    primary = h["primary"]
    native = h["native"]
    if native and native.lower() != h["primary"].split(" (")[0].lower():
        return f"{flag}   {primary}   ·   {native}   ·   {locale_id}"
    return f"{flag}   {primary}   ·   {locale_id}"


def _format_keyboard_display(code: str, description: str) -> str:
    """'🇺🇸  English (US)  ·  us'."""
    h = ld.humanize_keyboard(code, description)
    flag = h["flag"] or "⌨"
    return f"{flag}   {description}   ·   {code}"


def _format_timezone_display(tz: str) -> str:
    """'🇺🇸  Chicago, United States  ·  America/Chicago'."""
    h = ld.humanize_timezone(tz)
    flag = h["flag"] or "🌐"
    if h["country_name"]:
        location = f"{h['city']}, {h['country_name']}"
    else:
        location = h["city"]
    return f"{flag}   {location}   ·   {tz}"


# ──────────────────────────────────────────────────────────────────────
#  TimezoneMap — custom Cairo widget (promoted to hero, 360px tall)
# ──────────────────────────────────────────────────────────────────────
class TimezoneMap(Gtk.DrawingArea):
    """Stylized equirectangular world map drawn in Cairo.

    Renders: void background, continent silhouettes (slate fill +
    ECG-blue outline), tropic/equator/meridian grid (very subtle),
    major-city constellation (dim dots with labels), and the selected
    timezone as a 3-tier glowing ECG-blue pin with city label.

    Sized as the page's visual centerpiece — 360px tall, full width.
    Click anywhere to pick the nearest IANA timezone."""

    def __init__(self, zone_tab: dict[str, tuple[float, float]]):
        super().__init__()
        self._zone_tab = zone_tab
        self.selected_tz: str | None = None
        self._click_callback = None
        self.set_content_height(360)
        self.set_hexpand(True)
        self.set_draw_func(self._on_draw)
        self.add_css_class("forge-timezone-map")

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("released", self._on_click_released)
        self.add_controller(click)

    def set_click_callback(self, cb) -> None:
        self._click_callback = cb

    def set_timezone(self, tz: str) -> None:
        self.selected_tz = tz
        self.queue_draw()

    @staticmethod
    def _unproject(x: float, y: float, w: float, h: float) -> tuple[float, float]:
        lon = x / w * 360.0 - 180.0
        lat = 90.0 - y / h * 180.0
        return (lat, lon)

    def _nearest_timezone(self, lat: float, lon: float) -> str | None:
        best = None
        best_dist = float("inf")
        for tz, coord in self._zone_tab.items():
            tz_lat, tz_lon = coord
            dlat = tz_lat - lat
            dlon = tz_lon - lon
            if dlon > 180:
                dlon -= 360
            elif dlon < -180:
                dlon += 360
            d = dlat * dlat + dlon * dlon
            if d < best_dist:
                best_dist = d
                best = tz
        return best

    def _on_click_released(self, gesture, _n_press, x, y):
        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return
        lat, lon = self._unproject(x, y, w, h)
        tz = self._nearest_timezone(lat, lon)
        if tz is None:
            return
        self.set_timezone(tz)
        if self._click_callback is not None:
            self._click_callback(tz)

    @staticmethod
    def _project(lat: float, lon: float, w: float, h: float) -> tuple[float, float]:
        x = (lon + 180.0) / 360.0 * w
        y = (90.0 - lat) / 180.0 * h
        return (x, y)

    def _draw_continent(self, cr, polygon, width, height):
        if not polygon:
            return
        segments: list[list[tuple[float, float]]] = [[]]
        prev_lon = None
        for lat, lon in polygon:
            if prev_lon is not None and abs(lon - prev_lon) > 180:
                segments.append([])
            segments[-1].append((lat, lon))
            prev_lon = lon

        for seg in segments:
            if len(seg) < 3:
                continue
            cr.new_path()
            first = True
            for lat, lon in seg:
                x, y = self._project(lat, lon, width, height)
                if first:
                    cr.move_to(x, y)
                    first = False
                else:
                    cr.line_to(x, y)
            cr.close_path()
            cr.set_source_rgba(0.05, 0.13, 0.24, 1.0)
            cr.fill_preserve()
            cr.set_source_rgba(0.0, 0.6, 1.0, 0.50)
            cr.set_line_width(1.0)
            cr.stroke()

    def _on_draw(self, _area, cr, width, height):
        # Guard against sub-pixel allocations (GTK calls the draw func with
        # width/height of 0 during initial layout, before this DrawingArea
        # gets a real size). Without this, the border-frame rectangle below
        # becomes (0.5, 0.5, -1, -1) — a negative-size rect that pixman
        # rejects with a flood of "*** BUG *** pixman_region32_init_rect:
        # Invalid rectangle passed" on stderr. Mirrors the same w/h<=0 guard
        # already in _on_click_released.
        if width < 1 or height < 1:
            return
        # Void background
        cr.set_source_rgb(0x05 / 255, 0x08 / 255, 0x10 / 255)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Border frame
        cr.set_source_rgba(0.0, 0.6, 1.0, 0.22)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()

        # Continents first (beneath grid + cities)
        for polygon in CONTINENTS.values():
            self._draw_continent(cr, polygon, width, height)

        # Grid: equator/tropics + meridians every 30° (very subtle)
        cr.set_source_rgba(0.0, 0.6, 1.0, 0.07)
        cr.set_line_width(1.0)
        for lat in (-66.5, -23.5, 0.0, 23.5, 66.5):
            _, y = self._project(lat, 0, width, height)
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()
        for lon in range(-180, 181, 30):
            x, _ = self._project(0, lon, width, height)
            cr.move_to(x, 0)
            cr.line_to(x, height)
            cr.stroke()

        # City constellation
        cr.select_font_face("Inter", 0, 0)
        cr.set_font_size(9)
        for lat, lon, name in MAJOR_CITIES:
            x, y = self._project(lat, lon, width, height)
            cr.set_source_rgba(0.55, 0.70, 0.90, 0.95)
            cr.arc(x, y, 2.0, 0, 6.283185)
            cr.fill()
            cr.set_source_rgba(0.70, 0.82, 0.95, 0.75)
            cr.move_to(x + 5, y + 3)
            cr.show_text(name)

        # Selected timezone pin — 3-tier glow + bright center + label
        if self.selected_tz:
            coord = self._zone_tab.get(self.selected_tz)
            if coord:
                lat, lon = coord
                x, y = self._project(lat, lon, width, height)

                for r, a in ((20, 0.08), (14, 0.16), (9, 0.32), (6, 0.55)):
                    cr.set_source_rgba(0.0, 0.6, 1.0, a)
                    cr.arc(x, y, r, 0, 6.283185)
                    cr.fill()

                cr.set_source_rgba(0.85, 0.95, 1.0, 1.0)
                cr.arc(x, y, 3.8, 0, 6.283185)
                cr.fill()

                # Selected city label — pretty, bigger
                h = ld.humanize_timezone(self.selected_tz)
                label = h["city"]
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
                cr.select_font_face("Inter", 0, 1)
                cr.set_font_size(12)
                cr.move_to(x + 11, y - 7)
                cr.show_text(label)


# ──────────────────────────────────────────────────────────────────────
#  KeyboardLocalePage
# ──────────────────────────────────────────────────────────────────────
class KeyboardLocalePage(_ForgePage):
    tag = "keyboard_locale"
    title = "Keyboard, Locale, Timezone"

    def _build_body(self) -> Gtk.Widget:
        self._zone_tab = _load_zone_tab()

        self._locales = ld.list_locales()
        self._keymaps = ld.list_keyboard_layouts()
        # Index keymap codes -> description for fast lookup
        self._keymap_desc = {code: desc for code, desc in self._keymaps}
        self._keymap_codes = [c for c, _ in self._keymaps]
        self._timezones = _list_timezones()

        # Pre-format the display strings ONCE so the dropdowns are fast
        self._locale_display = [_format_locale_display(loc) for loc in self._locales]
        self._keymap_display = [_format_keyboard_display(c, d) for c, d in self._keymaps]
        self._timezone_display = [_format_timezone_display(tz) for tz in self._timezones]

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_hexpand(True)

        # ─── HERO REGION CARD ────────────────────────────────────────
        self._hero_card = self._build_hero_card()
        page.append(self._hero_card)

        # ─── INSTRUCTION LINE ────────────────────────────────────────
        # Lead the user — first-time Linux users have no reason to know
        # the prefs below are interactive. Decided wording.
        instruction = Gtk.Label(label=(
            "The configuration items shown above will be used during "
            "installation. To make any changes, scroll down and select "
            "the appropriate language, keyboard, and timezone before "
            "clicking 'Next' to advance to the next section."
        ))
        instruction.add_css_class("forge-instruction")
        instruction.set_halign(Gtk.Align.CENTER)
        instruction.set_justify(Gtk.Justification.CENTER)
        instruction.set_wrap(True)
        instruction.set_max_width_chars(72)
        page.append(instruction)

        # ─── LANGUAGE ────────────────────────────────────────────────
        lang_group = Adw.PreferencesGroup()
        lang_group.set_title("Language")
        lang_group.set_description(
            "System language. Sets the language of menus, dialogs, and "
            "regional formatting for numbers, dates, and currency."
        )
        self._locale_row = Adw.ComboRow()
        self._locale_row.set_title("Locale")
        # Searchable dropdown. AdwComboRow search needs BOTH:
        #   1. an `expression` so the search has text to match against
        #      (StringList items are GtkStringObject -> .string); and
        #   2. SUBSTRING match mode. AdwComboRow defaults to PREFIX, but our
        #      display strings begin with a flag emoji ("🇺🇸  English …"), so a
        #      PREFIX search for "en"/"chicago" never matches — the string
        #      starts with the flag, not the typed text. SUBSTRING matches
        #      anywhere. (GBC001.5 boot test — verified live in the target
        #      GNOME 49.4 / libadwaita 1.8.4 session: PREFIX -> 0 results,
        #      SUBSTRING -> the expected matches.)
        self._locale_row.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._locale_row.set_model(Gtk.StringList.new(self._locale_display))
        self._locale_row.set_enable_search(True)
        self._locale_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._locale_row.connect("notify::selected", self._on_locale_changed)
        lang_group.add(self._locale_row)
        page.append(lang_group)

        # ─── KEYBOARD ────────────────────────────────────────────────
        kb_group = Adw.PreferencesGroup()
        kb_group.set_title("Keyboard")
        kb_group.set_description(
            "Physical keyboard layout. Test it in the field below before "
            "moving on — special characters, symbols, the works."
        )
        self._keymap_row = Adw.ComboRow()
        self._keymap_row.set_title("Layout")
        # expression + SUBSTRING search (see locale row above for why).
        self._keymap_row.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._keymap_row.set_model(Gtk.StringList.new(self._keymap_display))
        self._keymap_row.set_enable_search(True)
        self._keymap_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._keymap_row.connect("notify::selected", self._on_keymap_changed)
        kb_group.add(self._keymap_row)

        test_row = Adw.ActionRow()
        test_row.set_title("Type to test")
        test_row.set_subtitle(
            "Characters typed here use the layout you picked above."
        )
        self._test_entry = Gtk.Entry()
        self._test_entry.set_placeholder_text(
            "Try shift, AltGr, dead keys — anything that matters to you."
        )
        self._test_entry.set_valign(Gtk.Align.CENTER)
        self._test_entry.set_hexpand(True)
        test_row.add_suffix(self._test_entry)
        kb_group.add(test_row)
        page.append(kb_group)

        # ─── PROMOTED WORLD MAP (above timezone — reinforces relationship) ─
        self._map = TimezoneMap(self._zone_tab)
        self._map.set_click_callback(self._on_map_clicked)
        page.append(self._map)

        # ─── TIMEZONE ────────────────────────────────────────────────
        tz_group = Adw.PreferencesGroup()
        tz_group.set_title("Timezone")
        tz_group.set_description(
            "Sets the system clock and the timestamps on log entries. "
            "You can also click the map above to pick a region."
        )
        self._tz_row = Adw.ComboRow()
        self._tz_row.set_title("Region")
        # expression + SUBSTRING search (see locale row above for why).
        self._tz_row.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._tz_row.set_model(Gtk.StringList.new(self._timezone_display))
        self._tz_row.set_enable_search(True)
        self._tz_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._tz_row.connect("notify::selected", self._on_tz_changed)
        tz_group.add(self._tz_row)
        page.append(tz_group)

        return page

    # ─── HERO CARD BUILDERS ──────────────────────────────────────────
    def _build_hero_card(self) -> Gtk.Widget:
        """The summary card at the top of the page. Two-column layout:
        big flag on the left (acts like an avatar), rich plain-English
        summary on the right with a ticking clock at the bottom."""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        card.add_css_class("forge-region-hero")
        card.set_hexpand(True)

        # Big flag — left side, acts like a profile photo
        self._hero_flag = Gtk.Label()
        self._hero_flag.add_css_class("forge-region-flag")
        self._hero_flag.set_label("🌐")
        self._hero_flag.set_valign(Gtk.Align.CENTER)
        self._hero_flag.set_halign(Gtk.Align.CENTER)
        self._hero_flag.set_size_request(108, 108)
        card.append(self._hero_flag)

        # Info column — right side
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._hero_native = Gtk.Label()
        self._hero_native.add_css_class("forge-region-native")
        self._hero_native.set_halign(Gtk.Align.START)
        self._hero_native.set_label("English")
        info.append(self._hero_native)

        self._hero_locale = Gtk.Label()
        self._hero_locale.add_css_class("forge-region-locale")
        self._hero_locale.set_halign(Gtk.Align.START)
        self._hero_locale.set_label("English (United States)")
        info.append(self._hero_locale)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 6)
        info.append(spacer)

        # Location row: 🗺 City, Country
        self._hero_location = Gtk.Label()
        self._hero_location.add_css_class("forge-region-location")
        self._hero_location.set_halign(Gtk.Align.START)
        self._hero_location.set_use_markup(True)
        info.append(self._hero_location)

        # Clock row: ⏰ HH:MM:SS · UTC offset
        self._hero_clock = Gtk.Label()
        self._hero_clock.add_css_class("forge-region-clock")
        self._hero_clock.set_halign(Gtk.Align.START)
        self._hero_clock.set_use_markup(True)
        info.append(self._hero_clock)

        card.append(info)

        return card

    def _update_hero_card(self, locale_id: str, tz_id: str) -> None:
        """Repaint the hero card with current selections. Called on row
        changes and once per second by the clock tick."""
        locale_h = ld.humanize_locale(locale_id)
        tz_h = ld.humanize_timezone(tz_id)

        # Flag — prefer the locale's country flag (matches the language),
        # fall back to the timezone's country if the locale is regionless.
        flag = locale_h["flag"] or tz_h["flag"] or "🌐"
        self._hero_flag.set_label(flag)

        # Native language name (the language's name for itself)
        self._hero_native.set_label(locale_h["native"] or locale_h["primary"])

        # English form + raw locale id
        self._hero_locale.set_label(
            f"{locale_h['primary']}   ·   {locale_id}"
        )

        # Location: city + country
        if tz_h["country_name"]:
            loc_text = f"{tz_h['city']}, {tz_h['country_name']}"
        else:
            loc_text = tz_h["city"]
        # Pango markup with a globe-pin glyph as a soft prefix.
        self._hero_location.set_markup(
            f"<span size='medium'>🗺  {GLib.markup_escape_text(loc_text)}</span>"
        )

        # Live clock + offset
        now = ld.format_local_clock(tz_id)
        offset = ld.format_offset(tz_id)
        self._hero_clock.set_markup(
            f"<span size='x-large' weight='bold'>{now}</span>"
            f"   <span size='medium' alpha='65%'>·   {offset}</span>"
        )

    def _tick_clock(self) -> bool:
        """1Hz timer callback — repaint only the clock+offset bits of
        the hero card. Returns True to keep the timer scheduled."""
        if self._current_locale and self._current_tz:
            self._update_hero_card(self._current_locale, self._current_tz)
        return True  # keep ticking

    # ─── ROW CHANGE HANDLERS ─────────────────────────────────────────
    def _selected_index(self, row: Adw.ComboRow) -> int:
        try:
            return row.get_selected()
        except Exception:
            return 0

    def _on_locale_changed(self, row: Adw.ComboRow, _pspec) -> None:
        idx = self._selected_index(row)
        if 0 <= idx < len(self._locales):
            self._current_locale = self._locales[idx]
            self._update_hero_card(self._current_locale, self._current_tz)

    def _on_keymap_changed(self, row: Adw.ComboRow, _pspec) -> None:
        idx = self._selected_index(row)
        if 0 <= idx < len(self._keymap_codes):
            self._current_keymap = self._keymap_codes[idx]
            # Keyboard layout doesn't show on hero card (locale flag
            # represents region); nothing else needs repainting.

    def _on_tz_changed(self, row: Adw.ComboRow, _pspec) -> None:
        idx = self._selected_index(row)
        if 0 <= idx < len(self._timezones):
            self._current_tz = self._timezones[idx]
            self._map.set_timezone(self._current_tz)
            self._update_hero_card(self._current_locale, self._current_tz)

    def _on_map_clicked(self, tz: str) -> None:
        """Map click -> sync timezone ComboRow selection."""
        try:
            idx = self._timezones.index(tz)
        except ValueError:
            return
        self._tz_row.set_selected(idx)

    # ─── INDEX SELECTION HELPERS ─────────────────────────────────────
    def _select_locale(self, value: str) -> None:
        normalized = _normalize_locale_for_lang(value)
        try:
            idx = self._locales.index(normalized)
        except ValueError:
            # Try without UTF-8 suffix
            base = normalized.split(".")[0]
            for i, loc in enumerate(self._locales):
                if loc.startswith(base + "."):
                    idx = i
                    break
            else:
                idx = 0
                for i, loc in enumerate(self._locales):
                    if loc.startswith("en_US"):
                        idx = i
                        break
        self._locale_row.set_selected(idx)
        self._current_locale = self._locales[idx]

    def _select_keymap(self, value: str) -> None:
        try:
            idx = self._keymap_codes.index(value)
        except ValueError:
            idx = 0
            for i, code in enumerate(self._keymap_codes):
                if code == "us":
                    idx = i
                    break
        self._keymap_row.set_selected(idx)
        self._current_keymap = self._keymap_codes[idx]

    def _select_timezone(self, value: str) -> None:
        try:
            idx = self._timezones.index(value)
        except ValueError:
            idx = 0
            for i, tz in enumerate(self._timezones):
                if tz == "UTC":
                    idx = i
                    break
        self._tz_row.set_selected(idx)
        self._current_tz = self._timezones[idx]
        self._map.set_timezone(self._current_tz)

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    def on_load(self, state):
        super().on_load(state)
        if not getattr(state, "_keyboard_locale_visited", False):
            state.keymap = _detect_keymap()
            state.locale = _normalize_locale_for_lang(_detect_locale())
            state.timezone = _detect_timezone()
            state._keyboard_locale_visited = True

        self._current_locale = state.locale
        self._current_keymap = state.keymap
        self._current_tz = state.timezone

        self._select_locale(state.locale)
        self._select_keymap(state.keymap)
        self._select_timezone(state.timezone)

        self._update_hero_card(self._current_locale, self._current_tz)

        # Start the 1Hz clock tick. We only schedule once; subsequent
        # on_load calls (re-entry to this page) reuse the existing timer.
        if not getattr(self, "_clock_timer_started", False):
            GLib.timeout_add_seconds(1, self._tick_clock)
            self._clock_timer_started = True

    def on_next(self, state):
        loc_idx = self._selected_index(self._locale_row)
        kb_idx = self._selected_index(self._keymap_row)
        tz_idx = self._selected_index(self._tz_row)

        if 0 <= loc_idx < len(self._locales):
            state.locale = self._locales[loc_idx]
        if 0 <= kb_idx < len(self._keymap_codes):
            state.keymap = self._keymap_codes[kb_idx]
        if 0 <= tz_idx < len(self._timezones):
            state.timezone = self._timezones[tz_idx]
        return True
