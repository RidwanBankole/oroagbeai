"""
Oro Agbe — IVR Handler
Africa's Talking IVR webhook endpoints.

Timing contract (mirrors the USSD handler pattern):
  Africa's Talking IVR has a short response timeout (~5-8 s). The full
  pipeline (geocode → weather → translate → TTS → Cloudinary) can take
  20-40 s, so we NEVER run it inside a request handler.

  Instead:

  1. /ivr/voice     — Farmer dials. Greet + show menu. Instant response.
  2. /ivr/action    — DTMF received. Kick off background pipeline for the
                      chosen city. Immediately play a hold message and
                      redirect to /ivr/status to poll for completion.
  3. /ivr/status    — Polling endpoint. If audio is ready → play it.
                      If still processing → play hold music and redirect
                      back to itself (up to MAX_POLLS times).
  4. /ivr/hangup    — Call ended. Logging only.

  Background pipeline retries up to 3 × with 10 s gaps, writing the
  finished audio URL to a shared /tmp JSON cache (same pattern as USSD).

Africa's Talking IVR XML docs:
  https://developers.africastalking.com/docs/voice/
"""

import hashlib
import json
import logging
import os
import threading
import time
from urllib.parse import urlencode

from flask import Blueprint, request, current_app

from app.location_service import geocode_city
from app.weather_service import get_weather, weather_to_english_text
from app.translation_service import translate_to_yoruba
from app.tts_service import synthesise_yoruba_speech

logger = logging.getLogger(__name__)

ivr_bp = Blueprint("ivr", __name__, url_prefix="/ivr")

# How many times /ivr/status will loop before giving up
MAX_POLLS = 6
# Seconds between each poll redirect (AT pauses this long playing hold audio)
POLL_INTERVAL_SECONDS = 8

# ── Audio cache (shared across Gunicorn workers via /tmp) ─────────────────────

_CACHE_DIR = "/tmp/oro_agbe_ivr_cache"
_CACHE_TTL = 900   # 15 minutes

os.makedirs(_CACHE_DIR, exist_ok=True)

# Track cities currently being processed so we don't launch duplicate threads
_PROCESSING: set[str] = set()
_PROCESSING_LOCK = threading.Lock()


def _cache_key(city: str) -> str:
    return hashlib.md5(city.strip().lower().encode()).hexdigest()


def _cache_path(city: str) -> str:
    return os.path.join(_CACHE_DIR, f"{_cache_key(city)}.json")


def _get_cached_audio_url(city: str) -> str | None:
    """Return a cached audio URL for *city* if it exists and is still fresh."""
    path = _cache_path(city)
    try:
        with open(path) as fh:
            item = json.load(fh)
        if time.time() - item["ts"] < _CACHE_TTL:
            logger.info("IVR cache hit for %s", city)
            return item["url"]
        os.remove(path)
    except FileNotFoundError:
        pass
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        logger.warning("IVR cache read error for %s: %s", city, exc)
    return None


def _set_cached_audio_url(city: str, url: str) -> None:
    """Persist audio *url* for *city* to the shared /tmp cache."""
    path = _cache_path(city)
    try:
        with open(path, "w") as fh:
            json.dump({"url": url, "ts": time.time()}, fh)
        logger.info("IVR cache written for %s", city)
    except OSError as exc:
        logger.warning("IVR cache write error for %s: %s", city, exc)


def _is_processing(city: str) -> bool:
    with _PROCESSING_LOCK:
        return city.strip().lower() in _PROCESSING


# ── Background pipeline ────────────────────────────────────────────────────────

