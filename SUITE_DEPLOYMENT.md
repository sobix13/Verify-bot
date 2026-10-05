# Rip Cars four-bot deployment and coordination

## Tested release set

| Application | Repository | Version | Responsibility |
| --- | --- | --- | --- |
| Ripcars Gate | [Ripcars-Gate](https://github.com/sobix13/Ripcars-Gate) | 1.0.2 | CAPTCHA, one required onboarding answer, Rippers, notification claims, server access |
| Ripcars Crew | [Ripcars-Crew](https://github.com/sobix13/Ripcars-Crew) | 1.0.1 | Moderation, warnings, support tickets, explicit temporary chat controls |
| Rip Cars Raffle | [Ripcars-Raffle](https://github.com/sobix13/Ripcars-Raffle) | 1.0.1 | Eligibility, entries, draws, claims and proof exports |
| Ripcars Verifier | [Verify-bot](https://github.com/sobix13/Verify-bot) | 1.0.1 | Wallet/account proof, qualified roles, deposit ledger and optional pull announcements |

Each repository includes its own source, dependencies, tests, guides and install packages. Previous release directories remain available. Use the versions above together for coordination protocol 2.

## What changed

The old Gate/Crew code used the `leases` table. The old Raffle/Verifier code used the separate `locks` table. That was not a shared mutex even though the filename and lock name matched.

The identical `ripcars_coordination.py` module now:

- Checks both legacy tables before acquiring a lease.
- Mirrors the same random token and expiry into both tables in one transaction.
- Requires both tokens to match for renewal or a checked mutation.
- Releases only rows matching the holder's own token.
- Preserves live legacy leases while releases are being upgraded.
- Makes each registered object ID unique within its guild.
- Refuses duplicate existing resource bindings for explicit review, without removing rows.
- Keeps file owners/groups unchanged and repairs shared file modes only when the process owns the file.

Gate adoption, scans, companion access and handoff use the common lease. Ticket responsibility transfers atomically; Gate cannot reclaim a foreign controller's resource through its review/bind UI. Crew renews long leases and checks ownership before its next ticket, slowmode or native-rule mutation. Raffle checks its lease inside the message registry transaction. Verifier checks leases before its controlled changes.

No extra Discord bot or always-running coordination service is required.

## Isolation and permissions

Use a separate Discord application/token, private directory, virtual environment and service account for each bot. Do not run all four in one Python environment. Gate retains its separately pinned SDK; database compatibility does not require identical SDK versions.

Set `COORDINATION_PATH=/var/lib/ripcars-bots/coordination.sqlite3` in all four environment files. Use the same local filesystem and `ripcars-bots` shared group. Do not place SQLite on NFS or give each container a different unshared path.

Private databases must never use the shared coordination path:

| Application | Default private database | Service |
| --- | --- | --- |
| Gate | /var/lib/ripcars-gate/gate.sqlite3 | ripcars-gate |
| Crew | /var/lib/ripcars-crew/crew.sqlite3 | ripcars-crew |
| Raffle | /var/lib/ripcars-raffle/raffle.sqlite3 | ripcars-raffle |
| Verifier | /var/lib/ripcars-verifier/verifier.sqlite3 | ripcars-verifier |

Grant application permissions manually according to each repository's `permissions.csv` and Doctor. Gate's integration registration is inactive until an admin confirms it. A bot joining the server is not automatically trusted. Raffle needs no channel/role management; Verifier controls only dedicated zero-permission qualifying roles below its managed role. Team/Admin/Moderator, OG and notification claims remain outside Verifier's role policy.

Shared registry write access is cooperation between trusted processes, not a security sandbox against a malicious bot.

## Recommended upgrade order

1. Record the current versions and paths. Back up every private database using that bot's documented SQLite/encrypted backup method; back up the shared registry separately. Preserve protected environment files. Never copy a live SQLite file without its SQLite backup method.
2. Announce a short maintenance window. Stop only the Rip Cars applications you have already installed. Do not stop unrelated services.
3. Install Gate 1.0.2, Crew 1.0.1, Raffle 1.0.1 and Verifier 1.0.1 using their own `DEPLOYMENT.md`. Their installers stage/test separate releases and do not launch companion bots.
4. Confirm identical shared paths and group access before starting.
5. Start the installed applications. New setup/features remain explicitly controlled in Discord.
6. Run each bot's Health/Doctor and the registry checker below. Complete the live acceptance checks with a normal test member.

Inspect service presence first:

```bash
systemctl list-unit-files ripcars-gate.service ripcars-crew.service ripcars-raffle.service ripcars-verifier.service --no-pager
```

For a reviewed maintenance window, this stops only installed Rip Cars units:

```bash
for suite_service in ripcars-gate ripcars-crew ripcars-raffle ripcars-verifier; do
    if systemctl cat "$suite_service.service" >/dev/null 2>&1; then
        sudo systemctl stop "$suite_service.service"
    fi
done
```

Restart only the reviewed units that you intend to use. Do not enable a partially configured application just because its unit exists.

Upgrading only one controller protects it against a legacy lease, but two unchanged legacy controllers still have their original mismatch. Complete the release set. Do not mix old Raffle/Verifier lock-only builds with old Gate/Crew lease-only builds.

## Shared registry inspection

The verifier repository supplies a read-only checker. It does not contact Discord, read private member databases, print tokens, repair resources, transfer ownership or release leases.

```bash
sudo -u ripcars-verifier /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/check_suite.py --registry /var/lib/ripcars-bots/coordination.sqlite3
```

To restrict the report to one server:

```bash
sudo -u ripcars-verifier /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/check_suite.py --registry /var/lib/ripcars-bots/coordination.sqlite3 --guild YOUR_NUMERIC_GUILD_ID
```

Exit 0 means protocol/registry checks passed, not that Discord or a chain provider is healthy. An ordinary live mirrored lease is displayed without being an error. Missing/manual/partial resources, temporary controls requiring controller review, legacy/divergent leases and missing protocol tables return exit 1. Review the responsible controller's state; never delete shared rows to make a report green.

Protected `pinned` and handed-off `external` resources are valid intentional states. If a temporary Crew slowmode is still intentionally active, its report needs no manual repair; inspect its expiry and let Crew restore it.

## Reproduce four-process compatibility tests

Clone the four repositories into sibling directories and install each repository's requirements in its own virtual environment. From the verifier checkout:

```bash
.venv/bin/python scripts/check_suite.py --integration \
  --gate ../Ripcars-Gate --gate-python ../Ripcars-Gate/.venv/bin/python \
  --crew ../Ripcars-Crew --crew-python ../Ripcars-Crew/.venv/bin/python \
  --raffle ../Ripcars-Raffle --raffle-python ../Ripcars-Raffle/.venv/bin/python
```

This creates marked temporary databases only. It launches actual storage/registry code in four separate Python processes, checks all 12 ordered contention pairs, initialization order, different guilds, both legacy lock formats, foreign/manual preservation, private table separation and Raffle consumption of a dedicated Verifier role. It never uses the production registry, credentials, live gateway or blockchain.

The ordinary `run_tests.sh` remains independent in every repository. Multi-process suite tests are a separate explicit command, not a requirement to install companion bots.

## Discord acceptance

- Pass Gate CAPTCHA and at least one question as a normal member; only then receive Rippers.
- Claim/unclaim a notification role. It must not count as proved holdings or deposit activity.
- Configure Crew with the same Rippers ID. Activate its support integration and explicitly transfer Ticket/Open/Closed in Gate before enabling ticket creation.
- Check that one member can see only their own open ticket, and that closure removes their access.
- Select the same Rippers ID in Raffle and Verifier.
- Keep the existing Holder Verification channel hidden until Gate's explicit holder activation/handoff. Verifier can instead create/select its own member-visible connection channel.
- Define dedicated Verifier tier/asset roles. Do not reuse OG, staff, Rippers or claim-panel roles.
- Use those role IDs in a Raffle platform campaign. Check eligibility before and at draw time. Raffle does not calculate or duplicate the financial ledger.
- Run Gate repair after Crew handoff and Verifier setup. It must preserve those foreign-owned resources and unknown channels.
- Apply a temporary Crew slowmode to a Gate-owned chat. Gate must leave it protected until Crew restores it; a manual intervening edit must remain for review.
- Confirm Crew ignores messages authored by bots, so Gate panels, raffle results and pull announcements are not automatically treated as member spam.
- Test a manual permission/role rename and a deleted bound object. Expect a review/blocker, not silent recreation or takeover.
- Check the independent commands: `/gate`, `/crew`, `/raffle`, `/verifier`.

The local tests exercise real SDKs and SQLite with simulated Discord/provider I/O. Production gateway, live permissions, wallet/backend integration and VPS operation still require the live checks. Third-party bots and Discord admins do not honor this SQLite lease; a last-moment external API edit remains possible. Preserve and report such changes instead of promising universal conflict prevention.

## Rollback

Keep previous packages and code releases. Follow each bot's code rollback guide. Do not restore one shared registry backup while another controller is running. Restoring ownership state requires all participating controllers to stop and an explicit reviewed recovery plan. Prefer rolling back the whole compatible release set; the earlier lease-table mismatch returns if both legacy controller families are restored.
