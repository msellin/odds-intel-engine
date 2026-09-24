#!/usr/bin/env bash
# BETFAIR-EXCHANGE GEO PROBE ([[#115]], 2026-09-24) — one-shot, 2 requests, read-only.
#
# WHY. From our Finnish VPS the Betfair Exchange site loads but every market query
# returns EMPTY — even inside a real browser — i.e. the exchange serves no markets to
# that jurisdiction (it answered in German). The owner wants to know whether a server
# genuinely hosted in a country Betfair serves (UK/Ireland) gets real prices, before
# deciding whether to rent one. Hetzner has no UK/IE location; run this on a throwaway
# hourly VM in London/Dublin (DigitalOcean, Vultr, AWS eu-west-2).
#
# It only READS public odds. No account, no login, no bets. The query body is exactly
# what betfair.com's own exchange page sends (recorded 2026-09-24), so an empty answer
# here means "not served", not "wrong query".
#
#   bash betfair_exchange_geo_probe.sh
set -euo pipefail
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
AK="nzIFcwyWhrlwYMrh"   # the public web-app key betfair.com's own page sends
echo "exit IP country: $(curl -s -m 8 https://ipinfo.io/country)"

BODY='{"filter":{"marketBettingTypes":["ASIAN_HANDICAP_SINGLE_LINE","ASIAN_HANDICAP_DOUBLE_LINE","ODDS"],"productTypes":["EXCHANGE"],"marketTypeCodes":["MATCH_ODDS"],"contentGroup":{"language":"en"},"turnInPlayEnabled":true,"maxResults":0,"selectBy":"RANK","eventTypeIds":[1]},"facets":[{"type":"EVENT_TYPE","skipValues":0,"maxValues":10,"next":{"type":"COMPETITION","skipValues":0,"maxValues":5,"next":{"type":"EVENT","skipValues":0,"maxValues":10,"next":{"type":"MARKET","maxValues":1,"next":{"type":"COMPETITION","maxValues":1}}}}}],"currencyCode":"EUR","locale":"en"}'

curl -s -m 25 -A "$UA" -H "Content-Type: application/json" -H "Accept: application/json" \
     -H "Referer: https://www.betfair.com/" -X POST \
     "https://scan-inbf.betfair.com/www/sports/navigation/facet/v1/search?_ak=${AK}&alt=json" \
     -d "$BODY" -o /tmp/bf_facet.json -w "facet search: HTTP %{http_code}, %{size_download} bytes\n"

MARKETS=$(python3 - <<'EOF'
import json
d = json.load(open("/tmp/bf_facet.json"))
m = list((d.get("attachments") or {}).get("markets") or {})
ev = (d.get("attachments") or {}).get("events") or {}
print(f"# events {len(ev)}, markets {len(m)}", flush=True)
for v in list(ev.values())[:5]:
    print(f"#   {v.get('name')}  {v.get('openDate')}", flush=True)
print(",".join(m[:5]))
EOF
)
echo "$MARKETS" | grep '^#' || true
IDS=$(echo "$MARKETS" | grep -v '^#' | tail -1)
if [ -z "$IDS" ]; then
  echo "VERDICT: no markets served here — Betfair Exchange is NOT readable from this location."
  exit 0
fi
sleep 2
curl -s -m 25 -A "$UA" -H "Accept: application/json" \
     "https://ero.betfair.com/www/sports/exchange/readonly/v1/bymarket?_ak=${AK}&alt=json&currencyCode=EUR&locale=en&marketIds=${IDS}&rollupLimit=10&rollupModel=STAKE&types=MARKET_STATE,RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST,RUNNER_DESCRIPTION" \
     -o /tmp/bf_prices.json -w "prices: HTTP %{http_code}, %{size_download} bytes\n"
python3 - <<'EOF'
import json
d = json.load(open("/tmp/bf_prices.json"))
for et in d.get("eventTypes", []):
    for node in et.get("eventNodes", []):
        name = (node.get("event") or {}).get("eventName")
        for mn in node.get("marketNodes", []):
            st = mn.get("state") or {}
            print(f"{name} | matched {st.get('totalMatched')}")
            for r in mn.get("runners", []):
                ex = r.get("exchange") or {}
                b = (ex.get("availableToBack") or [{}])[0]
                l = (ex.get("availableToLay") or [{}])[0]
                print(f"   {(r.get('description') or {}).get('runnerName'):28s} back {b.get('price')} ({b.get('size')})  lay {l.get('price')} ({l.get('size')})")
print("VERDICT: markets AND prices served here." if d.get("eventTypes") else "VERDICT: markets listed but no prices.")
EOF
