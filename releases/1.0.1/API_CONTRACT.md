# Rip Cars backend integration contract

Version 1. This document defines the bridge the team must implement. It is **not documentation for an already-discovered public Rip Cars API**. The verifier cannot independently authenticate a website account, interpret an undocumented custody system or know the historical USD value of an arbitrary contract call.

## Integration checklist

Provide the team with the verifier HTTPS origin, Discord server ID and required optional features. Exchange `PLATFORM_API_KEY` and `PLATFORM_WEBHOOK_SECRET` through a secret manager. Backend credentials must never appear in browser JavaScript, public channel messages or client API responses.

Implement only the features you want now:

| Feature | Team work | Verifier setting |
| --- | --- | --- |
| Website account linking | Authenticated connection page plus signed account callback | `account_linking` and `platform_connect_url` |
| Account points/balance/assets | Complete versioned snapshot endpoint | Corresponding platform data source |
| USD deposit points | Settled actual-deposit receipt pages with stable IDs | `deposit_mode=platform` |
| Pack-opening feed | Signed event from settled pack opening | `rip_feed=true` and channel |
| Faster onchain recheck | Optional signed chain hints or native Helius webhook | `chain_webhook=true` |

RPC/DAS wallet verification can be deployed independently of the team API. Platform integration is appropriate for custodial inventory, payments by card/SOL, platform-only points and complex program accounting.

## 1. Authenticated website account connection

The Discord button opens your approved HTTPS connection page with query parameter `discord_link_token`. This token is single-use, expires in ten minutes and identifies a server-side Discord link session. Authenticate the website account with your existing login system. Obtain the stable account ID from the authenticated backend session, not a user-entered field.

The **backend** then calls:

```http
POST https://<verifier-origin>/api/platform/link
Content-Type: application/json
X-Ripcars-Timestamp: <current Unix seconds>
X-Ripcars-Event-Id: <unique stable request ID>
X-Ripcars-Signature: <lowercase hex HMAC-SHA256>
```

```json
{"session_token":"<discord_link_token>","account_id":"<authenticated-stable-account-id>"}
```

Successful response:

```json
{"ok":true,"guild_id":"123456789012345678","discord_id":"234567890123456789"}
```

Repeated accepted envelope returns `{"ok":true,"duplicate":true}`. Invalid authentication, missing/expired session, wrong purpose, already-owned account or disabled feature is HTTP 400. The browser does not call this route with a signing secret. Redact the short-lived `discord_link_token` from your website/proxy access logs and do not store it in analytics.

## 2. HMAC signing

Let `body` be the **exact bytes** sent over HTTP. With `PLATFORM_WEBHOOK_SECRET`, compute:

```text
HMAC-SHA256(secret, timestamp + "." + event_id + "." + body)
```

Encode as lowercase hexadecimal in `X-Ripcars-Signature`. Timestamp must be within five minutes of the verifier's UTC clock; run time synchronization on both servers. Event ID is 1–120 non-whitespace characters. Secret must be at least 32 characters. Retry with the same stable event identity and identical business payload, but a new current timestamp/signature if needed. The example sender is `examples/backend_bridge.py`.

Do not modify JSON whitespace/order after signing. Raw bytes, not a reparsed/sorted object, are authenticated. HMAC does not replace your platform login, settled payment validation or pack-opening authorization; your backend performs those before sending.

## 3. Account snapshot endpoint

Verifier sends this to `PLATFORM_API_URL` (a backend base URL that you configure, without a trailing `/v1`):

```http
GET <PLATFORM_API_URL>/v1/discord/members/<URL-encoded-account-id>
Authorization: Bearer <PLATFORM_API_KEY>
```

Subsequent deposit pages add `?cursor=<URL-encoded-cursor>`. Return HTTP 200 and:

```json
{
  "schema_version": 1,
  "account_id": "stable-account-id",
  "complete": true,
  "as_of": 1791072000,
  "platform_points": "25000",
  "token_balance": "12000.25",
  "assets": [
    {"id":"stable-asset-id","collection":"verified-collection-id","attributes":{"Model":"Roxy"}}
  ],
  "deposits": [
    {"id":"stable-receipt-id","kind":"deposit","status":"settled","usd_amount":"12.50"}
  ],
  "next_cursor": null
}
```

Rules:

