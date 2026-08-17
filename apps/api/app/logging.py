import json
import sys


def audit(correlation_id: str, user_id: str, org_id: str | None, action: str, outcome: str) -> None:
    # Every access decision is recorded, allow AND deny. The deny lines are the
    # evidence trail: when an attack is attempted, the refusal is what we can show.
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
