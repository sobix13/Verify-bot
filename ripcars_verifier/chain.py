from __future__ import annotations

import json
import time
from decimal import Decimal, ROUND_DOWN

from .config import TOKEN_PROGRAM, TOKEN_2022, address, canonical, decimal, source_hash, usd_micros
from .network import ProviderError


def transfers(transaction,wallet,sources,signature):
    """Count actual parsed SPL deposits, including CPI; never value SOL using today's price."""
    if not isinstance(transaction,dict) or not isinstance(transaction.get('meta'),dict): raise ProviderError('Transaction metadata is missing.')
    meta=transaction['meta']
    if meta.get('err') is not None: return []
    tx=transaction.get('transaction',{})
    if not tx.get('signatures') or tx['signatures'][0]!=signature: raise ProviderError('RPC transaction signature mismatch.')
    message=tx.get('message',{}); keys=[k.get('pubkey') if isinstance(k,dict) else k for k in message.get('accountKeys',[])]
    balances={}
    for b in meta.get('preTokenBalances',[])+meta.get('postTokenBalances',[]):
        if type(b.get('accountIndex')) is not int or not 0<=b['accountIndex']<len(keys): raise ProviderError('Malformed token balance index.')
        balances[keys[b['accountIndex']]]=b
    instructions=[]
    for i,ins in enumerate(message.get('instructions',[])): instructions.append((f'{i}:root',ins))
    for group in meta.get('innerInstructions') or []:
        for j,ins in enumerate(group.get('instructions',[])): instructions.append((f'{group["index"]}:{j}',ins))
    programs={ins.get('programId') for _,ins in instructions}; out=[]
    for index,ins in instructions:
        if ins.get('programId') not in (TOKEN_PROGRAM,TOKEN_2022): continue
        parsed=ins.get('parsed',{})
        if not isinstance(parsed,dict) or parsed.get('type') not in ('transfer','transferChecked'): continue
        info=parsed.get('info',{}); src=balances.get(info.get('source'),{}); dst=balances.get(info.get('destination'),{})
        if src.get('owner')!=wallet or dst.get('owner')==wallet: continue
        mint=src.get('mint'); decimals=src.get('uiTokenAmount',{}).get('decimals')
        if not mint or mint!=dst.get('mint') or decimals!=dst.get('uiTokenAmount',{}).get('decimals'): continue
        if parsed['type']=='transferChecked':
            if info.get('mint')!=mint or info.get('tokenAmount',{}).get('decimals')!=decimals: continue
            raw=info.get('tokenAmount',{}).get('amount')
        else: raw=info.get('amount')
        if not isinstance(raw,str) or not raw.isdigit() or len(raw)>30: continue
        for source in sources:
            if source['program_ids'] and not programs.intersection(source['program_ids']): continue
            if dst.get('owner') not in source['treasury_owners'] and info.get('destination') not in source['token_accounts']: continue
            spec=source['mints'].get(mint)
            if not spec or spec['decimals']!=decimals: continue
            # Token-2022 fees need a platform receipt or a custom fee-aware adapter.
            if ins['programId']==TOKEN_2022: continue
            usd=Decimal(raw)/(Decimal(10)**decimals)*decimal(spec['usd_per_token'])
            if usd<=0: continue
            if usd>Decimal('1000000000000'): raise ProviderError('A deposit exceeds the supported receipt amount. Review the configured USD valuation.')
            amount=usd_micros(format(usd.quantize(Decimal('0.000001'),rounding=ROUND_DOWN),'f'))
            if not amount: continue
            out.append({'event_id':f'solana:{signature}:{index}','usd_micros':amount,'source':source['id'],
                        'proof':{'signature':signature,'instruction':index,'mint':mint,'raw_amount':raw,'decimals':decimals,'usd_per_token':str(spec['usd_per_token']),'destination':info['destination'],'slot':transaction.get('slot'),'block_time':transaction.get('blockTime')}})
            break
    return out


def platform_interaction(transaction,sources):
    message=transaction.get('transaction',{}).get('message',{})
    keys={k.get('pubkey') if isinstance(k,dict) else k for k in message.get('accountKeys',[])}
    configured={v for source in sources for name in ('program_ids','treasury_owners','token_accounts') for v in source[name]}
    return bool(keys.intersection(configured))


