# Ripcars Verifier

Version **1.0.1**. A separate Discord bot for verified Rip Cars connections, qualifying roles, deposit points, and pack-opening announcements. Project website: [app.ripcars.io](https://app.ripcars.io).

Complete install packages and checksums: [releases/1.0.1](releases/1.0.1). Four-bot release matrix, upgrade order and read-only coordination checker: [SUITE_DEPLOYMENT.md](SUITE_DEPLOYMENT.md). Latest suite test evidence: [SUITE_TEST_RESULTS.txt](SUITE_TEST_RESULTS.txt).

All bot messages, configuration examples, source comments, and guides are English. Public embeds use maroon (`#800020`).

## What is implemented

| Feature | Available method | Setup requirement |
| --- | --- | --- |
| Wallet ownership | Expiring server-issued Ed25519 message; Phantom, Solflare, compatible injected Solana wallet | Verifier HTTPS connection site |
| Website account connection | Signed attestation from an authenticated Rip Cars backend session | Team backend integration |
| Current $CARS balance | Finalized Solana RPC or authenticated backend snapshot | Verified mint or backend contract |
| Current Hot Wheels assets | DAS inventory for approved collections, or backend snapshot | Verified collections or backend contract |
| Lifetime deposit points | Finalized SPL deposits into approved destinations, or settled USD backend receipts | Verified financial sources; one authoritative accounting method |
| Native platform points | Backend snapshot, kept separate from deposit points | Team-provided platform points |
| Qualifying roles | Any supported metric, exact thresholds, asset IDs, collection, or exact traits | Dedicated zero-permission roles |
| Pack announcements | Signed `rip.opened` event; car name, optional image and asset link | Backend integration; chosen channel |
| Refresh | Every 24 hours by default; manual refresh; signed/Helius webhook hints | Enabled data sources |
| Administration | Ten organized Discord sections, guides, diagnostics, settings revisions and imports | Manage Server or Administrator |
| Backups | Encrypted server-only Discord export and complete daily VPS backup | Protected backup password |
| Coordination | Shared resource registry and setup lease; manual overrides preserved | Same coordination path as Gate/Crew/Raffle |

Each integration can be skipped. A wallet-only installation works without the Rip Cars backend. Wallet signing authorizes linking; the verifier does not request a seed phrase or send a transaction.

## What still requires the team

No project mint, treasury, program, collection address, or public API has been invented. You chose to provide these later. Supply the verified addresses and/or implement the bridge in [API_CONTRACT.md](API_CONTRACT.md) before activating their features. The bridge routes in this project are a proposed integration contract, **not an assertion that those routes already exist on app.ripcars.io**.

The included test environment uses actual discord.py, aiohttp, SQLite, Ed25519 verification and authenticated encryption, with simulated Discord and provider responses. Live Discord, paid RPC, live Rip Cars accounts, and your VPS require the acceptance checks after installation. See [TEST_RESULTS.txt](TEST_RESULTS.txt) and [VALIDATION.json](VALIDATION.json).

## Start here

1. Follow [DEPLOYMENT.md](DEPLOYMENT.md) for Windows upload, dedicated service account, installation, HTTPS, updates and rollback.
2. Open `/verifier panel` and follow [ADMIN_GUIDE.md](ADMIN_GUIDE.md).
3. Choose only needed data methods and define separate qualifying roles.
4. Publish the connection panel, run `/verifier doctor` and `/verifier probe`, then activate.
5. Complete [ACCEPTANCE.md](ACCEPTANCE.md) with a non-admin test member before public use.

## Repository contents

| Path | Purpose |
| --- | --- |
| `main.py`, `ripcars_verifier/` | Discord bot, identity proof, data adapters, ledger, roles, web routes, admin/member UI |
| `web/` | Self-hosted branded wallet connection page |
| `tests/`, `run_tests.sh` | Isolated functional, security, recovery and Discord simulation checks |
| `requirements.txt`, `requirements-lock.txt` | Direct dependencies and verified complete runtime pins |
| `.env.example` | All environment keys, without real secrets |
| `scripts/` | Preflight, secret configuration, staging, install, TLS, backup, restore, rollback, packaging |
| `ripcars-verifier.service` | Hardened systemd service and watchdog |
| `examples/` | Backend sender, clearly labeled fixtures and settings templates |
| `ARCHITECTURE.md`, `COORDINATION.md` | Data trust boundaries and shared bot ownership |
| `EXTENDING.md`, `CHANGELOG.md` | Future integrations and versioned changes |
| `ripcars_coordination.py`, `scripts/check_suite.py`, `suite_tests/` | Identical shared protocol, read-only registry inspector and explicit four-process compatibility tests |
| `TROUBLESHOOTING.md`, `permissions.csv` | Concrete diagnostics and Discord permission requirements |

## Local test run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
PYTHON=.venv/bin/python bash run_tests.sh
```

Python 3.12 was used for release validation. Python 3.11+ is required; other Python versions must pass staging before activation.

## Bot boundaries

Gate continues to own CAPTCHA entry and the ordinary Rippers role. Crew owns moderation/support. Raffle uses the resulting qualifying roles through its existing role eligibility rules. Verifier does not grant OG, staff roles, claimable notification roles, tickets, or moderation punishments. It does not edit other bots' channels. Optional channel setup creates only unset verifier channels. Future capabilities need a reviewed adapter or feature module; the bot cannot automatically understand an undocumented contract.
