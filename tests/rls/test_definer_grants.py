"""Catalog-wide check: no SECURITY DEFINER function in `public` is executable by anon.

Postgres grants EXECUTE to PUBLIC on new functions by default, and Supabase also
default-grants it to anon, so any definer function is callable without a login
unless a migration revokes it. This checks the whole catalog, not a hand-kept list
(RR-W8-4).
"""

import psycopg
import pytest

from .conftest import DB_URL, _supabase_reachable

pytestmark = pytest.mark.skipif(
    not _supabase_reachable(), reason="local Supabase stack not reachable"
)

# Explicit allowlist of regprocedure text. Empty on purpose: an anon-callable
# SECURITY DEFINER function runs with its owner's rights for unauthenticated
# callers, so any future entry needs a written justification next to it.
ANON_ALLOWED: frozenset[str] = frozenset()

_DEFINER_SQL = """
select p.oid::regprocedure::text, has_function_privilege('anon', p.oid, 'EXECUTE')
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef
"""


def test_no_definer_function_is_anon_executable():
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(_DEFINER_SQL).fetchall()
    # Guard against a vacuous pass (wrong namespace, empty catalog).
    assert rows, (
        "no SECURITY DEFINER functions found in schema public; the catalog query is wrong"
    )
    exposed = {sig for sig, anon_ok in rows if anon_ok} - ANON_ALLOWED
    assert not exposed, "SECURITY DEFINER functions executable by anon:\n" + "\n".join(
        sorted(exposed)
    )
