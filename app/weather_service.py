"""
Oro Agbe — Weather Service
Fetches current weather + 24-hour forecast from Open-Meteo (free, no API key).
Returns structured English text ready for translation.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry-aware HTTP session
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    """
    Build a requests.Session that automatically retries on 429 / 5xx errors
    with exponential backoff.  The Retry class honours the Retry-After header
    that Open-Meteo sends on 429 responses, so we wait exactly as long as the
    API asks rather than hammering it.
    """
    session = requests.Session()
    retry = Retry(
        total=4,                        # up to 4 attempts total
        backoff_factor=2,               # waits: 2 s, 4 s, 8 s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        raise_on_status=False,          # we call raise_for_status() ourselves
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WeatherData:
    location: str
    current_time: str
    is_day: bool
    temperature: float
    feels_like: float
    humidity: int
    wind_speed: float
    wind_direction: str
    weather_condition: str
    current_precipitation: float
    uv_index: float
    today_high: float
    today_low: float
    today_precipitation_mm: float
    today_precipitation_hours: float
    today_wind_max: float
    sunrise: str
    sunset: str
    tomorrow_condition: str
    tomorrow_high: float
    tomorrow_low: float
    tomorrow_precipitation_mm: float
    summary_now: str
    summary_today: str
    advisory: str


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

WIND_DIRECTIONS = [
    "North", "North-East", "East", "South-East",
    "South", "South-West", "West", "North-West",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _wind_direction(degrees: float) -> str:
    idx = round(degrees / 45) % 8
    return WIND_DIRECTIONS[idx]


def _safe_time_only(value: str) -> str:
    if "T" in value:
        return value.split("T")[-1][:5]
    return value[:5]


def _pick_hour_value(
    hourly: Dict[str, List[Any]],
    field: str,
    current_time: str,
    default: float = 0.0,
) -> float:
    """
    Match the hourly value to the current timestamp from Open-Meteo current.time.
    Falls back to the first item only if matching fails.
    """
    times = hourly.get("time", [])
    values = hourly.get(field, [])
    if not values:
        return default
    try:
        idx = times.index(current_time)
        return values[idx]
    except ValueError:
        return values[0] if values else default


# ---------------------------------------------------------------------------
# Summary builders
# ---------------------------------------------------------------------------

def _build_now_summary(
    condition: str,
    temp: float,
    feels_like: float,
    humidity: int,
    wind_speed: float,
    is_day: bool,
) -> str:
    parts = []
    day_phrase = "daytime" if is_day else "night-time"
    parts.append(f"{condition} during the {day_phrase}")
    if feels_like >= temp + 2:
        parts.append(f"feels warmer than the measured {temp:.0f}°C")
    elif feels_like <= temp - 2:
        parts.append(f"feels cooler than the measured {temp:.0f}°C")
    else:
        parts.append(f"temperature is close to {temp:.0f}°C")
    if humidity >= 85:
        parts.append("air is very humid")
    elif humidity <= 35:
        parts.append("air is fairly dry")
    if wind_speed >= 35:
        parts.append("winds are strong")
    elif wind_speed >= 20:
        parts.append("winds are noticeable")
    return ". ".join(parts).strip() + "."


def _build_today_summary(
    today_condition: str,
    high: float,
    low: float,
    precip_mm: float,
    precip_hours: float,
    wind_max: float,
) -> str:
    parts = [
        f"Today will be {today_condition.lower()}",
        f"with a high of {high:.0f}°C and a low of {low:.0f}°C",
    ]
    if precip_mm >= 20:
        parts.append(f"heavy rainfall is likely, around {precip_mm:.1f} mm")
    elif precip_mm >= 5:
        parts.append(f"some rain is expected, around {precip_mm:.1f} mm")
    elif precip_mm > 0:
        parts.append(f"only light rainfall is expected, around {precip_mm:.1f} mm")
    else:
        parts.append("little or no rainfall is expected")
    if precip_hours >= 6:
        parts.append(f"wet conditions may last for about {precip_hours:.0f} hours")
    if wind_max >= 40:
        parts.append("strong winds may affect outdoor work")
    elif wind_max >= 25:
        parts.append("breezy conditions are expected")
    return ", ".join(parts) + "."


def _farming_advisory(
    weather_code: int,
    temperature: float,
    feels_like: float,
    humidity: int,
    current_precipitation: float,
    daily_precipitation: float,
    daily_precipitation_hours: float,
    wind_speed: float,
    uv_index: float,
) -> str:
    advice = []
    thunder = weather_code in {95, 96, 99}
    rainy = weather_code in {51, 53, 55, 61, 63, 65, 80, 81, 82}
    heavy_rain = daily_precipitation >= 20 or daily_precipitation_hours >= 8
    hot = temperature >= 35 or feels_like >= 38
    windy = wind_speed >= 35
    strong_uv = uv_index >= 8

    if thunder:
        advice.append(
            "Thunderstorm risk is high. Avoid field work and move indoors when clouds begin to build."
        )
    elif heavy_rain:
        advice.append(
            "Heavy rain may disrupt field work. Protect low-lying plots and improve drainage where possible."
        )
    elif rainy:
        advice.append(
            "Rain is likely. Delay pesticide or fertiliser application to reduce wash-off."
        )
    elif daily_precipitation < 1:
        advice.append(
            "Conditions look mostly dry. Irrigation may be needed for moisture-sensitive crops."
        )
    if hot:
        advice.append(
            "Heat stress is possible. Water crops early and reduce strenuous afternoon work."
        )
    if windy:
        advice.append(
            "Strong winds may damage light covers or young plants. Secure protective materials."
        )
    if humidity >= 90 and temperature >= 28:
        advice.append(
            "Warm and humid conditions may favour fungal growth. Monitor crops closely."
        )
    if strong_uv and daily_precipitation < 1:
        advice.append(
            "Sun intensity will be high. Newly transplanted crops may need extra moisture attention."
        )
    if current_precipitation > 0:
        advice.append(
            "Rain is already falling now, so postpone any spraying or harvesting that requires dry conditions."
        )
    if not advice:
        return "Conditions are generally favourable for routine farm activities today."
    return " ".join(advice)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_weather(lat: float, lon: float, location_name: str) -> Optional[WeatherData]:
    """
    Fetch and interpret weather from Open-Meteo.

    Uses a retry-aware session so transient 429 / 5xx errors are handled
    automatically with exponential backoff before giving up.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Africa/Lagos",
        "forecast_days": 2,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m,wind_direction_10m,is_day"
        ),
        "hourly": "uv_index",
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_hours,wind_speed_10m_max,"
            "sunrise,sunset"
        ),
    }
    try:
        logger.info("Fetching weather for %s (%s, %s)", location_name, lat, lon)
        session = _make_session()
        response = session.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            timeout=15,          # slightly longer to allow for retry waits
        )
        response.raise_for_status()
        data = response.json()

        current = data["current"]
        daily = data["daily"]
        hourly = data.get("hourly", {})

        today = {k: v[0] for k, v in daily.items()}
        tomorrow = (
            {k: v[1] for k, v in daily.items()}
            if len(daily["weather_code"]) > 1
            else today
        )

        current_time = current["time"]
        weather_code_now = current["weather_code"]
        weather_condition_now = WMO_CODES.get(weather_code_now, "Variable conditions")
        weather_condition_tomorrow = WMO_CODES.get(
            tomorrow["weather_code"], "Variable conditions"
        )

        uv_index = _pick_hour_value(hourly, "uv_index", current_time, default=0.0)
        sunrise = _safe_time_only(today["sunrise"])
        sunset = _safe_time_only(today["sunset"])

        summary_now = _build_now_summary(
            condition=weather_condition_now,
            temp=current["temperature_2m"],
            feels_like=current["apparent_temperature"],
            humidity=current["relative_humidity_2m"],
            wind_speed=current["wind_speed_10m"],
            is_day=bool(current["is_day"]),
        )
        summary_today = _build_today_summary(
            today_condition=weather_condition_now,
            high=today["temperature_2m_max"],
            low=today["temperature_2m_min"],
            precip_mm=today["precipitation_sum"],
            precip_hours=today["precipitation_hours"],
            wind_max=today["wind_speed_10m_max"],
        )
        advisory = _farming_advisory(
            weather_code=weather_code_now,
            temperature=current["temperature_2m"],
            feels_like=current["apparent_temperature"],
            humidity=current["relative_humidity_2m"],
            current_precipitation=current["precipitation"],
            daily_precipitation=today["precipitation_sum"],
            daily_precipitation_hours=today["precipitation_hours"],
            wind_speed=current["wind_speed_10m"],
            uv_index=uv_index,
        )

        return WeatherData(
            location=location_name,
            current_time=current_time,
            is_day=bool(current["is_day"]),
            temperature=current["temperature_2m"],
            feels_like=current["apparent_temperature"],
            humidity=current["relative_humidity_2m"],
            wind_speed=current["wind_speed_10m"],
            wind_direction=_wind_direction(current["wind_direction_10m"]),
            weather_condition=weather_condition_now,
            current_precipitation=current["precipitation"],
            uv_index=uv_index,
            today_high=today["temperature_2m_max"],
            today_low=today["temperature_2m_min"],
            today_precipitation_mm=today["precipitation_sum"],
            today_precipitation_hours=today["precipitation_hours"],
            today_wind_max=today["wind_speed_10m_max"],
            sunrise=sunrise,
            sunset=sunset,
            tomorrow_condition=weather_condition_tomorrow,
            tomorrow_high=tomorrow["temperature_2m_max"],
            tomorrow_low=tomorrow["temperature_2m_min"],
            tomorrow_precipitation_mm=tomorrow["precipitation_sum"],
            summary_now=summary_now,
            summary_today=summary_today,
            advisory=advisory,
        )

    except requests.RequestException as exc:
        logger.error("Weather API error: %s", exc)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("Weather parsing error: %s", exc)
        return None


