# Gate, Crew, Raffle, and Verifier coexistence

All four use the same `/var/lib/ripcars-bots/coordination.sqlite3` file and `ripcars-bots` shared group. Protocol 2 fixes the previous split between Gate/Crew's `leases` and Raffle/Verifier's `locks`. Both tables are now mirrored inside the same transaction:

```sql
resources(guild,key,object_id,kind,owner,baseline,desired,state)
leases(guild,key,token,expires)
locks(guild,name,token,expires)
suite_meta(key,value)
```

| Resource | Owner |
| --- | --- |
| Server skeleton, CAPTCHA, Rippers, notification claims | Gate |
| Moderation, support/tickets and their resources | Crew |
| Raffle definitions, entry UI and raffle state | Raffle |
| Wallet/account sessions, qualified holdings/deposit roles, pull outbox | Verifier |
| OG and staff decisions | Human administration |

Verifier reads Gate's `role:rippers` numeric binding. Its own registered keys start with `verifier:role:<bot-id>:` or `verifier:channel:<bot-id>:` and owner is `bot:<bot-id>`. Qualifying roles cannot be shared with claim panels or another bot. Selected preexisting channels are used for bot messages; selecting them does not claim ownership of their entire configuration.

Cross-bot setup uses the shared `server-setup` lease, with expiration, token ownership and renewal. Acquisition checks both legacy tables and writes both atomically. A held peer lease stops a setup operation with a readable retry message. A lost lease stops the next checked mutation. No bot attempts to control a same/higher hierarchy role by bypassing Discord permissions.

Each registered object ID is unique within its guild. Existing duplicate bindings stop protocol initialization for human review; no row is deleted. The exact same `ripcars_coordination.py` is shipped in all four repositories. See [SUITE_DEPLOYMENT.md](SUITE_DEPLOYMENT.md) for the tested version matrix, rollout and read-only suite checker. No coordination daemon or extra Discord application is needed.

## Existing holder channel

Gate initially keeps holder verification hidden pending explicit handoff. Merely selecting that hidden channel in Verifier does not expose it or override Gate permissions. Either:

1. Select a different existing member-visible connection channel or create Verifier's dedicated channel; or
2. Perform Gate's explicit holder feature activation/ownership handoff and grant the Verifier bot the intended access, then select that channel.

Gate remains the source of server access permissions. Verifier Doctor checks View Channel and Read Message History for members and bot, with concrete blockers. It does not repeat the earlier pattern of touching every unrelated channel.

## Manual changes

Changing or pinning a registered qualifying role stops automatic changes until reviewed. Preexisting human role grants are preserved. Human removal of an owned grant suspends that member/role automation until reviewed. Existing selected channels are not renamed, synchronized, recreated or permission-repaired by background tasks. Deleted bound roles/panels produce a blocker; they are not duplicated by name guessing.

Optional verifier channel setup creates only unset channels with distinct names. Name collision stops with a request to select the existing numeric ID. Partial setup saves each created ID immediately, so a subsequent retry preserves completed work.

## Permissions

The verifier managed role must be above its zero-permission qualifying roles. It does not need to be above Team/Admin, and it does not grant moderation authority. Users without the Gate member role cannot request a normal member connection or receive a qualifying grant from the worker.

For shared files, keep each original owner and the `ripcars-bots` group. The directory's setgid bit ensures new SQLite WAL/SHM files inherit that group. Do not copy another bot's private database into this shared directory. Backups never overwrite or include shared ownership data.

## Raffle integration

Use Verifier-created roles in Raffle's existing role eligibility filters/weights. Verifier does not create or draw a raffle, and Raffle does not calculate financial points. Claimable Collectors/Announcements/Game roles stay separate from proved point/holding tiers. OG remains manually granted.

## Future bots

Future bots must adopt the same lease/ownership protocol and receive explicit human permissions. Verifier can preserve registered/manual resources; it cannot force an unrelated bot that ignores the protocol to cooperate. A reviewed resource handoff must change ownership explicitly, not rely on a name collision. New integrations are described in EXTENDING.md.
