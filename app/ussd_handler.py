"""
Oro Agbe — USSD Handler
Africa's Talking USSD webhook endpoint.

USSD is text-only (no audio). The flow returns the Yoruba-translated
weather message directly as a text screen on the farmer's phone.

Flow:
  Session start  → Welcome menu (press 1 for weather, press 2 to choose city)
  Input "1"      → Detect location from phone, fetch & translate weather → display
  Input "2"      → Ask for city name input → fetch & translate weather → display
  Input "00"     → Return to main menu (any screen)

Africa's Talking USSD responses use a plain text format:
  - Start with "CON " to continue the session (show more menu options)
  - Start with "END " to close the session (final message, no more input)

Docs: https://developers.africastalking.com/docs/ussd/
"""

import logging
from flask import Blueprint, request
from app.location_service import geocode_city
from app.weather_service import get_weather, weather_to_english_text
from app.translation_service import translate_to_yoruba
from flask import current_app

logger = logging.getLogger(__name__)

ussd_bp = Blueprint("ussd", __name__, url_prefix="/ussd")

# ── USSD response prefix constants ────────────────────────────────────────────
CON = "CON "   # Continue — session stays open, farmer can press keys
END = "END "   # End     — session closes after this message


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: Build the Yoruba weather message for USSD display
# ══════════════════════════════════════════════════════════════════════════════

def _get_yoruba_weather(phone: str = "", city: str = "") -> str:
    """
    Run the location → weather → translate pipeline.
    Returns a Yoruba string ready to display on a USSD screen,
    or an error message in Yoruba if something fails.
    """
    hf_token = current_app.config.get("HF_API_TOKEN", "")

    # Step 1: Resolve location
    lat, lon, location_name = geocode_city(city_name=city)
    logger.info(f"USSD weather for {location_name} ({lat}, {lon})")

    # Step 2: Fetch weather
    weather = get_weather(lat, lon, location_name)
    if not weather:
        return "A ko le gba iroyin ojo loni.\nJowo gbiyanju lẹhin igba diẹ."
        # "We could not get the weather report today. Please try again later."

    # Step 3: Translate to Yoruba
    english_text = weather_to_english_text(weather)
    yoruba_text  = translate_to_yoruba(english_text, hf_token=hf_token)

    # Step 4: Truncate if too long for USSD screen (182 chars max per page)
    # We split into pages if needed — see _paginate() below
    return yoruba_text


def _paginate(text: str, page: int, page_size: int = 160) -> tuple[str, bool]:
    """
    Split a long Yoruba message into USSD-sized pages.

    Args:
        text:      Full Yoruba text.
        page:      Which page to return (0-indexed).
        page_size: Max characters per USSD screen (safe default: 160).

    Returns:
        (page_text, has_more) — the text for this page and whether more pages follow.
    """
    words    = text.split()
    pages    = []
    current  = ""

    for word in words:
        if len(current) + len(word) + 1 <= page_size:
            current += ("" if not current else " ") + word
        else:
            pages.append(current)
            current = word
    if current:
        pages.append(current)

    if not pages:
        return text, False

    page     = max(0, min(page, len(pages) - 1))
    has_more = page < len(pages) - 1
    return pages[page], has_more


# ══════════════════════════════════════════════════════════════════════════════
# MAIN USSD ROUTE
# ══════════════════════════════════════════════════════════════════════════════

@ussd_bp.route("/session", methods=["POST"])
def ussd_session():
    """
    Africa's Talking calls this URL for every USSD interaction.

    AT sends these fields:
      sessionId   — unique ID for this USSD session
      serviceCode — the short code dialled e.g. *384*123#
      phoneNumber — caller's number e.g. +2348031234567
      text        — all inputs so far, joined by * e.g. "1" or "2*Ibadan"
    """
    session_id   = request.form.get("sessionId", "")
    phone        = request.form.get("phoneNumber", "")
    service_code = request.form.get("serviceCode", "")
    text         = request.form.get("text", "").strip()

    logger.info(f"USSD session={session_id}, phone={phone}, text='{text}'")

    # Split input history by * to get each step the user has taken
    # e.g. text="2*Ibadan" means: chose option 2, then typed "Ibadan"
    inputs = [t.strip() for t in text.split("*")] if text else []

    response = _route_ussd(phone, inputs)
    logger.info(f"USSD response: {response[:80]}...")
    return response, 200, {"Content-Type": "text/plain; charset=utf-8"}


