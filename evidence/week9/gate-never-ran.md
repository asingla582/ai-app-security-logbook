# The eval gate never ran in CI

Source: `gh run list -R asingla582/ai-app-security-logbook -L 40` (17 runs before the
Week 9 branch) and `gh run view <id> --log`, grepping for `^evals`.

## What the logs show

12 of the 17 runs predate the `evals` job (their jobs are `api`, `security`, `web`
only). The other 5 had an `evals` job. In all 5 the job's log contains this line:

```
ANTHROPIC_API_KEY secret not available; skipping model-in-the-loop eval.
```

The `evals` job concluded `success` in all 5. The gate ran the model evals 0 of 5
times.

| Run id | Date (UTC) | Commit subject | `evals` job | Skip line logged |
|---|---|---|---|---|
| 33823118774 | 2026-09-04 | fix(evals): correct over-strict leak gate that scored refusals as leaks | success | yes |
| 34234411277 | 2026-09-08 | docs(security): threat model v1 + system card; harden fresh-clone set... | success | yes |
| 35114066596 | 2026-09-16 | evidence(week5): indirect content-poisoning red-team against the live... | success | yes |
| 36758011134 | 2026-09-30 | Merge week 7: secure tool calling | success | yes |
| 37713565008 | 2026-10-08 | Merge week 8: human approval (HITL), v0.8 | success | yes |

Log timestamps for the skip line: 2026-09-04T00:46:21Z, 2026-09-08T13:50:12Z,
2026-09-16T15:15:39Z, 2026-09-30T18:21:59Z, 2026-10-08T01:34:18Z.

The 12 earlier runs (32068013918 through 32670989725, 2026-08-17 to 2026-08-23)
have no `evals` job.

## The v0.8 merge run was red in two other jobs

Run 37713565008 (Merge week 8, 2026-10-08) concluded `failure`. `web` and `evals`
succeeded; `api` and `security` failed.

`api`, ruff:

```
Found 15 errors.
[*] 3 fixable with the `--fix` option.
```

The 15 are 12 E501 (line too long) and 3 F401 (unused import).

`security`, Trivy filesystem scan:

```
Total: 3 (HIGH: 2, CRITICAL: 1)
| next          | GHSA-vcvr-r3jv-pc5j | CRITICAL | fixed | 16.3.3 | 16.3.6 | Next.js: Remote Code Execution in next/og ImageResponse    |
| sharp         | GHSA-wq5f-xc86-pv6w | HIGH     |       | 0.35.4 | 0.35.5 | sharp : Vulnerability in librsvg dependency CVE-2026-96889 |
| source-map-js | CVE-2026-93749      |          |       | 1.2.1  | 1.2.2  | source-map-js: Denial of Service via malformed indexed source maps |
```

The `source-map-js` row leaves the severity cell blank in Trivy's table (it groups
under sharp's HIGH); the total line counts it as the second HIGH.

## An earlier red run

Run 35114066596 (the Week 5 evidence push, 2026-09-16) also failed `security`:

```
Total: 3 (HIGH: 1, CRITICAL: 2)
| next  | CVE-2026-75604      | CRITICAL | fixed | 16.3.1 | 15.5.24, 16.3.3 | Next.js: Unauthenticated Remote Code Execution on ... |
|       | GHSA-2xp9-vwfh-vxw4 |          |       |        |                 | Next.js: Unauthenticated Remote Code Execution in Image ... |
| sharp | GHSA-rgj7-g3m4-5g8c | HIGH     |       | 0.35.3 | 0.35.4          | sharp: Vulnerabilities in libheif ... |
```

## What this means

A green CI check for the model evals meant "the job did not fail", not "the model
evals passed". The scorecard added in Week 9 treats a model row that did not run as
FAIL (except on fork PRs, where the key is unavailable by design).
