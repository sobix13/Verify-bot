# Installation, updates, and recovery

These commands install **Ripcars Verifier** as its own service. Installation runs with sudo; the running bot uses the dedicated `ripcars-verifier` account, not root. Existing Gate, Crew, and Raffle services are preserved.

## 1. Prepare the Discord application

Create a separate application and bot in the Discord Developer Portal. Name it **Ripcars Verifier**. Enable **Server Members Intent**. Message Content Intent is unnecessary.

Invite with the `bot` and `applications.commands` scopes. Grant Manage Roles and the selected channel permissions in `permissions.csv`. Place its managed bot role above its qualifying roles. Administrator is unnecessary. Manage Channels is needed only if you use the optional channel creation action; remove it after setup if desired.

Keep the token on the VPS. Do not send tokens, RPC keys, webhook secrets, or backup passwords in Discord or chat.

## 2. Upload from Windows

Save `ripcars-verifier-1.0.1.tar.gz` and `RIPCARS_VERIFIER_SHA256SUMS.txt` in your existing folder:

```powershell
Set-Location 'C:\Users\macbook\Desktop\files'
$VerifierVps = Read-Host 'Enter the existing VPS IP or hostname'
$VerifierSshUser = Read-Host 'Enter your SSH user, for example memecult'
scp .\ripcars-verifier-1.0.1.tar.gz .\RIPCARS_VERIFIER_SHA256SUMS.txt "${VerifierSshUser}@${VerifierVps}:/tmp/"
ssh "${VerifierSshUser}@${VerifierVps}"
```

If the files are already uploaded, start at the next step. Do not paste PowerShell commands into the VPS shell.

## 3. Verify and unpack on the VPS

Ubuntu 24.04 / Debian with Python 3.11+ and systemd is the intended deployment environment. Existing Ubuntu versions with Python 3.12 are suitable.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates curl nginx certbot python3-certbot-nginx
python3 --version
cd /tmp
sha256sum --check --ignore-missing RIPCARS_VERIFIER_SHA256SUMS.txt
tar -xzf ripcars-verifier-1.0.1.tar.gz
cd /tmp/ripcars-verifier-1.0.1
bash scripts/install.sh --dry-run
sudo bash scripts/install.sh
```

The installer builds a fresh virtual environment, installs exact runtime pins, runs the full suite, validates the environment, and then switches the verifier's `current` symlink. Failed staging leaves the current release active. Updates create an encrypted full backup before switching code. The installer does not replace the shared coordination database.

On the first installation, secret prompts open automatically. Enter the bot token and the real HTTPS origin, for example `https://verify.ripcars.io` **only if your team controls and configures that DNS name**. RPC/DAS/backend values can stay blank initially. Enter generates independent webhook secrets and a backup password when they are absent. Secrets are saved only in `/etc/ripcars-verifier.env`, mode 600.

Keep a secure separate copy of this file, especially `BACKUP_PASSWORD`. Existing credentials are preserved during an update. A generated secret must be shared with the backend through your team's secret manager before that feature is enabled.

## 4. Paths and ports

| Item | Location |
| --- | --- |
| Release code and isolated environment | `/opt/ripcars-verifier/releases/<version>-<timestamp>/` |
| Active release | `/opt/ripcars-verifier/current` |
| Previous release pointer | `/opt/ripcars-verifier/previous-release` |
| Protected environment | `/etc/ripcars-verifier.env` |
| Private database | `/var/lib/ripcars-verifier/verifier.sqlite3` |
| Encrypted backups | `/var/lib/ripcars-verifier/backups/` |
| Shared coordination | `/var/lib/ripcars-bots/coordination.sqlite3` |
| Service | `ripcars-verifier.service` |
| Internal web listener | `127.0.0.1:8092` |
| Public wallet connection | Your verifier HTTPS origin, path `/link` |

The shared folder must have group `ripcars-bots` and setgid group write access. Existing file owners are preserved. A wrong existing group stops installation for review. See COORDINATION.md. Ensure port 8092 is free; changing it also requires updating nginx.

## 5. HTTPS

Point the chosen DNS hostname to this VPS. Port 80 must be reachable for certificate issuance; port 443 serves the connection site. Port 8092 stays on loopback and should not be opened publicly. Use a separate hostname so existing websites and reverse proxies retain their routes.

```bash
cd /opt/ripcars-verifier/current
read -r -p 'Verifier DNS hostname: ' verifier_domain
read -r -p 'Certificate contact email: ' verifier_email
sudo bash scripts/configure_https.sh "$verifier_domain" "$verifier_email"
curl -fsS "https://$verifier_domain/healthz"
sudo certbot renew --dry-run
```

The script requires the domain to match `PUBLIC_URL`. It creates only the verifier's new nginx file and refuses to overwrite an existing verifier virtual host. It runs `nginx -t` before reload. If you use another reverse proxy, map the same hostname to `http://127.0.0.1:8092`, preserve the request body, impose a 256 KiB request limit and apply IP rate limiting. TLS must terminate before the browser uses the connection page.

