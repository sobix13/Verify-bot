# Validation: Verifier 1.1.0

Validated 2026-10-20 with Python 3.12.14 and discord.py 2.7.1.

- `PYTHON=<runtime>/bin/python bash run_tests.sh`: 225 tests passed, no failures, errors or skips. Compilation, dependency compatibility, Pyflakes, shell and wallet-client JavaScript syntax passed.
- New policy tests cover first-check rejection, qualified-member 12-hour monitoring, departure after the last qualification is lost, role preservation on provider failure, no inactive webhook reenrollment, manual role removal, concurrent queued requests, API/RPC separation, cached point preservation, persisted group-update progress, request-limit pausing, initial-job migration and identity changes during a provider read.
- The separate four-process suite passed 21 tests against the actual current implementations and the existing Gate/Crew sources, using only temporary private data and shared coordination.

Wallet and platform identities remain separate. No new runtime dependency or inter-bot request path was introduced. Raffle does not call this bot or its data providers.

Not checked here: live Discord, production RPC/platform responses, actual team API credentials, the user's VPS or production backup repository. Advanced RPC/token onboarding is a later phase, not part of this refresh-policy change.
