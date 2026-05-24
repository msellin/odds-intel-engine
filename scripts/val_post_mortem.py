"""VAL-POST-MORTEM: review the 14 days of LLM-generated post-mortem notes.

Reads model_evaluations WHERE market='post_mortem', tries multiple parse
strategies (notes are LLM output and the schema drifts), aggregates by
category × market × bot, and dumps a structured summary to stdout +
dev/active/val-post-mortem-2026-05-24.md.
"""
import os, sys, json, re
from collections import Counter, defaultdict
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()


def parse_notes(raw):
    """Best-effort: extract list of loss-classification dicts from a notes blob.

    Real-world rows have one of:
      - {"loss_classifications": [ ... ]}
      - {"classifications": [ ... ]}
      - {"losses": [ ... ]} (rare)
      - mixed-shape, sometimes a top-level dict per match
    Returns list of {"match", "category", "reason", "market"?, "selection"?}.
    """
    if not raw:
        return []
    # Try strict JSON first
    try:
        data = json.loads(raw)
    except Exception:
        # Some notes are NOT proper JSON (truncated, double-encoded, etc.) — regex fallback
        # Find every "category": "..." plus the nearest "match" + "reason"
        out = []
        for m in re.finditer(
            r'\{[^{}]*?"match"\s*:\s*"(?P<match>[^"]+)"[^{}]*?"category"\s*:\s*"(?P<cat>[A-Z_]+)"[^{}]*?"reason"\s*:\s*"(?P<reason>[^"]+)"',
            raw, re.S,
        ):
            out.append({"match": m.group("match"), "category": m.group("cat"), "reason": m.group("reason")})
        return out
    # JSON parsed — try multiple key names
    for key in ("loss_classifications", "classifications", "losses", "items"):
        v = data.get(key) if isinstance(data, dict) else None
        if isinstance(v, list):
            return [e for e in v if isinstance(e, dict)]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def sniff_market(entry):
    txt = (entry.get("match","") + " " + entry.get("reason","")).lower()
    if "under 2.5" in txt or "under 1.5" in txt or "under 3.5" in txt: return "OU-under"
    if "over 2.5" in txt or "over 1.5" in txt or "over 3.5" in txt:    return "OU-over"
    if "btts" in txt or "both teams" in txt:                            return "BTTS"
    if "handicap" in txt or " ah " in txt:                              return "AH"
    if "home win" in txt or "draw"  in txt or "away win" in txt:        return "1X2"
    return "?"


cur.execute("""SELECT date, total_bets, hits, hit_rate, roi, notes
               FROM model_evaluations WHERE market='post_mortem' ORDER BY date""")
rows = cur.fetchall()

all_classifications = []
parse_stats = Counter()
per_day = []

for date, n_bets, hits, hr, roi, notes in rows:
    losses = parse_notes(notes or "")
    parse_stats[len(losses)] += 1
    day = []
    for entry in losses:
        cat = entry.get("category", "UNKNOWN")
        mkt = entry.get("market") or sniff_market(entry)
        rec = {"date": str(date), "match": entry.get("match","?"),
               "category": cat, "market": mkt,
               "reason": entry.get("reason","")[:400]}
        all_classifications.append(rec)
        day.append(rec)
    per_day.append((date, n_bets, hits, hr, roi, day))

print("\n=== Parse coverage ===")
print(f"  rows: {len(rows)}")
print(f"  rows with ≥1 parsed classification: {sum(1 for _,_,_,_,_,d in per_day if d)}")
print(f"  total classifications parsed: {len(all_classifications)}")

print("\n=== Per-day overview ===")
print(f"  {'date':<12}{'bets':>5}{'hits':>5}{'hr':>7}{'roi':>9}  losses parsed")
for d, n, h, hr, r, day in per_day:
    hr_s = f"{float(hr):.2f}" if hr is not None else "—"
    r_s  = f"{float(r):+.1f}%"  if r is not None else "—"
    print(f"  {str(d):<12}{n:>5}{h:>5}{hr_s:>7}{r_s:>9}  {len(day)}")

print("\n=== Category distribution ===")
cats = Counter(c["category"] for c in all_classifications)
for cat, n in cats.most_common():
    pct = 100*n/sum(cats.values()) if cats else 0
    print(f"  {cat:<22}{n:>4}  ({pct:.1f}%)")

print("\n=== Market × Category ===")
mxc = defaultdict(Counter)
for c in all_classifications:
    mxc[c["market"]][c["category"]] += 1
cats_sorted = sorted({c for d in mxc.values() for c in d})
print("  market".ljust(12) + "".join(c[:14].ljust(15) for c in cats_sorted) + "Total")
for mkt in sorted(mxc):
    tot = 0
    line = mkt.ljust(12)
    for c in cats_sorted:
        v = mxc[mkt].get(c, 0); tot += v
        line += str(v).ljust(15)
    line += str(tot)
    print(line)

print("\n=== All MODEL_ERROR reasons ===")
for c in [x for x in all_classifications if x["category"] == "MODEL_ERROR"]:
    print(f"  {c['date']} [{c['market']}] {c['match']}")
    print(f"    {c['reason']}")
print(f"  TOTAL MODEL_ERROR: {sum(1 for x in all_classifications if x['category']=='MODEL_ERROR')}")

print("\n=== All INFORMATION_GAP reasons ===")
for c in [x for x in all_classifications if x["category"] == "INFORMATION_GAP"]:
    print(f"  {c['date']} [{c['market']}] {c['match']}")
    print(f"    {c['reason']}")
print(f"  TOTAL INFORMATION_GAP: {sum(1 for x in all_classifications if x['category']=='INFORMATION_GAP')}")

print("\n=== All VARIANCE reasons (truncated) ===")
for c in [x for x in all_classifications if x["category"] == "VARIANCE"][:20]:
    print(f"  {c['date']} [{c['market']}] {c['match']}: {c['reason'][:120]}")
print(f"  TOTAL VARIANCE: {sum(1 for x in all_classifications if x['category']=='VARIANCE')}")

# Phrase frequency on reasons
print("\n=== Frequent phrases across all reasons ===")
all_reason = " ".join(c["reason"] for c in all_classifications).lower()
phrases = ["align: low", "alignment: low", "calibrat",
           "underestim", "overestim",
           "negative clv", "extreme drift",
           "raw probability", "post-bet",
           "drift", "miscalibrat", "longshot",
           "low alignment", "high alignment",
           "home advantage", "away upset"]
for p in phrases:
    n = all_reason.count(p)
    if n:
        print(f"  {p!r:<25}{n}")

cur.close(); conn.close()
