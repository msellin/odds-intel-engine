"""
OddsIntel — WC2026 analytical insight articles (WC-E3-E4)

Generates four Gemini-powered analytical posts that reference our actual
model numbers (Monte Carlo `p_advance` / `p_winner`, international ELO,
roster value). Each article lives at `/world-cup/insights/[slug]` and is
stored in `wc_articles` with the structured `model_inputs` JSON that fed
the prompt, so the page can render an "as of <generated_at>" footer.

The four article slugs:
  - group-of-death       : toughest WC2026 group (lowest variance in
                           p_advance ⇒ tightest race)
  - cinderella-story     : biggest underdogs — p_advance high vs ELO rank
  - squad-value-vs-model : highest total_squad_value_eur with surprisingly
                           low p_advance (and the inverse)
  - champions-favourites : top-5 by p_winner, brief reasoning per team

Idempotent: re-running the same day skips any slug whose `refresh_after`
is still in the future, so APScheduler can fire this multiple times
without burning Gemini quota.

Usage:
  python3 scripts/generate_wc_insights.py
  python3 scripts/generate_wc_insights.py --dry-run
  python3 scripts/generate_wc_insights.py --force     # ignore refresh_after
  python3 scripts/generate_wc_insights.py --only group-of-death
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pvariance

from dotenv import load_dotenv
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google import genai
from workers.api_clients.db import execute_query, execute_write

console = Console()

# Cheapest tier — matches workers/jobs/wc_match_previews.py.
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_MIN_INTERVAL_S = 1.0

REFRESH_AFTER_HOURS = 24

# Slugs — kept in sync with the FE generateStaticParams.
SLUG_GROUP_OF_DEATH = "group-of-death"
SLUG_CINDERELLA = "cinderella-story"
SLUG_SQUAD_VALUE = "squad-value-vs-model"
SLUG_CHAMPIONS = "champions-favourites"

ALL_SLUGS = [
    SLUG_GROUP_OF_DEATH,
    SLUG_CINDERELLA,
    SLUG_SQUAD_VALUE,
    SLUG_CHAMPIONS,
]

# Cliché blacklist — same set as the match-preview job, plus a couple of
# article-specific ones.
_BANNED_PHRASES = [
    "clash of titans", "should be a cracker", "guaranteed", "sure bet",
    "banker", "can't lose", "lock", "no-brainer", "must-win",
    "magical night", "epic encounter",
    "the beautiful game", "kings of football",
]


def _scrub_cliches(text: str) -> str:
    out = text
    for phrase in _BANNED_PHRASES:
        out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


_gemini_client: genai.Client | None = None


def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    return _gemini_client


# ── Data containers ────────────────────────────────────────────────────────

@dataclass
class TeamRow:
    team_id: str
    name: str
    group: str
    elo: float | None
    p_advance: float
    p_winner: float
    p_r16: float
    p_qf: float
    p_sf: float
    p_final: float
    squad_value_eur: int | None  # total_squad_value_eur


# ── Loader ─────────────────────────────────────────────────────────────────

def load_team_rows() -> tuple[list[TeamRow], datetime | None, int]:
    """Join the latest Monte Carlo snapshot with team meta, latest int. ELO,
    and the latest roster_strength snapshot.

    Returns (rows, snapshot_at, n_sims). Returns ([], None, 0) when there is
    no Monte Carlo snapshot in the DB yet."""
    snap = execute_query(
        """
        SELECT snapshot_at, n_sims
        FROM wc_monte_carlo_results
        ORDER BY snapshot_at DESC
        LIMIT 1
        """,
    )
    if not snap:
        return [], None, 0
    snapshot_at = snap[0]["snapshot_at"]
    n_sims = int(snap[0]["n_sims"])

    rows = execute_query(
        """
        WITH mc AS (
            SELECT *
            FROM wc_monte_carlo_results
            WHERE snapshot_at = %s
        ),
        elo AS (
            SELECT DISTINCT ON (team_id) team_id, elo_rating
            FROM team_elo_international
            ORDER BY team_id, match_date DESC
        ),
        roster AS (
            SELECT DISTINCT ON (team_id) team_id, total_squad_value_eur
            FROM team_roster_strength
            ORDER BY team_id, snapshot_date DESC
        )
        SELECT
            t.id::text AS team_id,
            t.name AS name,
            mc.p_advance::float AS p_advance,
            mc.p_winner::float  AS p_winner,
            mc.p_r16::float     AS p_r16,
            mc.p_qf::float      AS p_qf,
            mc.p_sf::float      AS p_sf,
            mc.p_final::float   AS p_final,
            elo.elo_rating::float AS elo,
            roster.total_squad_value_eur AS squad_value_eur
        FROM mc
        JOIN teams t ON t.id = mc.team_id
        LEFT JOIN elo ON elo.team_id = mc.team_id
        LEFT JOIN roster ON roster.team_id = mc.team_id
        """,
        [snapshot_at],
    ) or []

    # Derive group letter via fixture-grouping (mirrors wc_monte_carlo.py).
    # We re-use the union-find approach from there but in-process — pulling
    # the fixtures from the matches table.
    fixtures = execute_query(
        """
        SELECT m.id::text AS match_id,
               m.home_team_id::text AS home_id,
               m.away_team_id::text AS away_id,
               m.date::date AS match_date
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = 1
          AND m.season = 2026
          AND m.date::date <= DATE '2026-06-27'
        ORDER BY m.date ASC
        """,
    ) or []
    group_by_team = _derive_groups(fixtures)

    out: list[TeamRow] = []
    for r in rows:
        out.append(TeamRow(
            team_id=r["team_id"],
            name=r["name"],
            group=group_by_team.get(r["team_id"], "?"),
            elo=float(r["elo"]) if r.get("elo") is not None else None,
            p_advance=float(r["p_advance"] or 0.0),
            p_winner=float(r["p_winner"] or 0.0),
            p_r16=float(r["p_r16"] or 0.0),
            p_qf=float(r["p_qf"] or 0.0),
            p_sf=float(r["p_sf"] or 0.0),
            p_final=float(r["p_final"] or 0.0),
            squad_value_eur=(int(r["squad_value_eur"])
                             if r.get("squad_value_eur") is not None else None),
        ))
    return out, snapshot_at, n_sims


def _derive_groups(fixtures: list[dict]) -> dict[str, str]:
    """Union-find on the group fixtures → {team_id: group_letter}."""
    from collections import defaultdict
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    teams_by_root: dict[str, set[str]] = defaultdict(set)
    earliest_by_root: dict[str, str] = {}

    for f in fixtures:
        parent.setdefault(f["home_id"], f["home_id"])
        parent.setdefault(f["away_id"], f["away_id"])
        union(f["home_id"], f["away_id"])
    for f in fixtures:
        root = find(f["home_id"])
        teams_by_root[root].add(f["home_id"])
        teams_by_root[root].add(f["away_id"])
        d = str(f["match_date"])
        cur = earliest_by_root.get(root)
        if cur is None or d < cur:
            earliest_by_root[root] = d

    roots = sorted(teams_by_root.keys(),
                   key=lambda r: earliest_by_root.get(r, "9999-01-01"))
    alpha = "ABCDEFGHIJKL"
    out: dict[str, str] = {}
    for i, root in enumerate(roots):
        label = alpha[i] if i < len(alpha) else f"G{i+1}"
        for tid in teams_by_root[root]:
            out[tid] = label
    return out


# ── Analytical hooks ──────────────────────────────────────────────────────

def compute_group_of_death(rows: list[TeamRow]) -> dict:
    """For each group compute the variance of p_advance — lowest variance ⇒
    tightest race ⇒ "group of death". Return both the toughest group and
    every group's stats so the model can cite a runner-up."""
    by_group: dict[str, list[TeamRow]] = {}
    for r in rows:
        if r.group == "?":
            continue
        by_group.setdefault(r.group, []).append(r)

    summaries = []
    for letter, members in sorted(by_group.items()):
        adv = [m.p_advance for m in members]
        if not adv:
            continue
        v = pvariance(adv) if len(adv) > 1 else 0.0
        # Also compute the gap between top advance % and bottom — a more
        # human readable companion stat.
        gap = max(adv) - min(adv)
        teams_sorted = sorted(members, key=lambda m: m.p_advance, reverse=True)
        summaries.append({
            "group": letter,
            "variance": round(v, 6),
            "gap": round(gap, 4),
            "teams": [
                {"name": t.name, "p_advance": round(t.p_advance, 4),
                 "elo": round(t.elo, 0) if t.elo is not None else None}
                for t in teams_sorted
            ],
        })

    # Lowest variance = tightest race = "group of death".
    summaries.sort(key=lambda s: s["variance"])
    return {
        "toughest_group": summaries[0] if summaries else None,
        "runner_up": summaries[1] if len(summaries) > 1 else None,
        "all_groups": summaries,
    }


