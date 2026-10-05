# Troubleshooting

Use `/verifier doctor` for permissions/configuration, `/verifier probe` for live sources/site, and `/verifier health` for process/database/queue status. **Operations → Recent errors** exports redacted error IDs and context privately. VPS logs use the same `RCV-...` ID.

```bash
sudo systemctl --no-pager --full status ripcars-verifier
sudo journalctl -u ripcars-verifier -n 100 --no-pager -l
curl -fsS http://127.0.0.1:8092/healthz
```

| Symptom | Cause/check | Action |
| --- | --- | --- |
| Commands missing | Bot invite lacks scopes, wrong test guild or global propagation | Invite with bot/applications.commands; check TEST_GUILD_ID; restart and read sync errors |
| Privileged intent error | Server Members Intent disabled | Enable it in Developer Portal; restart |
| Activation blocked | Missing actual source/channel/role setup | Read the complete Doctor attachment, fix each named requirement, publish and probe |
| Provider probe says wrong cluster | Endpoint is devnet/testnet or unsupported genesis method | Configure a verified mainnet endpoint supporting getGenesisHash |
| $CARS mint invalid | Account is not the team's actual SPL mint | Obtain verified mint; do not use an example asset address |
| Wallet page unavailable | DNS, TLS, nginx or port mismatch | Check PUBLIC_URL, loopback health, nginx -t and public health URL |
| Account link unavailable | Team login/callback not implemented or feature off | Keep optional account feature off until API_CONTRACT is implemented |
| Signature rejected | Wrong wallet, changed nonce, expired link, unsupported provider | Request one fresh link, use the selected wallet's browser/extension and sign that exact message |
| Existing identity belongs to another Discord user | Ownership tombstone deliberately prevents credit transfer | Escalate to a reviewed identity/ledger migration; disconnect alone does not transfer ownership |
| Channel selector edit conflict | Another admin saved a newer revision | Reopen /verifier panel; prior selection is not overwritten |
| 403 / 50013 on role update | Manage Roles or hierarchy missing | Grant Manage Roles and move Verifier above qualifying roles only |
| Role changed or pinned | Manual override or ownership conflict | Review baseline/registry; preserve the human choice; bind a dedicated role |
| Claim role cannot be adopted | It belongs to a claim panel/peer bot | Create a separate proved qualifying role |
| Missing Access / 50001 posting | Bot cannot see selected channel/category | Grant its actual bot role View/History/Send/Embed; Attachment for logs |
| Members cannot see holder channel | Gate keeps holder feature staff-only until handoff | Perform explicit Gate holder handoff, or choose/create a dedicated member-visible connection channel |
| Historical transaction unavailable | RPC lacks archival history or transaction version support | Use an archival mainnet provider or settled platform receipt method before credits are established |
| History still syncing | Configured page/transaction budget is partial | Leave worker active; inspect checkpoint/queue; expand budget within provider limits |
| RPC-to-platform switch refused | Existing accounting ledger could duplicate credits | Keep authoritative method or perform an audited ledger migration |
| Receipt valuation changed | Source/backend replay changed amount or wallet attribution | Review the exact immutable receipt; do not erase history to hide the conflict |
| Roles do not update during outage | Last valid state is deliberately preserved | Fix the provider; queue refresh; inspect completeness/time in member review |
| Native platform points show Not enabled | Separate metric is disabled | Enable platform points only after backend supplies that field |
| Pack feed quiet | Feature skipped, wrong signature, no backend event, private destination or paused pending queue | Verify team sender/secret, feed toggle/channel and delivery export |
| Delivery in review | Discord result was uncertain or process restarted mid-send | Check event footer in destination, then mark sent/retry/discard explicitly |
| Backup button rejects | Missing/short password or attachment above 7 MiB | Set secure VPS password; download retained encrypted file through SSH if too large |
| Restore rejects password | Wrong old password or corrupted/tampered file | Use matching backup password and original bytes; do not bypass authentication |
| Shared database permission denied | Group/setgid/WAL access mismatch | Inspect original ownership and shared ripcars-bots group; preserve existing owners |
| Worker unhealthy | Task crashed, database failing, or unusually slow batch | Read redacted logs, reduce scan budgets/check provider latency; restart only after finding cause |
| Installer stopped before activation | Candidate tests/config/shared permissions failed | Fix the reported cause and rerun from the release folder; previous active code remains |

To review a member privately, use `/verifier review_member`. If staff intentionally removed a qualifying role, `resume_automation:true` explicitly re-enables its automatic decision after review. Export settings/revisions before major changes. Settings restore changes configuration only; encrypted database restore is an offline operation.

Do not grant Administrator merely to hide a 403. Doctor describes the specific permission and hierarchy needed. A bot cannot grant itself permissions above its own authority, so a human adjusts those permissions once.
