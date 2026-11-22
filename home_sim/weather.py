"""The house's weather: Jay's real sky, shared.

At run start the house asks Open-Meteo (free, no key) what it is doing
outside Jay's window at home in Webster Groves, MO, and stores that as
the night's weather. A ghost-visitor override file may replace it for
a few nights. A failed lookup never breaks a night: the house keeps
the last weather it knew, and if there is none, a neutral default.

Pure functions where possible: resolve_weather() takes a state dict and
the merged overrides and returns a new weather dict; it never touches
the house's state file. The only I/O is the fetch itself, behind an
injectable fetcher so tests never touch the network. A fetcher is a
callable taking a URL and returning the parsed Open-Meteo JSON.

Fetcher convention (deliberate, read carefully): in resolve_weather(),
fetcher=None means OFFLINE — no live lookup is attempted at all, and
the house falls back to cached, then default. Home's default is
offline for exactly this reason: the test suite constructs Homes
without fetchers and must never touch the network. The production
runner (__main__) passes open_meteo_fetcher explicitly. fetch_live_
weather() is the other way round: fetcher=None there means "use the
real Open-Meteo fetcher", since that function exists to do a live
lookup.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=38.59&longitude=-90.36"
    "&current=temperature_2m,weather_code,wind_speed_10m"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph"
    "&timezone=America%2FChicago"
)
FETCH_TIMEOUT_SECONDS = 10
# Wind that earns its own condition: a clear night with a 25 mph wind
# is a windy night, not a clear one. Precipitation codes win over wind.
WINDY_MPH = 25

CONDITIONS = ("clear", "cloudy", "rain", "snow", "storm", "fog", "wind")

_ADJECTIVES = {
    "clear": "clear", "cloudy": "cloudy", "rain": "rainy",
    "snow": "snowy", "storm": "stormy", "fog": "foggy", "wind": "windy",
}

# One scene sentence per condition, rotated by night so the wording
# varies instead of repeating the same boilerplate every night. The
# variant is chosen from the night date, so it is stable within one
# night and different across nights.
WEATHER_SENTENCES = {
    "clear": (
        "The sky is clear tonight.",
        "Stars show through the window.",
        "The night is clear and still.",
    ),
    "cloudy": (
        "Clouds cover the night sky.",
        "The sky is overcast; no stars show.",
        "Cloud hangs low over the roof.",
    ),
    "rain": (
        "Rain ticks against the window.",
        "Rain keeps coming down outside.",
        "I can hear rain on the roof.",
    ),
    "snow": (
        "Snow falls softly past the window.",
        "Snow is coming down, quiet and steady.",
        "Snow dusts the sill.",
    ),
    "storm": (
        "Thunder rolls somewhere far off.",
        "Lightning flickers at the edge of the sky.",
        "A storm is moving through; the house ticks with it.",
    ),
    "fog": (
        "Fog presses against the glass.",
        "The world outside is fog, nothing beyond the window.",
        "Fog has swallowed the garden.",
    ),
    "wind": (
        "Wind moves around the eaves.",
        "The wind is up tonight, rattling the latch.",
        "Wind pushes at the shutters.",
    ),
}

# Clear and freezing: the cold gets said out loud.
COLD_CLEAR_SENTENCES = (
    "The night is clear and cold.",
    "The sky is clear and the air bites.",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_weather() -> dict:
    """Neutral weather: clear, temperature unknown, nothing known yet.

    Three bookkeeping keys ride along with the four the task names:
    nights_left counts an active override's remaining nights, override_
    file names the file that provided it (so an expired override never
    re-activates while its file still sits in the folder), and nudge_
    night records which night the garden already got its rain for.
    """
    return {"condition": "clear", "temp_f": None, "source": "default",
            "as_of": None, "nights_left": 0, "override_file": None,
            "nudge_night": None}


def condition_from_code(code, wind_mph=None) -> str:
    """Map a WMO weather code (and wind speed) to the small vocabulary.

    Precipitation wins over wind: a rainy night with a gale is rainy,
    not windy. An unknown or missing code is cloudy — overcast is the
    honest shrug, neither clear nor wet.
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "cloudy"
    if code in (95, 96, 99):
        return "storm"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (45, 48):
        return "fog"
    windy = (wind_mph is not None
             and isinstance(wind_mph, (int, float))
             and wind_mph >= WINDY_MPH)
    if code in (0, 1):
        return "wind" if windy else "clear"
    if code in (2, 3):
        return "wind" if windy else "cloudy"
    return "wind" if windy else "cloudy"


