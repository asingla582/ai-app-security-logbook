def test_create_and_list_orgs(alice_client):
    r = alice_client.post("/orgs", json={"name": "Org A"})
    assert r.status_code == 201
    assert any(o["name"] == "Org A" for o in alice_client.get("/orgs").json())


def test_list_members_of_own_org(alice_client):
    org_id = alice_client.post("/orgs", json={"name": "Org A"}).json()["id"]
    members = alice_client.get(f"/orgs/{org_id}/members").json()
    assert len(members) == 1
    assert members[0]["role"] == "owner"


def test_create_and_read_notes_in_own_org(alice_client):
    org_id = alice_client.post("/orgs", json={"name": "Org A"}).json()["id"]
    alice_client.post(f"/orgs/{org_id}/notes", json={"title": "hello", "body": "world"})
    notes = alice_client.get(f"/orgs/{org_id}/notes").json()
    assert [n["title"] for n in notes] == ["hello"]
