"""A finalized, wallet-signed transfer to the same owner proves a Discord link."""
from __future__ import annotations

import json
import secrets
import time
from decimal import Decimal

from .auth import bind_wallet
from .config import TOKEN_PROGRAM, TOKEN_2022, address, b58decode, canonical, wallet_kinds
from .network import ProviderError, Rpc


def amount_text(proof):
    return format(Decimal(proof['raw_amount']).scaleb(-proof['decimals']), 'f')


async def mint_details(rpc,mint):
    address(mint)
    data=await rpc.call('getAccountInfo',[mint,{'encoding':'jsonParsed','commitment':'finalized'}])
    account=data.get('value') if isinstance(data,dict) else None
    parsed=account.get('data',{}).get('parsed',{}) if isinstance(account,dict) else {}
    info=parsed.get('info',{})
    if not account or account.get('owner') not in (TOKEN_PROGRAM,TOKEN_2022) or parsed.get('type')!='mint' or info.get('isInitialized') is not True:
        raise ValueError('The configured address is not an initialized Solana token mint.')
    decimals=info.get('decimals')
    if type(decimals) is not int or not 4<=decimals<=18:
        raise ValueError('Self-transfer verification needs a token with 4 to 18 decimal places.')
    # Extensions can change transfer authorization and fees; require a reviewed adapter.
    if info.get('extensions'):
        raise ValueError('This token has extensions not supported by self-transfer verification. Keep this method off.')
    return account['owner'],decimals


def check_transfer(tx,signature,proof):
    if not isinstance(tx,dict):
        raise ValueError('Transaction is not finalized or not available yet. Wait a minute and check again.')
    meta=tx.get('meta'); transaction=tx.get('transaction',{})
    if not isinstance(meta,dict) or 'err' not in meta:
        raise ProviderError('Transaction metadata is incomplete. No wallet was verified.')
    if meta['err'] is not None:
        raise ValueError('This transaction failed on the network. It cannot verify your wallet.')
    if not transaction.get('signatures') or transaction['signatures'][0]!=signature:
        raise ProviderError('RPC returned a different transaction signature.')
    slot=tx.get('slot'); stamp=tx.get('blockTime')
    if type(slot) is not int or slot<=proof['slot'] or type(stamp) is not int or not proof['created']-2<=stamp<=proof['expires']:
        raise ValueError('Use a new transaction made after this request and before its expiry.')
    message=transaction.get('message',{}); accounts=message.get('accountKeys',[])
    # Self-transfers may succeed without ownership checks in the token program.
    # Require an actual transaction signer AND token-account ownership independently.
    if not any(isinstance(a,dict) and a.get('pubkey')==proof['wallet'] and a.get('signer') is True for a in accounts):
        raise ValueError('The wallet you entered did not sign this transaction.')
    keys=[a.get('pubkey') if isinstance(a,dict) else a for a in accounts]
    owners={}
    for balance in meta.get('preTokenBalances',[])+meta.get('postTokenBalances',[]):
        index=balance.get('accountIndex'); value=balance.get('uiTokenAmount',{})
        if type(index) is not int or not 0<=index<len(keys):
            raise ProviderError('Transaction token-account metadata is malformed.')
        identity=(balance.get('owner'),balance.get('mint'),value.get('decimals'),balance.get('programId'))
        if keys[index] in owners and owners[keys[index]]!=identity:
            raise ValueError('Token-account ownership or mint changed during this transaction.')
        owners[keys[index]]=identity
    instructions=list(message.get('instructions',[]))
    for group in meta.get('innerInstructions') or []:
        instructions.extend(group.get('instructions',[]))
    expected=(proof['wallet'],proof['mint'],proof['decimals'],proof['program'])
    for ins in instructions:
        if ins.get('programId')!=proof['program']: continue
        parsed=ins.get('parsed',{})
        if not isinstance(parsed,dict) or parsed.get('type') not in ('transfer','transferChecked'): continue
        info=parsed.get('info',{})
        if info.get('authority')!=proof['wallet']: continue
        if any(owners.get(info.get(key))!=expected for key in ('source','destination')): continue
        if parsed['type']=='transferChecked':
            value=info.get('tokenAmount',{})
            if info.get('mint')!=proof['mint'] or value.get('decimals')!=proof['decimals']: continue
            raw=value.get('amount')
        else: raw=info.get('amount')
        if raw==proof['raw_amount']:
            return
    raise ValueError('No matching self-transfer found. Check the wallet, token and exact amount in your request.')


