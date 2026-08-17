# Code Standard

The bar every line in this repo is held to. In a security codebase, code quality and security review are the same discipline: clear code is auditable code.

## Comments

Comment the non-obvious **why**, never the **what**.

A comment earns its place only by explaining something the code cannot say itself: a security trade-off, a subtle authorization decision, a reason the obvious approach was rejected, a non-local invariant a reader would otherwise miss. In a security codebase these comments are content. A one-line note on *why* a check exists is often what makes the check trustworthy to a reviewer.

**Write comments like:**
```python
# RLS handles tenant isolation at the row level; this app-side check is
# defense-in-depth for the case where a query bypasses the policy layer.
```
```python
# Retrieved content is labeled UNTRUSTED here and never upgraded; a document
# cannot promote itself to an instruction, even if it asks to.
```

**Never write comments like:**
```python
# loop through the users        <- narrates the obvious
# increment the counter          <- restates the code
# helper function                <- adds nothing
```

Delete these on sight. Do not add a docstring that merely restates a function's name and signature. Reserve docstrings for public interfaces where the contract (inputs, outputs, raised errors, security assumptions) genuinely needs stating.

## Naming and structure

- Names are precise, not verbose. `authorize_retrieval` over `check_if_the_user_is_allowed_to_retrieve_documents`.
- Small, single-purpose functions. If a function needs a comment to explain its second half, it's two functions.
- No defensive `try/except` around code that can't meaningfully fail. Handle the errors that can actually happen; let the rest surface.
- No dead code, no commented-out blocks. Version control remembers; the file shouldn't.

## Commits and history

- **Conventional commits:** `feat(retrieval): authorize document access at the query layer`.
- **Small, reviewable diffs.** One logical change per commit. A commit a reviewer can understand in under a minute.
- Commit messages explain intent, not mechanics. *Why* this change, not a restatement of the diff.
- PR descriptions and release notes are written in the same voice.

## Review pass (every push)

Before code is pushed, it gets a read, every line. This is both a quality pass and a security pass; they're the same pass.

- [ ] Every comment explains a non-obvious *why*. Narration comments removed.
- [ ] No trivial docstrings; public-interface docstrings state the real contract.
- [ ] Names are precise; functions are single-purpose.
- [ ] No boilerplate defensiveness, no dead code, no commented-out blocks.
- [ ] The diff is small and tells one story.
- [ ] Security review: does this change touch a trust boundary, an authorization check, or untrusted input? If yes, is that reasoning captured in a comment or ADR?
