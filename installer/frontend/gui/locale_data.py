# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Locale / keyboard / timezone humanization helpers.

Turns the raw machine-truth strings the GUI gets from `locale -a`,
`localectl list-keymaps`, and `timedatectl list-timezones` into the
human-readable form a normal person should be shown — flag emojis,
native-script names, country-aware timezone labels, live UTC offsets.

Data sources:
  * `/usr/share/iso-codes/json/iso_3166-1.json` — country alpha_2 +
    English name + flag emoji codepoint.
  * `/usr/share/iso-codes/json/iso_639-3.json` — language alpha_2 /
    alpha_3 + English name.
  * Curated `NATIVE_LANGUAGE_NAMES` table — each language's name for
    itself (top ~70 languages, covers ~99 % of the world's users).
  * `/usr/share/X11/xkb/rules/evdev.lst` — keyboard layout codes +
    descriptions.
  * `/usr/share/zoneinfo/zone1970.tab` — timezone country mapping +
    comment field.
  * `/usr/share/i18n/locales/` — full supported-locale list (the
    installer enables one during install; the live system's `locale -a`
    only shows already-generated ones, typically 1-3).
  * Python stdlib `zoneinfo` — live UTC offset + local time.

All file loads are memoized via `functools.lru_cache`.
"""

import json
import os
from datetime import datetime
from functools import lru_cache

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


# Each language's own name for itself. iso-codes only has English names;
# this curated set covers the top ~70 languages by speaker population
# plus regional ones. Unknown codes fall back to the English name.
NATIVE_LANGUAGE_NAMES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "pa": "ਪੰਜਾਬੀ",
    "te": "తెలుగు",
    "ta": "தமிழ்",
    "mr": "मराठी",
    "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
    "or": "ଓଡ଼ିଆ",
    "as": "অসমীয়া",
    "tr": "Türkçe",
    "pl": "Polski",
    "nl": "Nederlands",
    "el": "Ελληνικά",
    "uk": "Українська",
    "vi": "Tiếng Việt",
    "th": "ภาษาไทย",
    "he": "עברית",
    "fa": "فارسی",
    "ur": "اردو",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "sw": "Kiswahili",
    "ro": "Română",
    "hu": "Magyar",
    "cs": "Čeština",
    "sk": "Slovenčina",
    "sv": "Svenska",
    "nb": "Norsk Bokmål",
    "nn": "Norsk Nynorsk",
    "no": "Norsk",
    "da": "Dansk",
    "fi": "Suomi",
    "is": "Íslenska",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "et": "Eesti",
    "sl": "Slovenščina",
    "hr": "Hrvatski",
    "sr": "Srpski",
    "bs": "Bosanski",
    "mk": "Македонски",
    "bg": "Български",
    "mn": "Монгол",
    "ka": "ქართული",
    "hy": "Հայերեն",
    "az": "Azərbaycan",
    "kk": "Қазақ",
    "ky": "Кыргыз",
    "uz": "Oʻzbek",
    "tg": "Тоҷикӣ",
    "tk": "Türkmen",
    "sq": "Shqip",
    "be": "Беларуская",
    "ca": "Català",
    "eu": "Euskara",
    "gl": "Galego",
    "ga": "Gaeilge",
    "cy": "Cymraeg",
    "br": "Brezhoneg",
    "oc": "Occitan",
    "af": "Afrikaans",
    "am": "አማርኛ",
    "ti": "ትግርኛ",
    "om": "Afaan Oromoo",
    "ha": "Hausa",
    "yo": "Yorùbá",
    "ig": "Igbo",
    "zu": "isiZulu",
    "xh": "isiXhosa",
    "rw": "Kinyarwanda",
    "my": "မြန်မာ",
    "km": "ខ្មែរ",
    "lo": "ລາວ",
    "ne": "नेपाली",
    "si": "සිංහල",
    "tl": "Filipino",
    "ceb": "Cebuano",
    "haw": "ʻŌlelo Hawaiʻi",
    "mi": "Te Reo Māori",
}


# ──────────────────────────────────────────────────────────────────────
#  Countries (iso-codes 3166-1)
# ──────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_countries() -> dict[str, dict]:
    path = "/usr/share/iso-codes/json/iso_3166-1.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)["3166-1"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}
    return {
        entry["alpha_2"]: {
            "name": entry.get("name", entry["alpha_2"]),
            "flag": entry.get("flag", ""),
        }
        for entry in data
        if "alpha_2" in entry
    }


def country_name(alpha_2: str) -> str:
    if not alpha_2:
        return ""
    return _load_countries().get(alpha_2.upper(), {}).get("name", alpha_2)


def country_flag(alpha_2: str) -> str:
    if not alpha_2:
        return ""
    return _load_countries().get(alpha_2.upper(), {}).get("flag", "")


# ──────────────────────────────────────────────────────────────────────
#  Languages (iso-codes 639-3 + native-name curated table)
# ──────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_languages() -> dict[str, str]:
    path = "/usr/share/iso-codes/json/iso_639-3.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)["639-3"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for entry in data:
        name = entry.get("name", "")
        if not name:
            continue
        a2 = entry.get("alpha_2")
        a3 = entry.get("alpha_3")
        if a2:
            out[a2.lower()] = name
        if a3:
            out[a3.lower()] = name
    return out


def language_english_name(code: str) -> str:
    if not code:
        return ""
    return _load_languages().get(code.lower(), code)


def language_native_name(code: str) -> str:
    if not code:
        return ""
    return NATIVE_LANGUAGE_NAMES.get(code.lower(), language_english_name(code))


# ──────────────────────────────────────────────────────────────────────
#  Locale parsing / formatting
# ──────────────────────────────────────────────────────────────────────
def parse_locale(locale_id: str) -> tuple[str, str, str]:
    """('en_US.UTF-8',) -> ('en', 'US', 'UTF-8'). Defensive — accepts
    `en_US`, `en_US.utf8`, `pt_BR.UTF-8@latin`, etc."""
    base = locale_id.split(".")[0]
    encoding = locale_id.split(".", 1)[1] if "." in locale_id else "UTF-8"
    encoding = encoding.split("@")[0]
    base = base.split("@")[0]
    if "_" in base:
        lang, country = base.split("_", 1)
        return (lang.lower(), country.upper(), encoding)
    return (base.lower(), "", encoding)


def humanize_locale(locale_id: str) -> dict[str, str]:
    """Return rich-text dict for a locale id.

    Keys: primary, native, country_name, country_code, flag, raw."""
    lang, country, _enc = parse_locale(locale_id)
    return {
        "primary": (
            f"{language_english_name(lang)} ({country_name(country)})"
            if country
            else language_english_name(lang)
        ),
        "native": language_native_name(lang),
        "country_name": country_name(country) if country else "",
        "country_code": country,
        "flag": country_flag(country) if country else "🌐",
        "raw": locale_id,
    }


@lru_cache(maxsize=1)
def list_locales() -> list[str]:
    """Full supported-locale list, sorted by display name.

    Reads from `/usr/share/i18n/locales/` rather than `locale -a` because
    the latter only shows locales already generated on the live system
    (typically 1-3), while we want to offer every locale glibc supports
    (~370). The installer will run `locale-gen` for whichever the user
    picks during install."""
    try:
        names = os.listdir("/usr/share/i18n/locales/")
    except OSError:
        return ["en_US.UTF-8"]
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        base = raw.split("@")[0]
        if "_" not in base or "." in base:
            continue
        lang, country = base.split("_", 1)
        if not (lang.isalpha() and country.isalpha() and len(country) == 2):
            continue
        key = f"{base}.UTF-8"
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    # Sort by humanized primary so the dropdown reads naturally.
    out.sort(key=lambda loc: humanize_locale(loc)["primary"].lower())
    return out


# ──────────────────────────────────────────────────────────────────────
#  Keyboard layouts (X11 evdev.lst)
# ──────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def list_keyboard_layouts() -> list[tuple[str, str]]:
    """[(code, description), ...] for every layout in evdev.lst's
    `! layout` section. Sorted by description."""
    path = "/usr/share/X11/xkb/rules/evdev.lst"
    out: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return [("us", "English (US)")]
    in_section = False
    for line in content.splitlines():
        if line.startswith("! layout"):
            in_section = True
            continue
        if line.startswith("!"):
            in_section = False
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    out.sort(key=lambda t: t[1].lower())
    return out


# Map xkb 2-letter layout code → ISO 3166-1 alpha-2 country code for the
# flag chip. Most xkb codes ARE the country code; the exceptions are
# language-coded ones (ara = generic Arabic, brai = Braille, etc.) and
# a few mismatches.
_XKB_TO_COUNTRY = {
    "ara": "",      # generic Arabic — multi-country
    "brai": "",     # Braille — N/A
    "epo": "",      # Esperanto
    "latam": "",    # Latin American Spanish — multi-country
    "ml": "ML",     # Bambara (Mali) — map to Mali flag
    "us": "US",
    "uk": "GB",     # NB: xkb uses "gb" not "uk", but defensive
    "gb": "GB",
}


def keyboard_layout_country(code: str) -> str:
    """Best-effort ISO 3166 alpha_2 for a keyboard layout code."""
    if not code:
        return ""
    code = code.lower()
    if code in _XKB_TO_COUNTRY:
        return _XKB_TO_COUNTRY[code]
    if len(code) == 2 and code.isalpha():
        # 2-letter xkb codes are almost always ISO 3166-1 alpha-2 lowercase
        return code.upper()
    return ""


def humanize_keyboard(code: str, description: str = "") -> dict[str, str]:
    """Return rich-text dict for a keyboard layout."""
    cc = keyboard_layout_country(code)
    if not description:
        # Fall back to lookup in the layout list
        for c, d in list_keyboard_layouts():
            if c == code:
                description = d
                break
        if not description:
            description = code.upper()
    return {
        "primary": description,
        "country_code": cc,
        "country_name": country_name(cc) if cc else "",
        "flag": country_flag(cc) if cc else "⌨",
        "raw": code,
    }


# ──────────────────────────────────────────────────────────────────────
#  Timezones (zone1970.tab + zoneinfo runtime)
# ──────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_zone1970() -> dict[str, dict]:
    path = "/usr/share/zoneinfo/zone1970.tab"
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                ccs = parts[0].split(",")
                tz = parts[2]
                comment = parts[3] if len(parts) > 3 else ""
                out[tz] = {"country_code": ccs[0], "comment": comment}
    except OSError:
        pass
    return out


def timezone_city(tz_id: str) -> str:
    """'America/Chicago' -> 'Chicago'. Underscores -> spaces."""
    if "/" not in tz_id:
        return tz_id
    return tz_id.rsplit("/", 1)[1].replace("_", " ")


def timezone_country_code(tz_id: str) -> str:
    return _load_zone1970().get(tz_id, {}).get("country_code", "")


def humanize_timezone(tz_id: str) -> dict[str, str]:
    cc = timezone_country_code(tz_id)
    return {
        "city": timezone_city(tz_id),
        "country_code": cc,
        "country_name": country_name(cc) if cc else "",
        "flag": country_flag(cc) if cc else "🌐",
        "comment": _load_zone1970().get(tz_id, {}).get("comment", ""),
        "raw": tz_id,
    }


def timezone_offset_minutes(tz_id: str) -> int | None:
    if ZoneInfo is None:
        return None
    try:
        tz = ZoneInfo(tz_id)
        off = datetime.now(tz).utcoffset()
        if off is None:
            return None
        return int(off.total_seconds() // 60)
    except Exception:
        return None


def format_offset(tz_id: str) -> str:
    """'UTC-6' / 'UTC+5:30' / 'UTC' for offset-zero."""
    mins = timezone_offset_minutes(tz_id)
    if mins is None:
        return "UTC?"
    if mins == 0:
        return "UTC"
    sign = "+" if mins > 0 else "-"
    abs_mins = abs(mins)
    h, m = divmod(abs_mins, 60)
    if m == 0:
        return f"UTC{sign}{h}"
    return f"UTC{sign}{h}:{m:02d}"


def format_local_time(tz_id: str) -> str:
    """Current HH:MM at tz. '--:--' on failure."""
    if ZoneInfo is None:
        return "--:--"
    try:
        return datetime.now(ZoneInfo(tz_id)).strftime("%H:%M")
    except Exception:
        return "--:--"


def format_local_clock(tz_id: str) -> str:
    """Current HH:MM:SS at tz (for the ticking clock display)."""
    if ZoneInfo is None:
        return "--:--:--"
    try:
        return datetime.now(ZoneInfo(tz_id)).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"
