from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_me_requires_token():
    assert client.get("/me").status_code == 401


def test_me_rejects_garbage_token():
    r = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_response_has_correlation_id():
    r = client.get("/health")
    assert r.headers.get("X-Correlation-Id")


def test_incoming_correlation_id_is_echoed():
    r = client.get("/health", headers={"X-Correlation-Id": "trace-123"})
    assert r.headers["X-Correlation-Id"] == "trace-123"