- `account_id` must match the requested authenticated identity exactly.
- `complete=true` is required. An outage/incomplete inventory must not be represented as a complete empty list or zero balance.
- `as_of` is a current UTC Unix timestamp; default allowed age is 3600 seconds. Keep it identical across all pages of one snapshot.
- Decimal amounts/points are strings, not JSON floats. Negative/non-finite values are rejected. A single numeric amount is limited to 1 trillion units. USD receipts have at most six decimal places.
- Include every requested metric. Native platform points and USD deposit points are different. `platform_points` must represent the team's actual application metric.
- `token_balance` and `assets` are complete current values, not deltas. Include them consistently on each page when that source is enabled. Asset IDs must be unique; maximum 10,000 assets per snapshot.
- `assets.attributes` is a string-valued mapping of verified traits. Actual collection/trait IDs come from the team; sample values are illustrative.
- `deposits` includes only settled actual deposits attributable to this authenticated account. Exclude fees, swaps, sales, withdrawals, failed/reverted payments and duplicate internal movements. Include optional `wallet` if it is a stable trusted attribution value. Do not omit/rewrite it on later replays.
- Receipt IDs must be stable and unique within the account, and unchanged USD values must be replayable. Refund/correction/migration behavior requires an explicit accounting extension; changing an already credited amount is rejected.
- Supply `settled_at` with the historical UTC Unix settlement timestamp. It is optional with `history_start=0`, and required if the admin configures a historical cutoff. Old receipts before that cutoff are skipped; a missing timestamp under a cutoff fails the check rather than guessing. Zero-value receipts earn zero points.
- A page contains at most 1,000 deposit receipts; cursor is null at the end. Repeating cursors, changing snapshot timestamps or more than 100 pages fails the current refresh and preserves roles. For larger histories, extend the adapter with a reviewed resumable receipt cursor rather than returning partial data as complete.
- Maximum response body is 2 MiB. Server error/timeout triggers bounded retry. Authentication errors and other non-200 results fail the check, never turn holdings into zero.

The verifier can skip fields for disabled features. Its provider probe checks required fields before activation. For onchain+card/SOL combined accounting, normalize everything into this authoritative receipt ledger; do not also enable RPC deposit crediting for the same server.

## 4. Pack-opening event

Call from the backend job/transaction after a real settled pack opening:

```http
POST https://<verifier-origin>/api/webhooks/rip
```

Use the HMAC headers above and this JSON:

```json
{
  "schema_version": 1,
  "type": "rip.opened",
  "id": "stable-pack-opening-id",
  "guild_id": 123456789012345678,
  "car_name": "Roxy",
  "asset_id": "stable-car-asset-id",
  "occurred_at": 1791072000,
  "image_url": "https://<public-image-host>/car.png",
  "asset_url": "https://app.ripcars.io/<actual-team-provided-asset-path>"
}
```

`image_url` and `asset_url` are optional public HTTPS links. An optional valid Solana `wallet` is accepted for stored event attribution but is not displayed by default. `guild_id` accepts an exact integer or a decimal string; use a string in JavaScript to avoid snowflake precision loss. Output IDs are strings. The verifier normalizes either exact input representation before deduplication.

Success means the event was authenticated and durably queued, **not necessarily already posted to Discord**. Duplicate ID/payload returns `duplicate=true`; changed content with an existing pack ID or reused conflicting envelope is rejected. Car names/asset IDs cannot force mentions. Include stable image URLs that Discord can retrieve without a bearer token. The verifier neither downloads an arbitrary image into its process nor scrapes the public website for pack events.

## 5. Chain webhook hints

Generic signed hints use `CHAIN_WEBHOOK_SECRET`:

```http
POST https://<verifier-origin>/api/webhooks/chain
```

```json
{"guild_id":123456789012345678,"wallet":"<linked-Solana-wallet>"}
```

Native Helius enhanced webhooks use:

```http
POST https://<verifier-origin>/api/webhooks/helius
Authorization: Bearer <CHAIN_WEBHOOK_SECRET>
```

Send a bounded array of events containing `accountData[].account`. Both routes only wake the next finalized check for existing connected users. Neither trusts webhook amounts, transfer descriptions, role IDs or prices to award points. Without an existing link or enabled chain hints, they do not create one. Daily refresh remains available if hints fail.

## 6. Backend acceptance

Before enabling platform mode, the team should demonstrate:

1. Authenticated account linking, replay rejection and wrong-owner handling.
2. Fresh complete snapshot with one known account and current assets/balance/points.
3. One real settled deposit, stable re-delivery and exact USD value.
4. Withdrawal/fee/internal transfer excluded from receipts.
5. Successful pack event with real image/link, duplicate retry, and failed-delivery review.
6. Provider failure reported as failure/incomplete rather than an empty complete inventory.

None of these live platform assertions were validated by the offline release tests. The test fixtures exercise the verifier side of this contract.