def compute_cinderella(rows: list[TeamRow]) -> dict:
    """Underdogs whose p_advance punches above their ELO weight.

    Rank teams by ELO ascending (worst → best), and by p_advance descending,
    then surface those with the biggest positive (ELO rank - p_advance rank).
    """
    with_elo = [r for r in rows if r.elo is not None]
    if not with_elo:
        return {"top_underdogs": [], "all": []}

    elo_ranks = {
        t.team_id: i
        for i, t in enumerate(sorted(with_elo, key=lambda r: r.elo or 0.0))
    }  # 0 = worst ELO
    adv_ranks = {
        t.team_id: i
        for i, t in enumerate(sorted(with_elo, key=lambda r: -r.p_advance))
    }  # 0 = highest p_advance

    enriched = []
    for t in with_elo:
        elo_rank = elo_ranks[t.team_id]
        adv_rank = adv_ranks[t.team_id]
        # Surprise = how much better the team performs vs its ELO.
        # ELO rank = 0 (worst) advancing high (low adv_rank) ⇒ big surprise.
        surprise = elo_rank - adv_rank  # higher = more surprising
        enriched.append({
            "name": t.name,
            "group": t.group,
            "elo": round(t.elo or 0.0, 0),
            "elo_rank": elo_rank + 1,           # 1-indexed for human display
            "p_advance": round(t.p_advance, 4),
            "advance_rank": adv_rank + 1,
            "surprise_score": surprise,
        })

    enriched.sort(key=lambda x: -x["surprise_score"])
    top = enriched[:6]
    return {"top_underdogs": top, "n_teams_considered": len(enriched)}


