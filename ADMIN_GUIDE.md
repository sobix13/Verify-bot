# Discord administration

Open `/verifier panel`. Every admin panel is private, bound to its opener, and expires after 15 minutes. Server-side Manage Server/Administrator checks apply to every interaction. Concurrent edits use revision checks; reopen a stale panel instead of overwriting another admin's change.

## Section map

| Section | Decisions and actions |
| --- | --- |
| Overview | Import Gate role, create unset verifier channels, publish/update the connection panel, Doctor, activation/pause |
| Connections | Enable wallet and/or website account links; set the approved account connect URL; wallet limit |
| Data sources | Choose RPC/platform balance, DAS/platform assets, optional native platform points; mint and approved collections |
| Deposit tracking | Off/RPC/platform; guided payment source form, advanced source JSON, historical start, ledger export and rescan |
| Assets and role rules | Add/update dedicated role rules, export/review rules, create or explicitly bind qualifying roles |
| Rip announcements | Enable/skip pack announcements and optional chain refresh hints |
| Channels and access | Gate member role, connection channel, optional pull channel and staff-only log channel |
| Health and backups | Doctor, live probes, encrypted server backup, all-member refresh, interval, settings export and recent errors |
| Public messages | Connection title/description and announcement title; update the existing public message |
| Guide and revisions | Setup instructions, full settings export, saved revision IDs |

Channel selectors retain the selected numeric ID after saving. Each selected channel is also shown by ID. No channels are selected by a fuzzy name match.

## First installation

1. Deploy the service and HTTPS connection site using DEPLOYMENT.md.
2. **Channels and access:** choose Gate's ordinary Rippers role. Importing it from the shared registry avoids duplicate member roles. Do not choose OG or a staff role.
3. Choose an existing member-visible connection text channel and a staff-only log channel. Optionally choose the pack channel. Or use **Overview → Create unset verifier channels** with temporary Manage Channels permission.
4. **Connections:** enable wallet linking. Enable website linking only when the team-approved login page and signed backend callback exist. Both methods are optional independently.
5. **Data sources / Deposit tracking:** enable only implemented, configured integrations. Use the decision table below.
6. **Assets and role rules:** define separate qualifying roles. Choose **Create and bind qualifying roles** while paused. Existing names are never automatically adopted; enter the exact role ID to adopt explicitly. Place roles below the bot and give them zero server permission bits.
7. **Overview → Publish connection panel**. Posting updates the existing bot-owned message. A deleted/uncertain panel requires explicit reset and channel review; it is not silently duplicated.
8. Run `/verifier doctor`, then `/verifier probe`. Fix the specific blockers. Live probes check HTTPS, mainnet identity, selected provider methods, mint identity and authenticated backend completeness.
9. **Activate**. Activation repeats diagnostics and provider checks. Platform mode requires a connected test account before activation; account linking itself works while automation is paused.
10. Use a non-admin test member to connect and verify roles, including a second check and source failure. Follow ACCEPTANCE.md.

## Choose a method

| Need | Choose | Evidence required |
| --- | --- | --- |
| Ownership of a non-custodial Solana wallet | Wallet linking | User's exact server nonce signed by that wallet |
| Rip Cars account or custodial inventory | Account linking + platform source | Team-authenticated account ID and complete backend snapshot |
| Current $CARS in connected wallets | Balance: RPC | Verified mint; all token accounts of each connected owner |
| Current approved digital Hot Wheels | Assets: DAS | Approved verified collection IDs; actual current wallet owner |
| Deposits in approved legacy SPL payment tokens | Deposits: RPC | Approved treasuries/token accounts, optional platform program IDs, mint/decimals/USD valuation |
| SOL, card payments, complex contract receipts, Token-2022 fee deposits | Deposits: platform | Settled historical USD receipts from the platform accounting backend |
| Native application points | Platform points: on | Explicit backend points field; independent of deposit points |
| Near-real-time refresh | Chain webhook: on | HMAC or native Helius authentication; finalized RPC remains authoritative |
| Public opened-pack feed | Rip announcements: on | Signed settled `rip.opened` events from backend |

The product's public Solana asset URLs are not payment destination or collection addresses. Do not configure them as financial sources.

## Payment source form

Choose **Add or edit a payment source**:

1. **Source ID:** an internal identifier such as `payments`; reuse it to replace that source.
2. **Program IDs:** optional, one verified program per line. A configured program must actually execute in the transaction. Leaving this empty means direct transfers to the approved destination can qualify.
3. **Treasury owners:** approved owner public keys, one per line. These are owner addresses, not token-account addresses.
4. **Destination token accounts:** approved exact token account public keys, one per line. At least one treasury or token account is required.
5. **Payment mints:** one line per accepted token: `mint,decimals,USD-per-token`.

