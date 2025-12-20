# Post storage investigation

- **Current DB path:** resolves to `sqlite+aiosqlite:///./imprint.db` for normal runs; test runs are forced to `sqlite+aiosqlite:///file:imprint_test?mode=memory&cache=shared&uri=true`.
- **Recent posts source:** local SQLite tables `essays`/`essay_versions` queried via `EssayService.list_latest_published`. No relay dependency for the recent feed; filters are author/tag/days/imprint_only.
- **Root cause of disappearance:** `init_models` was dropping and recreating the database whenever `PYTEST_CURRENT_TEST` was set. If a global `DATABASE_URL` pointed at the real dev DB, running tests triggered a drop on that file, wiping posts.
- **Fix applied:** force test runs to use the isolated in-memory test DB regardless of `DATABASE_URL`, add a guard that refuses to reset any database whose URL is not clearly a test database, and log the resolved DB URL at startup for visibility.

This keeps dev data intact, prevents accidental resets during tests, and documents the expected storage locations.
