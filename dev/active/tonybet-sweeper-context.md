Parent: PRIORITY_QUEUE.md #101 EE-SWEEPERS-2026-09-23 (Tonybet sub-item)

## Key facts
- API: `https://platform.tonybet.com/api/event/list` (limit ≤ 100; `time_gte/time_lte`
  "YYYY-MM-DD HH:MM:SS" UTC; status 0 = pre-match). 20bet = same book.
- Outcome ids (Sportradar UOF): 1/2/3 1x2; 12/13 over/under; 1714/1715 AH home/away;
  74/76 btts yes/no; 9/10/11 DC 1X/12/X2; 4/5 DNB home/away.
- Every outcome has `probabilities` (margin-free). Every event has `vendorEventId`
  `sr:match:N`.
- Women's teams: `competitors.gender == 2`, name has no "W". Youth: `ageGroup` "U21"/"U19"/"YOUTH".

## Decisions
- Proxy: `TONYBET_PROXY`, else `EPICBET_RESIDENTIAL_PROXY` (both the zone exit on the VPS).
- Fair probs: latest-value table, not append.

## Next steps
(see tasks file)
