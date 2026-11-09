-- Governed merchant order / payment journal. No money custody at FM Tecnologia.
-- Number 0024 is reserved by open PR #142 (recovery followups).
CREATE TABLE merchant_accounts (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  provider text NOT NULL DEFAULT 'asaas' CHECK (provider='asaas'),
  mode text NOT NULL CHECK (mode IN ('sandbox','production')),
  credentials_encrypted bytea NOT NULL,
  public_id text NOT NULL UNIQUE,
  verified_at timestamptz,
  delivery_fee_cents bigint NOT NULL DEFAULT 0 CHECK (delivery_fee_cents BETWEEN 0 AND 100000),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE merchant_orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 12 AND 100),
  request_hash text NOT NULL,
  customer_name text NOT NULL CHECK (length(customer_name) BETWEEN 1 AND 120),
  customer_phone text NOT NULL DEFAULT '' CHECK (length(customer_phone)<=20),
  customer_email text NOT NULL DEFAULT '' CHECK (length(customer_email)<=254),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending','charging','awaiting_payment','paid','refunded','disputed','review','canceled'
  )),
  items_cents bigint NOT NULL CHECK (items_cents BETWEEN 1 AND 100000000),
  delivery_fee_cents bigint NOT NULL DEFAULT 0 CHECK (delivery_fee_cents BETWEEN 0 AND 100000),
  total_cents bigint GENERATED ALWAYS AS (items_cents + delivery_fee_cents) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  paid_at timestamptz,
  settled_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,id),
  UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX merchant_orders_tenant_created ON merchant_orders(tenant_id,created_at DESC);
CREATE TABLE merchant_order_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id uuid NOT NULL,
  offer_id uuid NOT NULL,
  product_name text NOT NULL CHECK (length(product_name) BETWEEN 1 AND 120),
  unit_price_cents bigint NOT NULL CHECK (unit_price_cents BETWEEN 1 AND 100000000),
  quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 99),
  line_cents bigint GENERATED ALWAYS AS (unit_price_cents * quantity) STORED,
  FOREIGN KEY (tenant_id,order_id) REFERENCES merchant_orders(tenant_id,id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id,offer_id) REFERENCES offers(tenant_id,id) ON DELETE RESTRICT
);
CREATE INDEX merchant_order_items_order ON merchant_order_items(tenant_id,order_id);
CREATE TABLE merchant_charges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id uuid NOT NULL,
  provider text NOT NULL CHECK (provider='asaas'),
  mode text NOT NULL CHECK (mode IN ('sandbox','production')),
  external_reference text NOT NULL,
  provider_customer_id text,
  provider_payment_id text,
  invoice_url text,
  status text NOT NULL DEFAULT 'creating' CHECK (status IN (
    'creating','unknown','pending','paid','refunded','disputed','review'
  )),
  amount_cents bigint NOT NULL CHECK (amount_cents BETWEEN 1 AND 100100000),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,order_id),
  UNIQUE(tenant_id,external_reference),
  UNIQUE(tenant_id,provider_payment_id),
  FOREIGN KEY (tenant_id,order_id) REFERENCES merchant_orders(tenant_id,id) ON DELETE RESTRICT
);
CREATE TABLE merchant_payment_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (provider='asaas'),
  external_event_id text NOT NULL CHECK (length(external_event_id) BETWEEN 1 AND 160),
  provider_payment_id text NOT NULL,
  event_type text NOT NULL,
  payload_sha256 text NOT NULL,
  disposition text NOT NULL DEFAULT 'queued' CHECK (disposition IN (
    'queued','processing','applied','ignored','review'
  )),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 20),
  lease_until timestamptz,
  processed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,external_event_id)
);
CREATE INDEX merchant_event_lookup ON merchant_payment_events(tenant_id,provider_payment_id);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY[
  'merchant_accounts','merchant_orders','merchant_order_items',
  'merchant_charges','merchant_payment_events'
 ] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format(
    'CREATE POLICY %I ON %I USING (tenant_id=app_tenant() OR app_system()) '
    'WITH CHECK (tenant_id=app_tenant() OR app_system())',
    tab||'_tenant_rw',tab);
 END LOOP;
END $$;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fm_app') THEN
  GRANT SELECT,INSERT,UPDATE ON merchant_accounts,merchant_orders,merchant_order_items,
    merchant_charges,merchant_payment_events TO fm_app;
 END IF;
END $$;
