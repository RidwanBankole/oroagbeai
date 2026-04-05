"""
Oro Agbe — Local Pipeline Test CLI
Run this script to test the full pipeline on your machine
without needing a real phone call.
Usage:
    python test_pipeline.py
"""
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def banner():
    print(f"""
            ORO AGBE — PIPELINE TEST
            Farmer's Matter | Oko L'owó
""")


def step(n, label):
    print(f"\n{CYAN}{BOLD}[Step {n}]{RESET} {label}")
    print("─" * 50)


def ok(msg):
    print(f"{GREEN}✓ {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}⚠ {msg}{RESET}")


def fail(msg):
    print(f"{RED}✗ {msg}{RESET}")


def run_full_pipeline(city: str = ""):
    banner()

    base_url = os.getenv("BASE_URL", "http://localhost:5000")

    cloudinary_creds = {
        "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
        "api_key":    os.getenv("CLOUDINARY_API_KEY", ""),
        "api_secret": os.getenv("CLOUDINARY_API_SECRET", ""),
    }

    has_cloudinary = all(cloudinary_creds.values())
    if not has_cloudinary:
        warn("Cloudinary credentials not fully set. Audio will fall back to local URL.")

    # ── Step 1: Location ─────────────────────────────────────────────────────
    step(1, "Resolving location from city input")
    from app.location_service import geocode_city

    result = geocode_city(city_name=city)
    if not result:
        fail(f"Could not geocode '{city}'. Check city name and internet connection.")
        sys.exit(1)

    lat, lon, location_name = result
    ok(f"Location → {location_name}  ({lat}, {lon})")

    # ── Step 2: Weather ──────────────────────────────────────────────────────
    step(2, "Fetching weather from wttr.in")
    from app.weather_service import get_weather, weather_to_english_text

    weather = get_weather(lat, lon, location_name)
    if not weather:
        fail("Weather fetch failed. Check your internet connection.")
        sys.exit(1)

    english_text = weather_to_english_text(weather)
    ok("Weather fetched successfully.")
    print(f"\n{BOLD}English text:{RESET}\n{english_text}\n")

    # ── Step 3: Translation ──────────────────────────────────────────────────
    step(3, "Translating English → Yoruba (Groq llama-3.3-70b-versatile)")
    from app.translation_service import translate_to_yoruba

    yoruba_text = translate_to_yoruba(english_text)
    if not yoruba_text:
        fail("Translation returned empty string.")
        sys.exit(1)

    ok("Translation complete.")
    print(f"\n{BOLD}Yoruba text:{RESET}\n{yoruba_text}\n")

    # ── Step 4: TTS ──────────────────────────────────────────────────────────
    step(4, "Synthesising Yoruba speech (MMS-TTS-YOR → Cloudinary)")
    from app.tts_service import synthesise_yoruba_speech

    audio_url = synthesise_yoruba_speech(
        yoruba_text,
        base_url=base_url,
        cloudinary_creds=cloudinary_creds if has_cloudinary else None,
    )

    if audio_url:
        ok(f"Audio ready: {audio_url}")
    else:
        warn("Audio generation failed. TTS step skipped.")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{GREEN}{BOLD}{'═'*50}")
    print("  Pipeline completed successfully! 🎉")
    print(f"{'═'*50}{RESET}\n")
    print(f"  Location:   {location_name}")
    print(f"  Weather:    {weather.weather_condition}, {weather.temperature}°C")
    print(f"  Translated: {yoruba_text[:80]}...")
    if audio_url:
        print(f"  Audio:      {audio_url}")
    print()


if __name__ == "__main__":
    banner()
    city = input("Enter the city name: ").strip()
    if not city:
        print(f"{RED}✗ No city entered. Exiting.{RESET}")
        sys.exit(1)
    run_full_pipeline(city=city)