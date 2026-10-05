from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal

from .chain import assets_by_owner, scan_deposits, token_balance
from .config import canonical, decimal, role_metric
from .network import Rpc, ProviderError
from .platform import Platform
from .storage import Conflict


def readiness(cfg,env):
    blockers=[]
    if not env.get('PUBLIC_URL'): blockers.append('Set PUBLIC_URL to the verifier HTTPS origin on the VPS.')
    if not cfg['member_role']: blockers.append('Choose the Gate member role.')
    if not cfg['verification_channel']: blockers.append('Choose the connection panel channel.')
    if not cfg['public_message']: blockers.append('Publish the connection panel before activation.')
    if not cfg['log_channel']: blockers.append('Choose a staff-only log channel.')
    if cfg['balance_mode']=='rpc' and not cfg['mint']: blockers.append('Set the verified $CARS mint.')
    if cfg['balance_mode']=='rpc' or cfg['deposit_mode']=='rpc':
        if not env.get('RPC_URL'): blockers.append('Configure RPC_URL on the VPS.')
    if cfg['asset_mode']=='das':
        if not env.get('DAS_URL'): blockers.append('Configure DAS_URL on the VPS.')
        if not cfg['collections']: blockers.append('Add verified Rip Cars collection IDs.')
    platform=cfg['account_linking'] or cfg['platform_points'] or 'platform' in (cfg['balance_mode'],cfg['asset_mode'],cfg['deposit_mode'])
    if platform and not (env.get('PLATFORM_API_URL') and env.get('PLATFORM_API_KEY')): blockers.append('Configure the Rip Cars backend bridge URL and key.')
    if cfg['account_linking'] and not cfg['platform_connect_url']: blockers.append('Set the team-provided account connect page URL.')
    if cfg['account_linking'] or cfg['rip_feed']:
        if len(env.get('PLATFORM_WEBHOOK_SECRET',''))<32: blockers.append('Configure PLATFORM_WEBHOOK_SECRET on both servers.')
    if cfg['deposit_mode']=='rpc' and not cfg['sources']: blockers.append('Add verified payment sources and destination addresses.')
    if cfg['chain_webhook'] and (len(env.get('CHAIN_WEBHOOK_SECRET',''))<32 or not env.get('RPC_URL')): blockers.append('Configure the signed chain webhook secret and RPC URL.')
    if cfg['rip_feed'] and not cfg['rip_channel']: blockers.append('Choose the rip announcement channel.')
    if cfg['rules'] and any(r['enabled'] and not r['role_id'] for r in cfg['rules']): blockers.append('Bind or create all enabled qualifying roles.')
    for rule in cfg['rules']:
        if not rule['enabled']: continue
        mode={'deposit_points':cfg['deposit_mode']!='off','platform_points':cfg['platform_points'],'token_balance':cfg['balance_mode']!='off','asset_count':cfg['asset_mode']!='off'}[rule['kind']]
        if not mode: blockers.append(f'Rule {rule["key"]}: enable its data source or disable the rule.')
    return blockers