def compute_squad_value(rows: list[TeamRow]) -> dict:
    """For each group: compare total squad value EUR vs p_advance.

    Highlight outliers — teams whose squad value rank inside their group
    doesn't match their p_advance rank inside the group."""
    by_group: dict[str, list[TeamRow]] = {}
    for r in rows:
        if r.group == "?" or r.squad_value_eur is None:
            continue
        by_group.setdefault(r.group, []).append(r)

    outliers: list[dict] = []
    overall_data = []
    for letter, members in sorted(by_group.items()):
        if len(members) < 2:
            continue
        by_value = sorted(members, key=lambda m: -(m.squad_value_eur or 0))
        by_adv = sorted(members, key=lambda m: -m.p_advance)
        value_rank = {m.team_id: i for i, m in enumerate(by_value)}
        adv_rank = {m.team_id: i for i, m in enumerate(by_adv)}

        # Group-level summary so the prompt can reference it.
        group_block = {
            "group": letter,
            "teams": [
                {
                    "name": m.name,
                    "squad_value_eur": int(m.squad_value_eur or 0),
                    "p_advance": round(m.p_advance, 4),
                    "value_rank": value_rank[m.team_id] + 1,
                    "advance_rank": adv_rank[m.team_id] + 1,
                }
                for m in by_value
            ],
        }
        overall_data.append(group_block)

        for m in members:
            mismatch = value_rank[m.team_id] - adv_rank[m.team_id]
            # Positive ⇒ team's model ranks them BETTER than their squad value
            # (over-performer for their cost). Negative ⇒ expensive but model
            # is cold on them (the headline "expensive doesn't always win").
            if abs(mismatch) >= 2:
                outliers.append({
                    "name": m.name,
                    "group": letter,
                    "squad_value_eur": int(m.squad_value_eur or 0),
                    "p_advance": round(m.p_advance, 4),
                    "value_rank_in_group": value_rank[m.team_id] + 1,
                    "advance_rank_in_group": adv_rank[m.team_id] + 1,
                    "mismatch": mismatch,
                })

    outliers.sort(key=lambda x: -abs(x["mismatch"]))
    return {
        "outliers": outliers[:8],
        "groups": overall_data,
        "n_groups_with_value": len(overall_data),
    }


