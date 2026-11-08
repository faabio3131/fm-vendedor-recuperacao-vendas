-- Private product photos: tenant-scoped, non-public binary storage.
-- Deliberately use the already backed-up Postgres volume rather than an
-- unprovisioned external bucket. Quotas and normalized JPEG enforced by API.
ALTER TABLE offers ADD COLUMN brand text NOT NULL DEFAULT '' CHECK (char_length(brand) <= 120);

CREATE UNIQUE INDEX offers_tenant_id_id_uq ON offers (tenant_id,id);

CREATE TABLE offer_photos (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  offer_id    uuid NOT NULL,
  sort_order  integer NOT NULL DEFAULT 0,
  mime_type   text NOT NULL DEFAULT 'image/jpeg' CHECK (mime_type = 'image/jpeg'),
  bytes       bytea NOT NULL CHECK (octet_length(bytes) BETWEEN 1 AND 2000000),
  width       integer NOT NULL CHECK (width BETWEEN 1 AND 1920),
  height      integer NOT NULL CHECK (height BETWEEN 1 AND 1920),
  created_at  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, offer_id) REFERENCES offers(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX offer_photos_offer_idx ON offer_photos(tenant_id,offer_id,sort_order,created_at);
ALTER TABLE offer_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE offer_photos FORCE ROW LEVEL SECURITY;
CREATE POLICY offer_photos_tenant_rw ON offer_photos
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fm_app') THEN
    GRANT SELECT, INSERT, DELETE ON offer_photos TO fm_app;
  END IF;
END $$;

-- Immutable choice at enqueue time. If a photo is later deleted, the
-- queued image fails closed rather than silently sending a text-only fake.
ALTER TABLE messages ADD COLUMN outbound_kind text NOT NULL DEFAULT 'text'
  CHECK (outbound_kind IN ('text','offer_photo'));
ALTER TABLE messages ADD COLUMN outbound_photo_id uuid
  REFERENCES offer_photos(id) ON DELETE SET NULL;
ALTER TABLE messages ADD CONSTRAINT offer_photo_requires_reference
  CHECK (outbound_kind <> 'offer_photo' OR direction = 'out');
