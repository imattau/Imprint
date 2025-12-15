# Manual Smoke Test Checklist

- [ ] Sign in/out with Nip07/local and readonly; navbar updates correctly.
- [ ] Publish new post, view detail, verify appears in feeds and history.
- [ ] Revise own post: open editor via Revise, publish revision, history increments; attempt non-author revise blocked.
- [ ] Draft lifecycle: save draft, list drafts, publish draft removes it from drafts.
- [ ] Filters: author/tag/days filters on home/essays and partials show expected items.
- [ ] Engagement: like toggles count; zap modal opens, invoice returned (mock/dev).
- [ ] Revert: choose prior revision and publish new version with same content.
- [ ] Relay: publish doesn’t spam (observe logs/backoff); health endpoints OK.
- [ ] Admin: update settings, backup and restore flow completes.
- [ ] Editor UI: Markdown/Visual toggle, preview toggle, expand/collapse.
