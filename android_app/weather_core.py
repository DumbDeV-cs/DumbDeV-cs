"""Open-Meteo geocoding and forecast helpers for the Android build."""
from dataclasses import dataclass
import requests

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

@dataclass
class Place:
    name: str
    admin: str
    country: str
    lat: float
    lon: float
    population: int = 0

    @property
    def label(self):
        return ", ".join(x for x in (self.name, self.admin, self.country) if x)


def get_json(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=12, headers={"User-Agent": "StealthWeatherPro/2.0"})
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError("Can't reach the weather service. Check your connection and try again.") from exc
    except ValueError as exc:
        raise RuntimeError("The weather service sent an unreadable response.") from exc


def search_places(query, count=7):
    base, _, rest = query.partition(",")
    base, rest = base.strip(), rest.strip().casefold()
    if len(base) < 2:
        return []
    data = get_json(GEO, {"name": base, "count": 30, "language": "en", "format": "json"})
    found, seen = [], set()
    for row in data.get("results") or []:
        place = Place(row.get("name", ""), row.get("admin1") or row.get("admin2") or "",
                      row.get("country", ""), row["latitude"], row["longitude"], row.get("population") or 0)
        if place.label not in seen:
            found.append(place)
            seen.add(place.label)
    found.sort(key=lambda p: (0 if rest and rest in p.label.casefold() else 1 if rest else 0,
                              0 if p.name.casefold() == base.casefold() else 1 if p.name.casefold().startswith(base.casefold()) else 2,
                              -(p.population or 0), p.label.casefold()))
    return found[:count]


def forecast(place, units="metric"):
    metric = units == "metric"
    data = get_json(FORECAST, {
        "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 7,
        "temperature_unit": "celsius" if metric else "fahrenheit",
        "wind_speed_unit": "kmh" if metric else "mph",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m,wind_direction_10m",
        "hourly": "temperature_2m,weather_code,is_day,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max",
    })
    try:
        c, h, d = data["current"], data["hourly"], data["daily"]
        hour = c["time"][:13] + ":00"
        start = next((i for i, t in enumerate(h["time"]) if t >= hour), 0)
        hours = [{"time": h["time"][i], "temp": h["temperature_2m"][i], "code": h["weather_code"][i],
                  "day": bool(h["is_day"][i]), "rain": (h.get("precipitation_probability") or [None] * len(h["time"]))[i]}
                 for i in range(start, min(start + 8, len(h["time"]))) ]
        days = [{"date": d["time"][i], "code": d["weather_code"][i], "high": d["temperature_2m_max"][i],
                 "low": d["temperature_2m_min"][i], "uv": d["uv_index_max"][i]} for i in range(len(d["time"]))]
        return {"time": c["time"], "temp": c["temperature_2m"], "feels": c["apparent_temperature"],
                "humidity": c["relative_humidity_2m"], "wind": c["wind_speed_10m"], "direction": c.get("wind_direction_10m"),
                "code": c["weather_code"], "day": bool(c["is_day"]), "sunrise": d["sunrise"][0],
                "sunset": d["sunset"][0], "hours": hours, "days": days}
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The forecast came back incomplete. Please try again.") from exc


def condition(code):
    return {
        0: ("Clear sky", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "clouds"), 3: ("Overcast", "clouds"),
        45: ("Fog", "fog"), 48: ("Freezing fog", "fog"), 51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"),
        55: ("Heavy drizzle", "rain"), 56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
        61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"), 66: ("Freezing rain", "rain"),
        67: ("Freezing rain", "rain"), 71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
        77: ("Snow grains", "snow"), 80: ("Rain showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
        85: ("Snow showers", "snow"), 86: ("Heavy snow showers", "snow"), 95: ("Thunderstorm", "storm"),
        96: ("Thunderstorm and hail", "storm"), 99: ("Thunderstorm and hail", "storm"),
    }.get(code, ("Cloudy", "clouds"))


def emoji(kind, day=True):
    return {"clear": "☀" if day else "☾", "clouds": "☁", "rain": "☂", "snow": "❄", "storm": "ϟ", "fog": "≋"}.get(kind, "☁")