For Solana USDC, Circle's published mint is `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`, so the payment line can be:

```text
EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v,6,1
```

You must still provide **your actual Rip Cars deposit destination**, and the USD unit price is your explicit accounting policy. This is not an automatic historical exchange-rate oracle. Use settled platform USD receipts when a token's dollar price varies. More than one program or treasury is supported. Overlapping source matches count one parsed transfer once.

History start is a Unix timestamp: `0` means attempt full available history. A limited provider can fail on old transactions; use an archival RPC or the platform receipt method. Rescan resets progress cursors, never earned credits. Partial historical scans do not downgrade deposit roles.

## Role rule form

The five fields are:

```text
Unique rule key: deposit_1000
Role name: Rips 1K
Metric: deposit_points
Threshold: 1000
Options: {"selector":{},"role_id":0,"enabled":true}
```

Metrics: `deposit_points`, `platform_points`, `token_balance`, `asset_count`. Thresholds are exact decimal strings; all enabled rules that qualify may be granted, so tiers are cumulative. Exclusive highest-tier groups require a future reviewed rule extension.

For a Roxy-style trait rule, after verifying the actual attribute name/value with the team:

```json
{"selector":{"attribute":"Model","value":"Roxy"},"role_id":0,"enabled":true}
```

Selectors can also contain `asset_ids` or a verified `collection`; combined filters all have to match. Asset rules count distinct current assets. Changing a source/rule pauses Verifier for review. Disable a rule with `"enabled":false` to remove only grants previously owned by this bot on the next active check. Deleting a rule from the configuration leaves existing grants for explicit admin cleanup.

DAS does not guarantee that custom model traits are included in its inventory response. Use exact IDs/verified collections, or the team's complete platform trait snapshot. A trait-based DAS rule requires a connected holder test wallet for the probe. Missing required traits preserve that affected role and appear as rules waiting for evidence; they are not treated as a verified negative result.

OG is manual. Rippers remains Gate-owned. Notification/claim roles must not be reused as qualifying roles. Current holding roles are removed only after a complete verified snapshot says the user is below threshold; earned deposit history remains after wallet disconnection. A role manually removed by staff suspends automation for that member/role until `/verifier review_member ... resume_automation:true`.

For a deliberate manual rename or pin, pause Verifier, update the rule name to match, then run `/verifier review_role rule_key:<key>` to inspect its old/new baseline. `/verifier review_role rule_key:<key> accept_changes:true` explicitly accepts a safe rename/unpin of that same owned role. A different role ID needs a new rule key; foreign ownership cannot be reclaimed through this command.

## Pack feed

Choose a member-visible text channel under Channels, enable the feed, and arrange backend signing using API_CONTRACT.md. Feed messages contain the car name, asset ID, optional image/link and a stable event footer. They do not ping users or roles.

If a network failure leaves delivery uncertain, it enters `review`. Review the destination first:

```text
/verifier review_delivery event_id:rip:<platform-event-id> decision:Mark an existing message as sent message_id:<existing-id>
/verifier review_delivery event_id:rip:<platform-event-id> decision:Retry sending
/verifier review_delivery event_id:rip:<platform-event-id> decision:Discard this announcement
```

Do not select Retry if the message already exists. Mark-as-sent verifies bot ownership and the matching event footer. Retries are intentionally admin-reviewed for uncertain sends.

## Exports, advanced settings and revisions

All server settings can be exported, edited, and imported inside Discord with `/verifier import_settings file:<attachment>`. This supports larger source/rule lists, brand/color, retry budgets, freshness limits and scan page budgets without adding clutter to the main panel. The full strict schema is in `examples/settings-wallet-only.json`. Files are limited to 256 KiB. Import validates and pauses; credentials are not part of this JSON.

**Guide and revisions** lists saved audit IDs. `/verifier restore_settings revision_id:<id>` restores that server's configuration while paused; it does not restore deleted Discord objects or roll back financial records.

## Member flow

Complete Gate → open connection panel → Connect wallet/account → authenticate/sign → return to My status. Refresh is limited to once per minute and coalesces queued work. Disconnect is private and owner-bound. Up to three wallets are allowed by default; admins can change the limit.

Wallet-only users need Phantom, Solflare, a compatible injected wallet, or its mobile wallet browser. This release does not include a hosted WalletConnect/mobile deep-link service. A user without a compatible wallet can use website account linking if the team supplies that flow.
