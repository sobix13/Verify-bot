from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import address, b58decode, safe_url
from .storage import Conflict


def token_hash(token):
    if not isinstance(token,str) or not 32<=len(token)<=100:
        raise ValueError('Invalid connection session.')
    return hashlib.sha256(token.encode()).hexdigest()


class Linking:
    def __init__(self,store,public_url):
        self.store=store; self.origin=safe_url(public_url,origin_only=True)

    async def create(self,guild,user,purpose='wallet',now=None):
        now=time.time() if now is None else now
        if purpose not in ('wallet','account'): raise ValueError('Unknown connection purpose.')
        token=secrets.token_urlsafe(32)
        def issue(c):
            count=c.execute('SELECT COUNT(*) FROM sessions WHERE guild=? AND user=? AND expires>? AND used=0',(guild,user,now)).fetchone()[0]
            if count>=5: raise ValueError('Too many open connection links. Wait ten minutes before creating another.')
            c.execute('INSERT INTO sessions(token,guild,user,purpose,expires) VALUES(?,?,?,?,?)',(token_hash(token),guild,user,purpose,now+600))
        await self.store.run(issue)
        return token

    async def challenge(self,token,wallet,now=None):
        address(wallet); now=time.time() if now is None else now
        hashed=token_hash(token)
        def issue(c):
            row=c.execute('SELECT * FROM sessions WHERE token=?',(hashed,)).fetchone()
            if not row or row['used'] or row['expires']<=now or row['purpose']!='wallet': raise ValueError('This wallet link expired or was already used.')
            stamp=datetime.fromtimestamp(now,timezone.utc).isoformat()
            expiry=datetime.fromtimestamp(row['expires'],timezone.utc).isoformat()
            message=(f'{urlsplit(self.origin).netloc} wants you to sign in with your Solana account:\n'
                     f'{wallet}\n\nLink this wallet to Discord user {row["user"]} in server {row["guild"]} for Rip Cars role checks.\n'
                     f'This signature authorizes account linking only.\n\nURI: {self.origin}\nVersion: 1\nChain ID: solana:mainnet\n'
                     f'Nonce: {secrets.token_hex(16)}\nIssued At: {stamp}\nExpiration Time: {expiry}\nRequest ID: {hashed[:16]}')
            c.execute('UPDATE sessions SET wallet=?,message=? WHERE token=?',(wallet,message,hashed))
            return message
        return await self.store.run(issue)

    async def verify(self,token,wallet,signature,now=None):
        now=time.time() if now is None else now; address(wallet); hashed=token_hash(token)
        rows=await self.store.query('SELECT * FROM sessions WHERE token=?',(hashed,))
        if not rows: raise ValueError('Connection session was not found.')
        row=rows[0]
        if row['used'] or row['expires']<=now or row['purpose']!='wallet' or row['wallet']!=wallet or not row['message']:
            raise ValueError('Wallet challenge expired, changed, or was already used.')
        try:
            sig=base64.b64decode(signature,validate=True)
            if len(sig)!=64: raise ValueError()
            Ed25519PublicKey.from_public_bytes(b58decode(wallet)).verify(sig,row['message'].encode())
        except (ValueError,TypeError,InvalidSignature):
            raise ValueError('The signature did not match the wallet challenge.') from None
        cfg,_=await self.store.settings(row['guild'])
        if not cfg['wallet_linking']: raise ValueError('Wallet linking is paused.')
        def bind(c):
            current=c.execute('SELECT * FROM sessions WHERE token=?',(hashed,)).fetchone()
            if current['used'] or current['expires']<=now or current['message']!=row['message']: raise Conflict('Connection changed. Create a new link.')
            old=c.execute("SELECT * FROM links WHERE guild=? AND kind='wallet' AND identity=?",(row['guild'],wallet)).fetchone()
            if old and old['user']!=row['user']: raise Conflict('This wallet belongs to another Discord connection. It is not reassigned automatically.')
            count=c.execute("SELECT COUNT(*) FROM links WHERE guild=? AND user=? AND kind='wallet' AND active=1",(row['guild'],row['user'])).fetchone()[0]
            if not (old and old['active']) and count>=cfg['max_wallets']: raise ValueError('Wallet limit reached. Disconnect an existing wallet first.')
            c.execute("INSERT INTO links VALUES(?,?,'wallet',?,1,?) ON CONFLICT(guild,kind,identity) DO UPDATE SET active=1",(row['guild'],row['user'],wallet,now))
            c.execute('UPDATE sessions SET used=1 WHERE token=?',(hashed,))
            return row['guild'],row['user']
        result=await self.store.run(bind); await self.store.enqueue(*result); return result

    async def attest_account(self,token,account_id,now=None):
        now=time.time() if now is None else now
        if not isinstance(account_id,str) or not 1<=len(account_id)<=128 or any(ord(c)<33 for c in account_id):
            raise ValueError('Invalid platform account ID.')
        hashed=token_hash(token)
        def bind(c):
            row=c.execute('SELECT * FROM sessions WHERE token=?',(hashed,)).fetchone()
            if not row or row['used'] or row['expires']<=now or row['purpose']!='account': raise ValueError('Account link expired or was already used.')
            old=c.execute("SELECT * FROM links WHERE guild=? AND kind='account' AND identity=?",(row['guild'],account_id)).fetchone()
            if old and old['user']!=row['user']: raise Conflict('This platform account is already linked to another Discord user.')
            other=c.execute("SELECT identity FROM links WHERE guild=? AND user=? AND kind='account' AND active=1 AND identity<>?",(row['guild'],row['user'],account_id)).fetchone()
            if other: raise Conflict('Disconnect the current platform account before linking another.')
            c.execute("INSERT INTO links VALUES(?,?,'account',?,1,?) ON CONFLICT(guild,kind,identity) DO UPDATE SET active=1",(row['guild'],row['user'],account_id,now))
            c.execute('UPDATE sessions SET used=1 WHERE token=?',(hashed,))
            return row['guild'],row['user']
        result=await self.store.run(bind); await self.store.enqueue(*result); return result

    async def disconnect(self,guild,user,kind,identity):
        await self.store.execute('UPDATE links SET active=0 WHERE guild=? AND user=? AND kind=? AND identity=?',(guild,user,kind,identity))
        await self.store.enqueue(guild,user)


def check_hmac(secret,timestamp,event_id,body,signature,now=None):
    now=time.time() if now is None else now
    if not isinstance(secret,str) or len(secret)<32: raise ValueError('Webhook secret is not configured.')
    if not isinstance(event_id,str) or not 1<=len(event_id)<=120 or any(ord(c)<33 for c in event_id): raise ValueError('Invalid webhook ID.')
    try: stamp=int(timestamp)
    except (TypeError,ValueError): raise ValueError('Invalid webhook timestamp.') from None
    if abs(now-stamp)>300: raise ValueError('Webhook timestamp is outside the five-minute acceptance window.')
    value=str(timestamp).encode()+b'.'+event_id.encode()+b'.'+body
    expected=hmac.new(secret.encode(),value,hashlib.sha256).hexdigest()
    if not isinstance(signature,str) or not hmac.compare_digest(expected,signature): raise ValueError('Webhook authentication failed.')
    return stamp