async def token_balance(rpc,wallet,mint):
    address(wallet); address(mint)
    data=await rpc.call('getTokenAccountsByOwner',[wallet,{'mint':mint},{'encoding':'jsonParsed','commitment':'finalized'}])
    if not isinstance(data,dict) or not isinstance(data.get('value'),list): raise ProviderError('Token account list is incomplete.')
    result=Decimal(0); known_decimals=None
    for row in data['value']:
        account=row.get('account',{})
        info=account.get('data',{}).get('parsed',{}).get('info',{})
        value=info.get('tokenAmount',{})
        if account.get('owner') not in (TOKEN_PROGRAM,TOKEN_2022) or info.get('owner')!=wallet or info.get('mint')!=mint:
            raise ProviderError('Unexpected token account identity.')
        raw=value.get('amount'); decimals=value.get('decimals')
        if not isinstance(raw,str) or not raw.isdigit() or type(decimals) is not int or not 0<=decimals<=18: raise ProviderError('Invalid raw token balance.')
        if known_decimals is not None and decimals!=known_decimals: raise ProviderError('Mint decimals changed between accounts.')
        known_decimals=decimals; result+=Decimal(raw)/(Decimal(10)**decimals)
    return format(result,'f')


async def assets_by_owner(rpc,wallet,collections,max_pages=50):
    items={}; last_page=None
    for page in range(1,max_pages+1):
        data=await rpc.call('getAssetsByOwner',{'ownerAddress':wallet,'page':page,'limit':100,'options':{'showUnverifiedCollections':False}})
        if not isinstance(data,dict) or not isinstance(data.get('items'),list): raise ProviderError('DAS response is incomplete.')
        batch=data['items']
        ids=tuple(a.get('id') for a in batch)
        if batch and ids==last_page: raise ProviderError('DAS pagination repeated a page.')
        last_page=ids
        for asset in batch:
            if asset.get('burnt') or asset.get('ownership',{}).get('owner')!=wallet: continue
            if asset.get('interface') not in ('V1_NFT','V2_NFT','ProgrammableNFT','MplCoreAsset'): continue
            groups=[g['group_value'] for g in asset.get('grouping',[]) if g.get('group_key')=='collection' and g.get('verified',True) is True]
            collection=next((g for g in groups if g in collections),None)
            if not collection: continue
            content=asset.get('content',{}); metadata=content.get('metadata',{})
            attrs={a['trait_type']:str(a.get('value','')) for a in metadata.get('attributes',[]) if isinstance(a,dict) and isinstance(a.get('trait_type'),str)}
            items[asset['id']]={'id':asset['id'],'collection':collection,'name':str(metadata.get('name',''))[:200],'attributes':attrs,'image':content.get('links',{}).get('image','')}
        if len(batch)<100: return list(items.values())
    raise ProviderError('DAS pagination limit reached. Existing roles were preserved.')


async def scan_deposits(store,rpc,guild,user,wallet,cfg):
    rows=await store.query('SELECT value FROM checkpoints WHERE guild=? AND wallet=?',(guild,wallet))
    state=json.loads(rows[0]['value']) if rows else {}
    fingerprint=source_hash(cfg)
    if state.get('source_hash')!=fingerprint:
        state={'head':None,'before':None,'candidate':None,'source_hash':fingerprint,'complete':False}
    remaining=cfg['scan_transactions']
    for _ in range(cfg['scan_pages']):
        options={'limit':cfg['scan_limit'],'commitment':'finalized'}
        if state.get('before'): options['before']=state['before']
        if state.get('head'): options['until']=state['head']
        page=await rpc.call('getSignaturesForAddress',[wallet,options])
        if not isinstance(page,list): raise ProviderError('Signature history is incomplete.')
        if not state.get('candidate') and page: state['candidate']=page[0]['signature']
        finished=not page; processed=None
        for row in page:
            signature=row.get('signature')
            if not isinstance(signature,str) or row.get('confirmationStatus')!='finalized': raise ProviderError('History contains a non-finalized or malformed signature.')
            if row.get('blockTime') is not None and row['blockTime']<cfg['history_start']:
                finished=True; break
            if remaining<=0: break
            remaining-=1
            if row.get('err') is not None:
                deposits=[]; status='failed'; related=False
            else:
                tx=await rpc.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':0}])
                if tx is None: raise ProviderError('Historical transaction is unavailable. Use an archival provider or platform receipts.')
                deposits=transfers(tx,wallet,cfg['sources'],signature); status='deposit' if deposits else 'other'
                related=bool(deposits) or platform_interaction(tx,cfg['sources'])
            for deposit in deposits:
                await store.credit(guild,user,wallet,deposit['event_id'],deposit['usd_micros'],deposit['source'],deposit['proof'])
            if related:
                await store.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)',(guild,wallet,signature,status,canonical({'deposit_events':[d['event_id'] for d in deposits]}),time.time()))
            processed=signature
        if processed: state['before']=processed
        if finished:
            state['head']=state.get('candidate') or state.get('head'); state['candidate']=None; state['before']=None; state['complete']=True
        else: state['complete']=False
        await store.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)',(guild,wallet,canonical(state)))
        if finished: return True
        if remaining<=0: return False
        if not processed: raise ProviderError('History pagination made no progress.')
    return False
