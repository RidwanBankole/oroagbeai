"""
Oro Agbe — USSD Handler
Africa's Talking USSD webhook endpoint.

USSD is text-only. The flow returns the Yoruba-translated
weather message directly as text on the farmer's phone.

Flow:
  Session start  → Welcome menu
  Input "1"      → Ibadan weather
  Input "2"      → Osogbo weather
  Input "3"      → Ife weather
  Input "4"      → Iragbiji weather
  Input "5"      → Ask user to type any city name
  Input "00"     → Return to main menu

Africa's Talking USSD responses use plain text:
  - Start with "CON " to continue the session
  - Start with "END " to close the session

Timing contract:
  Africa's Talking kills a USSD session after ~5 seconds with no response.
  Fetching weather + translating via Groq can take 10-30 s, so we NEVER do
  that work inside the request handler.  Instead:

  1. On a cache MISS the handler immediately returns a CON "hold" message
     (< 1 s) and spawns a background thread to fetch + translate + cache.
  2. The user sees "Jowo pe pada lẹyin iṣẹju kan" and dials again.
  3. On the second dial the cache is warm → instant response.
  4. For the 4 preset cities the cache is pre-warmed at app startup so
     most users never hit step 1.

Cache:
  JSON files in /tmp/oro_agbe_cache/ — shared across all Gunicorn workers
  on the same Render instance without needing Redis.  TTL = 15 minutes.
"""

import hashlib
import json
import logging
import os
import threading
import time

from flask import Blueprint, request

from app.location_service import geocode_city
from app.weather_service import get_weather, weather_to_english_text
from app.translation_service import translate_to_yoruba

logger = logging.getLogger(__name__)

ussd_bp = Blueprint("ussd", __name__, url_prefix="/ussd")

CON = "CON "
END = "END "

PRESET_CITIES = {
    "1": "Ibadan",
    "2": "Osogbo",
    "3": "Ife",
    "4": "Iragbiji",
}

# ---------------------------------------------------------------------------
# File-based cache — shared across all Gunicorn workers via /tmp
# ---------------------------------------------------------------------------

_CACHE_DIR    = "/tmp/oro_agbe_cache"
_USSD_CACHE_TTL = 900   # 15 minutes

os.makedirs(_CACHE_DIR, exist_ok=True)

# Track cities currently being fetched so we don't launch duplicate threads
_FETCHING: set[str] = set()
_FETCHING_LOCK = threading.Lock()


def _cache_path(city: str) -> str:
    key = hashlib.md5(city.strip().lower().encode()).hexdigest()
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _get_cached_weather_text(city: str) -> str | None:
    """Return cached Yoruba text for *city* if it exists and is still fresh."""
    path = _cache_path(city)
    try:
        with open(path) as fh:
            item = json.load(fh)
        if time.time() - item["ts"] < _USSD_CACHE_TTL:
            logger.info("Cache hit for %s", city)
            return item["text"]
        os.remove(path)   # expired
    except FileNotFoundError:
        pass
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Cache read error for %s: %s", city, exc)
    return None


def _set_cached_weather_text(city: str, text: str) -> None:
    """Persist Yoruba *text* for *city* to the shared /tmp cache."""
    path = _cache_path(city)
    try:
        with open(path, "w") as fh:
            json.dump({"text": text, "ts": time.time()}, fh)
        logger.info("Cache written for %s", city)
    except OSError as exc:
        logger.warning("Cache write error for %s: %s", city, exc)


# ---------------------------------------------------------------------------
# Background fetch — runs OUTSIDE the AT 5-second window
# ---------------------------------------------------------------------------

