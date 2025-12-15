# Imprint Implementation Audit

## Feature map (current vs expected)
- **Authentication/Sessions**: Nip07, Nip46, local signer, readonly sessions; session cookies, navbar state, auth modal. Expected: enforce signer for publishing; readonly blocked from signing flows.
- **Editor/Publish**: Markdown/Visual editor (EasyMDE), HTMX preview, expand; publish to relays; drafts CRUD; identifier-based revisions; revert endpoint. Expected: author-only revisions; drafts tied to author; identifier reuse blocked across authors.
- **Feeds/Essays**: Home/essays listing with filters (author/tag/days/imprint flag); detail page with history; history/revisions per author; recent fragment for HTMX partials.
- **Engagement**: Likes and zap modal; engagement batch endpoints; engagement bar in listings. Expected: batch engagement without N+1, accurate counts, zap invoice flow.
- **Admin**: Settings (relays, theme, blocked pubkeys), backup/restore, auth token; instance settings in templates.
- **Relays**: Relay client with publish/fetch, backoff, TTL cache; indexer toggled by settings.
- **Payments/Zaps**: Zap modal, invoice generation; lightning address fallback placeholder.
- **Tests**: Pytest suite covering auth, drafts, engagement, history, etc.

## Known gaps / stubs / placeholders
| Severity | File:Line | Finding | Notes |
| --- | --- | --- | --- |
| High | app/main.py:326-345 | Editor load for identifier does not enforce author in history listing; fixed partially but broader ACL needs audit | P0 ACL |
| High | app/main.py:374-415 | Publish allows identifier if existing belongs to other author (now guarded) but drafts/revert need consistent checks | P0 ACL |
| High | app/main.py:675-713 | Revert requires ownership; relies on DB only, no relay check | |
| High | app/nostr/relay_client.py | Relay publish/fetch bypassed in tests; no rate limiting beyond simple backoff; no batching beyond per-call | P0 relay reliability |
| High | app/templates/fragments/essays_list.html | Engagement bar previously interactive in partials causing HTMX cascades; now rendered with flag but underlying counts still zeroed | P1 engagement correctness |
| Medium | app/services/essays.py | `get_draft` returns any draft without author filter; consumers must check | tighten ownership |
| Medium | app/templates/partials/engagement_bar.html | Static counts default to zero; no hydration unless batch endpoint used | Engagement accuracy |
| Medium | app/static/editor.js:207 | TODO for server-side editor mode preference | |
| Medium | app/main.py:148 | Placeholder lightning address for tests | Acceptable for tests; document |
| Medium | app/main.py recent fragment | Partials do not redirect non-HTMX; potential UX mismatch | |
| Low | app/templates/* placeholders | Placeholder inputs in settings/auth modal | Cosmetic |
| Low | app/nostr/relay_client.py | `_should_skip` skips relay I/O in tests; doc this behaviour | |

## Baseline test/lint status
- `poetry run pytest -q` (subset `tests/test_drafts.py tests/test_engagement.py tests/test_revise_authorization.py`) **timed out at 120s in this environment**; full suite not executed. Prior known failure: `test_published_draft_disappears_from_list` (draft publish 404). Needs rerun after fixes.
- No configured linter found; ruff not run.

## Reported bugs (from users/tests)
- P0: Unauthorized revise allowed for non-authors (fixed partially; needs full coverage).
- Editor preview lockup/expand UI issues (recently addressed).
- Test failure: draft publish returning 404 instead of redirect.
- Engagement partials using interactive HX endpoints leading to cascades.

## Notes
- Relay client caches and backoff exist but no batching/queue; relay spam risk if called frequently.
- History/revisions limited to author in queries; detail page history visible to all.
