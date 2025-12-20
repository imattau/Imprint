# Remediation Plan

## Workstreams and priorities

### P0 (must fix first)
- **Access control (revise/drafts/publish)**  
  - Scope: Editor load, publish (new/revise), drafts save/publish/delete, revert.  
  - Acceptance: Non-author cannot load editor for another identifier, cannot publish/revert; author flows unchanged.  
  - Tests: Extend `tests/test_revise_authorization.py`; add draft publish/save ownership tests.  
  - Risk: Medium (ACL regressions).  
  - Dependencies: Session/auth utilities.

- **Relay publish/receive reliability**  
  - Scope: Ensure relay_client used everywhere, backoff/batching; document test skip; guard against spam.  
  - Acceptance: Publish/fetch limited to configured relays with cooldown; no silent failures; tests mock relay_client.  
  - Tests: Unit around relay_client backoff/cache; publish path asserts relay_client invoked.  
  - Risk: Medium (network).  
  - Dependencies: None.

- **Engagement correctness (no “fake” counts)**  
  - Scope: Remove interactive HX hooks from static partials; ensure batch endpoint hydrates counts; likes/zaps reflect storage/cache.  
  - Acceptance: Recent fragment uses batch engagement; counts consistent for viewer; no redundant HX GETs.  
  - Tests: Update `tests/test_engagement.py` for batch outputs and interactive vs. non-interactive fragments.  
  - Risk: Medium.

- **Feed/filter correctness and partial rendering**  
  - Scope: Ensure `/partials/*` HTMX endpoints mirror filters and handle non-HTMX; tag/days filters applied consistently.  
  - Acceptance: Partial routes return correct slices or redirect on non-HTMX; filters honored.  
  - Tests: Add filter regression tests for partials/full views.  
  - Risk: Low-Medium.

### P1
- **Draft lifecycle & history dedupe**  
  - Scope: Draft ownership filtering; published drafts removed; history latest per identifier.  
  - Tests: Extend draft tests; history counts.  
  - Risk: Medium.

- **Revision history & revert**  
  - Scope: Author-only revert; relays updated; ensure supersedes chain correct.  
  - Tests: Revert regression test.

- **Zap modal & invoice**  
  - Scope: Harden invoice generation; validate lightning address; graceful errors.  
  - Tests: Zap modal happy/error paths.

- **Engagement batching**  
  - Scope: Ensure `/posts/engagement` batches without N+1; cache optional.  
  - Tests: Batch returns all ids; reflects likes state.

### P2
- **Admin UI cleanup + backup/restore**  
  - Scope: Ensure backup/restore paths tested; admin guards.  
  - Tests: Backup/restore regression.

- **Editor UX polish**  
  - Scope: Mode preference persistence; preview toggle stability; expand styling.  
  - Tests/Manual: Manual checklist updates.

## Execution approach
1. Phase P0 tasks sequentially: ACL -> Engagement -> Filters -> Relay resilience. After each, add/adjust tests and run `poetry run pytest -q` (note if environment timeout).  
2. Update this plan and `docs/implementation_audit.md` with status as tasks complete.  
3. Produce `docs/manual_smoke_test.md` with checklist once P0 stabilizes.

## Current status
- Audit complete; tests not yet passing (timeout; draft publish test previously failing).  
- ACL fixes applied (editor/publish/drafts/revert ownership) with new regression tests in `tests/test_acl.py`.  
- Engagement partial fix applied (non-interactive recent). Needs verification.  
- Next action: run pytest suite when environment allows; proceed to engagement batching and filter correctness.