def _fetch_and_cache(city: str) -> None:
    """
    Full pipeline: geocode → weather → translate → cache.
    Designed to run in a daemon thread.  Retries up to 3 times with a
    10-second gap so transient Open-Meteo errors don't permanently poison
    the cache state.
    """
    try:
        geocoded = geocode_city(city_name=city)
        if not geocoded:
            logger.warning("Background fetch: could not geocode '%s'", city)
            return

        lat, lon, location_name = geocoded
        logger.info("Background fetch: weather for %s (%s, %s)", location_name, lat, lon)

        weather = None
        for attempt in range(1, 4):
            weather = get_weather(lat, lon, location_name)
            if weather:
                break
            logger.warning("Background fetch attempt %d failed for %s, retrying...", attempt, city)
            time.sleep(10)

        if not weather:
            logger.error("Background fetch: all attempts failed for %s", city)
            return

        english_text = weather_to_english_text(weather)
        yoruba_text  = translate_to_yoruba(english_text)

        if yoruba_text:
            _set_cached_weather_text(city, yoruba_text)
        else:
            logger.error("Background fetch: translation returned empty for %s", city)

    except Exception:
        logger.exception("Background fetch crashed for %s", city)
    finally:
        with _FETCHING_LOCK:
            _FETCHING.discard(city.strip().lower())


def _trigger_background_fetch(city: str) -> None:
    """Launch a background thread for *city* unless one is already running."""
    key = city.strip().lower()
    with _FETCHING_LOCK:
        if key in _FETCHING:
            logger.info("Background fetch already in progress for %s", city)
            return
        _FETCHING.add(key)

    t = threading.Thread(target=_fetch_and_cache, args=(city,), daemon=True)
    t.start()
    logger.info("Background fetch started for %s", city)


# ---------------------------------------------------------------------------
# Startup pre-warm — called from your app factory / main.py
# ---------------------------------------------------------------------------

def prewarm_preset_cities() -> None:
    """
    Kick off background fetches for all 4 preset cities at startup.
    Call this once after the Flask app is created, e.g.:

        from app.ussd_handler import prewarm_preset_cities
        prewarm_preset_cities()

    Workers that already have a warm /tmp cache will skip the fetch.
    """
    for city in PRESET_CITIES.values():
        if not _get_cached_weather_text(city):
            logger.info("Pre-warming cache for %s", city)
            _trigger_background_fetch(city)
            time.sleep(1)   # gentle spacing so we don't hammer Open-Meteo
        else:
            logger.info("Pre-warm skipped (cache warm): %s", city)


# ---------------------------------------------------------------------------
# Core weather getter — instant if cached, triggers background fetch if not
# ---------------------------------------------------------------------------

# Yoruba hold message shown to the user while the background fetch runs
_HOLD_MESSAGE = (
    "Iroyin oju ojo fun ilu yii ko ti ṣetan.\n"
    "A n pese e nisisiyi.\n\n"
    "Jowo pe pada lẹyin iṣẹju kan."
)


def _get_yoruba_weather(phone: str = "", city: str = "") -> str | None:
    """
    Return cached Yoruba weather text for *city*, or None if the cache is
    cold (in which case a background fetch has already been triggered).
    The phone argument is kept for API compatibility but is not used.
    """
    if not city:
        return "A ko ri ilu naa. Jowo yan ilu tabi tẹ orúkọ ilu."

    cached = _get_cached_weather_text(city)
    if cached:
        return cached

    # Cache miss — kick off background fetch and tell user to try again
    _trigger_background_fetch(city)
    return None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _paginate(text: str, page: int, page_size: int = 160) -> tuple[str, bool]:
    """Split a long Yoruba message into USSD-sized pages."""
    words  = text.split()
    pages: list[str] = []
    current = ""

    for word in words:
        extra = len(word) if not current else len(word) + 1
        if len(current) + extra <= page_size:
            current += ("" if not current else " ") + word
        else:
            if current:
                pages.append(current)
            current = word
    if current:
        pages.append(current)

    if not pages:
        return text, False

    page     = max(0, min(page, len(pages) - 1))
    has_more = page < len(pages) - 1
    return pages[page], has_more


# ---------------------------------------------------------------------------
# USSD route
# ---------------------------------------------------------------------------

