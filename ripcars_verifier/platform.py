from __future__ import annotations

import time
from urllib.parse import quote

from .config import decimal, safe_url, usd_micros, discord_id
from .network import ProviderError


def validate_snapshot(data,account_id,max_age,now=None):
    now=time.time() if now is None else now
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('account_id')!=account_id or data.get('complete') is not True:
        raise ProviderError('Platform response identity, version or completeness is invalid.')
    stamp=data.get('as_of')
    if type(stamp) not in (int,float) or not now-max_age<=stamp<=now+60:
        raise ProviderError('Platform snapshot is stale or has an invalid timestamp.')
    for key in ('platform_points','token_balance'):
        if key in data: decimal(data[key])
    if 'assets' in data:
        if not isinstance(data['assets'],list) or len(data['assets'])>10000: raise ProviderError('Invalid platform asset list.')
        ids=set()
        for item in data['assets']:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not 1<=len(item['id'])<=200 or item['id'] in ids:
                raise ProviderError('Platform assets require unique stable IDs.')
            ids.add(item['id'])
            if not isinstance(item.get('attributes',{}),dict): raise ProviderError('Invalid asset attributes.')
    if not isinstance(data.get('deposits',[]),list) or len(data.get('deposits',[]))>1000: raise ProviderError('Invalid platform deposit page.')
    for deposit in data.get('deposits',[]):
        if not isinstance(deposit,dict) or deposit.get('kind')!='deposit' or not isinstance(deposit.get('id'),str) or not 1<=len(deposit['id'])<=200:
            raise ProviderError('Platform receipts must identify actual deposits.')
        usd_micros(deposit['usd_amount'])
        if deposit.get('status')!='settled': raise ProviderError('An unsettled receipt was supplied as a deposit.')
        if not isinstance(deposit.get('wallet',''),str): raise ProviderError('Invalid receipt wallet.')
        if 'settled_at' in deposit and (type(deposit['settled_at']) not in (int,float) or not 0<=deposit['settled_at']<=now+60): raise ProviderError('Invalid deposit settlement time.')
    cursor=data.get('next_cursor')
    if cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=200): raise ProviderError('Invalid platform pagination cursor.')
    return data


class Platform:
    """Explicit backend bridge contract; this is not an assumed public Rip Cars API."""
    def __init__(self,http,base,key):
        self.http=http; self.base=safe_url(base); self.key=key
        if not key or len(key)<16: raise ValueError('Configure PLATFORM_API_KEY on the VPS.')

    async def pages(self,account_id,max_age):
        cursor=None; visited=set(); stamp=None
        for _ in range(100):
            url=self.base+'/v1/discord/members/'+quote(account_id,safe='')
            if cursor: url+='?cursor='+quote(cursor,safe='')
            data=await self.http.request('GET',url,headers={'Authorization':'Bearer '+self.key})
            validate_snapshot(data,account_id,max_age)
            if stamp is not None and data['as_of']!=stamp: raise ProviderError('Platform snapshot changed during pagination.')
            stamp=data['as_of']; yield data
            cursor=data.get('next_cursor')
            if cursor is None: return
            if cursor in visited: raise ProviderError('Platform pagination repeated a cursor.')
            visited.add(cursor)
        raise ProviderError('Platform pagination limit reached.')


def rip_event(data,now=None):
    now=time.time() if now is None else now
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('type')!='rip.opened': raise ValueError('Unsupported rip event.')
    for key in ('id','car_name','asset_id'):
        if not isinstance(data.get(key),str) or not 1<=len(data[key])<=(200 if key=='id' else 250): raise ValueError('Missing rip event field: '+key)
    guild=discord_id(data.get('guild_id'))
    if type(data.get('occurred_at')) not in (int,float) or not 0<=data['occurred_at']<=now+60: raise ValueError('Invalid event time.')
    for key in ('image_url','asset_url'):
        if data.get(key): safe_url(data[key])
    if data.get('wallet'): __import__('ripcars_verifier.config',fromlist=['address']).address(data['wallet'])
    # No user-controlled ping, amount or arbitrary embed fields are forwarded.
    result={key:data.get(key,'') for key in ('id','guild_id','car_name','asset_id','occurred_at','image_url','asset_url','wallet')}
    result['guild_id']=guild; return result