def compute_champions(rows: list[TeamRow]) -> dict:
    """Top-5 by p_winner with their full per-stage probabilities."""
    sorted_rows = sorted(rows, key=lambda r: -r.p_winner)
    top5 = sorted_rows[:5]
    return {
        "top5": [
            {
                "name": t.name,
                "group": t.group,
                "elo": round(t.elo, 0) if t.elo is not None else None,
                "p_advance": round(t.p_advance, 4),
                "p_qf": round(t.p_qf, 4),
                "p_sf": round(t.p_sf, 4),
                "p_final": round(t.p_final, 4),
                "p_winner": round(t.p_winner, 4),
            }
            for t in top5
        ],
    }


# ── Prompts ────────────────────────────────────────────────────────────────

def _common_voice_rules() -> str:
    banned = ", ".join(f'"{p}"' for p in _BANNED_PHRASES)
    return f"""Voice rules:
- Data-driven, specific, no fluff. No hype, no clichés.
- DO NOT use any of these phrases: {banned}.
- Cite the actual numbers from the INPUTS block (round to whole percents).
- Use simple markdown: a one-line intro paragraph, then short body paragraphs.
  No headings inside the article — the page renders its own h1.
- 250-400 words total."""


def prompt_group_of_death(payload: dict, n_sims: int) -> str:
    return f"""You are OddsIntel's analyst. Write a short SEO article answering:
"Which is the toughest group at WC2026?"

The data: for each WC group, we compute the variance of advancement probability
(`p_advance`) across the four teams. The lower the variance, the tighter the
race ⇒ more competitive ⇒ "group of death". Numbers come from {n_sims:,}
Monte Carlo simulations of the tournament.

{_common_voice_rules()}

Structure:
1. One-sentence hook: which group is the toughest and why.
2. Cite the toughest group's four teams, their p_advance %, and the gap
   between top and bottom. Lower gap = closer race.
3. Compare with the runner-up "tough group" for context.
4. End with one sentence on the EASIEST group (highest variance) — name the
   clear favourite there.

INPUTS:
```json
{json.dumps(payload, indent=2)}
```

Return ONLY a JSON object — no other text:
{{
  "title": "...",
  "description": "... (max 160 chars, SEO meta description)",
  "body_md": "... (markdown body, no leading h1)"
}}"""


def prompt_cinderella(payload: dict, n_sims: int) -> str:
    return f"""You are OddsIntel's analyst. Write a short SEO article answering:
"Who are the biggest WC2026 Cinderella story candidates in our model?"

The data: per-team `p_advance` from {n_sims:,} Monte Carlo simulations vs
their international ELO. The "surprise_score" = ELO rank (worst→best) minus
advance rank (best→worst). A high positive surprise_score means a team with
modest ELO that our model still gives a real shot to advance.

{_common_voice_rules()}

Structure:
1. One-sentence hook: which is the #1 Cinderella pick.
2. Walk through the top 3-4 underdogs with their actual p_advance % and ELO.
3. One sentence of caveat — these are still long shots, the model just sees
   more upside than the ranking would suggest.

INPUTS:
```json
{json.dumps(payload, indent=2)}
```

Return ONLY a JSON object — no other text:
{{
  "title": "...",
  "description": "... (max 160 chars, SEO meta description)",
  "body_md": "... (markdown body, no leading h1)"
}}"""


def prompt_squad_value(payload: dict, n_sims: int) -> str:
    return f"""You are OddsIntel's analyst. Write a short SEO article answering:
"Does the most expensive squad always win the World Cup group?"

The data: per-team total squad market value (EUR, from transfermarkt) vs our
model's `p_advance` from {n_sims:,} Monte Carlo simulations. For each group we
rank teams by squad value and by p_advance. An "outlier" is any team whose
value rank inside its group differs from its advance rank by 2+ places.

{_common_voice_rules()}

Structure:
1. One-sentence hook: name the most striking outlier — an expensive squad the
   model is cold on, or a cheap squad the model loves.
2. Walk through 2-3 of the top outliers with their actual numbers (squad value
   in EUR billions/millions, p_advance %).
3. End with one sentence on what this tells us — the model values ELO, current
   form and group structure, not just star power.

INPUTS (top outliers + raw per-group data):
```json
{json.dumps(payload, indent=2)}
```

Return ONLY a JSON object — no other text:
{{
  "title": "...",
  "description": "... (max 160 chars, SEO meta description)",
  "body_md": "... (markdown body, no leading h1)"
}}"""