def _route_ussd(phone: str, inputs: list[str]) -> str:
    """
    Route the USSD session based on the input history.

    inputs[0] = first key press (main menu choice)
    inputs[1] = second input (city name, or page navigation)
    inputs[2] = third input (page navigation after city lookup)
    """

    # ── Level 0: No input yet — show main menu ──────────────────────────────
    if not inputs or inputs == [""]:
        return (
            CON
            + "E kaabo si Oro Agbe 🌾\n"
            + "Ẹ tẹ nọmba fún oju ojo:\n\n"
            + "1. Oju ojo ìbílẹ̀ mi\n"
            + "   (Weather for my area)\n"
            + "2. Yan ilu mìíràn\n"
            + "   (Choose a different city)"
        )

    first = inputs[0]

    # ── Option 1: Weather for caller's own location ─────────────────────────
    if first == "1":
        return _handle_own_location(phone, inputs)

    # ── Option 2: Weather for a chosen city ────────────────────────────────
    elif first == "2":
        return _handle_city_choice(phone, inputs)

    # ── 00: Back to main menu (from any screen) ─────────────────────────────
    elif first == "00":
        return (
            CON
            + "E kaabo si Oro Agbe 🌾\n"
            + "Ẹ tẹ nọmba fún oju ojo:\n\n"
            + "1. Oju ojo ìbílẹ̀ mi\n"
            + "2. Yan ilu mìíràn"
        )

    # ── Unknown input ────────────────────────────────────────────────────────
    else:
        return END + "Aṣiṣe: Jọwọ tẹ 1 tabi 2.\nError: Please press 1 or 2."


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: Option 1 — Weather for caller's own location
# ══════════════════════════════════════════════════════════════════════════════

def _handle_own_location(phone: str, inputs: list[str]) -> str:
    """
    Fetch weather for the caller's phone-prefix location.
    Supports pagination if the Yoruba message is longer than one screen.

    inputs = ["1"]           → first page
    inputs = ["1", "1"]      → next page (user pressed 1 to continue)
    inputs = ["1", "0"]      → user pressed 0 to exit
    """
    yoruba_text = _get_yoruba_weather(phone=phone)

    # Determine which page the user is on
    # Each "1" after the first means "show next page"
    next_presses = inputs[1:] if len(inputs) > 1 else []
    page         = sum(1 for p in next_presses if p == "1")

    # Did user press 0 to exit pagination?
    if next_presses and next_presses[-1] == "0":
        return END + "E dupe ololufe wa. O daro!\nThank you. Goodbye!"

    page_text, has_more = _paginate(yoruba_text, page)

    if has_more:
        return (
            CON
            + f"{page_text}\n\n"
            + "─────────────────\n"
            + "1. Tẹsiwaju (Next)\n"
            + "0. Pari (Exit)\n"
            + "00. Akojọ akọkọ (Main menu)"
        )
    else:
        return (
            END
            + f"{page_text}\n\n"
            + "─────────────────\n"
            + "E dupe, agbẹ wa. O daro!\n"
            + "Thank you, our farmer. Goodbye!"
        )


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: Option 2 — Weather for a chosen city
# ══════════════════════════════════════════════════════════════════════════════

def _handle_city_choice(phone: str, inputs: list[str]) -> str:
    """
    Ask the farmer to type a city name, then fetch and show weather.
    Supports pagination for long Yoruba messages.

    inputs = ["2"]             → prompt for city name
    inputs = ["2", "Ibadan"]   → city typed, show first page
    inputs = ["2", "Ibadan", "1"] → next page
    inputs = ["2", "Ibadan", "0"] → exit
    """

    # ── No city entered yet — ask for it ────────────────────────────────────
    if len(inputs) == 1:
        return (
            CON
            + "Ẹ tẹ orúkọ ilu rẹ:\n"
            + "Type your city name:\n\n"
            + "Àpẹẹrẹ / Example:\n"
            + "Ibadan, Lagos, Akure,\n"
            + "Abeokuta, Osogbo..."
        )

    city = inputs[1].strip()

    # ── Validate city — try to geocode it ───────────────────────────────────
    if not city:
        return (
            CON
            + "Ẹ tẹ orúkọ ilu rẹ daadaa:\n"
            + "Please enter a valid city name."
        )

    # Determine which page we're on (inputs after city name)
    nav_inputs = inputs[2:] if len(inputs) > 2 else []
    page       = sum(1 for p in nav_inputs if p == "1")

    # User pressed 0 to exit
    if nav_inputs and nav_inputs[-1] == "0":
        return END + "E dupe ololufe wa. O daro!\nThank you. Goodbye!"

    # Only geocode and fetch on first arrival (page == 0, no nav yet)
    # or every time — it's fast enough for USSD
    yoruba_text = _get_yoruba_weather(phone=phone, city=city)

    # Check if geocoding found the city
    geocoded = geocode_city(city)
    if not geocoded:
        return (
            CON
            + f"A ko ri '{city}' ninu ètò wa.\n"
            + f"We could not find '{city}'.\n\n"
            + "Jọwọ gbiyanju ilu miran:\n"
            + "2. Gbiyanju lẹẹkan si (Try again)\n"
            + "1. Oju ojo ìbílẹ̀ mi (My area)\n"
            + "00. Akojọ akọkọ (Main menu)"
        )

    _, _, location_name = geocoded
    page_text, has_more = _paginate(yoruba_text, page)

    if has_more:
        return (
            CON
            + f"Oju ojo: {location_name}\n"
            + "─────────────────\n"
            + f"{page_text}\n\n"
            + "1. Tẹsiwaju (Next)\n"
            + "0. Pari (Exit)\n"
            + "00. Akojọ akọkọ (Main menu)"
        )
    else:
        return (
            END
            + f"Oju ojo: {location_name}\n"
            + "─────────────────\n"
            + f"{page_text}\n\n"
            + "E dupe, agbẹ wa. O daro!\n"
            + "Thank you, our farmer. Goodbye!"
        )
