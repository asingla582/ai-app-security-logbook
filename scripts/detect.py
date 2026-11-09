"""Week 10: run the audit detection rules across every org. Exits 1 on any high finding.

  set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate
  python ../../scripts/detect.py
"""

import json
import os

import psycopg
from app.db import db_for_user
from app.tools import REGISTRY

DB_URL = os.environ["SUPABASE_DB_URL"]
TOOLS = [spec.name for spec in REGISTRY.values() if spec.requires_approval]


def main() -> int:
    with psycopg.connect(DB_URL) as admin:
        owners = admin.execute(
            "select distinct on (org_id) org_id, user_id from memberships "
            "where role = 'owner' order by org_id, user_id"
        ).fetchall()
    high = total = 0
    for org_id, owner in owners:
        with db_for_user(str(owner)) as conn:
            rows = conn.execute(
                "select rule, severity, ts, correlation_id, user_id, subject_id, detail "
                "from audit_anomalies(%s, %s)", (org_id, TOOLS),
            ).fetchall()
        for rule, severity, ts, cid, user_id, subject, detail in rows:
            total += 1
            high += severity == "high"
            print(json.dumps({"org_id": str(org_id), "rule": rule, "severity": severity,
                              "ts": ts.isoformat(), "correlation_id": cid,
                              "user_id": str(user_id), "subject_id": subject, "detail": detail}))
    print(f"{total} finding(s), {high} high, across {len(owners)} org(s)")
    return 1 if high else 0


if __name__ == "__main__":
    raise SystemExit(main())