def open_meteo_fetcher(url: str) -> dict:
    """The real fetch: one GET to Open-Meteo, 10-second timeout, no key."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "calibos-home/1.0"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_live_weather(fetcher=None) -> dict:
    """One live lookup. Returns {"condition", "temp_f"}.

    Raises on any failure (no network, timeout, bad response, missing
    fields) — the caller decides the fallback; this function never
    invents weather. fetcher=None means the real Open-Meteo fetcher.
    """
    fetch = fetcher or open_meteo_fetcher
    data = fetch(OPEN_METEO_URL)
    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    wind = current.get("wind_speed_10m")
    if temp is None or code is None:
        raise ValueError("Open-Meteo response has no current conditions")
    return {"condition": condition_from_code(code, wind), "temp_f": temp}


def weather_adjective(condition: str) -> str:
    """'rain' -> 'rainy', for the digest header."""
    return _ADJECTIVES.get(condition, "clear")


def weather_sentence(weather: dict | None, night_date: str,
                     tick: int = 0) -> str:
    """One scene sentence for tonight's weather, varied by night and
    by moment: the variant rotates as the ticks pass, so a visit that
    shows the scene after every action does not repeat one line ten
    times in a row, and different nights still read differently."""
    wx = weather or {}
    condition = wx.get("condition") or "clear"
    temp = wx.get("temp_f")
    if condition == "clear" and temp is not None and temp <= 32:
        pool = COLD_CLEAR_SENTENCES
    else:
        pool = WEATHER_SENTENCES.get(condition, WEATHER_SENTENCES["clear"])
    seed = tick + sum(ord(c) for c in f"{night_date}|{condition}")
    return pool[seed % len(pool)]


def resolve_weather(state: dict, overrides: dict | None,
                    fetcher=None, for_night: bool = True) -> dict:
    """Tonight's weather, by precedence: override, live sky, cached,
    default. Returns a new weather dict; the input state is untouched.

    fetcher=None means OFFLINE: no live lookup is attempted. Pass a
    callable (open_meteo_fetcher, or a fake in tests) to fetch live.
    for_night=False is a visit: an active override is used but the
    night is not consumed, since a visit is not a night.

    An override lasts its stated nights, then the live sky resumes.
    The activating file is remembered, so an expired override never
    re-activates while its file still sits in the folder; deleting the
    file ends the override early, and a newer file replaces an active
    one. A visit never consumes a night.
    """
    current = state.get("weather") or default_weather()
    merged = (overrides or {}).get("weather")
    # 1. An active override from the same file continues its countdown.
    if (current.get("source") == "override"
            and current.get("nights_left", 0) > 0
            and merged
            and merged.get("file") == current.get("override_file")):
        new = dict(current)
        if for_night:
            new["nights_left"] -= 1
        return new
    # 2. A fresh override file activates — unless this same file's
    # nights have already run out.
    if merged and not (current.get("source") == "override"
                       and current.get("override_file")
                       == merged.get("file")):
        nights = merged.get("nights", 1)
        if type(nights) is not int or nights < 1:
            nights = 1
        return {"condition": merged["condition"],
                "temp_f": merged.get("temp_f"),
                "source": "override", "as_of": _utc_now(),
                "nights_left": nights - 1 if for_night else nights,
                "override_file": merged.get("file"),
                "nudge_night": current.get("nudge_night")}
    # 3. Otherwise the live sky, falling back to cached, then default.
    if fetcher is not None:
        try:
            live = fetch_live_weather(fetcher)
            return {"condition": live["condition"],
                    "temp_f": live["temp_f"],
                    "source": "live", "as_of": _utc_now(),
                    "nights_left": 0, "override_file": None,
                    "nudge_night": current.get("nudge_night")}
        except Exception:
            pass
    if current.get("condition"):
        cached = dict(current)
        if cached.get("source") in ("live", "override"):
            # It was real weather once; now it is last known weather.
            # A default that was never fetched stays a default.
            cached["source"] = "cached"
        return cached
    return default_weather()