class Engine:
    def __init__(self,store,http,env=None):
        self.store=store; self.http=http; self.env=dict(os.environ if env is None else env); self.locks={}

    async def snapshot(self,guild,user,cfg):
        links=await self.store.links(guild,user)
        wallets=[r['identity'] for r in links if r['kind']=='wallet']; accounts=[r['identity'] for r in links if r['kind']=='account']
        out={'wallets':wallets,'accounts':accounts,'assets':[],'deposit_points':'0','platform_points':'0','token_balance':'0','complete':{},'checked_at':time.time()}
        use_platform=cfg['platform_points'] or 'platform' in (cfg['balance_mode'],cfg['asset_mode'],cfg['deposit_mode'])
        if use_platform:
            # A missing account is known absence. A failed API call is unknown state.
            if accounts:
                gateway=Platform(self.http,self.env.get('PLATFORM_API_URL',''),self.env.get('PLATFORM_API_KEY',''))
                first=None
                async for page in gateway.pages(accounts[0],cfg['snapshot_max_age']):
                    first=page if first is None else first
                    if cfg['deposit_mode']=='platform':
                        if 'deposits' not in page: raise ProviderError('Backend omitted the requested deposit ledger.')
                        from .config import usd_micros
                        for receipt in page['deposits']:
                            if cfg['history_start']:
                                if 'settled_at' not in receipt: raise ProviderError('Backend receipts need settled_at when a history start is configured.')
                                if receipt['settled_at']<cfg['history_start']: continue
                            amount=usd_micros(receipt['usd_amount'])
                            if not amount: continue
                            await self.store.credit(guild,user,receipt.get('wallet',''),f'platform:{accounts[0]}:{receipt["id"]}',amount,'platform',{'id':receipt['id'],'account_id':accounts[0],'kind':'deposit','status':'settled','usd_amount':receipt['usd_amount'],'settled_at':receipt.get('settled_at'),'as_of':page['as_of']})
                if first is None: raise ProviderError('Backend returned no snapshot.')
                for enabled,key in ((cfg['platform_points'],'platform_points'),(cfg['balance_mode']=='platform','token_balance'),(cfg['asset_mode']=='platform','assets')):
                    if enabled:
                        if key not in first: raise ProviderError('Backend omitted requested field '+key+'.')
                        out[key]=first[key]
            for enabled,key in ((cfg['platform_points'],'platform_points'),(cfg['balance_mode']=='platform','token_balance'),(cfg['asset_mode']=='platform','asset_count'),(cfg['deposit_mode']=='platform','deposit_points')):
                if enabled: out['complete'][key]=True
        if cfg['balance_mode']=='rpc':
            rpc=Rpc(self.http,self.env.get('RPC_URL',''),self.env.get('RPC_FALLBACK_URL',''))
            total=Decimal(0)
            for wallet in wallets: total+=decimal(await token_balance(rpc,wallet,cfg['mint']),maximum=Decimal('1e30'))
            out['token_balance']=format(total,'f'); out['complete']['token_balance']=True
        if cfg['asset_mode']=='das':
            rpc=Rpc(self.http,self.env.get('DAS_URL','')); assets={}
            for wallet in wallets:
                for asset in await assets_by_owner(rpc,wallet,cfg['collections']): assets[asset['id']]=asset
            out['assets']=list(assets.values()); out['complete']['asset_count']=True
        if cfg['deposit_mode']=='rpc':
            rpc=Rpc(self.http,self.env.get('RPC_URL',''),self.env.get('RPC_FALLBACK_URL','')); done=True
            for wallet in wallets:
                done=await scan_deposits(self.store,rpc,guild,user,wallet,cfg) and done
            out['complete']['deposit_points']=done
        out['deposit_points']=format(Decimal(await self.store.total(guild,user))/1000000,'f')
        out['incomplete_rules']=[r['key'] for r in cfg['rules'] if r['enabled'] and role_metric(r,out) is None]
        # Disconnecting an account removes holding-based eligibility, not earned deposit history.
        return out

    async def refresh(self,guild,user):
        async with self.locks.setdefault((guild,user),asyncio.Lock()):
            cfg,revision=await self.store.settings(guild)
            if not cfg['enabled']: raise ValueError('Verifier is paused. Finish setup and activate it first.')
            data=await self.snapshot(guild,user,cfg)
            def save(c):
                current=c.execute('SELECT revision FROM settings WHERE guild=?',(guild,)).fetchone()[0]
                if current!=revision: raise Conflict('Settings changed during the check. Retry with the new settings.')
                c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',(guild,user,canonical(data),time.time()))
            await self.store.run(save)
            return data,cfg,revision

    async def status(self,guild,user):
        rows=await self.store.query('SELECT * FROM snapshots WHERE guild=? AND user=?',(guild,user))
        return json.loads(rows[0]['value']) if rows else None


def role_plan(cfg,snapshot,current_roles,previous_grants):
    additions=[]; removals=[]; preserved=[]
    for rule in cfg['rules']:
        if not rule['role_id']: continue
        rid=rule['role_id']
        if not rule['enabled']:
            if rid in previous_grants and rid in current_roles: removals.append(rid)
            continue
        value=role_metric(rule,snapshot)
        if value is None: preserved.append(rid); continue
        qualifies=value>=decimal(rule['threshold'])
        if qualifies and rid not in current_roles: additions.append(rid)
        if not qualifies and rid in current_roles and rid in previous_grants: removals.append(rid)
    # Role grants outside the configured rules require explicit admin cleanup.
    return additions,removals,preserved
