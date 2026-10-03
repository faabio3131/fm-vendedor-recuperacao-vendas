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