def weather_to_english_text(w: WeatherData) -> str:
    """Render the weather in a clean, useful style."""
    return (
        f"Weather report for {w.location}. "
        f"Right now, it is {w.weather_condition.lower()} with a temperature of {w.temperature:.0f}°C, "
        f"feeling like {w.feels_like:.0f}°C. "
        f"Humidity is {w.humidity}% and wind is blowing from the {w.wind_direction} "
        f"at {w.wind_speed:.0f} km/h. "
        f"Current rainfall is {w.current_precipitation:.1f} mm and UV index is {w.uv_index:.0f}. "
        f"{w.summary_now} "
        f"{w.summary_today} "
        f"Sunrise is at {w.sunrise} and sunset is at {w.sunset}. "
        f"Tomorrow, expect {w.tomorrow_condition.lower()} with a high of {w.tomorrow_high:.0f}°C, "
        f"a low of {w.tomorrow_low:.0f}°C, and about {w.tomorrow_precipitation_mm:.1f} mm of rainfall. "
        f"Farming advice: {w.advisory}"
    )


def weather_to_structured_text(w: WeatherData) -> str:
    """Optional alternative renderer for apps, WhatsApp bots, or TTS systems."""
    return (
        f"Location: {w.location}\n"
        f"Now: {w.weather_condition}, {w.temperature:.0f}°C, feels like {w.feels_like:.0f}°C.\n"
        f"Humidity: {w.humidity}%\n"
        f"Wind: {w.wind_direction}, {w.wind_speed:.0f} km/h\n"
        f"Rain now: {w.current_precipitation:.1f} mm\n"
        f"UV index: {w.uv_index:.0f}\n"
        f"Today: High {w.today_high:.0f}°C, Low {w.today_low:.0f}°C, "
        f"Rain {w.today_precipitation_mm:.1f} mm, Wet hours {w.today_precipitation_hours:.0f}\n"
        f"Sunrise: {w.sunrise}, Sunset: {w.sunset}\n"
        f"Tomorrow: {w.tomorrow_condition}, High {w.tomorrow_high:.0f}°C, "
        f"Low {w.tomorrow_low:.0f}°C, Rain {w.tomorrow_precipitation_mm:.1f} mm\n"
        f"Summary: {w.summary_today}\n"
        f"Advice: {w.advisory}"
    )