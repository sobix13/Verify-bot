# Future updates and adapter development

The current implementation is Solana mainnet plus an explicit authenticated platform bridge. It is extensible source code, not a universal decoder for unknown contracts. New chains/programs/payment policies need a reviewed implementation and tests.

## Stable extension boundaries

| Area | Files | Required behavior |
| --- | --- | --- |
| Identity proof | `auth.py`, `web.py`, `member_ui.py` | Bind origin/nonce/user/server/purpose; single use; no private keys; authenticated backend identity |
| Onchain balances/assets | `chain.py`, `network.py` | Correct chain/finality, ownership, complete pagination, exact raw amounts; failure must not be zero |
| Backend integration | `platform.py`, API_CONTRACT | Versioned identity-matched complete snapshots and settled stable receipt IDs |
| Financial accounting | `storage.py`, `chain.py`, `engine.py` | Canonical idempotent event IDs, integer USD units, immutable valuation, source-method migration protection |
| Eligibility | `config.py`, `engine.py`, `bot.py` | Separate metrics, complete data only, dedicated safe roles, registry ownership and revision check |
| Public feed | `platform.py`, `web.py`, `bot.py` | Signed bounded events, persistent dedup/outbox and uncertain delivery review |
| Admin features | `admin_ui.py`, `commands.py` | Short branch explanations; owner-bound private views; authorization and strict settings schema |
| Operations | `backups.py`, `operations.py`, scripts | Redacted actionable errors, authenticated backups, paused restore and tested releases |

## Adding a new program or payment source

If it uses ordinary parsed SPL transfers with a known stable USD policy, add a source in Discord; a code update is unnecessary. Multiple programs/owners/token accounts and mints are already configurable.

For program-specific escrow, routed swaps, fee-bearing Token-2022 transfers, variable-price SOL, non-parsed custom instructions or net settlement calculations, write a dedicated parser or use platform receipts. Preserve the canonical transaction+instruction identity, prove user attribution, distinguish deposits from fees/internal movement and document historical price evidence. An event name or wallet interaction alone is not a deposit.

A future alternative data provider should implement the same complete ownership snapshot, stable receipt pagination and failure semantics. Never add a fallback that converts unknown data to an empty list or credits both an RPC payment and its platform receipt.

## Adding another blockchain

Add an explicit chain setting, network identity/finality validation, wallet signature proof bound to that chain, address types, asset/mint selectors and canonical event namespace. Introduce it behind a disabled feature flag and a schema migration. The present Solana challenge and addresses cannot be reused to prove EVM ownership. Test wrong-chain signatures, chain reorganization/finality policy, NFT types and multi-chain receipt deduplication.

## Larger backend histories

Current platform pagination caps a check at 100 pages. For larger histories, add a persisted account receipt checkpoint with a stable snapshot/watermark contract. Commit receipts idempotently, advance the cursor only after processed records are durably credited, and hold financial-role changes until coverage is complete. Provide interruption, replay, cursor-expiry and backend snapshot-change tests.

## New eligibility features

Possible reviewed additions include exclusive highest-tier groups, composite all/any metric requirements, source-specific deposit campaigns, expiring contribution credits, multiple car clubs and per-role display policies. Keep proof-based roles separate from notification claims and human OG decisions. These are extension suggestions, not features enabled by this release.

Raffle already consumes Discord role eligibility; do not make it a second financial calculator. Crew remains responsible for moderation/support and Gate for entry.

## Migrations

1. Increment VERSION and, if needed, the database schema version.
2. Export settings and create a full encrypted VPS backup before changing data.
3. Add transactional schema migration and compatibility checks; never silently discard unknown fields/financial events.
4. A deposit-method change requires mapping old canonical receipts to the new source and demonstrating no overlaps, owner changes or repricing. Provide an audited dry-run diff before writing a migration tool.
5. Identity reassignment must move or preserve the appropriate historical ledger ownership explicitly. A simple tombstone deletion would allow reuse and is insufficient.
6. Add tests for old database upgrade, incomplete migration rollback and restoration. Update settings/API/coordination guides.
7. Stage in a clean virtual environment and test a separate Discord server/application with actual provider fixtures.
8. Activate code only after acceptance. A schema-incompatible rollback requires the release's reviewed restore/migration path.

## Secrets and provider changes

Pause the affected feature, configure new credentials on the VPS and backend, restart, run probes and reactivate. Public configuration is available in Discord; secrets remain in the protected environment. Keep old backup passwords while their files exist. Never store RPC credentials in exported settings, release packages or shared ownership records.

## Adding future bots

Use namespaced persistent component IDs, private databases, the same shared resource schema and setup lease, explicit role ownership and human-approved resource handoff. Another bot that ignores the protocol can still create duplicates/conflicts; registry cooperation and Discord hierarchy are both necessary. Do not claim that Verifier can automatically control any arbitrary external bot.
