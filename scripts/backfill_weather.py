"""
OddsIntel — Weather Backfill

Backfills match_weather for all finished matches with a venue_af_id.

Five-phase flow:
  0.  Discover venues referenced by matches but not yet in our venues table — fetch from AF
  1.  Seed venue city/country from AF /venues endpoint for venues missing city
  1b. Re-fetch ungeocodeable venues from AF to get address (used in Nominatim fallback)
  2.  Geocode venues missing lat/lon:
        a. Open-Meteo geocoding API (city-level, fast, free)
        b. Nominatim / OpenStreetMap fallback (venue name + address, free, 1 req/s)
        c. Gemini AI batch fallback (single prompt for all remaining — city-level OK for weather)
  3.  Fetch historical weather from Open-Meteo archive API per venue+date range,
      then upsert into match_weather for each match's kickoff hour

Open-Meteo archive API is free, no key required. Handles past dates back to 1940.
Nominatim (OpenStreetMap) is free, no key required. Rate limit: 1 req/s.
Gemini AI batch requires GEMINI_API_KEY.

Usage:
  python3 scripts/backfill_weather.py
  python3 scripts/backfill_weather.py --dry-run   # count only, no writes
"""

from __future__ import annotations

import argparse
import json
import os
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
_GEMINI_MODEL = "gemini-2.5-flash"


# ─── Phase 0: discover unknown venues from matches ──────────────────────────

def _discover_missing_venues(dry_run: bool) -> int:
    """Fetch venues referenced by matches but not yet in our venues table."""
    rows = execute_query(
        """SELECT DISTINCT m.venue_af_id
           FROM matches m
           LEFT JOIN venues v ON v.af_id = m.venue_af_id
           WHERE m.venue_af_id IS NOT NULL AND v.af_id IS NULL"""
    )
    if not rows:
        console.print("  All match venues already in venues table — skipping")
        return 0

    console.print(f"  {len(rows)} venues in matches but not in venues table — fetching from AF")
    if dry_run:
        return len(rows)

    fetched = []
    no_data = []
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("venues", total=len(rows))
        for row in rows:
            try:
                raw = get_venue(row["venue_af_id"])
                if raw:
                    fetched.append(parse_venue(raw))
                else:
                    no_data.append(row["venue_af_id"])
            except Exception as e:
                console.print(f"  [yellow]AF venue {row['venue_af_id']} failed: {e}[/yellow]")
                no_data.append(row["venue_af_id"])
            progress.advance(task)
            time.sleep(0.07)

    stored = store_venues(fetched)

    # Insert placeholder rows for venues AF has no data on so Phase 0 skips them next run
    if no_data:
        store_venues([{"af_id": vid} for vid in no_data])
        console.print(f"  {len(no_data)} venues had no AF data — placeholder inserted")

    console.print(f"  {stored} new venues added")
    return stored


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


def _geocode_ai_batch(venues: list[dict]) -> dict[int, tuple[float, float]]:
    """
    Single Gemini prompt for all remaining ungeocodeable venues.
    Returns {af_id: (lat, lon)} for those the model knows.
    Venues where the model is uncertain are omitted (not null-padded).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        console.print("  [yellow]GEMINI_API_KEY not set — skipping AI geocoding[/yellow]")
        return {}

    try:
        from google import genai
    except ImportError:
        console.print("  [yellow]google-genai not installed — skipping AI geocoding[/yellow]")
        return {}

    venue_lines = "\n".join(
        json.dumps({
            "af_id": v["af_id"],
            "name": v.get("name") or "",
            "city": v.get("city") or "",
            "country": (v.get("country") or "").replace("-", " "),
            "address": v.get("address") or "",
        })
        for v in venues
    )

    prompt = f"""You are a geocoding assistant for football/soccer venues.

For each venue below return its latitude and longitude.
- City-level accuracy is fine — we only need weather data, not routing.
- If you genuinely don't know a specific venue, use the centre of its city.
- Only return null if you don't know the city either.
- Return ONLY a JSON array, no markdown, no explanation.
- Format: [{{"af_id": 123, "lat": 51.5074, "lon": -0.1278}}, ...]