def _run_pipeline(city: str, base_url: str, cloudinary_creds: dict) -> None:
    """
    Full pipeline: geocode → weather → translate → TTS → cache audio URL.
    Runs in a daemon thread. Retries weather fetch up to 3 times with 10 s gap.
    """
    try:
        geocoded = geocode_city(city_name=city)
        if not geocoded:
            logger.warning("IVR pipeline: could not geocode '%s'", city)
            return

        lat, lon, location_name = geocoded
        logger.info("IVR pipeline: weather for %s (%s, %s)", location_name, lat, lon)

        weather = None
        for attempt in range(1, 4):
            weather = get_weather(lat, lon, location_name)
            if weather:
                break
            logger.warning("IVR pipeline attempt %d failed for %s, retrying...", attempt, city)
            time.sleep(10)

        if not weather:
            logger.error("IVR pipeline: all weather attempts failed for %s", city)
            return

        english_text = weather_to_english_text(weather)
        yoruba_text  = translate_to_yoruba(english_text)
        if not yoruba_text:
            logger.error("IVR pipeline: translation failed for %s", city)
            return

        audio_url = synthesise_yoruba_speech(
            yoruba_text,
            base_url=base_url,
            cloudinary_creds=cloudinary_creds,
        )
        if audio_url:
            _set_cached_audio_url(city, audio_url)
            logger.info("IVR pipeline complete for %s: %s", city, audio_url)
        else:
            logger.error("IVR pipeline: TTS failed for %s", city)

    except Exception:
        logger.exception("IVR pipeline crashed for %s", city)
    finally:
        with _PROCESSING_LOCK:
            _PROCESSING.discard(city.strip().lower())


def _trigger_pipeline(city: str, base_url: str, cloudinary_creds: dict) -> None:
    """Launch the background pipeline unless one is already running for *city*."""
    key = city.strip().lower()
    with _PROCESSING_LOCK:
        if key in _PROCESSING:
            logger.info("IVR pipeline already running for %s", city)
            return
        _PROCESSING.add(key)

    t = threading.Thread(
        target=_run_pipeline,
        args=(city, base_url, cloudinary_creds),
        daemon=True,
    )
    t.start()
    logger.info("IVR pipeline thread started for %s", city)


# ── Startup pre-warm ──────────────────────────────────────────────────────────

PRESET_CITIES = ["Ibadan", "Osogbo", "Ife", "Iragbiji"]


def prewarm_ivr_cities(base_url: str, cloudinary_creds: dict) -> None:
    """
    Kick off background pipelines for all preset cities at startup.
    Call this once from your app factory after the Flask app is created:

        from app.ivr_handler import prewarm_ivr_cities
        prewarm_ivr_cities(app.config["BASE_URL"], {...cloudinary_creds...})
    """
    for city in PRESET_CITIES:
        if not _get_cached_audio_url(city):
            logger.info("IVR pre-warming cache for %s", city)
            _trigger_pipeline(city, base_url, cloudinary_creds)
            time.sleep(2)   # gentle spacing
        else:
            logger.info("IVR pre-warm skipped (cache warm): %s", city)


# ── XML helpers ───────────────────────────────────────────────────────────────

def xml_response(content: str) -> tuple:
    return content, 200, {"Content-Type": "text/xml; charset=utf-8"}


def say(text: str, voice: str = "woman") -> str:
    return f'<Say voice="{voice}">{text}</Say>'


def play(url: str) -> str:
    return f'<Play url="{url}"/>'


def ivr_xml(*elements: str) -> str:
    inner = "\n  ".join(elements)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  {inner}\n</Response>'


# ── Route 1 — Entry point ─────────────────────────────────────────────────────

@ivr_bp.route("/voice", methods=["GET", "POST"])
def voice_entry():
    """
    Farmer dials. Greet and present menu.
    This does NO slow work — instant response.
    """
    caller  = request.values.get("callerNumber", "unknown")
    session = request.values.get("sessionId", "")
    logger.info("Incoming call: caller=%s, session=%s", caller, session)

    action_url = current_app.config["BASE_URL"] + "/ivr/action"

    xml = ivr_xml(
        f'<GetDigits numDigits="1" timeout="15" callbackUrl="{action_url}">'
        f'{say("E kaabo si Oro Agbe. Welcome to Oro Agbe, the farmer weather service. "
               "Press 1 for Ibadan. "
               "Press 2 for Osogbo. "
               "Press 3 for Ife. "
               "Press 4 for Iragbiji. "
               "Press 0 to repeat this message.")}'
        f'</GetDigits>',
        say("We did not receive your input. Please call back and try again. Goodbye.")
    )
    return xml_response(xml)


# ── Route 2 — Action (DTMF received) ─────────────────────────────────────────

