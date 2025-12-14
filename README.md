# Imprint

Imprint is a minimal long-form publishing app for Nostr (NIP-23) built with FastAPI, Jinja2, and HTMX. It supports Markdown authoring, versioned publishing to relays, and discovery through a background indexer.

## Features
- Write Markdown essays with live previews.
- Publish to Nostr as kind 30023 events with NIP-23 tags and versioning.
- Track revisions and show history for each essay.
- Browse a feed sourced from local cache and background relay indexing.
- Manage relays and view the configured public key (npub) from your environment key.

## Setup
1. Install dependencies with Poetry:

```bash
poetry install
```

2. Set environment variables (examples):

```bash
export NOSTR_NSEC="nsec1..."  # or NOSTR_SK_HEX with 32-byte hex
export NOSTR_RELAYS="wss://relay.damus.io,wss://nos.lol"
```

3. Run the app:

```bash
poetry run uvicorn app.main:app --reload
```

The app uses SQLite by default (`imprint.db`). Override with `DATABASE_URL` if needed.

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
poetry run pytest
```

Tests cover event signing/verification, version increment logic, and database relationships.

## Notes and assumptions
- The app expects a single private key via `NOSTR_NSEC` (NIP-19) or `NOSTR_SK_HEX`. The key is only used in-memory; never log it.
- The UI is kept lightweight with HTMX for partial updates and Markdown previews rendered server-side.
- Relays are treated as untrusted; events are signature-checked before indexing.
