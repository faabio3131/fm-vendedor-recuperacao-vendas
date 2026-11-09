-- Multi-gateway merchant payment accounts. Preserve every existing Asaas account and charge.
-- No provider may gain ownership of another tenant's payment artifacts.
ALTER TABLE merchant_accounts DROP CONSTRAINT IF EXISTS merchant_accounts_provider_check;
ALTER TABLE merchant_accounts ADD CONSTRAINT merchant_accounts_provider_check
  CHECK (provider IN ('asaas','mercadopago','pagbank','pagarme','picpay'));
ALTER TABLE merchant_accounts ADD COLUMN active boolean NOT NULL DEFAULT true;
ALTER TABLE merchant_accounts DROP CONSTRAINT merchant_accounts_pkey;
ALTER TABLE merchant_accounts ADD CONSTRAINT merchant_accounts_pkey PRIMARY KEY (tenant_id,provider);
CREATE UNIQUE INDEX merchant_accounts_one_active ON merchant_accounts(tenant_id) WHERE active;

ALTER TABLE merchant_charges DROP CONSTRAINT IF EXISTS merchant_charges_provider_check;
ALTER TABLE merchant_charges ADD CONSTRAINT merchant_charges_provider_check
  CHECK (provider IN ('asaas','mercadopago','pagbank','pagarme','picpay'));

ALTER TABLE merchant_payment_events DROP CONSTRAINT IF EXISTS merchant_payment_events_provider_check;
ALTER TABLE merchant_payment_events ADD CONSTRAINT merchant_payment_events_provider_check
  CHECK (provider IN ('asaas','mercadopago','pagbank','pagarme','picpay'));
ALTER TABLE merchant_payment_events
  DROP CONSTRAINT IF EXISTS merchant_payment_events_tenant_id_external_event_id_key;
ALTER TABLE merchant_payment_events ADD CONSTRAINT merchant_payment_events_tenant_provider_event_unique
  UNIQUE(tenant_id,provider,external_event_id);
CREATE INDEX merchant_charges_provider_lookup
  ON merchant_charges(tenant_id,provider,provider_payment_id);
