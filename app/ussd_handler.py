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
"""

import logging
import time
from flask import Blueprint, request, current_app
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

# Cache final Yoruba weather text so pagination does not refetch weather
_USSD_WEATHER_CACHE = {}
_USSD_CACHE_TTL = 900  # 15 minutes


def _cache_key(city: str) -> str:
    return city.strip().lower()


def _get_cached_weather_text(city: str) -> str | None:
    key = _cache_key(city)
    item = _USSD_WEATHER_CACHE.get(key)

    if not item:
        return None

    if time.time() - item["ts"] > _USSD_CACHE_TTL:
        _USSD_WEATHER_CACHE.pop(key, None)
        return None

    return item["text"]


def _set_cached_weather_text(city: str, text: str) -> None:
    key = _cache_key(city)
    _USSD_WEATHER_CACHE[key] = {
        "text": text,
        "ts": time.time(),
    }


def _get_yoruba_weather(phone: str = "", city: str = "") -> str:
    """
    Run the location → weather → translate pipeline.
    The phone argument is preserved so other code does not break,
    but it is no longer used for location logic.
    """
    try:
        if not city:
            return "A ko ri ilu naa. Jowo yan ilu tabi tẹ orúkọ ilu."

        cached = _get_cached_weather_text(city)
        if cached:
            logger.info("Using cached Yoruba weather for %s", city)
            return cached

        hf_token = current_app.config.get("HF_API_TOKEN", "")

        geocoded = geocode_city(city_name=city)
        if not geocoded:
            return f"A ko ri '{city}' ninu ètò wa. Jowo gbiyanju ilu miran."

        lat, lon, location_name = geocoded
        logger.info("USSD weather for %s (%s, %s)", location_name, lat, lon)

        weather = get_weather(lat, lon, location_name)
        if not weather:
            return "A ko le gba iroyin ojo loni. Jowo gbiyanju lẹhin igba diẹ."

        english_text = weather_to_english_text(weather)
        yoruba_text = translate_to_yoruba(english_text, hf_token=hf_token)

        if not yoruba_text:
            return "A ko le tumọ iroyin ojo lọwọlọwọ. Jowo gbiyanju lẹẹkansi."

        _set_cached_weather_text(city, yoruba_text)
        return yoruba_text

    except Exception:
        logger.exception("Failed to build Yoruba weather response")
        return "Aṣiṣe ṣẹlẹ nigba gbigba iroyin ojo. Jowo gbiyanju lẹẹkansi."


def _paginate(text: str, page: int, page_size: int = 160) -> tuple[str, bool]:
    """
    Split a long Yoruba message into USSD-sized pages.
    """
    words = text.split()
    pages = []
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

    page = max(0, min(page, len(pages) - 1))
    has_more = page < len(pages) - 1
    return pages[page], has_more


@ussd_bp.route("/session", methods=["POST"])
def ussd_session():
    """
    Africa's Talking calls this URL for every USSD interaction.
    """
    try:
        session_id = request.form.get("sessionId", "")
        phone = request.form.get("phoneNumber", "")
        service_code = request.form.get("serviceCode", "")
        text = request.form.get("text", "").strip()

        logger.info(
            "USSD session=%s, phone=%s, serviceCode=%s, text='%s'",
            session_id,
            phone,
            service_code,
            text,
        )

        inputs = [t.strip() for t in text.split("*")] if text else []

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
    """
    Route the USSD session based on the input history.
    """
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


def _handle_own_location(phone: str, inputs: list[str]) -> str:
    """
    Handle preset city options.

    inputs = ["1"]       → Ibadan weather first page
    inputs = ["1", "1"]  → next page
    inputs = ["1", "0"]  → exit
    """
    city = PRESET_CITIES.get(inputs[0])
    if not city:
        return END + "Aṣiṣe: ilu ko pe."

    next_presses = inputs[1:] if len(inputs) > 1 else []

    if next_presses:
        if next_presses[-1] == "0":
            return END + "E dupe. O daro!"
        if next_presses[-1] == "00":
            return _main_menu()

    page = sum(1 for p in next_presses if p == "1")

    # Fetch once per city, then paginate cached text
    yoruba_text = _get_yoruba_weather(phone=phone, city=city)
    page_text, has_more = _paginate(yoruba_text, page)

    if has_more:
        return (
            CON
            + f"Oju ojo: {city}\n"
            + "-----------------\n"
            + f"{page_text}\n\n"
            + "1. Tẹsiwaju\n"
            + "0. Pari\n"
            + "00. Akojọ akọkọ"
        )

    return (
        END
        + f"Oju ojo: {city}\n"
        + "-----------------\n"
        + f"{page_text}\n\n"
        + "E dupe, agbẹ wa. O daro!"
    )


def _handle_city_choice(phone: str, inputs: list[str]) -> str:
    """
    Ask the user to type a city name, then fetch and show weather.

    inputs = ["5"]               → prompt for city name
    inputs = ["5", "Lagos"]      → city typed, show first page
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

    nav_inputs = inputs[2:] if len(inputs) > 2 else []

    if nav_inputs:
        if nav_inputs[-1] == "0":
            return END + "E dupe. O daro!"
        if nav_inputs[-1] == "00":
            return _main_menu()

    page = sum(1 for p in nav_inputs if p == "1")

    # Fetch once per city, then paginate cached text
    yoruba_text = _get_yoruba_weather(phone=phone, city=city)

    if yoruba_text.startswith("A ko ri"):
        return (
            CON
            + f"{yoruba_text}\n"
            + "Jọwọ tẹ orúkọ ilu miran.\n"
            + "00. Pada si akojọ akọkọ"
        )

    page_text, has_more = _paginate(yoruba_text, page)
    location_name = city.title()

    if has_more:
        return (
            CON
            + f"Oju ojo: {location_name}\n"
            + "-----------------\n"
            + f"{page_text}\n\n"
            + "1. Tẹsiwaju\n"
            + "0. Pari\n"
            + "00. Akojọ akọkọ"
        )

    return (
        END
        + f"Oju ojo: {location_name}\n"
        + "-----------------\n"
        + f"{page_text}\n\n"
        + "E dupe, agbẹ wa. O daro!"
    )