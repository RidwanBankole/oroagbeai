import os
import time
import threading
import requests
import logging
from flask import Flask, request, jsonify
from app.ussd_handler import ussd_bp
from app.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def keep_alive():
    def ping():
        time.sleep(60)
        while True:
            base_url = os.getenv("BASE_URL", "")
            if base_url:
                try:
                    resp = requests.get(f"{base_url}/health", timeout=10)
                    logger.info(f"Keep-alive ping → {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Keep-alive ping failed: {e}")
            time.sleep(600)

    thread = threading.Thread(target=ping, name="keep-alive", daemon=True)
    thread.start()
    logger.info("Keep-alive thread started")


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    
    from flask_cors import CORS
    CORS(app, resources={r"/pipeline": {"origins": "*"}})
    
    app.config.from_object(Config)
    app.register_blueprint(ussd_bp)

    @app.route("/health")
    def health():
        return {"status": "ok", "project": "Oro Agbe - Farmer's Matter"}, 200

    @app.route("/pipeline", methods=["POST"])
    def pipeline():
        """
        Full pipeline: city → geocode → weather → translate → TTS
        Request JSON:  { "city": "Ibadan" }
        Response JSON: { "location", "english_text", "yoruba_text", "audio_url",
                         "weather": { condition, temperature, feels_like, humidity,
                                      wind_speed, wind_direction, today_high,
                                      today_low, sunrise, sunset,
                                      tomorrow_condition, tomorrow_high,
                                      tomorrow_low, advisory } }
        """
        data = request.get_json(silent=True) or {}
        city = (data.get("city") or "").strip()

        if not city:
            return jsonify({"error": "city is required"}), 400

        # Step 1 — geocode
        try:
            from app.location_service import geocode_city
            result = geocode_city(city_name=city)
            if not result:
                return jsonify({"error": f"Could not resolve location for '{city}'"}), 404
            lat, lon, location_name = result
        except Exception as e:
            logger.error("Geocode error: %s", e)
            return jsonify({"error": "Location lookup failed"}), 500

        # Step 2 — weather
        try:
            from app.weather_service import get_weather, weather_to_english_text
            weather = get_weather(lat, lon, location_name)
            if not weather:
                return jsonify({"error": "Weather fetch failed"}), 502
            english_text = weather_to_english_text(weather)
        except Exception as e:
            logger.error("Weather error: %s", e)
            return jsonify({"error": "Weather fetch failed"}), 500

        # Step 3 — translate
        try:
            from app.translation_service import translate_to_yoruba
            yoruba_text = translate_to_yoruba(english_text)
            if not yoruba_text:
                return jsonify({"error": "Translation returned empty"}), 502
        except Exception as e:
            logger.error("Translation error: %s", e)
            return jsonify({"error": "Translation failed"}), 500

        # Step 4 — TTS
        try:
            from app.tts_service import synthesise_yoruba_speech
            cloudinary_creds = {
                "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
                "api_key":    os.getenv("CLOUDINARY_API_KEY", ""),
                "api_secret": os.getenv("CLOUDINARY_API_SECRET", ""),
            }
            has_cloudinary = all(cloudinary_creds.values())
            base_url       = os.getenv("BASE_URL", "http://localhost:5000")
            audio_url      = synthesise_yoruba_speech(
                yoruba_text,
                base_url=base_url,
                cloudinary_creds=cloudinary_creds if has_cloudinary else None,
            )
        except Exception as e:
            logger.error("TTS error: %s", e)
            audio_url = None   # TTS failure is non-fatal — return text results

        return jsonify({
            "location":     location_name,
            "english_text": english_text,
            "yoruba_text":  yoruba_text,
            "audio_url":    audio_url,
            "weather": {
                "condition":         weather.weather_condition,
                "temperature":       weather.temperature,
                "feels_like":        weather.feels_like,
                "humidity":          weather.humidity,
                "wind_speed":        weather.wind_speed,
                "wind_direction":    weather.wind_direction,
                "today_high":        weather.today_high,
                "today_low":         weather.today_low,
                "sunrise":           weather.sunrise,
                "sunset":            weather.sunset,
                "tomorrow_condition": weather.tomorrow_condition,
                "tomorrow_high":     weather.tomorrow_high,
                "tomorrow_low":      weather.tomorrow_low,
                "advisory":          weather.advisory,
            },
        }), 200

    if os.getenv("ENABLE_KEEP_ALIVE", "false").lower() == "true":
        keep_alive()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)