"""Scorecard data: categories, thresholds, and the test-to-category mapping.

Thresholds are fixed here BEFORE the first scorecard run (Week 9 spec). A miss is
reported, never re-thresholded. This file is data only; scoring lives in scorecard.py.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    kind: Literal["deterministic", "model", "watch"]
    threshold: str
    required: bool


DET = "100% of tests pass"
MODEL = "every case passes in every one of N runs"

CATEGORIES: list[Category] = [
    Category("structural", "Structural (prompt slot, roles, hashing)", "deterministic", DET, True),
    Category("cross_tenant", "Cross-tenant isolation", "deterministic", DET, True),
    Category("approval_gate", "Approval gate", "deterministic", DET, True),
    Category("output_handling", "Output handling", "deterministic", DET, True),
    Category("trajectory", "Trajectory", "deterministic", DET, True),
    Category("tool_abuse_limits", "Tool abuse and rate limits", "deterministic", DET, True),
    Category("direct_injection", "Direct injection", "model", MODEL, True),
    Category("indirect_injection", "Indirect injection", "model", MODEL, True),
    Category("tool_injection", "Tool injection", "model", MODEL, True),
    Category("known_residuals", "Known residuals", "watch", "behavior unchanged (pinned)", True),
    Category("platform_hygiene", "Platform hygiene (ruff, Trivy, grants)", "deterministic", "0 findings", True),
    Category("other_deterministic", "Other deterministic", "deterministic", DET, True),
]

# On a fork PR, secrets are unavailable by design, so these rows are not required
# (spec "Settled" item 2). Everything else is required on every event.
FORK_PR_OPTIONAL = frozenset(
    {"direct_injection", "indirect_injection", "tool_injection", "known_residuals"}
)

# Promptfoo suites: category key -> the Promptfoo config that feeds it.
PROMPTFOO_SOURCES = {
    "direct_injection": "evals/promptfooconfig.yaml",
    "indirect_injection": "evals/promptfooconfig.indirect.yaml",
    "tool_injection": "evals/promptfooconfig.tools.yaml",
    # Residual cases ride in the direct-injection run (the config includes this file).
    "known_residuals": "evals/known-residuals.yaml",
}

# Prompt functions (evals/chat_prompt.py) that always serve the ACTIVE chat template:
# chat_active reads ACTIVE_CHAT_VERSION, and the *_rag ones go through
# assemble_chat_prompt. Any other gate prompt must be chat_v<ACTIVE_CHAT_VERSION>.
ACTIVE_PROMPT_FUNCTIONS = frozenset({"chat_active", "chat_v4_rag", "chat_v5_rag"})

# Promptfoo cases whose description starts with this are routed to known_residuals
# (evals/promptfooconfig.yaml includes known-residuals.yaml).
RESIDUAL_DESCRIPTION_PREFIX = "KNOWN RESIDUAL"

# Test file (repo-relative) -> category key. Every test file maps to exactly one key;
# a file not listed here falls to other_deterministic.
FILE_MAP: dict[str, str] = {
    # Structural
    "apps/api/tests/test_prompting.py": "structural",
    # Cross-tenant isolation
    "tests/rls/test_isolation.py": "cross_tenant",
    "tests/rls/test_chat_isolation.py": "cross_tenant",
    "tests/rls/test_rag_isolation.py": "cross_tenant",
    "tests/rls/test_tool_calls_isolation.py": "cross_tenant",
    "apps/api/tests/test_attacks.py": "cross_tenant",
    "apps/api/tests/test_chat_attacks.py": "cross_tenant",
    "apps/api/tests/test_tool_attacks.py": "cross_tenant",
    "apps/api/tests/test_orgs.py": "cross_tenant",
    "apps/api/tests/test_auth.py": "cross_tenant",
    "apps/api/tests/test_documents.py": "cross_tenant",
    # Choice: the RR-W8-4 anon-grant class test and the record_model_call membership
    # check are grant/privilege hygiene on the database, so they live in platform_hygiene
    # (the spec lists "grants" under platform hygiene), not cross_tenant.
    "tests/rls/test_definer_grants.py": "platform_hygiene",
    "tests/rls/test_record_model_call_membership.py": "platform_hygiene",
    # ruff and Trivy outcomes arrive as synthetic junit cases from the runner.
    "scorecard/ruff": "platform_hygiene",
    "scorecard/trivy": "platform_hygiene",
    # The runner's static check that every gate config scores the active chat template.
    # Platform hygiene, next to the other synthetic checks: in structural it would make
    # that row look run when pytest never ran.
    "scorecard/prompt_pin": "platform_hygiene",
    # Approval gate
    "tests/rls/test_pending_actions.py": "approval_gate",
    "apps/api/tests/test_actions.py": "approval_gate",
    "apps/api/tests/test_approval_gate.py": "approval_gate",
    "apps/api/tests/test_approval_flags.py": "approval_gate",
    "apps/web/src/app/(app)/chat/pendingState.test.ts": "approval_gate",
    "apps/web/src/lib/actions.test.ts": "approval_gate",
    # Output handling
    "apps/api/tests/test_output_handling.py": "output_handling",
    "apps/api/tests/test_allowlist_sources.py": "output_handling",
    "apps/api/tests/test_redaction.py": "output_handling",
    "apps/web/src/app/(app)/chat/AssistantMarkdown.test.tsx": "output_handling",
    "apps/web/src/app/(app)/chat/ApprovalCard.test.tsx": "output_handling",
    "apps/web/src/app/(app)/chat/ToolIndicator.test.tsx": "output_handling",
    # Trajectory
    "apps/api/tests/test_trajectories.py": "trajectory",
    # Tool abuse and rate limits
    "apps/api/tests/test_tools.py": "tool_abuse_limits",
    "apps/api/tests/test_limits.py": "tool_abuse_limits",
    "apps/api/tests/test_tool_pipeline.py": "tool_abuse_limits",
    "apps/api/tests/test_tool_turn_integration.py": "tool_abuse_limits",
    # Indirect injection (structural half: provenance, RAG assembly)
    "apps/api/tests/test_provenance.py": "indirect_injection",
    "apps/api/tests/test_rag_chat.py": "indirect_injection",
    # Other deterministic (core chat plumbing, the scorecard's own tests, web client)
    "apps/api/tests/test_chat.py": "other_deterministic",
    "apps/web/src/lib/api.test.ts": "other_deterministic",
    "tests/scorecard/test_scorecard.py": "other_deterministic",
    "tests/scorecard/test_runner.py": "other_deterministic",
}

# (file, test-name prefix, category key). Scoped to a file so a stray test elsewhere
# that happens to share the prefix is not swept into a WATCH row. Applied before FILE_MAP.
NAME_OVERRIDES: list[tuple[str, str, str]] = [
    ("apps/api/tests/test_redaction.py", "test_residual_", "known_residuals"),
]
