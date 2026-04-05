"""
Oro Agbe — Weather Service
Fetches current weather + 2-day forecast from wttr.in (free, no API key, no rate limits).
Returns structured English text ready for translation.
Timeout strategy:
  Africa's Talking kills USSD sessions after ~5 s, so we must respond fast.
  - connect_timeout = 3 s  (fail fast if the host is unreachable)
  - read_timeout    = 5 s  (fail fast if the host stalls)
  No retries on this layer — the USSD handler's background fetch loop
  handles retries safely outside the 5-second AT window.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

# Hard timeouts: (connect, read) — must keep total well under AT's 5 s limit
_TIMEOUT = (3, 5)

# ---------------------------------------------------------------------------
# Data model — unchanged so all callers stay compatible
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
# wttr.in weather code → English condition
# wttr.in uses WWO codes (similar to WMO but not identical)
# ---------------------------------------------------------------------------
WWO_CODES = {
    113: "Clear sky",
    116: "Partly cloudy",
    119: "Overcast",
    122: "Overcast",
    143: "Fog",
    176: "Light rain showers",
    179: "Light snow showers",
    182: "Light sleet",
    185: "Light sleet",
    200: "Thunderstorm",
    227: "Light snow",
    230: "Heavy snow",
    248: "Fog",
    260: "Fog",
    263: "Light drizzle",
    266: "Light drizzle",
    281: "Light freezing drizzle",
    284: "Dense freezing drizzle",
    293: "Light rain",
    296: "Light rain",
    299: "Moderate rain",
    302: "Moderate rain",
    305: "Heavy rain",
    308: "Heavy rain",
    311: "Light freezing rain",
    314: "Moderate freezing rain",
    317: "Light sleet",
    320: "Moderate sleet",
    323: "Light snow",
    326: "Moderate snow",
    329: "Heavy snow",
    332: "Heavy snow",
    335: "Heavy snow",
    338: "Heavy snow",
    350: "Light sleet",
    353: "Light rain showers",
    356: "Moderate rain showers",
    359: "Violent rain showers",
    362: "Light sleet showers",
    365: "Moderate sleet showers",
    368: "Light snow showers",
    371: "Heavy snow showers",
    374: "Light sleet showers",
    377: "Moderate sleet showers",
    386: "Thunderstorm with light rain",
    389: "Thunderstorm with heavy rain",
    392: "Thunderstorm with light snow",
    395: "Thunderstorm with heavy snow",
}

WIND_DIRECTIONS = [
    "North", "North-East", "East", "South-East",
    "South", "South-West", "West", "North-West",
]

# wttr.in wind direction strings → our standard labels
_WTTR_WIND_MAP = {
    "N":   "North",
    "NNE": "North-East",
    "NE":  "North-East",
    "ENE": "East",
    "E":   "East",
    "ESE": "East",
    "SE":  "South-East",
    "SSE": "South-East",
    "S":   "South",
    "SSW": "South-West",
    "SW":  "South-West",
    "WSW": "West",
    "W":   "West",
    "WNW": "West",
    "NW":  "North-West",
    "NNW": "North-West",
}

# Thunderstorm WWO codes
_THUNDER_CODES = {200, 386, 389, 392, 395}
# Rainy WWO codes
_RAIN_CODES    = {176, 263, 266, 293, 296, 299, 302, 305, 308, 353, 356, 359}


# ---------------------------------------------------------------------------
# Helpers — unchanged signatures
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
    times  = hourly.get("time", [])
    values = hourly.get(field, [])
    if not values:
        return default
    try:
        idx = times.index(current_time)
        return values[idx]
    except ValueError:
        return values[0] if values else default


def _build_now_summary(
    condition: str, temp: float, feels_like: float,
    humidity: int, wind_speed: float, is_day: bool,
) -> str:
    parts = []
    parts.append(f"{condition} during the {'daytime' if is_day else 'night-time'}")
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
    today_condition: str, high: float, low: float,
    precip_mm: float, precip_hours: float, wind_max: float,
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
    weather_code: int, temperature: float, feels_like: float,
    humidity: int, current_precipitation: float,
    daily_precipitation: float, daily_precipitation_hours: float,
    wind_speed: float, uv_index: float,
) -> str:
    advice     = []
    thunder    = weather_code in _THUNDER_CODES
    rainy      = weather_code in _RAIN_CODES
    heavy_rain = daily_precipitation >= 20 or daily_precipitation_hours >= 8
    hot        = temperature >= 35 or feels_like >= 38
    windy      = wind_speed >= 35
    strong_uv  = uv_index >= 8

    if thunder:
        advice.append("Thunderstorm risk is high. Avoid field work and move indoors when clouds begin to build.")
    elif heavy_rain:
        advice.append("Heavy rain may disrupt field work. Protect low-lying plots and improve drainage where possible.")
    elif rainy:
        advice.append("Rain is likely. Delay pesticide or fertiliser application to reduce wash-off.")
    elif daily_precipitation < 1:
        advice.append("Conditions look mostly dry. Irrigation may be needed for moisture-sensitive crops.")
    if hot:
        advice.append("Heat stress is possible. Water crops early and reduce strenuous afternoon work.")
    if windy:
        advice.append("Strong winds may damage light covers or young plants. Secure protective materials.")
    if humidity >= 90 and temperature >= 28:
        advice.append("Warm and humid conditions may favour fungal growth. Monitor crops closely.")
    if strong_uv and daily_precipitation < 1:
        advice.append("Sun intensity will be high. Newly transplanted crops may need extra moisture attention.")
    if current_precipitation > 0:
        advice.append("Rain is already falling now, so postpone any spraying or harvesting that requires dry conditions.")
    if not advice:
        return "Conditions are generally favourable for routine farm activities today."
    return " ".join(advice)


# ---------------------------------------------------------------------------
# wttr.in parsing helpers
# ---------------------------------------------------------------------------
def _wttr_condition(wwo_code: int) -> str:
    return WWO_CODES.get(wwo_code, "Variable conditions")


def _wttr_wind_dir(compass: str) -> str:
    return _WTTR_WIND_MAP.get(compass.upper(), "North")


def _wttr_precip_hours(hourly_list: list) -> float:
    """Count hours in the day where precipitation > 0."""
    return sum(
        1 for h in hourly_list
        if float(h.get("precipMM", 0)) > 0
    )


def _parse_time_hhmm(hhmm: str) -> str:
    """
    wttr.in returns sunrise/sunset as '06:23 AM' or '07:00 PM'.
    Convert to 24-hour HH:MM for consistency with the rest of the app.
    """
    try:
        from datetime import datetime
        return datetime.strptime(hhmm.strip(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        return hhmm[:5]


# ---------------------------------------------------------------------------
# Public API — same signature as before
# ---------------------------------------------------------------------------
def get_weather(lat: float, lon: float, location_name: str) -> Optional[WeatherData]:
    """
    Fetch weather from wttr.in (free, no API key, no rate limits).
    Returns None immediately on any error — no retries.
    """
    url    = f"https://wttr.in/{lat},{lon}"
    params = {"format": "j1"}   # j1 = full JSON response

    try:
        logger.info("Fetching weather for %s (%s, %s)", location_name, lat, lon)
        response = requests.get(
            url,
            params=params,
            timeout=_TIMEOUT,
            headers={"User-Agent": "OroAgbe-FarmWeatherApp/1.0"},
        )
        response.raise_for_status()
        data = response.json()

        current_cond  = data["current_condition"][0]
        today_data    = data["weather"][0]
        tomorrow_data = data["weather"][1] if len(data["weather"]) > 1 else today_data

        # --- current values ---
        temperature   = float(current_cond["temp_C"])
        feels_like    = float(current_cond["FeelsLikeC"])
        humidity      = int(current_cond["humidity"])
        wind_speed    = float(current_cond["windspeedKmph"])
        wind_dir_str  = _wttr_wind_dir(current_cond["winddir16Point"])
        wwo_code_now  = int(current_cond["weatherCode"])
        condition_now = _wttr_condition(wwo_code_now)
        precip_now    = float(current_cond["precipMM"])
        uv_index      = float(current_cond.get("uvIndex", 0))

        # wttr.in does not expose is_day directly — infer from observation time
        obs_time      = current_cond.get("localObsDateTime", "")
        is_day        = True
        try:
            hour = int(obs_time.split(" ")[1].split(":")[0])
            ampm = obs_time.split(" ")[2] if len(obs_time.split(" ")) > 2 else "AM"
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            is_day = 6 <= hour < 19
        except (IndexError, ValueError):
            is_day = True

        current_time  = obs_time or "N/A"

        # --- today values ---
        today_high    = float(today_data["maxtempC"])
        today_low     = float(today_data["mintempC"])
        today_hourly  = today_data.get("hourly", [])
        today_precip  = sum(float(h.get("precipMM", 0)) for h in today_hourly)
        today_precip_hours = _wttr_precip_hours(today_hourly)
        today_wind_max = max(
            (float(h.get("windspeedKmph", 0)) for h in today_hourly),
            default=wind_speed,
        )
        sunrise       = _parse_time_hhmm(today_data["astronomy"][0]["sunrise"])
        sunset        = _parse_time_hhmm(today_data["astronomy"][0]["sunset"])

        # today's dominant condition = first hourly entry's code
        wwo_today     = int(today_hourly[0]["weatherCode"]) if today_hourly else wwo_code_now

        # --- tomorrow values ---
        tmrw_hourly   = tomorrow_data.get("hourly", [])
        wwo_tmrw      = int(tmrw_hourly[0]["weatherCode"]) if tmrw_hourly else wwo_code_now
        condition_tmrw = _wttr_condition(wwo_tmrw)
        tmrw_high     = float(tomorrow_data["maxtempC"])
        tmrw_low      = float(tomorrow_data["mintempC"])
        tmrw_precip   = sum(float(h.get("precipMM", 0)) for h in tmrw_hourly)

        return WeatherData(
            location=location_name,
            current_time=current_time,
            is_day=is_day,
            temperature=temperature,
            feels_like=feels_like,
            humidity=humidity,
            wind_speed=wind_speed,
            wind_direction=wind_dir_str,
            weather_condition=condition_now,
            current_precipitation=precip_now,
            uv_index=uv_index,
            today_high=today_high,
            today_low=today_low,
            today_precipitation_mm=today_precip,
            today_precipitation_hours=today_precip_hours,
            today_wind_max=today_wind_max,
            sunrise=sunrise,
            sunset=sunset,
            tomorrow_condition=condition_tmrw,
            tomorrow_high=tmrw_high,
            tomorrow_low=tmrw_low,
            tomorrow_precipitation_mm=tmrw_precip,
            summary_now=_build_now_summary(
                condition=condition_now,
                temp=temperature,
                feels_like=feels_like,
                humidity=humidity,
                wind_speed=wind_speed,
                is_day=is_day,
            ),
            summary_today=_build_today_summary(
                today_condition=_wttr_condition(wwo_today),
                high=today_high,
                low=today_low,
                precip_mm=today_precip,
                precip_hours=today_precip_hours,
                wind_max=today_wind_max,
            ),
            advisory=_farming_advisory(
                weather_code=wwo_code_now,
                temperature=temperature,
                feels_like=feels_like,
                humidity=humidity,
                current_precipitation=precip_now,
                daily_precipitation=today_precip,
                daily_precipitation_hours=today_precip_hours,
                wind_speed=wind_speed,
                uv_index=uv_index,
            ),
        )

    except requests.RequestException as exc:
        logger.error("Weather API error: %s", exc)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("Weather parsing error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Text formatters — unchanged
# ---------------------------------------------------------------------------
def weather_to_english_text(w: WeatherData) -> str:
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


if __name__ == "__main__":
    import json

    # Test coordinates — Ibadan, Osogbo, Ife, Iragbiji
    test_cities = [
        (7.3786064, 3.8969928, "Ibadan"),
        (7.7827994, 4.5417680, "Osogbo"),
        (7.4834240, 4.5593440, "Ife"),
        (7.9167000, 4.8333000, "Iragbiji"),
    ]

    for lat, lon, name in test_cities:
        print(f"\n{'='*60}")
        print(f"Testing: {name} ({lat}, {lon})")
        print('='*60)

        weather = get_weather(lat, lon, name)

        if weather is None:
            print(f"FAILED: get_weather() returned None for {name}")
            continue

        print(f"get_weather() SUCCESS")
        print(f"\n--- WeatherData fields ---")
        print(f"Location       : {weather.location}")
        print(f"Current time   : {weather.current_time}")
        print(f"Is day         : {weather.is_day}")
        print(f"Temperature    : {weather.temperature}°C")
        print(f"Feels like     : {weather.feels_like}°C")
        print(f"Humidity       : {weather.humidity}%")
        print(f"Wind           : {weather.wind_direction} at {weather.wind_speed} km/h")
        print(f"Condition      : {weather.weather_condition}")
        print(f"Precipitation  : {weather.current_precipitation} mm")
        print(f"UV index       : {weather.uv_index}")
        print(f"Today high/low : {weather.today_high}°C / {weather.today_low}°C")
        print(f"Today rain     : {weather.today_precipitation_mm} mm over {weather.today_precipitation_hours} hrs")
        print(f"Today wind max : {weather.today_wind_max} km/h")
        print(f"Sunrise/Sunset : {weather.sunrise} / {weather.sunset}")
        print(f"Tomorrow       : {weather.tomorrow_condition}, {weather.tomorrow_high}°C / {weather.tomorrow_low}°C, {weather.tomorrow_precipitation_mm} mm")

        print(f"\n--- Summary now ---")
        print(weather.summary_now)

        print(f"\n--- Summary today ---")
        print(weather.summary_today)

        print(f"\n--- Farming advisory ---")
        print(weather.advisory)

        print(f"\n--- weather_to_english_text() ---")
        print(weather_to_english_text(weather))

        print(f"\n--- weather_to_structured_text() ---")
        print(weather_to_structured_text(weather))