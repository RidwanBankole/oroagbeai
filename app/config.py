"""
Oro Agbe — Configuration
All environment variables and app settings live here.
Copy .env.example to .env and fill in your values.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Flask ──────────────────────────────────────────────────────────────
    SECRET_KEY = os.getenv("SECRET_KEY", "oro-agbe-dev-secret")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # ── Africa's Talking ───────────────────────────────────────────────────
    AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")          # 'sandbox' for testing
    AT_API_KEY  = os.getenv("AT_API_KEY", "")                  # Get from AT dashboard
    AT_VOICE_NUMBER = os.getenv("AT_VOICE_NUMBER", "")         # Your AT phone number

    # ── Hugging Face ───────────────────────────────────────────────────────
    HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")               # From huggingface.co/settings/tokens
    HF_TRANSLATION_MODEL = os.getenv(
        "HF_TRANSLATION_MODEL",
        "facebook/nllb-200-distilled-600M"                     # Lighter, faster variant
    )
    HF_TTS_MODEL = os.getenv(
        "HF_TTS_MODEL",
        "facebook/mms-tts-yor"                                 # Yoruba TTS
    )

    # ── Cloudinary (audio hosting) ─────────────────────────────────────────
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

    # ── Open-Meteo ────────────────────────────────────────────────────────
    # No API key needed — fully free
    WEATHER_API_BASE = "https://api.open-meteo.com/v1/forecast"
    GEOCODING_API_BASE = "https://nominatim.openstreetmap.org/search"

    # ── Nigerian phone prefix → approximate coordinates ────────────────────
    # Maps network area codes to (lat, lon, city_name)
    # Extend this as needed for more precise coverage
    PHONE_LOCATION_MAP = {
        # Lagos area
        "0801": (6.5244, 3.3792, "Lagos"),
        "0802": (6.5244, 3.3792, "Lagos"),
        "0803": (6.5244, 3.3792, "Lagos"),
        # Ibadan / Oyo
        "0805": (7.3775, 3.9470, "Ibadan"),
        "0807": (7.3775, 3.9470, "Ibadan"),
        # Abuja
        "0806": (9.0765, 7.3986, "Abuja"),
        "0808": (9.0765, 7.3986, "Abuja"),
        # Abeokuta / Ogun
        "0809": (7.1557, 3.3451, "Abeokuta"),
        # Ondo / Akure
        "0810": (7.2526, 5.1932, "Akure"),
        # Osun / Oshogbo
        "0811": (7.7719, 4.5624, "Osogbo"),
        # Default fallback — Ibadan
        "default": (7.3775, 3.9470, "Ibadan"),
    }

    # ── Audio settings ─────────────────────────────────────────────────────
    AUDIO_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
    AUDIO_SAMPLE_RATE = 16000

    # ── App base URL (set this to your ngrok or deployed URL) ──────────────
    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
