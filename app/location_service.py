"""
Oro Agbe — Location Service
Maps a caller's phone number to approximate GPS coordinates.

Strategy (in order of preference):
1. Phone prefix → known city mapping (fast, no external call)
2. Geocoding via Nominatim (for city names typed by user)
3. Default fallback (Ibadan, Oyo State)
"""

import re
import logging
import requests
from geopy.geocoders import Nominatim
from typing import Tuple, Optional
from app.config import Config

logger = logging.getLogger(__name__)


def geocode_city(city_name: str) -> Optional[Tuple[float, float, str]]:
    """
    Look up coordinates for a city name using Geopy (OpenStreetMap).
    Biased to Nigeria.

    Returns:
        (lat, lon, display_name) or None if not found.
    """
    geolocator = Nominatim(user_agent="geo_app")
    try: 
        location = geolocator.geocode(city_name)
        lat, lon, name = location.latitude, location.longitude, city_name
        print(f"Coordinate for {name} is : ({lat}, {lon})")
        return lat, lon, name
    
    except Exception as e:
        logger.error(f"Geocoding error for '{city_name}': {e}")
        return None