import psycopg

from .conftest import RlsConn


def test_alice_sees_only_her_org(people):
    with RlsConn(people["alice"]["id"]) as c:
        names = {r[0] for r in c.execute("select name from organizations").fetchall()}
    assert names == {"Org A"}


def test_alice_cannot_read_bob_notes(people):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        rows = c.execute("select title from notes where org_id = %s", (bob_org,)).fetchall()
    assert rows == []


def test_alice_cannot_read_bob_memberships(people):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        rows = c.execute("select user_id from memberships where org_id = %s", (bob_org,)).fetchall()
    assert rows == []


def test_alice_cannot_insert_note_into_bob_org(people):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        try:
            c.execute(
                "insert into notes (org_id, title, body, created_by) values (%s, 'x', 'x', %s)",
                (bob_org, people["alice"]["id"]),
            )
            inserted = True
        except psycopg.Error:
            inserted = False
    assert inserted is False
