"""
Oro Agbe — Test Suite
Run with: python -m pytest tests/ -v

These tests verify each component independently, including integration
with real external APIs (weather fetch, translation, TTS).
Set environment variables in .env before running integration tests.
"""

import os
import sys
import pytest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure parent directory is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import Config
from app.location_service import phone_to_location, geocode_city, resolve_location
from app.weather_service import get_weather, weather_to_english_text, WeatherData


# ══════════════════════════════════════════════════════════════════════════════
# LOCATION SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationService:

    def test_known_prefix_lagos(self):
        lat, lon, city = phone_to_location("08011234567")
        assert city == "Lagos"
        assert 6.0 < lat < 7.0
        assert 3.0 < lon < 4.0

    def test_known_prefix_ibadan(self):
        lat, lon, city = phone_to_location("08051234567")
        assert city == "Ibadan"

    def test_unknown_prefix_falls_back_to_default(self):
        lat, lon, city = phone_to_location("09991234567")
        assert city == "Ibadan"  # default fallback

    def test_international_format_normalised(self):
        # +2348051234567 should normalise to 08051234567
        lat, lon, city = phone_to_location("+2348051234567")
        assert city == "Ibadan"

    def test_resolve_location_prefers_city_input(self):
        """When a city name is given, it should take priority over phone."""
        with patch("app.location_service.geocode_city") as mock_geo:
            mock_geo.return_value = (9.0, 7.4, "Abuja")
            lat, lon, city = resolve_location(phone_number="08011234567", city_input="Abuja")
        assert city == "Abuja"

    def test_resolve_location_falls_back_to_phone(self):
        with patch("app.location_service.geocode_city") as mock_geo:
            mock_geo.return_value = None
            lat, lon, city = resolve_location(phone_number="08031234567", city_input="UnknownCity")
        assert city == "Lagos"