@ivr_bp.route("/action", methods=["GET", "POST"])
def ivr_action():
    """
    DTMF key press received. Kick off the background pipeline immediately,
    then redirect to /ivr/status to poll for completion.
    This handler must return in << 5 seconds — it does no slow work itself.
    """
    digit  = request.values.get("dtmfDigits", "").strip()
    caller = request.values.get("callerNumber", "unknown")
    logger.info("DTMF received: digit='%s', caller=%s", digit, caller)

    city_map = {"1": "Ibadan", "2": "Osogbo", "3": "Ife", "4": "Iragbiji"}

    if digit == "0":
        entry_url = current_app.config["BASE_URL"] + "/ivr/voice"
        return xml_response(ivr_xml(f'<Redirect>{entry_url}</Redirect>'))

    city = city_map.get(digit)
    if not city:
        return xml_response(ivr_xml(
            say("Invalid option. Please call back and press 1 to 4 for weather. Goodbye.")
        ))

    cfg = current_app.config
    cloudinary_creds = {
        "cloud_name": cfg.get("CLOUDINARY_CLOUD_NAME", ""),
        "api_key":    cfg.get("CLOUDINARY_API_KEY", ""),
        "api_secret": cfg.get("CLOUDINARY_API_SECRET", ""),
    }

    # Kick off background pipeline (no-op if already running or cache is warm)
    _trigger_pipeline(city, cfg["BASE_URL"], cloudinary_creds)

    # Redirect immediately to the polling status endpoint
    status_url = (
        cfg["BASE_URL"]
        + "/ivr/status?"
        + urlencode({"city": city, "poll": 1})
    )
    return xml_response(ivr_xml(
        say(f"Please hold while we prepare the weather report for {city}."),
        f'<Redirect>{status_url}</Redirect>',
    ))


# ── Route 3 — Status (polling loop) ──────────────────────────────────────────

@ivr_bp.route("/status", methods=["GET", "POST"])
def ivr_status():
    """
    Polling endpoint called repeatedly by Africa's Talking via <Redirect>.

    - If audio is ready  → play it and close the call.
    - If still loading   → play a short hold message and redirect back here,
                           incrementing the poll counter each time.
    - If MAX_POLLS hit   → fall back to AT's own TTS reading the Yoruba text,
                           or a sorry message if translation also failed.
    """
    city      = request.values.get("city", "Ibadan")
    poll      = int(request.values.get("poll", 1))
    caller    = request.values.get("callerNumber", "")
    cfg       = current_app.config

    logger.info("IVR status poll=%d for city=%s caller=%s", poll, city, caller)

    audio_url = _get_cached_audio_url(city)

    if audio_url:
        # ── Audio ready — play and close ──────────────────────────────────
        logger.info("IVR audio ready for %s: %s", city, audio_url)
        xml = ivr_xml(
            say(f"Here is the weather report for {city}."),
            play(audio_url),
            say("Thank you for calling Oro Agbe. E se, agbẹ wa. Goodbye."),
        )
        return xml_response(xml)

    if poll >= MAX_POLLS:
        # ── Gave up waiting — apologise and hang up ───────────────────────
        logger.warning("IVR max polls reached for %s", city)
        xml = ivr_xml(
            say(
                f"We are sorry, the weather report for {city} is not ready yet. "
                "Please call back in a minute. E dupe fun pipe. Goodbye."
            )
        )
        return xml_response(xml)

    # ── Still processing — hold and poll again ────────────────────────────
    next_poll  = poll + 1
    status_url = (
        cfg["BASE_URL"]
        + "/ivr/status?"
        + urlencode({"city": city, "poll": next_poll})
    )

    # Play ~8 s of hold audio so AT waits before following the redirect.
    # If you have a real hold-music URL set HOLD_AUDIO_URL in your config.
    hold_audio = cfg.get("HOLD_AUDIO_URL", "")

    if hold_audio:
        hold_element = play(hold_audio)
    else:
        hold_element = say(
            "Your weather report is being prepared. Please continue to hold."
        )

    xml = ivr_xml(
        hold_element,
        f'<Redirect>{status_url}</Redirect>',
    )
    return xml_response(xml)


# ── Route 4 — Hangup ─────────────────────────────────────────────────────────

@ivr_bp.route("/hangup", methods=["GET", "POST"])
def ivr_hangup():
    """Called by Africa's Talking when the call ends. Used for logging only."""
    caller   = request.values.get("callerNumber", "unknown")
    duration = request.values.get("durationInSeconds", "0")
    logger.info("Call ended: caller=%s, duration=%ss", caller, duration)
    return "", 200