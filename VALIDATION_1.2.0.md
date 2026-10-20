# Validation: Verifier 1.2.0

Validated 2026-10-20 with Python 3.12.14 and discord.py 2.7.1.

- Full run_tests.sh: 251 tests passed. Compilation, Pyflakes, dependency checks, shell syntax and wallet-client JavaScript syntax passed.
- Self-transfer regressions cover wallet transaction signer verification (including successful unsigned self-transfers), account ownership, authority, exact raw amount, mint, decimals, token program, timestamps, slot boundaries, failed and unfinalized transactions, signature replay, concurrent requests, tenant/user isolation, cancellation, provider failures, retry limits, existing identities, source changes during RPC, atomic link/job writes, restored pending requests and actual Discord modal callbacks.
- The RPC-only credential tool is tested for wrong network/unsupported providers, preserving unrelated credentials, private environment mode, previous-file retention and leaving the file unchanged after a failed probe.
- Existing role qualification cycles, 12-hour monitoring, provider-failure role preservation, platform/RPC separation, optional signed wallet linking, account links, webhooks and backups remain covered by the full suite.
- Separate four-process compatibility suite: 21 tests passed against actual Gate, Crew, Raffle and Verifier implementations with temporary private databases.

No new dependency, inter-bot network call, transaction builder or signing key was added. All public/admin Verifier UI text is English.

Not checked: the actual CARS mint/precision, a production RPC subscription, self-transfer support in the chosen wallet app, live Discord interactions or the user's VPS. No real funds were transferred. Use SELF_TRANSFER_SETUP.md and test with a non-admin member before activation for the community. Ordinary SPL mints with 4–18 decimals are supported; extension mints and incomplete transaction metadata are rejected.