@ussd_bp.route("/session", methods=["POST"])
def ussd_session():
    """Africa's Talking calls this URL for every USSD interaction."""
    try:
        session_id   = request.form.get("sessionId", "")
        phone        = request.form.get("phoneNumber", "")
        service_code = request.form.get("serviceCode", "")
        text         = request.form.get("text", "").strip()

        logger.info(
            "USSD session=%s, phone=%s, serviceCode=%s, text='%s'",
            session_id, phone, service_code, text,
        )

        inputs   = [t.strip() for t in text.split("*")] if text else []
        response = _route_ussd(phone, inputs)
        logger.info("USSD response: %s...", response[:120])

        return response, 200, {"Content-Type": "text/plain; charset=utf-8"}

    except Exception:
        logger.exception("USSD session failed")
        return (
            END + "Aṣiṣe ṣẹlẹ. Jowo gbiyanju lẹẹkansi.",
            200,
            {"Content-Type": "text/plain; charset=utf-8"},
        )


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _main_menu() -> str:
    return (
        CON
        + "E kaabo si Oro Agbe\n"
        + "Yan ilu fun iroyin oju ojo:\n\n"
        + "1. Ibadan\n"
        + "2. Osogbo\n"
        + "3. Ife\n"
        + "4. Iragbiji\n"
        + "5. Tẹ orúkọ ilu rẹ"
    )


def _route_ussd(phone: str, inputs: list[str]) -> str:
    if not inputs or inputs == [""]:
        return _main_menu()
    if "00" in inputs:
        return _main_menu()

    first = inputs[0]
    if first in PRESET_CITIES:
        return _handle_own_location(phone, inputs)
    elif first == "5":
        return _handle_city_choice(phone, inputs)
    else:
        return END + "Aṣiṣe: Jọwọ tẹ 1, 2, 3, 4, tabi 5."


def _weather_response(city: str, phone: str, next_presses: list[str]) -> str:
    """
    Shared helper used by both preset-city and custom-city flows.
    Returns a CON hold message if the cache is cold, or the paginated
    weather text if the cache is warm.
    """
    if next_presses:
        if next_presses[-1] == "0":
            return END + "E dupe. O daro!"
        if next_presses[-1] == "00":
            return _main_menu()

    page         = sum(1 for p in next_presses if p == "1")
    yoruba_text  = _get_yoruba_weather(phone=phone, city=city)

    # Cache cold → hold message (background fetch already triggered)
    if yoruba_text is None:
        return (
            END
            + f"Oju ojo: {city.title()}\n"
            + "-----------------\n"
            + _HOLD_MESSAGE
        )

    # Geocode / translate error messages from _get_yoruba_weather
    if yoruba_text.startswith("A ko ri"):
        return (
            CON
            + f"{yoruba_text}\n"
            + "Jọwọ tẹ orúkọ ilu miran.\n"
            + "00. Pada si akojọ akọkọ"
        )

    page_text, has_more = _paginate(yoruba_text, page)
    label = city.title()

    if has_more:
        return (
            CON
            + f"Oju ojo: {label}\n"
            + "-----------------\n"
            + f"{page_text}\n\n"
            + "1. Tẹsiwaju\n"
            + "0. Pari\n"
            + "00. Akojọ akọkọ"
        )
    return (
        END
        + f"Oju ojo: {label}\n"
        + "-----------------\n"
        + f"{page_text}\n\n"
        + "E dupe, agbẹ wa. O daro!"
    )


def _handle_own_location(phone: str, inputs: list[str]) -> str:
    """
    Handle preset city options (inputs[0] in {"1","2","3","4"}).
      inputs = ["1"]       → first page (or hold if cold)
      inputs = ["1", "1"]  → next page
      inputs = ["1", "0"]  → exit
    """
    city = PRESET_CITIES.get(inputs[0])
    if not city:
        return END + "Aṣiṣe: ilu ko pe."
    return _weather_response(city, phone, inputs[1:])


def _handle_city_choice(phone: str, inputs: list[str]) -> str:
    """
    Handle free-text city entry (inputs[0] == "5").
      inputs = ["5"]               → prompt for city name
      inputs = ["5", "Lagos"]      → fetch (or hold) first page
      inputs = ["5", "Lagos", "1"] → next page
      inputs = ["5", "Lagos", "0"] → exit
    """
    if len(inputs) == 1:
        return (
            CON
            + "Ẹ tẹ orúkọ ilu rẹ:\n"
            + "Apẹẹrẹ:\n"
            + "Ibadan, Lagos, Akure,\n"
            + "Abeokuta, Osogbo..."
        )

    city = inputs[1].strip()
    if city == "00":
        return _main_menu()
    if not city:
        return CON + "Jọwọ tẹ orúkọ ilu to pe."

    return _weather_response(city, phone, inputs[2:])