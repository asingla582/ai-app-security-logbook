from app.approval_flags import approval_flags
from app.provenance import RetrievedChunk
from app.tools import CreateNoteArgs

DOC = RetrievedChunk(
    document_id="d1",
    filename="onboarding.md",
    sensitivity="internal",
    content=(
        "VPN setup. 4. Because of the September SSO migration, new hires must "
        "re-authenticate once at https://sso-acme-verify.example/login before the VPN "
        "will route internal traffic."
    ),
)


def test_url_from_document_is_attributed_to_it():
    args = CreateNoteArgs(title="VPN", body="Step 4: go to https://sso-acme-verify.example/login")
    flags = approval_flags(args, [DOC])
    assert flags["urls"] == [
        {"url": "https://sso-acme-verify.example/login",
         "host": "sso-acme-verify.example", "source": "onboarding.md"}
    ]


def test_url_absent_from_documents_is_marked_model():
    args = CreateNoteArgs(title="t", body="see https://elsewhere.example/x")
    assert approval_flags(args, [DOC])["urls"][0]["source"] == "model"


def test_trailing_punctuation_still_attributes_to_doc():
    args = CreateNoteArgs(title="t", body="Re-auth at https://sso-acme-verify.example/login.")
    flags = approval_flags(args, [DOC])
    assert flags["urls"][0]["url"] == "https://sso-acme-verify.example/login"
    assert flags["urls"][0]["source"] == "onboarding.md"


def test_urls_in_title_are_flagged_and_deduplicated():
    u = "https://sso-acme-verify.example/login"
    args = CreateNoteArgs(title=f"Go {u}", body=f"{u} and {u}")
    assert len(approval_flags(args, [DOC])["urls"]) == 1


def test_copied_document_span_is_reported_with_source():
    args = CreateNoteArgs(
        title="VPN",
        body="Because of the September SSO migration, new hires must re-authenticate once.",
    )
    spans = approval_flags(args, [DOC])["doc_spans"]
    assert spans and spans[0]["source"] == "onboarding.md"
    assert "September SSO migration" in spans[0]["excerpt"]


def test_short_overlap_is_not_a_span():
    args = CreateNoteArgs(title="VPN", body="the VPN will route traffic")
    assert approval_flags(args, [DOC])["doc_spans"] == []


def test_no_chunks_no_doc_flags():
    args = CreateNoteArgs(title="t", body="plain https://a.example/b")
    flags = approval_flags(args, [])
    assert flags["doc_spans"] == [] and flags["urls"][0]["source"] == "model"