class TransferProof:
    def __init__(self,store,http,env):
        self.store,self.http,self.env=store,http,env

    def rpc(self):
        return Rpc(self.http,self.env.get('RPC_URL',''),self.env.get('RPC_FALLBACK_URL',''))

    @staticmethod
    def require_enabled(cfg):
        if not cfg.get('wallet_transfer') or cfg['balance_mode']!='rpc' or not cfg['mint']:
            raise ValueError('Self-transfer verification is not configured. Ask a server admin to finish Token holder setup.')

    async def create(self,guild,user,wallet):
        wallet=address(wallet.strip()); now=time.time()
        cfg,_=await self.store.settings(guild); self.require_enabled(cfg)
        active=await self.store.query('SELECT * FROM transfer_proofs WHERE guild=? AND user=? AND used=0 AND expires>?',(guild,user,now))
        if active:
            if active[0]['wallet']==wallet and active[0]['mint']==cfg['mint']: return active[0]
            raise ValueError('Finish or cancel your current request before starting another.')
        def reserve(c):
            recent=c.execute("SELECT created FROM audit WHERE guild=? AND actor=? AND action='self_transfer_start' ORDER BY id DESC LIMIT 1",(guild,user)).fetchone()
            if recent and now-recent['created']<60:
                raise ValueError('A wallet request was just started. Wait one minute, then reopen your request.')
            c.execute("INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,'self_transfer_start','Ownership request',?)",(guild,user,now))
        await self.store.run(reserve)
        rpc=self.rpc(); program,decimals=await mint_details(rpc,cfg['mint'])
        slot=await rpc.call('getSlot',[{'commitment':'finalized'}])
        if type(slot) is not int or slot<0: raise ProviderError('RPC did not provide a valid finalized slot.')
        now=time.time()
        def issue(c):
            live=json.loads(c.execute('SELECT value FROM settings WHERE guild=?',(guild,)).fetchone()[0])
            self.require_enabled(live)
            if live['mint']!=cfg['mint']: raise ValueError('Token settings changed. Start again.')
            c.execute('UPDATE transfer_proofs SET used=-1 WHERE used=0 AND expires<=?',(now,))
            old=c.execute('SELECT * FROM transfer_proofs WHERE guild=? AND user=? AND used=0',(guild,user)).fetchone()
            if old:
                if old['wallet']==wallet and old['mint']==cfg['mint']: return dict(old)
                raise ValueError('Finish or cancel your current request first.')
            reserved=c.execute('SELECT user FROM transfer_proofs WHERE guild=? AND wallet=? AND used=0',(guild,wallet)).fetchone()
            linked=c.execute("SELECT user,active FROM links WHERE guild=? AND kind='wallet' AND identity=?",(guild,wallet)).fetchone()
            if reserved or linked and linked['user']!=user:
                raise ValueError('This wallet is already linked or has a pending request for another member.')
            if linked and linked['active']:
                raise ValueError('This wallet is already verified for you. Use Refresh on the member panel; no new transfer is needed.')
            count=c.execute("SELECT COUNT(*) FROM links WHERE guild=? AND user=? AND kind='wallet' AND active=1",(guild,user)).fetchone()[0]
            if count>=live['max_wallets']: raise ValueError('Wallet limit reached. Disconnect a wallet first.')
            raw=str((secrets.randbelow(10**min(decimals,6)-1)+1)*10**max(0,decimals-6))
            pid=secrets.token_urlsafe(18)
            c.execute('INSERT INTO transfer_proofs(id,guild,user,wallet,mint,program,decimals,raw_amount,slot,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (pid,guild,user,wallet,cfg['mint'],program,decimals,raw,slot,now,now+1200))
            return dict(c.execute('SELECT * FROM transfer_proofs WHERE id=?',(pid,)).fetchone())
        return await self.store.run(issue)

    async def verify(self,guild,user,pid,signature):
        signature=signature.strip()
        if len(b58decode(signature))!=64: raise ValueError('Paste the transaction signature from your wallet, not a wallet address or explorer link.')
        now=time.time()
        def claim(c):
            row=c.execute('SELECT * FROM transfer_proofs WHERE id=? AND guild=? AND user=?',(pid,guild,user)).fetchone()
            if not row or row['used'] or row['expires']<=now: raise ValueError('This request expired, was cancelled, or was already used. Start a new request.')
            if row['next_check']>now: raise ValueError('A check is already running or was just attempted. Wait 30 seconds.')
            cfg=json.loads(c.execute('SELECT value FROM settings WHERE guild=?',(guild,)).fetchone()[0])
            self.require_enabled(cfg)
            if cfg['mint']!=row['mint']: raise ValueError('Token settings changed. Start a new request.')
            c.execute('UPDATE transfer_proofs SET next_check=? WHERE id=?',(now+30,pid))
            return dict(row)
        proof=await self.store.run(claim)
        tx=await self.rpc().call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':0}])
        check_transfer(tx,signature,proof)
        def finish(c):
            current=c.execute('SELECT * FROM transfer_proofs WHERE id=?',(pid,)).fetchone()
            cfg=json.loads(c.execute('SELECT value FROM settings WHERE guild=?',(guild,)).fetchone()[0])
            self.require_enabled(cfg)
            if current['used'] or current['expires']<=time.time() or cfg['mint']!=proof['mint']:
                raise ValueError('The request or token settings changed during the check. Start again.')
            if c.execute('SELECT 1 FROM transfer_proofs WHERE signature=?',(signature,)).fetchone():
                raise ValueError('This transaction has already been used for verification.')
            bind_wallet(c,guild,user,proof['wallet'],cfg['max_wallets'],time.time())
            c.execute('UPDATE transfer_proofs SET used=1,signature=? WHERE id=?',(signature,pid))
            c.execute('INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,?,?,?)',
                      (guild,user,'wallet_self_transfer',canonical({'wallet':proof['wallet'],'mint':proof['mint'],'signature':signature,'slot':tx['slot']}),time.time()))
            self.store.queue_check(c,guild,user,wallet_kinds(cfg),time.time())
        await self.store.run(finish)

    async def cancel(self,guild,user,pid):
        await self.store.execute('UPDATE transfer_proofs SET used=-1 WHERE id=? AND guild=? AND user=? AND used=0',(pid,guild,user))
