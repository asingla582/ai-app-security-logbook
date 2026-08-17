"""Week 1 cross-tenant attack suite.

Alice attempts to reach Bob's tenant through the API. Every attempt must be denied.
This file IS the evidence: `make attack` runs it and captures the output.
"""


def test_tampered_org_id_notes_denied(alice_client, bob_org_id):
    # Alice asks for Bob's notes by putting his org id in the path
    assert alice_client.get(f"/orgs/{bob_org_id}/notes").status_code == 404


def test_tampered_org_id_members_denied(alice_client, bob_org_id):
    assert alice_client.get(f"/orgs/{bob_org_id}/members").status_code == 404


def test_alice_cannot_post_note_to_bob_org(alice_client, bob_org_id):
    r = alice_client.post(f"/orgs/{bob_org_id}/notes", json={"title": "x", "body": "x"})
    assert r.status_code == 404


def test_no_token_denied(anon_client):
    assert anon_client.get("/orgs").status_code == 401


def test_expired_token_denied(expired_client):
    assert expired_client.get("/orgs").status_code == 401
