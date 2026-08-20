"""Fixtures for API-level tests.

Mints real Supabase access tokens for two users and exposes TestClients carrying
them, so the tests exercise the app exactly as the browser would. Skips cleanly if
no local Supabase stack is reachable.
"""

import os
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import SUPABASE_JWT_SECRET
from app.gateway import FakeGateway, get_gateway
from app.main import app


@pytest.fixture(autouse=True)
def _use_fake_gateway():
    # Every test drives the deterministic fake, never the real Anthropic API:
    # free, offline, and no key required. Selection is server-side only.
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    yield
    app.dependency_overrides.pop(get_gateway, None)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _reachable() -> bool:
    if not SERVICE_KEY:
        return False
    try:
        psycopg.connect(DB_URL).close()
        return True
    except psycopg.Error:
        return False


def _new_user_token(tag: str) -> str:
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    email = f"{tag}-{uuid.uuid4().hex[:8]}@example.com"
    password = "Password123!"
    httpx.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
    ).raise_for_status()
    grant = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SERVICE_KEY},
        json={"email": email, "password": password},
    )
    grant.raise_for_status()
    return grant.json()["access_token"]


@pytest.fixture(scope="session")
def require_supabase():
    # Only tests that need real users/rows depend on this; token-rejection tests
    # (no token, expired token) and the auth unit tests run without a database.
    if not _reachable():
        pytest.skip("local Supabase stack not reachable")


def _client(token: str | None) -> TestClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app, headers=headers)


@pytest.fixture
def alice_client(require_supabase) -> TestClient:
    return _client(_new_user_token("alice"))


@pytest.fixture
def bob_setup(require_supabase):
    token = _new_user_token("bob")
    client = _client(token)
    org_id = client.post("/orgs", json={"name": "Org B"}).json()["id"]
    return {"client": client, "org_id": org_id}


@pytest.fixture
def bob_org_id(bob_setup) -> str:
    return bob_setup["org_id"]


@pytest.fixture
def anon_client() -> TestClient:
    return _client(None)


@pytest.fixture
def bob_conversation(require_supabase) -> str:
    client = _client(_new_user_token("bob"))
    client.post("/orgs", json={"name": "Org B"})
    conv_id = client.post("/conversations").json()["id"]
    client.post(f"/conversations/{conv_id}/messages", json={"content": "bob's private message"})
    return conv_id


@pytest.fixture
def expired_client() -> TestClient:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": int(time.time()) - 60},
        SUPABASE_JWT_SECRET,
        algorithm="HS256",
    )
    return _client(token)