def prompt_champions(payload: dict, n_sims: int) -> str:
    return f"""You are OddsIntel's analyst. Write a short SEO article answering:
"Who are the top-5 favourites to win WC2026 — and why?"

The data: top-5 teams by `p_winner` from {n_sims:,} Monte Carlo simulations.
For each team you also have p_advance, p_qf, p_sf, p_final + ELO.

{_common_voice_rules()}

Structure:
1. One-sentence hook naming the #1 favourite + their winner %.
2. Then one short paragraph per team (#1 → #5). Each paragraph: cite the
   winner %, plus ONE other interesting number (p_final, p_qf, ELO, group).
   Avoid repeating the same phrasing across teams.
3. End with one sentence on the field as a whole — the top-5 combined % vs
   the rest of the 48-team field.

INPUTS:
```json
{json.dumps(payload, indent=2)}
```

Return ONLY a JSON object — no other text:
{{
  "title": "...",
  "description": "... (max 160 chars, SEO meta description)",
  "body_md": "... (markdown body, no leading h1)"
}}"""


PROMPT_BUILDERS: dict[str, callable] = {
    SLUG_GROUP_OF_DEATH: prompt_group_of_death,
    SLUG_CINDERELLA: prompt_cinderella,
    SLUG_SQUAD_VALUE: prompt_squad_value,
    SLUG_CHAMPIONS: prompt_champions,
}


# ── Gemini call ────────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> dict | None:
    """Call Gemini-2.5-flash-lite with transient-error retries.

    Returns {title, description, body_md} or None on hard failure."""
    client = _get_gemini()
    transient = ("ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded")
    response = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            break
        except Exception as exc:
            if type(exc).__name__ in transient and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            console.print(f"  [red]Gemini error: {type(exc).__name__}: {exc}[/red]")
            return None
    if response is None:
        return None
    text = (response.text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        console.print("  [yellow]No JSON in Gemini response[/yellow]")
        return None
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError as e:
        console.print(f"  [yellow]Bad JSON from Gemini: {e}[/yellow]")
        return None
    title = (obj.get("title") or "").strip()
    description = (obj.get("description") or "").strip()
    body_md = _scrub_cliches((obj.get("body_md") or "").strip())
    if not title or not description or not body_md:
        console.print("  [yellow]Missing fields in Gemini JSON[/yellow]")
        return None
    return {"title": title, "description": description, "body_md": body_md}


# ── Build payloads ─────────────────────────────────────────────────────────

def build_payload(slug: str, rows: list[TeamRow], snapshot_at, n_sims: int) -> dict:
    """Return the structured payload + computed analytics for the given slug."""
    base = {
        "snapshot_at": snapshot_at.isoformat() if hasattr(snapshot_at, "isoformat") else str(snapshot_at),
        "n_sims": n_sims,
    }
    if slug == SLUG_GROUP_OF_DEATH:
        base["analytics"] = compute_group_of_death(rows)
    elif slug == SLUG_CINDERELLA:
        base["analytics"] = compute_cinderella(rows)
    elif slug == SLUG_SQUAD_VALUE:
        base["analytics"] = compute_squad_value(rows)
    elif slug == SLUG_CHAMPIONS:
        base["analytics"] = compute_champions(rows)
    else:
        raise ValueError(f"unknown slug: {slug}")
    return base


# ── Storage ────────────────────────────────────────────────────────────────

def needs_refresh(slug: str, force: bool) -> bool:
    if force:
        return True
    rows = execute_query(
        "SELECT refresh_after FROM wc_articles WHERE slug = %s",
        [slug],
    )
    if not rows:
        return True
    ra = rows[0]["refresh_after"]
    if isinstance(ra, str):
        try:
            ra = datetime.fromisoformat(ra.replace("Z", "+00:00"))
        except ValueError:
            return True
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=timezone.utc)
    return ra <= datetime.now(timezone.utc)