# ══════════════════════════════════════════════════════════════════════════════
# WEATHER SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestWeatherService:

    MOCK_OPEN_METEO_RESPONSE = {
        "current_weather": {
            "temperature": 31.0,
            "windspeed": 12.0,
            "winddirection": 45.0,
            "weathercode": 2,
        },
        "hourly": {
            "relativehumidity_2m": [72],
            "apparent_temperature": [35.0],
            "uv_index": [6.0],
        },
        "daily": {
            "weathercode": [2, 61],
            "temperature_2m_max": [34.0, 29.0],
            "temperature_2m_min": [24.0, 22.0],
            "precipitation_sum": [0.0, 12.5],
            "windspeed_10m_max": [18.0, 22.0],
            "winddirection_10m_dominant": [45.0, 90.0],
            "sunrise": ["2025-01-01T06:32", "2025-01-02T06:33"],
            "sunset":  ["2025-01-01T18:47", "2025-01-02T18:46"],
        }
    }

    def test_get_weather_success(self):
        with patch("app.weather_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = self.MOCK_OPEN_METEO_RESPONSE
            mock_get.return_value = mock_resp

            weather = get_weather(7.37, 3.94, "Ibadan")

        assert weather is not None
        assert weather.location == "Ibadan"
        assert weather.temperature == 31.0
        assert weather.humidity == 72
        assert weather.weather_condition == "Partly cloudy"

    def test_get_weather_api_failure_returns_none(self):
        with patch("app.weather_service.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")
            result = get_weather(7.37, 3.94, "Ibadan")
        assert result is None

    def test_weather_to_english_text_contains_key_info(self):
        w = WeatherData(
            location="Ibadan",
            temperature=31.0, feels_like=35.0, humidity=72,
            wind_speed=12.0, wind_direction="North-East",
            weather_condition="Partly cloudy",
            precipitation_mm=0.0, uv_index=6.0,
            sunrise="06:32", sunset="18:47",
            forecast_tomorrow="Light rain. High of 29°C.",
            advisory="Good conditions for field work.",
        )
        text = weather_to_english_text(w)

        assert "Ibadan" in text
        assert "31" in text
        assert "72" in text
        assert "North-East" in text
        assert "farmer" in text.lower()

    def test_farming_advisory_thunderstorm(self):
        from app.weather_service import _farming_advisory
        advisory = _farming_advisory({"weathercode": 95, "temperature_2m": 30,
                                       "precipitation_sum": 25, "windspeed_10m_max": 15})
        assert "thunderstorm" in advisory.lower() or "indoors" in advisory.lower()

    def test_farming_advisory_clear_day(self):
        from app.weather_service import _farming_advisory
        advisory = _farming_advisory({"weathercode": 0, "temperature_2m": 28,
                                       "precipitation_sum": 0, "windspeed_10m_max": 10})
        assert "good" in advisory.lower() or "harvest" in advisory.lower() or "field" in advisory.lower()


# ══════════════════════════════════════════════════════════════════════════════
# TRANSLATION SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestTranslationService:

    def test_translate_via_api_success(self):
        from app.translation_service import _translate_via_api

        with patch("app.translation_service.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"translation_text": "Ekaaro, agbẹ wa."}]
            mock_post.return_value = mock_resp

            result = _translate_via_api("Good day, farmer.", "fake-token")

        assert result == "Ekaaro, agbẹ wa."

    def test_translate_via_api_503_retries(self):
        from app.translation_service import _translate_via_api

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            if call_count < 2:
                mock_resp.status_code = 503
                mock_resp.json.return_value = {"estimated_time": 0.1}
            else:
                mock_resp.status_code = 200
                mock_resp.json.return_value = [{"translation_text": "Ekaaro"}]
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("app.translation_service.requests.post", side_effect=side_effect):
            with patch("app.translation_service.time.sleep"):
                result = _translate_via_api("Hello farmer", "fake-token")

        assert result == "Ekaaro"
        assert call_count == 2

    def test_translate_empty_string(self):
        from app.translation_service import translate_to_yoruba
        result = translate_to_yoruba("", hf_token="")
        assert result == ""

    def test_translate_returns_fallback_on_total_failure(self):
        from app.translation_service import translate_to_yoruba
        with patch("app.translation_service._translate_via_api", return_value=None):
            with patch("app.translation_service._translate_locally", return_value=None):
                result = translate_to_yoruba("Hello", hf_token="fake")
        assert len(result) > 0  # Should return fallback Yoruba message


# ══════════════════════════════════════════════════════════════════════════════
# TTS SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestTTSService:

    def test_tts_via_api_success(self, tmp_path):
        from app.tts_service import _tts_via_api, AUDIO_DIR
        import app.tts_service as tts_mod

        fake_wav = b"RIFF" + b"\x00" * 40  # Minimal fake WAV header

        with patch("app.tts_service.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = fake_wav
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            # Redirect output to tmp_path
            original_dir = tts_mod.AUDIO_DIR
            tts_mod.AUDIO_DIR = tmp_path
            try:
                result = _tts_via_api("Ekaaro", "fake-token")
            finally:
                tts_mod.AUDIO_DIR = original_dir

        assert result is not None
        assert result.exists()

    def test_tts_empty_text_returns_none(self):
        from app.tts_service import synthesise_yoruba_speech
        result = synthesise_yoruba_speech("", hf_token="fake")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# IVR HANDLER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestIVRHandler:

    @pytest.fixture
    def client(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app_module import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["BASE_URL"] = "http://localhost:5000"
        app.config["HF_API_TOKEN"] = "fake-token"
        return app.test_client()

    def test_health_endpoint(self):
        """Test the health check endpoint."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        # Import app directly
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.ivr_handler import ivr_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["BASE_URL"] = "http://localhost:5000"
        app.config["HF_API_TOKEN"] = ""
        app.config["CLOUDINARY_CLOUD_NAME"] = ""
        app.config["CLOUDINARY_API_KEY"] = ""
        app.config["CLOUDINARY_API_SECRET"] = ""
        app.register_blueprint(ivr_bp)
        client = app.test_client()

        resp = client.post("/ivr/voice", data={"callerNumber": "+2348031234567"})
        assert resp.status_code == 200
        assert b"Response" in resp.data
        assert b"Oro Agbe" in resp.data or b"weather" in resp.data.lower()

    def test_ivr_action_key_1(self):
        """Pressing 1 should redirect to weather route."""
        from app.ivr_handler import ivr_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["BASE_URL"] = "http://localhost:5000"
        app.config["HF_API_TOKEN"] = ""
        app.config["CLOUDINARY_CLOUD_NAME"] = ""
        app.config["CLOUDINARY_API_KEY"] = ""
        app.config["CLOUDINARY_API_SECRET"] = ""
        app.register_blueprint(ivr_bp)
        client = app.test_client()

        resp = client.post("/ivr/action", data={
            "dtmfDigits": "1",
            "callerNumber": "+2348031234567"
        })
        assert resp.status_code == 200
        assert b"Redirect" in resp.data or b"weather" in resp.data.lower()

    def test_ivr_action_invalid_key(self):
        """Invalid key press should give error message."""
        from app.ivr_handler import ivr_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["BASE_URL"] = "http://localhost:5000"
        app.config["HF_API_TOKEN"] = ""
        app.config["CLOUDINARY_CLOUD_NAME"] = ""
        app.config["CLOUDINARY_API_KEY"] = ""
        app.config["CLOUDINARY_API_SECRET"] = ""
        app.register_blueprint(ivr_bp)
        client = app.test_client()

        resp = client.post("/ivr/action", data={
            "dtmfDigits": "9",
            "callerNumber": "+2348031234567"
        })
        assert resp.status_code == 200
        assert b"Invalid" in resp.data



# ══════════════════════════════════════════════════════════════════════════════
# USSD HANDLER TESTS
# ══════════════════════════════════════════════════════════════════════════════
 
class TestUSSDHandler:
 
    @pytest.fixture
    def ussd_client(self):
        from app.ussd_handler import ussd_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["HF_API_TOKEN"] = ""
        app.config["BASE_URL"] = "http://localhost:5000"
        app.register_blueprint(ussd_bp)
        return app.test_client()
 
    def _post(self, client, text, phone="+2348031234567"):
        return client.post("/ussd/session", data={
            "sessionId":   "test-session-001",
            "serviceCode": "*384*123#",
            "phoneNumber": phone,
            "text":        text,
        })
 
    def test_main_menu_on_empty_input(self, ussd_client):
        resp = self._post(ussd_client, "")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert body.startswith("CON ")
        assert "1" in body    # Option 1 present
        assert "2" in body    # Option 2 present
 
    def test_option_1_triggers_weather(self, ussd_client):
        with patch("app.ussd_handler._get_yoruba_weather") as mock_weather:
            mock_weather.return_value = "Ojo loni dara fun oko. Otutu jẹ iwọn 31."
            resp = self._post(ussd_client, "1")
        body = resp.data.decode()
        assert resp.status_code == 200
        # Should either END (short text) or CON (needs pagination)
        assert body.startswith("END ") or body.startswith("CON ")
        assert "31" in body or "dara" in body
 
    def test_option_2_asks_for_city(self, ussd_client):
        resp = self._post(ussd_client, "2")
        body = resp.data.decode()
        assert resp.status_code == 200
        assert body.startswith("CON ")
        # Should prompt for city name
        assert "ilu" in body.lower() or "city" in body.lower()
 
    def test_option_2_with_city_returns_weather(self, ussd_client):
        with patch("app.ussd_handler._get_yoruba_weather") as mock_weather:
            with patch("app.ussd_handler.geocode_city") as mock_geo:
                mock_weather.return_value = "Ojo ni Ibadan dara loni."
                mock_geo.return_value = (7.37, 3.94, "Ibadan")
                resp = self._post(ussd_client, "2*Ibadan")
        body = resp.data.decode()
        assert resp.status_code == 200
        assert "Ibadan" in body or "dara" in body
 
    def test_unknown_city_shows_error(self, ussd_client):
        with patch("app.ussd_handler.geocode_city", return_value=None):
            with patch("app.ussd_handler._get_yoruba_weather",
                       return_value="A ko le gba iroyin ojo."):
                resp = self._post(ussd_client, "2*UnknownCityXYZ")
        body = resp.data.decode()
        assert resp.status_code == 200
        assert "CON " in body   # Should let them try again
        assert "ko ri" in body or "could not find" in body.lower()
 
    def test_invalid_option_returns_error(self, ussd_client):
        resp = self._post(ussd_client, "9")
        body = resp.data.decode()
        assert body.startswith("END ")
 
    def test_pagination_shows_next_option(self, ussd_client):
        # A very long Yoruba message should trigger pagination
        long_text = "Ojo loni dara " * 30   # ~420 chars — needs 3 pages
        with patch("app.ussd_handler._get_yoruba_weather", return_value=long_text):
            resp = self._post(ussd_client, "1")
        body = resp.data.decode()
        # First page should offer "Next" option
        assert body.startswith("CON ")
        assert "1" in body   # "Next" option
 
    def test_pagination_next_page(self, ussd_client):
        long_text = "Ojo loni dara " * 30
        with patch("app.ussd_handler._get_yoruba_weather", return_value=long_text):
            # User pressed 1 to go to next page
            resp = self._post(ussd_client, "1*1")
        body = resp.data.decode()
        assert resp.status_code == 200
 
    def test_exit_from_weather(self, ussd_client):
        with patch("app.ussd_handler._get_yoruba_weather",
                   return_value="Ojo loni dara " * 30):
            # User pressed 0 to exit pagination
            resp = self._post(ussd_client, "1*0")
        body = resp.data.decode()
        assert body.startswith("END ")
        assert "dupe" in body.lower() or "goodbye" in body.lower()
 
    def test_paginate_short_text(self):
        from app.ussd_handler import _paginate
        text = "Short text"
        page_text, has_more = _paginate(text, 0)
        assert page_text == text
        assert has_more is False
 
    def test_paginate_long_text_splits_correctly(self):
        from app.ussd_handler import _paginate
        long = "word " * 50    # 250 chars
        p0, more0 = _paginate(long, 0, page_size=100)
        p1, more1 = _paginate(long, 1, page_size=100)
        assert len(p0) <= 100
        assert len(p1) <= 100
        assert more0 is True    # More pages after page 0
        assert p0 != p1         # Different content on each page

# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TEST — Full pipeline (requires real API tokens)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestFullPipeline:
    """
    Integration tests that call real external APIs.
    Only run when INTEGRATION_TEST=true in environment.
    Requires: HF_API_TOKEN, valid network access.
    """
 
    @pytest.fixture(autouse=True)
    def skip_without_flag(self):
        if os.getenv("INTEGRATION_TEST", "").lower() != "true":
            pytest.skip("Set INTEGRATION_TEST=true to run integration tests")
 
    def test_weather_fetch_ibadan(self):
        weather = get_weather(7.3775, 3.9470, "Ibadan")
        assert weather is not None
        assert -10 < weather.temperature < 60   # Reasonable temperature
        assert 0 <= weather.humidity <= 100
 
    def test_translation_english_to_yoruba(self):
        from app.translation_service import translate_to_yoruba
        token = os.getenv("HF_API_TOKEN", "")
        result = translate_to_yoruba(
            "Good day, farmer. The weather today is partly cloudy with a temperature of 31 degrees.",
            hf_token=token
        )
        assert result is not None
        assert len(result) > 10
        print(f"\nYoruba translation: {result}")
 
    def test_tts_yoruba_speech(self, tmp_path):
        from app.tts_service import synthesise_yoruba_speech
        import app.tts_service as tts_mod
 
        original_dir = tts_mod.AUDIO_DIR
        tts_mod.AUDIO_DIR = tmp_path
        try:
            url = synthesise_yoruba_speech(
                "Ekaaro, agbẹ wa. Ojo loni dara fun iṣẹ oko.",
                hf_token=os.getenv("HF_API_TOKEN", ""),
                base_url="http://localhost:5000",
            )
        finally:
            tts_mod.AUDIO_DIR = original_dir
 
        assert url is not None
        print(f"\nAudio URL: {url}")






# @pytest.mark.integration
# class TestFullPipeline:
#     """
#     Integration tests that call real external APIs.
#     Only run when INTEGRATION_TEST=true in environment.
#     Requires: HF_API_TOKEN, valid network access.
#     """

#     @pytest.fixture(autouse=True)
#     def skip_without_flag(self):
#         if os.getenv("INTEGRATION_TEST", "").lower() != "true":
#             pytest.skip("Set INTEGRATION_TEST=true to run integration tests")

#     def test_weather_fetch_ibadan(self):
#         weather = get_weather(7.3775, 3.9470, "Ibadan")
#         assert weather is not None
#         assert -10 < weather.temperature < 60   # Reasonable temperature
#         assert 0 <= weather.humidity <= 100

#     def test_translation_english_to_yoruba(self):
#         from app.translation_service import translate_to_yoruba
#         token = os.getenv("HF_API_TOKEN", "")
#         result = translate_to_yoruba(
#             "Good day, farmer. The weather today is partly cloudy with a temperature of 31 degrees.",
#             hf_token=token
#         )
#         assert result is not None
#         assert len(result) > 10
#         print(f"\nYoruba translation: {result}")

#     def test_tts_yoruba_speech(self, tmp_path):
#         from app.tts_service import synthesise_yoruba_speech
#         import app.tts_service as tts_mod

#         original_dir = tts_mod.AUDIO_DIR
#         tts_mod.AUDIO_DIR = tmp_path
#         try:
#             url = synthesise_yoruba_speech(
#                 "Ekaaro, agbẹ wa. Ojo loni dara fun iṣẹ oko.",
#                 hf_token=os.getenv("HF_API_TOKEN", ""),
#                 base_url="http://localhost:5000",
#             )
#         finally:
#             tts_mod.AUDIO_DIR = original_dir

#         assert url is not None
#         print(f"\nAudio URL: {url}")
