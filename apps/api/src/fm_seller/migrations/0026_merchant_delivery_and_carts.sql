-- Address delivery zones + scoped conversational cart and charge jobs.
-- Depends on 0025_merchant_orders.sql; 0024 reserved for recovery PR #142.
CREATE TABLE merchant_delivery_zones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL CHECK (length(name) BETWEEN 2 AND 100),
  cep_start integer NOT NULL CHECK (cep_start BETWEEN 1000000 AND 99999999),
  cep_end integer NOT NULL CHECK (cep_end BETWEEN 1000000 AND 99999999),
  fee_cents bigint NOT NULL CHECK (fee_cents BETWEEN 0 AND 100000),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (cep_start <= cep_end),
  UNIQUE(tenant_id,id),
  UNIQUE(tenant_id,cep_start,cep_end)
);
CREATE INDEX merchant_delivery_zone_lookup ON merchant_delivery_zones
  (tenant_id,cep_start,cep_end) WHERE active;

CREATE TABLE merchant_order_fulfillment (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id uuid NOT NULL,
  method text NOT NULL CHECK (method IN ('pickup','delivery')),
  postal_code text NOT NULL DEFAULT '' CHECK (postal_code='' OR postal_code ~ '^[0-9]{8}$'),
  street text NOT NULL DEFAULT '' CHECK (length(street)<=150),
  street_number text NOT NULL DEFAULT '' CHECK (length(street_number)<=25),
  neighborhood text NOT NULL DEFAULT '' CHECK (length(neighborhood)<=100),
  city text NOT NULL DEFAULT '' CHECK (length(city)<=100),
  state text NOT NULL DEFAULT '' CHECK (state='' OR state ~ '^[A-Z]{2}$'),
  complement text NOT NULL DEFAULT '' CHECK (length(complement)<=120),
  reference text NOT NULL DEFAULT '' CHECK (length(reference)<=120),
  confirmed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,order_id),
  FOREIGN KEY (tenant_id,order_id)
    REFERENCES merchant_orders(tenant_id,id) ON DELETE CASCADE,
  CHECK (method='pickup' OR (
    postal_code<>'' AND length(street)>2 AND street_number<>'' AND
    length(neighborhood)>1 AND length(city)>1 AND state<>''
  ))
);
CREATE UNIQUE INDEX IF NOT EXISTS conversations_tenant_id_unique_for_cart ON conversations(tenant_id,id);

CREATE TABLE merchant_conversation_carts (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id uuid NOT NULL,
  stage text NOT NULL CHECK(stage IN (
    'items','method','postal_code','street','street_number','neighborhood',
    'city','state','complement','name','confirm','tax_id','queued','finished'
  )),
  items jsonb NOT NULL CHECK (jsonb_typeof(items)='array'),
  draft jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(draft)='object'),
  idempotency_key text NOT NULL,
  order_id uuid,
  expires_at timestamptz NOT NULL DEFAULT (now()+interval '24 hours'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id,conversation_id),
  UNIQUE(tenant_id,idempotency_key),
  FOREIGN KEY (tenant_id,conversation_id)
    REFERENCES conversations(tenant_id,id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id,order_id)
    REFERENCES merchant_orders(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX merchant_carts_expiry ON merchant_conversation_carts (expires_at);

CREATE TABLE merchant_chat_charge_jobs (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  tax_id_encrypted bytea,
  state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','processing','done','review','canceled')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 12),
  lease_until timestamptz,
  expires_at timestamptz NOT NULL DEFAULT (now()+interval '24 hours'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id,order_id),
  FOREIGN KEY(tenant_id,order_id) REFERENCES merchant_orders(tenant_id,id) ON DELETE CASCADE,
  FOREIGN KEY(tenant_id,conversation_id) REFERENCES conversations(tenant_id,id)
    ON DELETE CASCADE
);
CREATE INDEX merchant_chat_jobs_claim ON merchant_chat_charge_jobs (state,lease_until)
  WHERE state IN ('queued','processing');
DO $$ DECLARE tab text; BEGIN
  FOREACH tab IN ARRAY ARRAY[
    'merchant_delivery_zones','merchant_order_fulfillment',
    'merchant_conversation_carts','merchant_chat_charge_jobs'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
    EXECUTE format(
      'CREATE POLICY %I ON %I USING (tenant_id=app_tenant() OR app_system()) '
      'WITH CHECK(tenant_id=app_tenant() OR app_system())',
      tab||'_tenant_rw',tab
    );
  END LOOP;
END $$;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fm_app') THEN
    GRANT SELECT,INSERT,UPDATE,DELETE ON merchant_delivery_zones,
      merchant_order_fulfillment,merchant_conversation_carts,
      merchant_chat_charge_jobs TO fm_app;
  END IF;
END $$;
