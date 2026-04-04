"""
Oro Agbe — Local Pipeline Test CLI
Run this script to test the full pipeline on your machine
without needing a real phone call.

Usage:
    python test_pipeline.py
    python test_pipeline.py --phone 08031234567
    python test_pipeline.py --city "Ibadan"
    python test_pipeline.py --weather-only
    python test_pipeline.py --translate-only "Good day farmer, it is sunny today."
"""

import os
import sys
import argparse
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

    hf_token = os.getenv("HF_API_TOKEN", "")
    base_url  = os.getenv("BASE_URL", "http://localhost:5000")

    if not hf_token:
        warn("HF_API_TOKEN not set. Will attempt local model mode.")

    #  Step 1: Location 
    step(1, "Resolving location from city input")
    from app.location_service import geocode_city
    lat, lon, location_name = geocode_city(city_name=city)
    ok(f"Location → {location_name}  ({lat}, {lon})")

    # Step 2: Weather 
    step(2, "Fetching weather from Open-Meteo API")
    from app.weather_service import get_weather, weather_to_english_text
    weather = get_weather(lat, lon, location_name)
    if not weather:
        fail("Weather fetch failed. Check your internet connection.")
        sys.exit(1)

    english_text = weather_to_english_text(weather)
    print(f"\n{BOLD}English text:{RESET}\n{english_text}\n")

    # Step 3: Translation
    step(3, "Translating English → Yoruba  (NLLB-200)")
    from app.translation_service import translate_to_yoruba
    yoruba_text = translate_to_yoruba(english_text)
    if not yoruba_text:
        fail("Translation returned empty string.")
        sys.exit(1)
    ok(f"Translation complete.")
    print(f"\n{BOLD}Yoruba text:{RESET}\n{yoruba_text}\n")

    # ── Step 4: TTS ─────────────────────────────────────────────────────────
    step(4, "Synthesising Yoruba speech  (MMS-TTS-YOR)")
    from app.tts_service import synthesise_yoruba_speech
    audio_url = synthesise_yoruba_speech(
        yoruba_text,
        base_url=base_url,
    )
    if audio_url:
        ok(f"Audio ready: {audio_url}")
        # Try to play it if a player is available
        local_path = audio_url.replace(f"{base_url}/static/audio/", "static/audio/")
        if Path(local_path).exists():
            try:
                import subprocess
                subprocess.Popen(["aplay", local_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok("Playing audio... (aplay)")
            except Exception:
                pass
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


def run_weather_only(city: str = "Ibadan"):
    banner()
    step(1, f"Weather only for: {city}")
    from app.location_service import geocode_city, phone_to_location
    from app.weather_service import get_weather, weather_to_english_text

    result = geocode_city(city)
    if result:
        lat, lon, name = result
    else:
        lat, lon, name = phone_to_location("08051234567")

    weather = get_weather(lat, lon, name)
    if weather:
        ok(f"{name}: {weather.temperature}°C, {weather.weather_condition}")
        print(f"\n{weather_to_english_text(weather)}")
    else:
        fail("Could not fetch weather.")


def run_translate_only(text: str):
    banner()
    step(1, "Translation only")
    hf_token = os.getenv("HF_API_TOKEN", "")
    from app.translation_service import translate_to_yoruba
    result = translate_to_yoruba(text, hf_token=hf_token)
    ok(f"Input:  {text}")
    ok(f"Output: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Oro Agbe Pipeline Tester")
    parser.add_argument("--phone", default="08031234567", help="Caller phone number")
    parser.add_argument("--city", default="", help="City name override")
    parser.add_argument("--weather-only", action="store_true", help="Test weather fetch only")
    parser.add_argument("--translate-only", metavar="TEXT", help="Test translation only")
    args = parser.parse_args()

    if args.translate_only:
        run_translate_only(args.translate_only)
    elif args.weather_only:
        run_weather_only(city=args.city or "Ibadan")
    else:
        enter_city = input("Enter the city name: ")
        run_full_pipeline(city=enter_city)
