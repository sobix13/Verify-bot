# Token holder setup without a wallet connection

Verifier 1.2.0 adds an optional self-transfer proof. Existing wallet signatures and platform account links remain available. All member screens are English. Raffle still uses Discord roles only and makes no RPC or platform requests.

## What the admin needs

Get the official **Solana mainnet token mint**, a mainnet HTTPS RPC URL, an optional independent backup RPC URL, and the minimum token balance for each role. A mint identifies the token, not a team wallet, pool or treasury. No project address is supplied by this repository. Keep provider API keys on the VPS; never ask members for seed phrases or private keys.

## Configure the RPC on the VPS

Run as root after installing this release:

```sh
/opt/ripcars-verifier/current/.venv/bin/python /opt/ripcars-verifier/current/scripts/configure_rpc.py
```

Only main/backup RPC settings are prompted. Enter retains an existing URL; `-` clears the optional backup. Paste the complete HTTPS URL provided by the RPC service. Input is hidden. The tool tests both endpoints for Solana mainnet identity, health, finalized slots and transaction lookup before saving. Failure leaves the environment file unchanged. Success preserves all unrelated credentials, saves a private copy of the previous file and writes the environment with mode 600. It prints no provider URL or key.

```sh
systemctl restart ripcars-verifier.service
```

No RPC server, wallet connection site, public domain, platform API or signing key is needed for this method. The RPC provider must support JSON-parsed `getAccountInfo`, `getTransaction`, `getTokenAccountsByOwner`, `getSlot`, `getHealth` and `getGenesisHash` on mainnet. Transaction metadata must include token-account owners, mint, decimals and program identity.

## Configure Discord

1. Open `/verifier panel` and choose **Token holder setup**.
2. **Edit $CARS mint:** paste the official mint.
3. **Token balance source:** choose `rpc`.
4. Enable **Self-transfer verification**. Website **Wallet linking** can remain off.
5. **Set qualifying roles:** add rules with metric `token_balance`. For `1K CARS`, use threshold `1000`. Create/bind dedicated zero-permission roles below the bot role. OG and staff roles remain manual.
6. In **Channels and access**, select the ordinary Gate member role, member-visible verification channel and staff-only log channel.
7. **Check RPC connection**, then **Publish verification panel**. This updates the existing bot panel with **Verify wallet without connecting**.
8. Run Doctor and activate after its checks pass. Test with a non-admin member before community launch.

Editing the mint, source or proof method pauses automation for review. Other bot configuration, existing identities and qualification history are retained. Old settings interpret the new method as off. Opening the upgraded database does not rewrite existing settings, so a failed initial activation can return to the prior code without a configuration migration. Before a later manual rollback to 1.1.x, export settings and remove the new `wallet_transfer` field from its old-format settings import; keep this release available and never restore an old database over newer activity.

## Member flow

1. Choose **Verify wallet without connecting** and enter your public Solana wallet address.
2. The bot privately shows the exact mint and random token amount, destination equal to your own wallet, and a 20-minute expiry. Copy all amount digits.
3. In your own wallet, send that token amount back to that same wallet. A network fee in SOL applies. The bot never constructs or submits a transaction. If the wallet blocks self-transfers, cancel; do not send to another person or a team wallet.
4. Choose **Check transaction** and paste the transaction signature from your wallet. An unfinalized transaction can be checked again after 30 seconds.
5. A successful proof links the address and atomically queues its current balance check. The usual qualification rules decide which role to grant. The member can see the result under **My status**.

Already verified active wallets use **Refresh** without another transfer. Existing 12-hour monitoring applies only while a verified wallet qualification is active. Provider failures preserve current roles and go to Verifier's private staff log; no new Maestro notification path is added. Platform point updates remain independent and admin-requested.

Requests survive process restart. Reopen the same wallet request to recover its exact amount. Cancelled, expired or restored pending requests cannot link a wallet. A wallet cannot be silently reassigned to another member. Disconnect and relink preserve the same Discord ownership binding.

## Verification boundaries

The verifier requires successful finalized metadata, the exact requested mint, raw amount and decimals, a newer slot and valid time window, token accounts owned by the entered wallet, the correct token program and transfer authority, and the entered wallet as a transaction signer. A signature can complete only one proof. A transfer receipt alone is insufficient: SPL self-transfers can succeed without proving the claimed owner. See the official [Token transfer documentation](https://www.solana-program.com/docs/token) and [getTransaction documentation](https://solana.com/docs/rpc/http/gettransaction).

This release accepts parsed `transfer` and `transferChecked` instructions, including inner instructions and transfers between two token accounts of the same owner. It supports initialized ordinary SPL mints with 4–18 decimals. Token-2022 extension mints, low-precision mints, delegated/multisignature wallets without the owner signing, unsupported transaction versions and incomplete provider metadata are rejected. These limits are deliberate; do not bypass them. Proof transfers never earn deposit points.

Read-only RPC tests do not prove that a particular wallet app permits a self-transfer. A live test with the actual CARS mint, provider and chosen wallet is required before enabling this method for members.
