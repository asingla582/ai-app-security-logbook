"""Create or update .env with the local Supabase values, so a fresh clone just works.

The local anon/service keys are minted by `supabase start` and are not knowable in
advance, so `.env.example` ships them blank. This script reads the running stack's
values from `supabase status -o json` and writes them into `.env`, preserving every
other line (comments, ANTHROPIC_API_KEY, CHAT_MODEL, etc.). Idempotent and safe to
re-run: it never overwrites a non-Supabase value you have set.

Requires the Supabase stack to be running (`npx supabase start` first).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

# supabase status JSON key -> .env variable
FIELDS = {
    "API_URL": "SUPABASE_URL",
    "JWT_SECRET": "SUPABASE_JWT_SECRET",
    "DB_URL": "SUPABASE_DB_URL",
    "ANON_KEY": "SUPABASE_ANON_KEY",
    "SERVICE_ROLE_KEY": "SUPABASE_SERVICE_ROLE_KEY",
}


def supabase_status() -> dict:
    try:
        result = subprocess.run(
            ["npx", "supabase", "status", "-o", "json"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(
            "Could not read Supabase status. Start the stack first with "
            "`npx supabase start`.\n"
            f"({exc})"
        )
    return json.loads(result.stdout)


def main() -> None:
    status = supabase_status()
    values = {var: status.get(key) for key, var in FIELDS.items()}
    missing = [k for k, v in values.items() if not v]
    if missing:
        sys.exit(f"Supabase status did not return: {', '.join(missing)}")

    base = ENV if ENV.exists() else EXAMPLE
    lines = base.read_text().splitlines()

    written = set()
    out = []
    for line in lines:
        match = re.match(r"^([A-Z_]+)=", line)
        if match and match.group(1) in values:
            key = match.group(1)
            out.append(f"{key}={values[key]}")
            written.add(key)
        else:
            out.append(line)
    # Append any Supabase var the template somehow lacked.
    for key, val in values.items():
        if key not in written:
            out.append(f"{key}={val}")

    ENV.write_text("\n".join(out) + "\n")
    print(f"Wrote local Supabase values into {ENV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
