import pytest
from pydantic import ValidationError

from app.tools import (
    REGISTRY,
    CreateNoteArgs,
    SearchDocumentsArgs,
    anthropic_tools,
    validate_args,
)


def test_registry_has_exactly_two_tools():
    assert set(REGISTRY) == {"search_documents", "create_note"}


def test_anthropic_tools_exports_schema_without_tenant_fields():
    tools = anthropic_tools()
    names = {t["name"] for t in tools}
    assert names == {"search_documents", "create_note"}
    for t in tools:
        props = t["input_schema"]["properties"]
        assert "org_id" not in props and "user_id" not in props


def test_validate_args_rejects_extra_field():
    spec = REGISTRY["create_note"]
    with pytest.raises(ValidationError):
        validate_args(spec, {"title": "x", "body": "y", "org_id": "sneaky"})


def test_validate_args_rejects_tenant_injection_on_search():
    # The confused-deputy defense: a model-supplied org_id is refused before execution.
    spec = REGISTRY["search_documents"]
    with pytest.raises(ValidationError):
        validate_args(spec, {"query": "x", "org_id": "evil-tenant"})


def test_validate_args_rejects_oversized_body():
    spec = REGISTRY["create_note"]
    with pytest.raises(ValidationError):
        validate_args(spec, {"title": "x", "body": "z" * 4001})


def test_validate_args_rejects_oversized_title():
    spec = REGISTRY["create_note"]
    with pytest.raises(ValidationError):
        validate_args(spec, {"title": "t" * 201})


def test_validate_args_rejects_oversized_query():
    spec = REGISTRY["search_documents"]
    with pytest.raises(ValidationError):
        validate_args(spec, {"query": "q" * 1001})


def test_validate_args_accepts_valid_search():
    spec = REGISTRY["search_documents"]
    args = validate_args(spec, {"query": "quarterly numbers"})
    assert isinstance(args, SearchDocumentsArgs)
    assert args.query == "quarterly numbers"


def test_validate_args_accepts_valid_note_with_default_body():
    spec = REGISTRY["create_note"]
    args = validate_args(spec, {"title": "Standup"})
    assert isinstance(args, CreateNoteArgs)
    assert args.title == "Standup"
    assert args.body == ""
