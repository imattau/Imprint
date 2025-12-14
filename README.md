# Imprint

Imprint is a FastAPI web app for Nostr long-form (NIP-23) publishing. It serves HTML via Jinja2 templates, provides HTMX-powered interactivity, and stores data in SQLite using SQLAlchemy. A background task indexes content from configured relays.

## Prerequisites
- Python >= 3.11
- [Poetry](https://python-poetry.org/) for dependency management

## Quickstart
1. Install dependencies:

```bash
poetry install
```

2. Copy the example environment file and fill in your details:

```bash
cp .env.example .env
```

Update the `.env` with one of `NOSTR_NSEC` or `NOSTR_SK_HEX`, your relay list, a session secret, and optional host/port overrides.

3. Run the development server (hot reload enabled):

```bash
make run
# or: poetry run python tasks.py run
```

Open http://localhost:8000 to use the app.

## Managing environment
Key variables consumed via `.env` (see `.env.example`):
- `NOSTR_NSEC` or `NOSTR_SK_HEX`: private key for signing events (do **not** commit real keys). When present, the UI exposes a "Local (server)" signer option.
- `NOSTR_RELAYS`: comma-separated list of relays.
- `DATABASE_URL`: defaults to `sqlite+aiosqlite:///./imprint.db`.
- `SESSION_SECRET`: required for session cookies (defaults to a development value).
- `NIP46_RELAY`: optional default relay used when bootstrapping Nostr Connect (NIP-46).
- `APP_HOST` / `APP_PORT`: optional bind settings for development.

## Common tasks
All tasks are exposed through both the `Makefile` and `tasks.py` runner:

- Install deps: `make install`
- Run server: `make run`
- Run tests: `make test`
- Format code: `make format`
- Lint (Ruff + mypy): `make lint`
- Initialize the database schema (create tables via SQLAlchemy metadata): `make db`
- Clean caches: `make clean`

## Authentication & signers
- A session cookie tracks the active signer. Modes include:
  - **Extension (NIP-07)**: browser extensions provide the pubkey and sign events client-side.
  - **Remote signer (NIP-46)**: connect to a bunker:// URI or pubkey+relay; the server requests signatures over a relay.
  - **Read-only**: supply an `npub` to browse without signing.
  - **Local (server)**: if `NOSTR_NSEC`/`NOSTR_SK_HEX` is configured, the server signs on your behalf.
- Open the header **Sign in** control to switch accounts or sign out. Sessions include an expiry option (15m, 1h, 24h, or until the browser closes).
- Security notes:
  - The browser flow never asks for `nsec` secrets.
  - NIP-07 signed events are validated server-side (id/sig/pubkey) before publishing.
  - NIP-46 sessions create an ephemeral client secret per session and record the chosen relay.

## Admin console
- Administrators manage instance-wide settings at `/admin`.
- Configure either or both access gates:
  - `ADMIN_TOKEN`: secret string entered into the admin login form.
  - `ADMIN_NPUBS`: comma-separated npubs allowed to elevate. A matching signed-in npub automatically gains admin status.
- Admin POST forms use a session-scoped CSRF token; admin state is stored separately from user sessions via `is_admin`.
- The settings page controls:
  - Branding: `site_name`, `site_tagline`, `site_description`, `public_base_url`, `theme_accent`.
  - Discovery: `default_relays` (fallback for publishing/indexing), `max_feed_items`, `enable_public_essays_feed`.
  - Sessions: `session_default_minutes`, `enable_registrationless_readonly`.
  - Admin identity & payments: `instance_nostr_address`, `instance_admin_npub`, `lightning_address`, `donation_message`, `enable_payments`.
- Footer and header reflect the saved settings (site name, contact identity, optional Lightning donation line). The homepage hero copies the tagline/description.

## Publishing workflow
1. Visit `/editor` to create a draft. Use **Save draft** to store locally without publishing.
2. Use **Publish** to create a Nostr long-form (kind 30023) event with tags:
   - `d`: stable identifier
   - `title`: essay title
   - `published_at`: Unix timestamp
   - `version`: monotonically increasing integer
   - `status`: `published`
   - `summary`: optional
   - `supersedes`: prior event id when revising
3. The signed event is sent to all configured relays and persisted locally with the event id.

## Relay indexing
A background task connects to configured relays and subscribes to long-form events. Only events with `d` and `title` tags and content length over 30 characters are indexed. Signatures are verified before storage. Increase or change relays in `/settings` or via `NOSTR_RELAYS`.

## Relay management
Use the Settings page to add/remove relays. A quick connectivity test is available. Relays are stored in the local database.

## Testing
Run the test suite with:

```bash
make test
```

Tests cover event signing/verification, version increment logic, and database relationships.

## Browsing essays
- The homepage includes a **Recently published** section showing the latest version of each essay.
- Use `/essays` to browse the full list with `?days=7`, `?author=npub...`, or `?tag=topic` filters and load-more pagination.

## Notes and assumptions
- The app expects a single private key via `NOSTR_NSEC` (NIP-19) or `NOSTR_SK_HEX`. The key is only used in-memory; never log it.
- The UI is kept lightweight with HTMX for partial updates and Markdown previews rendered server-side.
- Relays are treated as untrusted; events are signature-checked before indexing.
