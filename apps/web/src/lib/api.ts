export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Me = {
  user: { email: string; name: string };
  tenant: { id: string; role: "owner" | "admin" | "agent" };
  tenants: { id: string; name: string; role: string }[];
  plan: { key: string | null; status: string };
  features: string[];
};

export type ProviderField = {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
};

export type Provider = {
  key: string;
  name: string;
  group: string;
  phase: number;
  description: string;
  webhook: boolean;
  generates_secret: boolean;
  enabled: boolean;
  fields: ProviderField[];
};

export type Connection = {
  provider: string;
  status: "pending" | "connected" | "needs_attention" | "disconnected";
  last_verified_at: string | null;
  last_error: string | null;
  values: Record<string, string>;
  webhook_url?: string;
  webhook_secret_once?: string;
  message?: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const TENANT_KEY = "fm_tenant";

export function getStoredTenant(): string | null {
  try {
    return window.localStorage.getItem(TENANT_KEY);
  } catch {
    return null;
  }
}

export function storeTenant(id: string): void {
  try {
    window.localStorage.setItem(TENANT_KEY, id);
  } catch {
    /* sem armazenamento: o primeiro cliente é usado */
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  const tenant = getStoredTenant();
  if (tenant) headers.set("X-Tenant-Id", tenant);
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } }).error;
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? "Algo deu errado.");
  }
  return data as T;
}

export type RecoverySummary = {
  days: number;
  cases_total: number;
  cases_by_status: Record<"open" | "recovered" | "purchased" | "stopped" | "exhausted", number>;
  recovered_cents: number;
  messages_sent: number;
  skipped_reasons: { reason: string; count: number }[];
  readiness: {
    whatsapp_connected: boolean;
    checkout_connected: boolean;
    approved_templates: number;
    consent_declared: boolean;
    recovery_enabled: boolean;
  };
};

export type RecoverySettings = {
  timezone: string;
  quiet_start: number;
  quiet_end: number;
  daily_cap: number;
  max_contacts_per_case: number;
  recovery_enabled: boolean;
  consent_declared: boolean;
  cold_enabled: boolean;
  cold_after_hours: number;
  number_daily_limit: number;
};

export type RecoverySequence = {
  trigger: string;
  enabled: boolean;
  steps: { delay_minutes: number; template_key: string }[];
  is_default: boolean;
};

export type RecoveryTemplate = {
  key: string;
  body: string;
  meta_status: "draft" | "submitted" | "approved" | "rejected";
  saved: boolean;
};

export type RecoveryCase = {
  id: string;
  trigger: string;
  source: "checkout" | "conversa" | "manual" | "importacao";
  note: string;
  product: string;
  amount_cents: number;
  status: string;
  closed_reason: string | null;
  recovered_cents: number | null;
  opened_at: string;
  contact: { name: string; phone: string; email: string };
  messages_sent: number;
};

export type ImportResult = {
  total: number;
  created: number;
  skipped: { reason: string; message: string; count: number }[];
  errors: { line: number; message: string }[];
  errors_total: number;
};

export type Suppression = { id: string; identity: string; reason: string; created_at: string };

export const brl = (cents: number): string =>
  (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

export type Offer = {
  id: string;
  name: string;
  description: string;
  price_cents: number;
  payment_url: string;
  active: boolean;
};

export type SellerSettings = {
  ai_enabled: boolean;
  ai_persona: string;
  active_offers: number;
  ai_available: boolean;
};

export type AiUsage = {
  replies: number;
  failures: number;
  tokens_in: number;
  tokens_out: number;
  limit: number | null;
  percent: number | null;
};

export type ConversationRow = {
  id: string;
  status: "bot" | "human" | "closed";
  handoff_reason: string | null;
  name: string;
  phone: string;
  preview: string;
  last_message_at: string;
  window_open: boolean;
};

export type ChatMessage = {
  id: string;
  direction: "in" | "out";
  author: "customer" | "bot" | "human" | "recovery" | "system";
  body: string;
  status: string;
  error: string | null;
  created_at: string;
};

export type ConversationDetail = {
  status: ConversationRow["status"];
  name: string;
  phone: string;
  window_open: boolean;
  messages: ChatMessage[];
};
