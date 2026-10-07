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


def _flag(body):
    return approval_flags(CreateNoteArgs(title="t", body=body), [DOC])["urls"]


def test_schemeless_doc_host_with_path_is_flagged():
    urls = _flag("Step 4: re-auth at sso-acme-verify.example/login first")
    assert urls == [{"url": "sso-acme-verify.example/login",
                     "host": "sso-acme-verify.example", "source": "onboarding.md"}]


def test_bare_doc_host_before_period_is_flagged():
    urls = _flag("Re-authenticate at sso-acme-verify.example.")
    assert len(urls) == 1
    assert urls[0]["host"] == "sso-acme-verify.example"
    assert urls[0]["source"] == "onboarding.md"


def test_uppercase_scheme_and_host_is_flagged():
    urls = _flag("go to HTTPS://SSO-ACME-VERIFY.example/login now")
    assert len(urls) == 1
    assert urls[0]["url"] == "HTTPS://SSO-ACME-VERIFY.example/login"
    assert urls[0]["source"] == "onboarding.md"


def test_trailing_slash_variant_still_attributes_to_doc():
    urls = _flag("go to https://sso-acme-verify.example/login/ now")
    assert len(urls) == 1 and urls[0]["source"] == "onboarding.md"


def test_full_and_schemeless_forms_make_one_entry():
    urls = _flag("see https://sso-acme-verify.example/login and sso-acme-verify.example/login")
    assert len(urls) == 1


def test_bare_domain_in_no_chunk_is_not_flagged():
    assert _flag("also try evil.example/x for details") == []