def store_article(slug: str, article: dict, payload: dict, dry_run: bool) -> bool:
    if dry_run:
        return True
    try:
        now = datetime.now(timezone.utc)
        refresh_after = now + timedelta(hours=REFRESH_AFTER_HOURS)
        execute_write(
            """
            INSERT INTO wc_articles
              (slug, title, description, body_md, generated_at, refresh_after, model_inputs)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (slug) DO UPDATE SET
              title         = EXCLUDED.title,
              description   = EXCLUDED.description,
              body_md       = EXCLUDED.body_md,
              generated_at  = EXCLUDED.generated_at,
              refresh_after = EXCLUDED.refresh_after,
              model_inputs  = EXCLUDED.model_inputs
            """,
            [
                slug,
                article["title"],
                article["description"],
                article["body_md"],
                now.isoformat(),
                refresh_after.isoformat(),
                json.dumps(payload, default=str),
            ],
        )
        return True
    except Exception as e:
        console.print(f"  [red]DB error storing article {slug}: {e}[/red]")
        return False


# ── Main ───────────────────────────────────────────────────────────────────

def run_wc_insights(
    dry_run: bool = False,
    force: bool = False,
    only: str | None = None,
) -> dict:
    """Callable entry point — scheduler imports this directly.

    Returns {generated, skipped, failed, slugs}.
    """
    console.print("[bold cyan]═══ WC Analytical Insights generator ═══[/bold cyan]\n")

    rows, snapshot_at, n_sims = load_team_rows()
    if not rows or n_sims == 0:
        console.print("[yellow]no monte carlo data, skipping[/yellow]")
        return {"generated": 0, "skipped": 0, "failed": 0, "slugs": []}

    console.print(
        f"[cyan]Loaded {len(rows)} teams from MC snapshot "
        f"({snapshot_at}, n_sims={n_sims:,})[/cyan]\n"
    )

    target_slugs = ALL_SLUGS if only is None else [only]
    if only is not None and only not in ALL_SLUGS:
        console.print(f"[red]Unknown slug: {only}. Valid: {ALL_SLUGS}[/red]")
        return {"generated": 0, "skipped": 0, "failed": 0, "slugs": []}

    generated = 0
    skipped = 0
    failed = 0
    last_call_ts = 0.0
    done_slugs: list[str] = []

    for slug in target_slugs:
        if not needs_refresh(slug, force):
            console.print(f"[dim]· {slug}: fresh, skipping[/dim]")
            skipped += 1
            continue

        payload = build_payload(slug, rows, snapshot_at, n_sims)
        prompt = PROMPT_BUILDERS[slug](payload["analytics"], n_sims)

        # Rate limit: 1 req/sec like the match-preview job.
        elapsed = time.monotonic() - last_call_ts
        if elapsed < GEMINI_MIN_INTERVAL_S:
            time.sleep(GEMINI_MIN_INTERVAL_S - elapsed)
        last_call_ts = time.monotonic()

        console.print(f"[bold]→ {slug}[/bold]")
        article = call_gemini(prompt)
        if article is None:
            failed += 1
            continue

        if dry_run:
            console.print(f"  [green]TITLE:[/green] {article['title']}")
            console.print(f"  [green]DESC:[/green]  {article['description']}")
            preview = article["body_md"][:100].replace("\n", " ")
            console.print(f"  [green]BODY:[/green]  {preview}…\n")
            generated += 1
            done_slugs.append(slug)
        else:
            ok = store_article(slug, article, payload, dry_run=False)
            if ok:
                console.print(f"  [green]✓ Stored {slug}[/green]")
                generated += 1
                done_slugs.append(slug)
            else:
                failed += 1

    console.print(
        f"\n[bold]Done:[/bold] {generated} generated · {skipped} skipped · {failed} failed"
    )
    if dry_run:
        console.print("[yellow](dry-run — nothing written to DB)[/yellow]")

    return {
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "slugs": done_slugs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate WC analytical insight articles")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print articles without writing to DB")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if refresh_after is in the future")
    parser.add_argument("--only", type=str, default=None,
                        choices=ALL_SLUGS,
                        help="Generate only one slug (debug/dev)")
    args = parser.parse_args()
    run_wc_insights(dry_run=args.dry_run, force=args.force, only=args.only)
