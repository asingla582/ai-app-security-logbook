import json
import sys


def audit(correlation_id: str, user_id: str, org_id: str | None, action: str, outcome: str) -> None:
    print(
        json.dumps(
            {
                "correlation_id": correlation_id,
                "user_id": user_id,
                "org_id": org_id,
                "action": action,
                "outcome": outcome,
            }
        ),
        file=sys.stderr,
    )
