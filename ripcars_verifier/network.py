from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets

import aiohttp
from aiohttp.abc import AbstractResolver

from .config import safe_url, MAINNET_GENESIS


class ProviderError(RuntimeError):
    pass


class PublicResolver(AbstractResolver):
    def __init__(self): self.resolver=aiohttp.resolver.ThreadedResolver()
    async def resolve(self,host,port=0,family=0):
        rows=await self.resolver.resolve(host,port,family)
        if not rows or any(not ipaddress.ip_address(r['host']).is_global for r in rows):
            raise OSError('Provider DNS resolved to a non-public address.')
        return rows
    async def close(self): await self.resolver.close()


class Http:
    def __init__(self,activity=None): self.session=None; self.activity=activity
    async def open(self):
        self.session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver(),limit=8),timeout=aiohttp.ClientTimeout(total=25),trust_env=False)
    async def close(self):
        if self.session: await self.session.close()
    async def request(self,method,url,payload=None,headers=None):
        safe_url(url)
        for attempt in range(3):
            if self.activity: self.activity()
            try:
                async with self.session.request(method,url,json=payload,headers=headers,allow_redirects=False) as response:
                    if response.status in (429,500,502,503,504): raise ProviderError('Provider is temporarily unavailable.')
                    if response.status!=200: raise ValueError(f'Provider returned HTTP {response.status}. Check its URL and credentials.')
                    body=bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body)>2*1024*1024: raise ValueError('Provider response exceeded the two-megabyte limit.')
                    return json.loads(body,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON value.')))
            except (aiohttp.ClientError,asyncio.TimeoutError,ProviderError):
                if attempt==2: raise ProviderError('Provider timed out or is unavailable. Existing role state was preserved.') from None
                await asyncio.sleep((2**attempt)+secrets.randbelow(100)/1000)


class Rpc:
    def __init__(self,http,url,fallback=''):
        self.http=http; self.urls=[u for u in (url,fallback) if u]
        self.verified=set()
        if not self.urls: raise ValueError('Configure RPC_URL on the VPS first.')
        for u in self.urls: safe_url(u)
    async def call(self,method,params):
        last=None
        for url in self.urls:
            try:
                if url not in self.verified:
                    identity=await self.http.request('POST',url,{'jsonrpc':'2.0','id':'ripcars-verifier','method':'getGenesisHash','params':[]})
                    if not isinstance(identity,dict) or identity.get('result')!=MAINNET_GENESIS or identity.get('jsonrpc')!='2.0' or identity.get('id')!='ripcars-verifier' or 'error' in identity:
                        raise ProviderError('Provider is not a verified Solana mainnet endpoint.')
                    self.verified.add(url)
                data=await self.http.request('POST',url,{'jsonrpc':'2.0','id':'ripcars-verifier','method':method,'params':params})
                if not isinstance(data,dict) or data.get('id')!='ripcars-verifier' or data.get('jsonrpc')!='2.0' or 'error' in data or 'result' not in data:
                    raise ProviderError('RPC response is missing a valid result. Check provider method support.')
                return data['result']
            except (ProviderError,ValueError) as exc: last=exc
        raise ProviderError(str(last))
