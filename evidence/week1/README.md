# Week 1 Evidence: Cross-Tenant Isolation

The claim: one organization cannot reach another organization's data, and every
attempt to try is refused and recorded. This directory is the proof.

## What was attacked

Two tenants, Alice (Org A) and Bob (Org B), each with a private note. Acting as
Alice, we attempted to reach Bob's tenant five ways:

| # | Attack | Layer | Result |
|---|--------|-------|--------|
| 1 | Read Bob's notes via a tampered org id in the URL | API | 404, denied + logged |
| 2 | Read Bob's member list via a tampered org id | API | 404, denied + logged |
| 3 | Write a note into Bob's org | API | 404, denied + logged |
| 4 | Call the API with no token | API | 401 |
| 5 | Call the API with an expired token | API | 401 |

At the database layer, the same isolation is proven directly (`tests/rls/`): as
Alice, `SELECT` and `INSERT` against Bob's rows return nothing / are rejected by
Row Level Security, independent of the API.

**Review finding (fixed):** while checking that the database-layer claim actually
held, the original `membership_insert` policy was found to allow any authenticated
user to insert a membership row, i.e. grant themselves into another org and then
read its rows. The API never exposed this, but RLS is the primary wall, so the gap
mattered. It was closed by tightening: membership inserts are now restricted to
org owners, and org creation runs through a `SECURITY DEFINER` function so the
first owner can be bootstrapped without loosening any read policy. A regression
test (`test_alice_cannot_grant_herself_into_bob_org`) locks it in.

## What held

Everything. Two independent walls stopped every attempt:

1. **Row Level Security** in Postgres. A query issued as Alice physically cannot
   see or write Bob's rows. This is the primary control.
2. **Application authorization.** The API checks membership before every
   org-scoped action and logs an `allow`/`deny` decision with a correlation id.
   This is defense-in-depth, not the only wall.

`attack-run.txt` shows each attack denied, with the `deny` audit line emitted at
the moment of refusal.

## Reproduce it

```
make up        # start Supabase + app, seed Alice and Bob
make attack    # run the cross-tenant attack suite, regenerate this evidence
```

## Honest residual note

This week has no AI in it, on purpose. The question was what boundaries must
exist *before* a model is introduced. The isolation shown here is strong because
it is enforced by the database, not by application code that could be bypassed
and not by anything a model is trusted to decide. That property is the
foundation everything in later weeks is built to preserve.
