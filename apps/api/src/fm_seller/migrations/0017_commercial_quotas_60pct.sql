-- Governed commercial quota rebalance approved 2026-10-23.
-- Same prices and feature matrix. Only three authorized commercial plans change.
-- Fails closed when a deployment's baseline was independently customized.
-- The migration runner already commits each file atomically and records its checksum.
DO $migration$
DECLARE
  eligible integer;
  affected integer;
BEGIN
  SELECT count(*) INTO eligible
  FROM plans
  WHERE (key = 'av-essencial'
          AND price_monthly_cents = 12990
          AND limits->>'ai_replies_per_month' = '500')
     OR (key = 'av-profissional'
          AND price_monthly_cents = 19990
          AND limits->>'ai_replies_per_month' = '5000')
     OR (key = 'av-escala'
          AND price_monthly_cents = 39990
          AND limits->>'ai_replies_per_month' = '15000');

  IF eligible <> 3 THEN
    RAISE EXCEPTION
      'Commercial quota baseline mismatch: expected three unchanged plans, found %',
      eligible;
  END IF;

  UPDATE plans
     SET limits = jsonb_set(
       limits,
       '{ai_replies_per_month}',
       to_jsonb(
         CASE key
           WHEN 'av-essencial' THEN 500
           WHEN 'av-profissional' THEN 1000
           WHEN 'av-escala' THEN 3000
         END
       ),
       true
     )
   WHERE key IN ('av-essencial', 'av-profissional', 'av-escala');

  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 3 THEN
    RAISE EXCEPTION 'Commercial quota rebalance incomplete: % of 3', affected;
  END IF;
END
$migration$;
