"""
Oro Agbe — IVR Handler
Africa's Talking IVR webhook endpoints.

Flow:
  1. /ivr/voice        — Entry point when farmer dials
  2. /ivr/action       — Handles DTMF key presses from the menu
  3. /ivr/weather      — Fetches weather, translates, synthesises, plays audio
  4. /ivr/hangup       — Called when the call ends (logging)

Africa's Talking IVR uses XML responses to control the call.
Docs: https://developers.africastalking.com/docs/voice/
"""

import logging
from flask import Blueprint, request, current_app, url_for
from app.location_service import geocode_city
from app.weather_service import get_weather, weather_to_english_text
from app.translation_service import translate_to_yoruba
from app.tts_service import synthesise_yoruba_speech

logger = logging.getLogger(__name__)
ivr_bp = Blueprint("ivr", __name__, url_prefix="/ivr")


# ══════════════════════════════════════════════════════════════════════════════
# XML RESPONSE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def xml_response(content: str) -> tuple:
    """Return a Flask response with XML content type."""
    return content, 200, {"Content-Type": "text/xml; charset=utf-8"}


def say(text: str, voice: str = "woman") -> str:
    """Africa's Talking <Say> element — plays TTS in English for menus."""
    return f'<Say voice="{voice}">{text}</Say>'


def play(url: str) -> str:
    """Africa's Talking <Play> element — plays an audio file URL."""
    return f'<Play url="{url}"/>'


def get_digits(prompt: str, num_digits: int = 1, timeout: int = 10, finish_on_key: str = "#") -> str:
    """Africa's Talking <GetDigits> element — captures DTMF input."""
    return (
        f'<GetDigits numDigits="{num_digits}" timeout="{timeout}" finishOnKey="{finish_on_key}">'
        f'{say(prompt)}'
        f'</GetDigits>'
    )


def ivr_xml(*elements: str) -> str:
    """Wrap elements in Africa's Talking <Response> root."""
    inner = "\n  ".join(elements)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  {inner}\n</Response>'


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 1 — ENTRY POINT  (farmer dials the number)
# ══════════════════════════════════════════════════════════════════════════════

@ivr_bp.route("/voice", methods=["GET", "POST"])
def voice_entry():
    """
    Africa's Talking calls this URL when a farmer dials our number.
    We greet them and present a simple menu.
    """
    caller  = request.values.get("callerNumber", "unknown")
    session = request.values.get("sessionId", "")

    logger.info(f"Incoming call: caller={caller}, session={session}")

    action_url = current_app.config["BASE_URL"] + "/ivr/action"

    xml = ivr_xml(
        get_digits(
            prompt=(
                "E kaabo si Oro Agbe. Welcome to Oro Agbe, the farmer's weather service. "
                "Press 1 to hear today's weather in Yoruba. "
                "Press 2 to hear weather for a different location. "
                "Press 0 to repeat this message."
            ),
            num_digits=1,
            timeout=15,
        ),
        # Fallback if no key pressed
        say("We did not receive your input. Please call back and try again. Goodbye."),
    )

    # Embed the action URL in the GetDigits tag properly
    xml = ivr_xml(
        f'<GetDigits numDigits="1" timeout="15" callbackUrl="{action_url}">'
        f'{say("E kaabo si Oro Agbe. Welcome to Oro Agbe, the farmer weather service. "
               "Press 1 to hear today weather in Yoruba. "
               "Press 0 to repeat this message.")}'
        f'</GetDigits>',
        say("We did not receive your input. Goodbye.")
    )

    return xml_response(xml)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 2 — ACTION  (handle key press)
# ══════════════════════════════════════════════════════════════════════════════

