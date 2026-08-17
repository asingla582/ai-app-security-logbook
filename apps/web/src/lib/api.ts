const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function apiFetch(path: string, token: string, init: RequestInit = {}) {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}
