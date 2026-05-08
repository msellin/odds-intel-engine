-- DB-STMT-TIMEOUT: Cap runaway queries at the database level.
-- Supavisor (Supabase transaction-mode pooler) strips per-connection options=
-- parameters, so this cannot be set from the psycopg2 client side.
-- 60s is safe for settlement (legitimate JOINs push 20-30s; 15s would kill them).
-- idle_in_transaction_session_timeout prevents stuck transactions from holding
-- pool connections indefinitely — a different leak vector from POOL-LEAK-FIX.

ALTER DATABASE postgres SET statement_timeout = '60s';
ALTER DATABASE postgres SET idle_in_transaction_session_timeout = '30s';
