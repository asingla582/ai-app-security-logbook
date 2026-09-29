import pytest

from app.limits import RateLimited, check_org_model_budget, check_user_tool_rate


class FakeConn:
    """Returns a fixed count for the single count(*) query the limit checks run."""

    def __init__(self, count):
        self._count = count

    def execute(self, sql, params=None):
        count = self._count

        class R:
            def fetchone(self):
                return (count,)

        return R()


def test_org_budget_allows_under_limit():
    check_org_model_budget(FakeConn(199), "org")  # 199 < 200, no raise


def test_org_budget_blocks_at_limit():
    with pytest.raises(RateLimited) as e:
        check_org_model_budget(FakeConn(200), "org")
    assert e.value.scope == "org_model_budget"
    assert e.value.retry_hint


def test_user_tool_rate_allows_under_limit():
    check_user_tool_rate(FakeConn(9), "user")  # 9 < 10, no raise


def test_user_tool_rate_blocks_at_limit():
    with pytest.raises(RateLimited) as e:
        check_user_tool_rate(FakeConn(10), "user")
    assert e.value.scope == "user_tool_rate"
    assert e.value.retry_hint
