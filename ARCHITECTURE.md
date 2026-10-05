# Architecture and accounting

## Components

| Component | Responsibility | Persistent evidence |
| --- | --- | --- |
| Discord member panel | Gate authorization, private connection links, status, refresh, disconnect | Link/session ownership, queued job |
| HTTPS service | Wallet challenge verification, signed backend events, health endpoint | Consumed nonce, webhook replay record, announcement outbox |
| Data adapters | Finalized RPC, DAS ownership, authenticated backend snapshots | Source-specific snapshot and history cursor |
| Deposit ledger | One credit per canonical payment event | Immutable event ID, USD micro-units, owner, source and proof |
| Role worker | Threshold decisions on complete data; owned-role updates only | Snapshot revision and role grant state |
| Shared registry | Explicit ownership and cross-bot setup exclusion | Existing Rip Cars resource/lease schema |
| Administration | Configuration, channel IDs, optional channel creation, diagnosis, settings history | Compare-and-swap settings revisions and audit records |
| Recovery | Encrypted SQLite snapshots and paused restore | Authenticated backup manifest and integrity check |

Private SQLite uses WAL and serialized transactions. The shared registry remains a separate file. Financial credits, nonce consumption, configuration changes and event enqueueing have transactional deduplication. The gateway, HTTP listener and scheduled worker share one process; systemd monitors its tasks with a watchdog.

## Identity boundaries

The Discord user/server ID comes from a checked server interaction, not a browser-provided identity. Gate membership or administrative authority is required to request a connection.

Wallet verification binds a ten-minute single-use nonce, domain, public key, Discord ID, server ID and purpose in the exact message signed by Ed25519. Session tokens are random and stored only as hashes. Browser requests require the exact HTTPS Origin. A new challenge invalidates the previous message. A consumed, expired, changed or wrong-wallet message is rejected.

The login message is a SIWS-style explicit message, not a transaction and not a full Wallet Standard `signIn` implementation. The page uses injected `signMessage`. No wallet private key, seed phrase, access token or wallet transaction is requested.

A platform account ID is trusted only when the team backend authenticates that account and signs the link callback. Reading a wallet or account ID from an unauthenticated browser is not proof. A linked identity stays reserved to its Discord owner after disconnection so another account cannot claim its historical credits. A changed Discord owner needs an audited migration.

## Data completeness

`deposit_points`, `platform_points`, `token_balance` and `asset_count` are separate metrics. Each has an explicit completeness flag. An unavailable provider, stale snapshot, invalid response, repeated pagination cursor, unknown archival transaction or wrong Solana cluster raises an error; it is never converted into zero holdings.

The last persisted snapshot and roles remain unchanged on a failed refresh. A partial onchain backfill may append valid credits but marks deposit completeness false; deposit-role changes wait until history coverage completes. Known absence of a connected wallet/account is distinct from provider failure and can remove current holding eligibility. Disconnection does not erase previously earned deposit credits.

## Accounting policy

One USD of verified settled deposits earns one deposit point. Values are stored as integer USD micro-units; display/rule calculations use decimal arithmetic. Backend receipts require exact USD strings with at most six decimals. Parsed SPL values below a micro-unit are rounded down per transfer; sub-micro transfers earn zero. No floating-point USD calculations are used.

RPC mode considers only parsed legacy SPL `transfer`/`transferChecked` instructions in a finalized successful transaction, including inner CPI instructions. The source token account's balance metadata must identify the linked owner, both sides must identify the same mint/decimals, the destination must match an approved treasury or exact token account, and any configured platform program must execute. Matching more than one source does not duplicate an instruction credit.

Canonical onchain event IDs include transaction signature and the exact instruction location (`solana:<signature>:<top-index>:root` or `solana:<signature>:<top-index>:<inner-index>`). Two actual transfers in one transaction can be two deposits; one transfer matching several configured sources remains one deposit.

Platform mode accepts only the authenticated backend's `kind=deposit`, `status=settled` receipts, with stable per-account IDs and historical USD amounts. It does not infer deposits from pack count, swap volume, gross trade volume, withdrawals, returned assets, refunds, fees or sales. A platform pack-open event cannot produce points.

The RPC adapter identifies approved wallet-to-destination payments. If the platform distinguishes a purchase, deposit, escrow or fee using custom contract state/logs, a plain destination transfer is not enough to determine that business meaning. Use platform receipts or a reviewed program-specific adapter. Configure only actual deposit destinations. Failed transaction signatures do not credit; successful known platform interactions that are not deposits can be recorded for audit without earning points.

Valuation in an RPC source is an explicit fixed USD-per-token policy. The bot does not pretend that today's SOL/$CARS price is a historical price. Variable-value payments, SOL, card payments, unsupported custom instructions and fee-bearing Token-2022 deposits use settled platform USD receipts. Token-2022 current balances are supported; Token-2022 deposit parsing is deliberately excluded until a fee-aware adapter exists.

