-- 437_bot_distribution_and_review_flag.sql — #162 W5.1 + W6.5 (2026-09-25): two READ-ONLY views that
-- #155 builds on (agreed with the bot session 2026-09-25: #162 builds the views, #155 wires the labels,
-- the publishers and the admin flag). Nothing reads them yet; they change no behaviour.
--
-- W5.1 bot_distribution — ONE answer to "may this bot's picks reach the public / the VIP channel", derived
-- from the bot's status (maturity_label; retired_at = retired) and the VIP flag, per owner policy
-- (dev/active/bots-session-handover-2026-09-25.md §3.1, #155):
--   EXPERIMENTAL  admin only · nothing sent (also when the bot is VIP: "VIP · EXPERIMENTAL" sends nothing)
--   TESTING       on /performance marked testing · picks SENT · own record · not in the headline
--   BETA / CALIBRATED  sent · own record · headline totals
--   ⭐ VIP is a CHANNEL on top of a public status (testing/beta/calibrated): live picks to the VIP channel
--      only; the public sees settled picks; own record, never the headline.
--   Real money is NOT a status (it is the per-bot switch, coolbet_placer_bots).
-- `hide_pending` on a NON-VIP bot is read as "this bot shares a VIP bot's picks" (the VIP twins, e.g.
-- bot_combined_1x2_ev8_v1, bot_ou_sharp_2anchor_v1) — such a bot never shows pending picks publicly, even
-- when promoted. This is a BOT-level ceiling only: #164's vip_guard holds back individual VIP-held picks.
-- NOT modelled here, on purpose: retired totals (#157 owns them — retired picks keep counting there);
-- recorded-only forward-test arms (their lock is PUBLISHED_ARMS in scripts/publish_picks_forward_test.py;
-- they are EXPERIMENTAL and must stay so); a "method" label (bot_config.family already carries it).
-- `pending_exposed` = the public can read this bot's pending picks TODAY although policy says it must not
-- (anon RLS hides pending only for vip / hide_pending bots) — the #162 audit B §4.5 leak list for #155.
-- OPEN (owner Q7): for a sim-ledger TESTING bot, does "sent" mean /picks, the public Telegram channel, or
-- both? `sent_public` is reported once; #155 decides which channels it drives.
--
-- W6.5 bot_review_flag — the retirement FLAG (owner rule §3.3: a flag, never automatic): an ACTIVE bot is
-- flagged when it has ≥ 50 settled legs WITH a sharp-anchor CLV and the upper end of the 95% interval of
-- that CLV is below 0. Over bot_performance (#159, the one per-bot computation). Both price bases are shown
-- (owner Q2 open: public "available" price vs our books); `review_flag` uses the PUBLIC basis, which is what
-- /performance publishes. Interval = mean ± 1.96·SD/√n (clv_public_sd is the leg SD, migration 433) —
-- somewhat too NARROW, because legs of the same match are correlated. `reason` says why a bot is (not)
-- flagged; in-play bots have no CLV basis and can never flag (reason 'no_clv_basis', not a silent false).

BEGIN;

CREATE OR REPLACE VIEW public.bot_distribution AS
WITH s AS (
    SELECT b.name AS bot_name,
           b.display_name,
           CASE WHEN b.retired_at IS NOT NULL OR b.maturity_label = 'retired' THEN 'retired'
                ELSE coalesce(b.maturity_label, 'experimental') END AS status,
           coalesce(b.vip, false) AS vip,
           coalesce(b.hide_pending, false) AS hide_pending,
           b.is_active
      FROM bots b
), d AS (
    SELECT s.*,
           (s.status IN ('testing','beta','calibrated')) AS public_status
      FROM s
)
SELECT d.bot_name,
       d.display_name,
       d.status,
       d.vip,
       d.is_active,
       CASE WHEN d.vip THEN 'VIP · ' || upper(d.status) ELSE upper(d.status) END AS label,
       d.public_status                                                        AS on_performance,
       (d.public_status AND NOT d.vip)                                        AS sent_public,
       (d.public_status AND d.vip)                                            AS vip_channel,
       (d.public_status AND NOT d.vip AND NOT d.hide_pending)                 AS pending_public,
       (d.status IN ('beta','calibrated') AND NOT d.vip)                      AS in_headline,
       d.public_status                                                        AS in_active_record,
       (d.status <> 'retired' AND NOT d.vip AND NOT d.hide_pending
        AND NOT (d.public_status AND NOT d.vip AND NOT d.hide_pending))       AS pending_exposed
  FROM d;

CREATE OR REPLACE VIEW public.bot_review_flag AS
WITH k AS (SELECT 50 AS min_n),                 -- the ONE threshold (owner rule §3.3)
p AS (
    SELECT b.name AS bot_name,
           coalesce(bp.settled, 0) AS settled,
           coalesce(bp.clv_n, 0) AS clv_n,
           bp.clv_public, bp.clv_public_sd,
           coalesce(bp.clv_own_n, 0) AS clv_own_n,
           bp.clv_own, bp.clv_own_sd
      FROM bots b
      LEFT JOIN bot_performance bp ON bp.bot_name = b.name
     WHERE b.is_active AND b.retired_at IS NULL
), u AS (
    SELECT p.*,
           CASE WHEN p.clv_n >= 2 THEN p.clv_public + 1.96 * coalesce(p.clv_public_sd, 0) / sqrt(p.clv_n::double precision) END AS clv_public_upper95,
           CASE WHEN p.clv_own_n >= 2 THEN p.clv_own + 1.96 * coalesce(p.clv_own_sd, 0) / sqrt(p.clv_own_n::double precision) END AS clv_own_upper95
      FROM p
)
SELECT u.bot_name,
       u.settled,
       u.clv_n              AS clv_n_public,
       u.clv_public,
       u.clv_public_upper95,
       u.clv_own_n,
       u.clv_own,
       u.clv_own_upper95,
       (u.clv_n >= k.min_n AND u.clv_public_upper95 < 0)       AS flag_public,
       (u.clv_own_n >= k.min_n AND u.clv_own_upper95 < 0)      AS flag_own,
       coalesce(u.clv_n >= k.min_n AND u.clv_public_upper95 < 0, false) AS review_flag,
       CASE WHEN u.clv_n = 0 THEN 'no_clv_basis'
            WHEN u.clv_n < k.min_n THEN 'n_below_' || k.min_n
            WHEN u.clv_public_upper95 < 0 THEN 'clv_ci_below_zero'
            ELSE 'clv_ci_not_below_zero' END                     AS reason,
       k.min_n
  FROM u CROSS JOIN k;

REVOKE ALL ON public.bot_distribution, public.bot_review_flag FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.bot_distribution, public.bot_review_flag TO service_role;

COMMIT;
