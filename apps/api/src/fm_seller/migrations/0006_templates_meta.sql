-- 0006: ligação dos templates com a Meta (envio para aprovação, status e motivo de recusa).

ALTER TABLE message_templates
  ADD COLUMN meta_name         text,
  ADD COLUMN meta_template_id  text,
  ADD COLUMN meta_language     text NOT NULL DEFAULT 'pt_BR',
  ADD COLUMN meta_category     text CHECK (meta_category IN ('MARKETING', 'UTILITY')),
  ADD COLUMN meta_version      int  NOT NULL DEFAULT 0,
  ADD COLUMN meta_reason       text,
  ADD COLUMN meta_synced_at    timestamptz;

-- A Meta também pausa e desativa templates; nenhum dos dois pode ser enviado.
ALTER TABLE message_templates DROP CONSTRAINT message_templates_meta_status_check;
ALTER TABLE message_templates ADD CONSTRAINT message_templates_meta_status_check
  CHECK (meta_status IN ('draft', 'submitted', 'approved', 'rejected', 'paused', 'disabled'));