Venues:
{venue_lines}"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt)
        raw = response.text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
        data = json.loads(raw)
    except Exception as e:
        console.print(f"  [yellow]AI geocoding failed: {e}[/yellow]")
        return {}

    results: dict[int, tuple[float, float]] = {}
    for item in data:
        af_id = item.get("af_id")
        lat = item.get("lat")
        lon = item.get("lon")
        if af_id and lat is not None and lon is not None:
            # Basic sanity check — valid coordinate range
            if -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180:
                results[int(af_id)] = (float(lat), float(lon))

    return results


def _geocode_venues(dry_run: bool) -> dict[int, tuple[float, float]]:
    """Return {af_id: (lat, lon)} for all venues, geocoding those missing coords.

    Fallback chain: Open-Meteo → Nominatim → Gemini AI (single batch prompt).
    """
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

    still_missing = []
    resolved = 0
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("geocoding", total=len(to_geocode))
        for r in to_geocode:
            city, country = _clean_location(r["city"] or "", r.get("country") or "")

            c = _geocode_open_meteo(city, country)
            source = "open_meteo"

            if not c:
                c = _geocode_nominatim(r.get("name"), r.get("address"), city, country)
                source = "nominatim"

            if c:
                coords[r["af_id"]] = c
                execute_write(
                    "UPDATE venues SET lat = %s, lon = %s, geocode_source = %s WHERE af_id = %s",
                    (c[0], c[1], source, r["af_id"]),
                )
                resolved += 1
            else:
                still_missing.append(r)
            progress.advance(task)

    # AI batch for everything Open-Meteo + Nominatim couldn't resolve
    if still_missing:
        console.print(f"  {len(still_missing)} venues still unresolved — trying AI batch")
        ai_coords = _geocode_ai_batch(still_missing)
        if ai_coords:
            console.print(f"  AI resolved {len(ai_coords)} venues")
            for r in still_missing:
                c = ai_coords.get(r["af_id"])
                if c:
                    coords[r["af_id"]] = c
                    execute_write(
                        "UPDATE venues SET lat = %s, lon = %s, geocode_source = %s WHERE af_id = %s",
                        (c[0], c[1], "ai", r["af_id"]),
                    )
                    resolved += 1

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

    console.print("\n[cyan]Phase 0 — Discover venues missing from venues table[/cyan]")
    _discover_missing_venues(dry_run)

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

    # Coverage summary
    total_finished = execute_query("SELECT COUNT(*) as n FROM matches WHERE status = 'finished'")[0]["n"]
    with_weather = execute_query("SELECT COUNT(*) as n FROM match_weather")[0]["n"]
    no_venue = execute_query(
        "SELECT COUNT(*) as n FROM matches WHERE status = 'finished' AND venue_af_id IS NULL"
    )[0]["n"]
    venue_no_coords = execute_query(
        """SELECT COUNT(*) as n FROM matches m
           JOIN venues v ON v.af_id = m.venue_af_id
           LEFT JOIN match_weather mw ON mw.match_id = m.id
           WHERE m.status = 'finished' AND v.lat IS NULL AND mw.match_id IS NULL"""
    )[0]["n"]
    unknown_venue = execute_query(
        """SELECT COUNT(*) as n FROM matches m
           LEFT JOIN venues v ON v.af_id = m.venue_af_id
           LEFT JOIN match_weather mw ON mw.match_id = m.id
           WHERE m.status = 'finished' AND m.venue_af_id IS NOT NULL
             AND v.af_id IS NULL AND mw.match_id IS NULL"""
    )[0]["n"]
    console.print(f"\n[bold]Coverage summary[/bold]")
    console.print(f"  Finished matches:        {total_finished:>7}")
    console.print(f"  With weather:            {with_weather:>7}  ({with_weather/total_finished*100:.1f}%)")
    console.print(f"  Gap — no venue_af_id:    {no_venue:>7}  (permanent, AF data missing)")
    console.print(f"  Gap — venue no coords:   {venue_no_coords:>7}  (venue in DB but ungeocodeable)")
    console.print(f"  Gap — venue not in DB:   {unknown_venue:>7}  (re-run to discover)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