@ivr_bp.route("/action", methods=["GET", "POST"])
def ivr_action():
    """Handle DTMF key press from the menu."""
    digit  = request.values.get("dtmfDigits", "").strip()
    caller = request.values.get("callerNumber", "unknown")

    logger.info(f"DTMF received: digit='{digit}', caller={caller}")

    if digit == "1":
        # Get weather for caller's location
        weather_url = current_app.config["BASE_URL"] + f"/ivr/weather?phone={caller}"
        xml = ivr_xml(
            say("Please hold for a moment while we get the weather for your location."),
            f'<Redirect>{weather_url}</Redirect>'
        )

    elif digit == "0":
        # Repeat — redirect back to entry
        entry_url = current_app.config["BASE_URL"] + "/ivr/voice"
        xml = ivr_xml(f'<Redirect>{entry_url}</Redirect>')

    else:
        xml = ivr_xml(
            say("Invalid option. Please call back and press 1 for weather. Goodbye.")
        )

    return xml_response(xml)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 3 — WEATHER  (the core pipeline)
# ══════════════════════════════════════════════════════════════════════════════

@ivr_bp.route("/weather", methods=["GET", "POST"])
def ivr_weather():
    """
    Core pipeline:
      1. Resolve location from phone number
      2. Fetch weather from Open-Meteo
      3. Translate English → Yoruba (NLLB-200)
      4. Synthesise Yoruba speech (MMS-TTS-YOR)
      5. Return <Play> XML to Africa's Talking
    """
    phone    = request.values.get("phone") or request.values.get("callerNumber", "")
    city     = request.values.get("city", "")
    cfg      = current_app.config

    logger.info(f"Weather pipeline started for phone={phone}, city={city}")

    # ── Step 1: Resolve location ───────────────────────────────────────────
    lat, lon, location_name = geocode_city(city_name=city)
    logger.info(f"Location resolved: {location_name} ({lat}, {lon})")

    # ── Step 2: Fetch weather ──────────────────────────────────────────────
    weather = get_weather(lat, lon, location_name)
    if not weather:
        xml = ivr_xml(
            say(
                "Sorry, we could not get the weather information right now. "
                "E jọwọ, a ko le gba iroyin ojo loni. Please try again later."
            )
        )
        return xml_response(xml)

    english_text = weather_to_english_text(weather)
    logger.info(f"English weather text: {english_text[:100]}...")

    # ── Step 3: Translate to Yoruba ────────────────────────────────────────
    yoruba_text = translate_to_yoruba(
        english_text,
        hf_token=cfg.get("HF_API_TOKEN", "")
    )
    logger.info(f"Yoruba text: {yoruba_text[:100]}...")

    # ── Step 4: Text-to-Speech ─────────────────────────────────────────────
    cloudinary_creds = {
        "cloud_name": cfg.get("CLOUDINARY_CLOUD_NAME", ""),
        "api_key":    cfg.get("CLOUDINARY_API_KEY", ""),
        "api_secret": cfg.get("CLOUDINARY_API_SECRET", ""),
    }

    audio_url = synthesise_yoruba_speech(
        yoruba_text,
        hf_token=cfg.get("HF_API_TOKEN", ""),
        base_url=cfg["BASE_URL"],
        cloudinary_creds=cloudinary_creds,
    )

    # ── Step 5: Build IVR response ─────────────────────────────────────────
    if audio_url:
        logger.info(f"Playing audio: {audio_url}")
        xml = ivr_xml(
            say(f"Here is the weather report for {location_name}."),
            play(audio_url),
            say("Thank you for calling Oro Agbe. E se, agbẹ wa. Goodbye."),
        )
    else:
        # Audio generation failed — read the Yoruba text using AT's TTS as fallback
        logger.warning("Audio generation failed. Reading Yoruba text via AT TTS.")
        xml = ivr_xml(
            say(f"Here is the weather report for {location_name}."),
            say(yoruba_text),
            say("Thank you for calling Oro Agbe. Goodbye."),
        )

    return xml_response(xml)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 4 — HANGUP  (call ended)
# ══════════════════════════════════════════════════════════════════════════════

@ivr_bp.route("/hangup", methods=["GET", "POST"])
def ivr_hangup():
    """Called by Africa's Talking when the call ends. Used for logging."""
    caller   = request.values.get("callerNumber", "unknown")
    duration = request.values.get("durationInSeconds", "0")
    logger.info(f"Call ended: caller={caller}, duration={duration}s")
    return "", 200