## 6. Discord setup

```text
/verifier panel
```

Select the Gate member role and existing text channels. Optional **Create unset verifier channels** creates `ripcars-connect`, `ripcars-pulls`, and staff-only `verifier-log` only when their selections are unset; it does not repair or overwrite existing channels. Configure data methods, role rules and optional feed. Publish, run Doctor and live probes, then activate. See ADMIN_GUIDE.md for the exact sequence.

No team addresses are embedded in this release. Activation of RPC/DAS/backend features waits for the required actual data. Wallet links can be tested while role automation is paused.

## 7. Process checks

```bash
sudo systemctl --no-pager --full status ripcars-verifier
sudo journalctl -u ripcars-verifier -n 100 --no-pager -l
curl -fsS http://127.0.0.1:8092/healthz
sudo /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/preflight.py --env-file /etc/ripcars-verifier.env
```

Discord checks: `/verifier doctor`, `/verifier probe`, `/verifier health`, and `/verifier status`. `healthz` reports HTTP 503 when the database, gateway or worker is unhealthy. systemd receives watchdog heartbeats while both tasks remain alive.

For a test server, set `TEST_GUILD_ID` to that server's numeric ID for immediate command sync. Clear it and restart for global command registration. A guild command copy may remain on the test server; global command availability can take time.

## 8. Change credentials or data sources

```bash
sudo /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/configure_env.py
sudo systemctl restart ripcars-verifier
sudo journalctl -u ripcars-verifier -n 40 --no-pager -l
```

Press Enter to preserve a value, or `-` to clear an optional integration. Data source addresses, rules, interval, public messages and channel IDs are changed inside Discord. Secrets and internal deployment paths stay on the VPS.

Changing financial/role sources pauses automation. Run Doctor and Probe before reactivation. RPC-to-platform deposit switching after any credit requires an audited migration; a simple dropdown change is blocked to prevent duplicate points. Changing valuations of credited receipts requires ledger review and is never applied retroactively automatically.

## 9. Update

Download the new version and its checksum file, upload to `/tmp`, verify, unpack, and run that release's `scripts/install.sh`. Do not copy files over the running directory.

```bash
cd /tmp
sha256sum --check --ignore-missing RIPCARS_VERIFIER_SHA256SUMS.txt
# Unpack the actual new release file, then change into its extracted folder.
sudo bash scripts/install.sh
sudo systemctl --no-pager --full status ripcars-verifier
```

Read the new CHANGELOG and schema notes first. This release's schema version is 1. A future schema migration must include a compatible rollback or an explicit backup restore plan. The installer's candidate test run does not connect to production Discord or spend funds.

## 10. Code rollback

```bash
sudo bash /opt/ripcars-verifier/current/scripts/rollback.sh
```

Rollback switches to the recorded staged release and preserves current data/settings. It is suitable for schema-compatible releases. It is not a financial ledger rollback. For changed schemas, follow the release-specific migration guide instead of guessing compatibility.

## 11. Backup

Discord: **Operations → Create encrypted backup** returns data for the invoking Discord server only. It omits other servers, live connection sessions, environment secrets and the shared coordination database. The same file also remains on the VPS. Attachments over 7 MiB require secure VPS download instead.

Automatic daily VPS backups contain the complete verifier database. Fourteen recent encrypted files are retained. Create a complete backup manually:

```bash
sudo /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/create_backup.py --env-file /etc/ripcars-verifier.env --service-user ripcars-verifier
```

Store a copy off the VPS. A backup password rotation does not decrypt old backups; retain the matching old password until those backups expire.

This release bounds a restorable SQLite database to 128 MiB. It rejects a larger backup instead of producing an unsupported recovery file. Monitor database growth and extend the reviewed limit/retention strategy before reaching it; the Discord attachment threshold is separately 7 MiB.

## 12. Restore safely

Restore authenticates the encrypted file, verifies archive members and SQLite integrity, clears pending link sessions, leaves qualifying automation paused, and places uncertain/pending announcements in review. Discarded announcements remain discarded. It refuses to overwrite an existing database path.

```bash
sudo systemctl stop ripcars-verifier
sudo /opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/restore_backup.py /path/to/downloaded-backup.rcvbackup --output /var/lib/ripcars-verifier/restored.sqlite3
sudo chown ripcars-verifier:ripcars-bots /var/lib/ripcars-verifier/restored.sqlite3
sudo chmod 600 /var/lib/ripcars-verifier/restored.sqlite3
sudoedit /etc/ripcars-verifier.env
# Set DATABASE_PATH="/var/lib/ripcars-verifier/restored.sqlite3".
sudo systemctl start ripcars-verifier
```

For multiple Discord servers, restore a **full VPS backup** to replace the whole instance. A server-only Discord backup must be reviewed in an isolated instance or merged using a reviewed tenant migration; replacing a multi-server database with it would omit other servers. The shared registry stays external and is not restored over Gate/Crew/Raffle ownership. Re-run Doctor, bind/review roles and channel selections, resolve announcement reviews, then activate explicitly.
