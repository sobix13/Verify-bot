# Integration and settings examples

All values here are illustrative unless expressly identified as a public standard. No Rip Cars treasury, program, $CARS mint, collection ID or backend API credential is supplied.

| File | Use |
| --- | --- |
| `settings-wallet-only.json` | Full valid schema, paused; select actual Gate role/channels in Discord |
| `settings-rpc-template.json` | Paused RPC/DAS method selection; actual mint, collections, financial sources and credentials still required |
| `settings-platform-template.json` | Paused account/backend methods; authenticated bridge/login URL still required |
| `role-rules-template.json` | Disabled point/holding tiers and illustrative Roxy/Gulf selectors; copy reviewed rules into exported settings before enabling |
| `platform_snapshot.json` | Shape of the team's backend response; replace account, asset IDs and stale fixed example timestamp |
| `rip_event.json` | Shape of a settled pack event; replace server/event/car/image IDs and time |
| `backend_bridge.py` | Server-side HMAC sender; your backend performs actual account/payment/opening authentication |

Settings imports are complete schema files, not partial patches. Importing forces Verifier paused and preserves its existing panel message binding. JSON IDs must remain exact; use a text editor rather than a spreadsheet that rounds snowflakes. API `guild_id` also supports an exact decimal string for JavaScript senders.

Rules in the role template are disabled and have role ID 0. Select supported real data methods, verify exact model attributes with the team, enable only needed rules, then create/bind their separate safe roles through the Discord panel. Source switching after credits requires an audited migration.
