const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function hex(bytes: number): string {
  const values = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(values, (v) => v.toString(16).padStart(2, "0")).join("");
}

// A W3C trace context for this request. The API records it as a link, never a parent,
// and issues its own correlation id. The browser exports no spans: that would need a
// public ingest endpoint, and anyone signed in could write to it.
export function newTraceparent(): string {
  return `00-${hex(16)}-${hex(8)}-01`;
}

export async function apiFetch(path: string, token: string, init: RequestInit = {}) {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      traceparent: newTraceparent(),
    },
  });
}
