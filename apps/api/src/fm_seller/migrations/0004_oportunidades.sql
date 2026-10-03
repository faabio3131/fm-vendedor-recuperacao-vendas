-- 0004: oportunidades de venda próprias (conversa que esfriou, registro manual, planilha, API),
-- além dos eventos de checkout. Tudo reaproveita os casos e passos da recuperação.

ALTER TABLE recovery_cases
  ADD COLUMN source text NOT NULL DEFAULT 'checkout'
    CHECK (source IN ('checkout', 'conversa', 'manual', 'importacao')),
  ADD COLUMN note text NOT NULL DEFAULT '' CHECK (length(note) <= 300);

-- Detecção de conversa que esfriou: o cliente escolhe se liga e depois de quantas horas.
ALTER TABLE tenant_settings
  ADD COLUMN cold_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN cold_after_hours smallint NOT NULL DEFAULT 3 CHECK (cold_after_hours BETWEEN 1 AND 48);

CREATE INDEX recovery_cases_source_idx ON recovery_cases (tenant_id, source, opened_at DESC);
