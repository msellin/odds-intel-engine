"""
OddsIntel — Weather Backfill

Backfills match_weather for all finished matches with a venue_af_id.

Four-phase flow:
  1.  Seed venue city/country from AF /venues endpoint for venues missing city
  1b. Re-fetch ungeocodeable venues from AF to get address (used in Nominatim fallback)
  2.  Geocode venues missing lat/lon — Open-Meteo first, Nominatim fallback
  3.  Fetch historical weather from Open-Meteo archive API per venue+date range,
      then upsert into match_weather for each match's kickoff hour

Open-Meteo archive API is free, no key required. Handles past dates back to 1940.
Nominatim (OpenStreetMap) is free, no key required. Rate limit: 1 req/s.

Usage:
  python3 scripts/backfill_weather.py
  python3 scripts/backfill_weather.py --dry-run   # count only, no writes
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from psycopg2.extras import execute_values
from workers.api_clients.api_football import get_venue, parse_venue
from workers.api_clients.db import execute_query, execute_write, get_conn
from workers.api_clients.supabase_client import store_venues

console = Console()

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_TIMEOUT = 15
_NOMINATIM_HEADERS = {"User-Agent": "OddsIntelEngine/1.0 geocoding@oddsintell.com"}


# ─── Phase 1: seed city from AF ─────────────────────────────────────────────

def _seed_venue_cities(dry_run: bool) -> int:
    rows = execute_query(
        "SELECT af_id FROM venues WHERE city IS NULL AND af_id IS NOT NULL"
    )
    if not rows:
        console.print("  All venues already have city — skipping AF fetch")
        return 0

    console.print(f"  {len(rows)} venues missing city — fetching from AF")
    if dry_run:
        return len(rows)

    fetched = []
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("venues", total=len(rows))
        for row in rows:
            try:
                raw = get_venue(row["af_id"])
                if raw:
                    fetched.append(parse_venue(raw))
            except Exception as e:
                console.print(f"  [yellow]AF venue {row['af_id']} failed: {e}[/yellow]")
            progress.advance(task)
            time.sleep(0.07)  # ~14 req/s, well within 900/min

    stored = store_venues(fetched)
    console.print(f"  {stored} venues updated with city/country/address")
    return stored


# ─── Phase 1b: backfill address for ungeocodeable venues ────────────────────

def _seed_venue_addresses(dry_run: bool) -> int:
    """Re-fetch venues that have city but no coords and no address yet.
    Address enables Nominatim fallback geocoding in Phase 2.
    """
    rows = execute_query(
        "SELECT af_id FROM venues WHERE city IS NOT NULL AND lat IS NULL AND address IS NULL"
    )
    if not rows:
        console.print("  No ungeocodeable venues missing address — skipping")
        return 0

    console.print(f"  {len(rows)} ungeocodeable venues — fetching address from AF")
    if dry_run:
        return len(rows)

    fetched = []
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("venues", total=len(rows))
        for row in rows:
            try:
                raw = get_venue(row["af_id"])
                if raw:
                    fetched.append(parse_venue(raw))
            except Exception as e:
                console.print(f"  [yellow]AF venue {row['af_id']} failed: {e}[/yellow]")
            progress.advance(task)
            time.sleep(0.07)

    stored = store_venues(fetched)
    console.print(f"  {stored} venues updated with address")
    return stored


# ─── Phase 2: geocode lat/lon ────────────────────────────────────────────────

def _clean_location(city: str, country: str) -> tuple[str, str]:
    """Normalise AF location strings that trip up geocoders."""
    # Strip parenthetical content: "Masazyr (Masazir)" → "Masazyr"
    city = re.sub(r'\s*\(.*?\)', '', city).strip()
    # Strip comma-separated suffixes: "Durban, KN" or "Derby, Derbyshire" → first part
    city = city.split(',')[0].strip()
    # Fix hyphenated country names: "Czech-Republic" → "Czech Republic"
    country = country.replace('-', ' ') if country else country
    return city, country


def _geocode_open_meteo(city: str, country: str) -> tuple[float, float] | None:
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
        console.print(f"  [yellow]Open-Meteo geocode failed for '{query}': {e}[/yellow]")
    return None


def _geocode_nominatim(venue_name: str | None, address: str | None,
                       city: str, country: str) -> tuple[float, float] | None:
    """Nominatim fallback — tries venue name then street address."""
    candidates = []
    if venue_name:
        candidates.append(f"{venue_name}, {city}, {country}")
    if address:
        candidates.append(f"{address}, {city}, {country}")

    for q in candidates:
        try:
            r = requests.get(
                _NOMINATIM_URL,
                params={"q": q, "format": "json", "limit": 1},
                headers=_NOMINATIM_HEADERS,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            results = r.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception as e:
            console.print(f"  [yellow]Nominatim failed for '{q}': {e}[/yellow]")
        time.sleep(1.1)  # Nominatim requires max 1 req/s

    return None


def _geocode_venues(dry_run: bool) -> dict[int, tuple[float, float]]:
    """Return {af_id: (lat, lon)} for all venues, geocoding those missing coords."""
    rows = execute_query(
        "SELECT af_id, name, city, country, address, lat, lon FROM venues WHERE city IS NOT NULL"
    )
    coords: dict[int, tuple[float, float]] = {}
    to_geocode = []
    for r in rows:
        if r.get("lat") is not None and r.get("lon") is not None:
            coords[r["af_id"]] = (float(r["lat"]), float(r["lon"]))
        else:
            to_geocode.append(r)

    if not to_geocode:
        console.print(f"  {len(coords)} venues already geocoded — skipping")
        return coords

    console.print(f"  {len(to_geocode)} venues need geocoding")
    if dry_run:
        return coords

    resolved = 0
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("geocoding", total=len(to_geocode))
        for r in to_geocode:
            city, country = _clean_location(r["city"] or "", r.get("country") or "")

            # Try Open-Meteo first (city-level, fast)
            c = _geocode_open_meteo(city, country)

            # Nominatim fallback using venue name / address
            if not c:
                c = _geocode_nominatim(
                    r.get("name"), r.get("address"), city, country
                )

            if c:
                coords[r["af_id"]] = c
                execute_write(
                    "UPDATE venues SET lat = %s, lon = %s WHERE af_id = %s",
                    (c[0], c[1], r["af_id"]),
                )
                resolved += 1
            progress.advance(task)

    console.print(f"  {len(coords)} venues with coords after geocoding ({resolved} newly resolved)")
    return coords


# ─── Phase 3: fetch historical weather and store ─────────────────────────────

def _fetch_archive(lat: float, lon: float, start_date: str, end_date: str) -> dict[str, dict] | None:
    """
    Fetch hourly weather from Open-Meteo archive for a date range.
    Returns {datetime_str: {temp_c, wind_kmh, rain_mm, humidity}} or None.
    datetime_str format: "2024-08-15T19:00" (UTC, no tz suffix).
    """
    try:
        r = requests.get(
            _ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m",
                "timezone": "UTC",
                "wind_speed_unit": "kmh",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        console.print(f"  [yellow]Archive fetch failed ({lat:.2f},{lon:.2f}): {e}[/yellow]")
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    temps = hourly.get("temperature_2m", [])
    winds = hourly.get("wind_speed_10m", [])
    rains = hourly.get("precipitation", [])
    hums = hourly.get("relative_humidity_2m", [])

    result = {}
    for i, t in enumerate(times):
        result[t] = {
            "temp_c": float(temps[i]) if i < len(temps) and temps[i] is not None else None,
            "wind_kmh": float(winds[i]) if i < len(winds) and winds[i] is not None else None,
            "rain_mm": float(rains[i]) if i < len(rains) and rains[i] is not None else None,
            "humidity": float(hums[i]) if i < len(hums) and hums[i] is not None else None,
        }
    return result


def _backfill_weather(coords: dict[int, tuple[float, float]], dry_run: bool) -> int:
    """
    For each venue with coords, fetch one archive call covering all its match dates,
    then upsert match_weather for each match's kickoff hour.
    """
    matches = execute_query(
        """SELECT m.id, m.date, m.venue_af_id
           FROM matches m
           LEFT JOIN match_weather mw ON mw.match_id = m.id
           WHERE m.status = 'finished'
             AND m.venue_af_id IS NOT NULL
             AND mw.match_id IS NULL
           ORDER BY m.venue_af_id, m.date"""
    )

    by_venue: dict[int, list[dict]] = defaultdict(list)
    skipped_no_coords = 0
    for m in matches:
        vid = m["venue_af_id"]
        if vid not in coords:
            skipped_no_coords += 1
            continue
        by_venue[vid].append(m)

    total_matches = sum(len(v) for v in by_venue.values())
    console.print(
        f"  {len(by_venue)} venues to archive-fetch, "
        f"{total_matches} matches, {skipped_no_coords} skipped (no coords)"
    )

    if dry_run or not by_venue:
        return total_matches

    stored = 0
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("venues", total=len(by_venue))

        for vid, venue_matches in by_venue.items():
            lat, lon = coords[vid]

            dates = []
            for m in venue_matches:
                raw_date = m["date"]
                if isinstance(raw_date, datetime):
                    dates.append(raw_date.date().isoformat())
                else:
                    dates.append(str(raw_date)[:10])

            start_date = min(dates)
            end_date = max(dates)

            archive = _fetch_archive(lat, lon, start_date, end_date)
            if not archive:
                progress.advance(task)
                continue

            rows_to_store = []
            for m in venue_matches:
                raw_dt = m["date"]
                try:
                    if isinstance(raw_dt, datetime):
                        kickoff_dt = raw_dt if raw_dt.tzinfo else raw_dt.replace(tzinfo=timezone.utc)
                    else:
                        kickoff_dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
                except Exception:
                    continue

                hour_key = kickoff_dt.strftime("%Y-%m-%dT%H:00")
                w = archive.get(hour_key)
                if not w:
                    for delta in (-1, 1, -2, 2):
                        alt_dt = kickoff_dt.replace(minute=0, second=0, microsecond=0)
                        alt_key = (alt_dt.replace(tzinfo=timezone.utc) +
                                   timedelta(hours=delta)).strftime("%Y-%m-%dT%H:00")
                        if alt_key in archive:
                            w = archive[alt_key]
                            break

                if w:
                    rows_to_store.append((m["id"], w["temp_c"], w["wind_kmh"], w["rain_mm"], w["humidity"]))

            if rows_to_store:
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            execute_values(
                                cur,
                                """INSERT INTO match_weather (match_id, temp_c, wind_kmh, rain_mm, humidity)
                                   VALUES %s
                                   ON CONFLICT (match_id) DO UPDATE SET
                                       temp_c = EXCLUDED.temp_c,
                                       wind_kmh = EXCLUDED.wind_kmh,
                                       rain_mm = EXCLUDED.rain_mm,
                                       humidity = EXCLUDED.humidity""",
                                rows_to_store,
                            )
                        conn.commit()
                    stored += len(rows_to_store)
                except Exception as e:
                    console.print(f"  [yellow]batch store failed venue {vid}: {e}[/yellow]")

            progress.advance(task)
            time.sleep(1.0)  # Open-Meteo free tier rate limit

    return stored


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    console.print("\n[bold cyan]Backfill match_weather[/bold cyan]")

    console.print("\n[cyan]Phase 1 — Seed venue city from AF[/cyan]")
    _seed_venue_cities(dry_run)

    console.print("\n[cyan]Phase 1b — Backfill address for ungeocodeable venues[/cyan]")
    _seed_venue_addresses(dry_run)

    console.print("\n[cyan]Phase 2 — Geocode venues (Open-Meteo + Nominatim fallback)[/cyan]")
    coords = _geocode_venues(dry_run)

    console.print("\n[cyan]Phase 3 — Fetch historical weather[/cyan]")
    stored = _backfill_weather(coords, dry_run)

    action = "would store" if dry_run else "stored"
    console.print(f"\n[bold green]Done — {action} weather for {stored} matches[/bold green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
