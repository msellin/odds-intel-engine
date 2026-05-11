"""
OddsIntel — Fetch Weather

Fetches kickoff-time weather for today's upcoming matches using Open-Meteo
(free, no API key). Stores results in match_weather table for use as model
features (temp, wind, rain affect scoring rates and BTTS).

Flow:
  1. Get today's upcoming matches with venue_af_id
  2. Look up venue city + country from venues table
  3. For venues without lat/lon: geocode via Open-Meteo geocoding API, cache back
  4. For each match: fetch hourly forecast for kickoff hour
  5. Upsert into match_weather

Called from fetch_enrichment pipeline (after venues are fetched).

Usage:
  python -m workers.jobs.fetch_weather
  python -m workers.jobs.fetch_weather --date 2026-05-11
"""

import sys
import argparse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query, execute_write

console = Console()

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10


def _geocode(city: str, country: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city via Open-Meteo geocoding. Returns None on failure."""
    query = f"{city}, {country}" if country else city
    try:
        r = requests.get(
            _GEOCODE_URL,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            return float(results[0]["latitude"]), float(results[0]["longitude"])
    except Exception as e:
        console.print(f"  [yellow]Geocode failed for '{query}': {e}[/yellow]")
    return None


def _fetch_forecast(lat: float, lon: float, kickoff_dt: datetime) -> dict | None:
    """
    Fetch hourly weather from Open-Meteo for the kickoff hour.
    Returns {temp_c, wind_kmh, rain_mm} or None on failure.
    """
    date_str = kickoff_dt.strftime("%Y-%m-%d")
    try:
        r = requests.get(
            _FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "UTC",
                "wind_speed_unit": "kmh",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        console.print(f"  [yellow]Forecast fetch failed ({lat},{lon}): {e}[/yellow]")
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    # Find the index closest to kickoff hour
    target_hour = kickoff_dt.strftime("%Y-%m-%dT%H:00")
    try:
        idx = times.index(target_hour)
    except ValueError:
        # Fallback to closest hour
        target_ts = int(kickoff_dt.replace(minute=0, second=0, microsecond=0).timestamp())
        diffs = []
        for t in times:
            try:
                ts = int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp())
                diffs.append(abs(ts - target_ts))
            except Exception:
                diffs.append(999999)
        idx = diffs.index(min(diffs)) if diffs else 0

    def _get(key):
        vals = hourly.get(key, [])
        return float(vals[idx]) if idx < len(vals) and vals[idx] is not None else None

    return {
        "temp_c": _get("temperature_2m"),
        "wind_kmh": _get("wind_speed_10m"),
        "rain_mm": _get("precipitation"),
        "humidity": _get("relative_humidity_2m"),
    }


def fetch_weather(target_date: str) -> int:
    """Fetch and store kickoff-time weather for all upcoming matches on target_date."""
    console.print(f"\n[cyan]Weather: Fetching kickoff-time weather for {target_date}...[/cyan]")

    next_date = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()

    # Get today's matches with venue_af_id
    matches = execute_query(
        """SELECT m.id, m.date, v.af_id, v.city, v.country, v.lat, v.lon
           FROM matches m
           JOIN venues v ON m.venue_af_id = v.af_id
           WHERE m.date >= %s AND m.date < %s
             AND m.status IN ('not_started', 'scheduled', 'upcoming', 'NS')
           ORDER BY m.date""",
        [f"{target_date}T00:00:00Z", f"{next_date}T00:00:00Z"],
    )

    if not matches:
        console.print("  No matches with venue data — skipping")
        return 0

    console.print(f"  {len(matches)} matches with venue data")

    # Geocode venues that lack lat/lon
    geocoded: dict[int, tuple[float, float]] = {}
    for m in matches:
        vid = m["af_id"]
        if vid in geocoded:
            continue
        if m.get("lat") is not None and m.get("lon") is not None:
            geocoded[vid] = (float(m["lat"]), float(m["lon"]))
        elif m.get("city"):
            coords = _geocode(m["city"], m.get("country", ""))
            if coords:
                geocoded[vid] = coords
                # Cache lat/lon back into venues so we don't re-geocode
                execute_write(
                    "UPDATE venues SET lat = %s, lon = %s WHERE af_id = %s",
                    (coords[0], coords[1], vid),
                )

    stored = 0
    for m in matches:
        vid = m["af_id"]
        coords = geocoded.get(vid)
        if not coords:
            continue

        lat, lon = coords
        kickoff_raw = m.get("date")
        try:
            if isinstance(kickoff_raw, datetime):
                kickoff_dt = kickoff_raw.replace(tzinfo=timezone.utc) if kickoff_raw.tzinfo is None else kickoff_raw
            else:
                kickoff_dt = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
        except Exception:
            continue

        weather = _fetch_forecast(lat, lon, kickoff_dt)
        if not weather:
            continue

        try:
            execute_write(
                """INSERT INTO match_weather (match_id, temp_c, wind_kmh, rain_mm, humidity)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (match_id) DO UPDATE SET
                       temp_c = EXCLUDED.temp_c,
                       wind_kmh = EXCLUDED.wind_kmh,
                       rain_mm = EXCLUDED.rain_mm,
                       humidity = EXCLUDED.humidity""",
                (m["id"], weather["temp_c"], weather["wind_kmh"],
                 weather["rain_mm"], weather["humidity"]),
            )
            stored += 1
        except Exception as e:
            console.print(f"  [yellow]store match_weather failed {m['id']}: {e}[/yellow]")

    console.print(f"  [green]{stored} matches with weather stored[/green]")
    return stored


def run_weather(target_date: str = None, **_kwargs) -> int:
    target_date = target_date or date.today().isoformat()
    return fetch_weather(target_date)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    run_weather(target_date=args.date)
