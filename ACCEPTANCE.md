# Live acceptance after installation

The release tests exercise simulated Discord/provider behavior. Complete this checklist with the real Discord application, your deployment and team data before public activation. Do not label an unchecked live item as passed.

## Infrastructure

- [ ] Separate Verifier application/token; correct server; Server Members Intent enabled.
- [ ] Service runs as `ripcars-verifier`; environment mode 600; private data directory mode 700.
- [ ] Shared registry path/group match Gate, Crew and Raffle; their services remain healthy.
- [ ] Loopback port 8092 and actual HTTPS domain both return a healthy verifier response.
- [ ] TLS certificate is valid; renewal dry run passes; no other vhost changed.
- [ ] `/verifier panel`, `/verifier doctor`, `/verifier probe`, `/verifier health` work.

## Identity and channel access

- [ ] A member without Gate/Rippers cannot request a normal wallet/account connection.
- [ ] A non-admin member with Gate can see connection/history and selected feed channels.
- [ ] The log channel is hidden from ordinary member roles; bot/staff can read it.
- [ ] Wallet signing shows the intended domain, wallet, Discord user and server.
- [ ] Actual Phantom/Solflare signature succeeds; wrong/expired/reused signature fails.
- [ ] Account linking uses the real authenticated backend identity, not a browser-entered ID.
- [ ] A second Discord user cannot claim the same identity, including after disconnection.
- [ ] Source/channel selectors keep chosen values when navigating/reopening the panel.

## Onchain and platform data

- [ ] Team signs off on actual $CARS mint, payment programs/destinations/mints and approved collections.
- [ ] Primary/fallback RPC are mainnet and support required finalized/archival methods.
- [ ] Known current token balance matches an explorer/provider at finalized commitment.
- [ ] Known held car asset, ownership, verified collection and exact trait/ID match.
- [ ] Transfer/sale/burn changes are reflected in current asset inventory on refresh.
- [ ] One known settled deposit receives exactly its historical USD points.
- [ ] Rechecking the same payment or matching two sources never adds another credit.
- [ ] Multiple actual transfers in one transaction are correctly distinguished.
- [ ] Fees, failed payments, withdrawals, swaps and internal movements do not receive points.
- [ ] Interrupted history resumes and partial coverage cannot downgrade deposit roles.
- [ ] Required backend fields are fresh and complete across pagination.
- [ ] Native platform points and derived deposit points have separate verified values.

Use an existing real receipt as evidence where possible. Offline release tests do not justify spending money merely to test a bot.

## Roles and coexistence

- [ ] Qualifying roles have zero server permission bits and sit below the Verifier role.
- [ ] All qualifying roles are adopted into the shared registry and are separate from claims.
- [ ] Exact boundary thresholds grant the intended cumulative roles.
- [ ] Below-threshold complete holdings remove only Verifier-owned grants.
- [ ] A provider outage does not remove any last-known eligible role.
- [ ] Human-added grants remain; human removal suspends automatic regrant until reviewed.
- [ ] OG, Gate member role, staff, tickets, claim panels and Raffle objects remain unchanged.
- [ ] Overlapping setup with another bot is blocked/retried, not applied simultaneously.
- [ ] Raffle can select the newly created roles for its eligibility rules.

## Pack announcements and operations

- [ ] Signed real pack event posts the intended car name, correct image and optional official asset link.
- [ ] A duplicate event does not create another announcement.
- [ ] Missing channel access produces a private diagnostic and recoverable pending state.
- [ ] An uncertain send can be reviewed against the footer and explicitly resolved.
- [ ] Daily refresh completes under the actual member count/provider quota.
- [ ] Discord server backup decrypts into an isolated new file; no other server's data is present.
- [ ] Full VPS backup is copied offsite with its matching encryption password.
- [ ] Restore clears sessions, pauses automation and preserves discarded/sent delivery states.
- [ ] A staged update and schema-compatible code rollback are rehearsed.

Record the operator, time, provider identities and any outstanding item in your deployment change log. Never include real tokens or secrets in the log.
