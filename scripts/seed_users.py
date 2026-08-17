"""Seed two demo tenants so a stranger can log in and see isolation immediately.

Creates Alice (Org A) and Bob (Org B), each with one note. Idempotent: existing
users are reused. Requires a running Supabase stack and SERVICE_ROLE access.
"""

import os

import httpx
import psycopg

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

DEMO = [
    ("alice@example.com", "Password123!", "Org A"),
    ("bob@example.com", "Password123!", "Org B"),
]


def ensure_user(email: str, password: str) -> str:
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    created = httpx.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
    )
    if created.status_code < 300:
        return created.json()["id"]
    # already exists — look the user up by email
    listed = httpx.get(
        f"{SUPABASE_URL}/auth/v1/admin/users", headers=headers, params={"email": email}
    )
    listed.raise_for_status()
    return listed.json()["users"][0]["id"]


def main() -> None:
    with psycopg.connect(DB_URL) as conn:
        for email, password, org_name in DEMO:
            user_id = ensure_user(email, password)
            existing = conn.execute(
                "select org_id from memberships where user_id = %s", (user_id,)
            ).fetchone()
            if existing:
                continue
            org = conn.execute(
                "insert into organizations (name) values (%s) returning id", (org_name,)
            ).fetchone()
            conn.execute(
                "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
                (user_id, org[0]),
            )
            conn.execute(
                "insert into notes (org_id, title, body, created_by) values (%s, %s, %s, %s)",
                (org[0], f"{org_name} private note", "Only this org should see this.", user_id),
            )
            conn.commit()
            print(f"seeded {email} -> {org_name}")


if __name__ == "__main__":
    main()