Exactly one authoritative deposit method operates per server. Existing credits prevent switching from RPC to platform or mixing both ledgers without an audited migration. Existing event owner/amount changes cause a conflict rather than silently rewriting totals. Financial source changes trigger resumable backfill and preserve deduplicated credits. They cannot automatically undo a past credit.

Historical scanning covers available finalized signatures on each **linked wallet**, from the configured start time. It does not enumerate the entire chain or discover every unlinked platform customer. It does not reliably recover obsolete ownership from an undocumented program or a provider without transaction history. Use a team accounting export for full platform business history.

## Assets and balances

RPC sums current raw token account units for the verified mint and connected owner, rather than trusting a rounded `uiAmount` float. DAS reads paginated current ownership, excludes burnt/non-NFT assets and uses the provider's verified collection filter plus the admin collection allowlist. Accepted interfaces include ordinary NFTs, programmable NFTs and Metaplex Core assets. DAS can support compressed assets when its provider supplies them. Platform mode can supply custodial/digital assets that are not visible in a user's personal wallet.

Model roles should use approved asset IDs, collection or exact platform traits. A display name alone is not sufficient proof of asset identity. The current snapshot counts distinct IDs. The bot does not fetch arbitrary NFT metadata URLs to discover trusted financial or role data.

Custom traits are provider-dependent, and standard DAS inventory responses may omit them. A missing required trait is unknown evidence for that role. Known matching assets can still prove a threshold; otherwise that role is preserved and rechecked. Use a backend trait snapshot or explicit approved asset IDs when the DAS provider lacks model traits.

## Role ownership

The bot creates/adopts zero-server-permission qualifying roles. It never adopts OG, Rippers, staff roles or claimable notification roles. The actual role must be below its bot role, unchanged from its recorded baseline and registered to this bot/rule. A configuration revision is rechecked before each mutation. Each Discord mutation also requires the active shared setup lease.

Preexisting manual grants are preserved. An owned role manually removed while still qualified becomes a manual override; automation waits for admin review. Pinned, externally owned, renamed or permission-changed roles stop that operation. Discord HTTP failures leave a pending owned grant record for a subsequent verified check, rather than assuming success.

## Announcement delivery

The backend sends a signed stable event. HMAC verification uses raw body bytes, a five-minute timestamp window and an envelope ID. The stable pack ID deduplicates across retries; changed payload with a reused ID is rejected. Enqueue and replay reservation are one transaction.

Outbox states are `pending`, `sending`, `sent`, `review` and `discarded`. The worker marks `sending` before sending. A definitive permission failure returns to pending with delay. An ambiguous network/cancellation result becomes `review` to avoid automatic duplicate posts. Restart also moves interrupted `sending` records to review. Admin can verify an existing message by event footer, retry or discard.

This release chooses conservative manual resolution of uncertain Discord sends. It does not claim universal exactly-once delivery across Discord and SQLite, which have no shared transaction.

## Operational protection

Outbound URLs require public HTTPS, normal TLS verification and public DNS addresses; redirects and proxy environment variables are disabled. Private/reserved IPs, DNS rebinding to intranet addresses, oversized responses and non-finite JSON are rejected. RPC primary and fallback must prove the pinned Solana mainnet genesis hash before data reads. Credentials are server-only and redacted from persisted error messages.

Public HTTP work has bounded request size and a process rate ceiling; nginx provides IP-level rate limiting. Member refresh has a one-minute cooldown. Jobs coalesce and provider requests are bounded/retried. A full production capacity assessment needs your actual member count, history volume and provider quotas; adjust budgets conservatively in exported Discord settings.

Backups use AES-GCM and a Scrypt-derived key. Discord exports are server-scoped; full daily backups stay in a protected VPS directory. Restoration verifies authenticated content and SQLite integrity, restores into a new file, clears link sessions, and pauses automation. Secrets and other bots' registry ownership are outside the backup.

## Primary technical references

The adapters were checked against these primary sources; the implementation and backend contract here are original project code.

- [Solana getTransaction](https://solana.com/docs/rpc/http/gettransaction)
- [Solana getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress)
- [Solana getTokenAccountsByOwner](https://solana.com/docs/rpc/http/gettokenaccountsbyowner)
- [Solana getGenesisHash](https://solana.com/docs/rpc/http/getgenesishash)
- [Solana SDK mainnet genesis constant](https://github.com/solana-labs/solana/blob/master/sdk/src/genesis_config.rs)
- [Helius DAS getAssetsByOwner](https://www.helius.dev/docs/api-reference/das/getassetsbyowner)
- [Phantom signing a message](https://docs.phantom.com/solana/signing-a-message)
- [Circle USDC contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses)
- [Rip Cars terms](https://ripcars.io/terms)
