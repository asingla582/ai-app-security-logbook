def _new_conversation(client):
    client.post("/orgs", json={"name": "A"})
    return client.post("/conversations").json()["id"]


def test_chat_roundtrip(alice_client):
    conv_id = _new_conversation(alice_client)
    r = alice_client.post(f"/conversations/{conv_id}/messages", json={"content": "hello"})
    assert r.status_code == 201
    assert "hello" in r.json()["reply"]
    got = alice_client.get(f"/conversations/{conv_id}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]


def test_conversation_title_from_first_message(alice_client):
    conv_id = _new_conversation(alice_client)
    alice_client.post(f"/conversations/{conv_id}/messages", json={"content": "plan my week"})
    titles = {c["id"]: c["title"] for c in alice_client.get("/conversations").json()}
    assert titles[conv_id] == "plan my week"


def test_delete_actually_deletes(alice_client):
    conv_id = _new_conversation(alice_client)
    alice_client.post(f"/conversations/{conv_id}/messages", json={"content": "hi"})
    assert alice_client.delete(f"/conversations/{conv_id}").status_code == 204
    assert alice_client.get(f"/conversations/{conv_id}").status_code == 404
