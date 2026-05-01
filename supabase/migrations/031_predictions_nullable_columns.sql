-- Fix: predictions.implied_probability and edge_percent are NOT NULL
-- but AF predictions don't have these values. Make them nullable.
ALTER TABLE predictions ALTER COLUMN implied_probability DROP NOT NULL;
ALTER TABLE predictions ALTER COLUMN edge_percent DROP NOT NULL;
