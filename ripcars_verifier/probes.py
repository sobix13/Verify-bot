from .chain import assets_by_owner, token_balance
from .config import TOKEN_PROGRAM, TOKEN_2022
from .network import Rpc, ProviderError
from .platform import Platform


async def probe_sources(bot,guild,cfg):
    """Read-only checks of real configured providers; never substitutes fixtures for production."""
    checks=[]
    wallets=await bot.db.query("SELECT identity FROM links WHERE guild=? AND kind='wallet' AND active=1 LIMIT 1",(guild,))
    accounts=await bot.db.query("SELECT identity FROM links WHERE guild=? AND kind='account' AND active=1 LIMIT 1",(guild,))
    wallet=wallets[0]['identity'] if wallets else '11111111111111111111111111111111'
    async def record(name,action):
        try:
            await action(); checks.append({'source':name,'ok':True,'detail':'Read-only provider check passed.'})
        except Exception as exc:
            checks.append({'source':name,'ok':False,'detail':bot.reporter.redact(exc)})
    if cfg['balance_mode']=='rpc' or cfg['deposit_mode']=='rpc' or cfg['chain_webhook']:
        async def rpc_check():
            rpc=Rpc(bot.http,bot.env.get('RPC_URL',''),bot.env.get('RPC_FALLBACK_URL',''))
            if await rpc.call('getHealth',[])!='ok': raise ProviderError('RPC is not healthy.')
            if cfg['balance_mode']=='rpc':
                info=await rpc.call('getAccountInfo',[cfg['mint'],{'encoding':'jsonParsed','commitment':'finalized'}])
                account=info.get('value') if isinstance(info,dict) else None
                if not account or account.get('owner') not in (TOKEN_PROGRAM,TOKEN_2022) or account.get('data',{}).get('parsed',{}).get('type')!='mint':
                    raise ProviderError('Configured $CARS address is not a parsed SPL mint.')
                await token_balance(rpc,wallet,cfg['mint'])
            if cfg['deposit_mode']=='rpc':
                await rpc.call('getSignaturesForAddress',[wallet,{'limit':1,'commitment':'finalized'}])
        await record('Finalized Solana RPC',rpc_check)
    if cfg['asset_mode']=='das':
        async def das_check():
            assets=await assets_by_owner(Rpc(bot.http,bot.env.get('DAS_URL','')),wallet,cfg['collections'])
            traits={r['selector']['attribute'] for r in cfg['rules'] if r['enabled'] and r['kind']=='asset_count' and 'attribute' in r['selector']}
            if traits and not assets: raise ProviderError('Connect a test wallet holding an approved asset to verify the required traits, or use exact asset IDs/collections.')
            for trait in traits:
                if any(trait not in a['attributes'] for a in assets): raise ProviderError('DAS omitted required trait '+trait+'. Use exact asset IDs, a verified collection or the team backend trait snapshot.')
        await record('DAS asset inventory',das_check)
    if cfg['platform_points'] or 'platform' in (cfg['balance_mode'],cfg['asset_mode'],cfg['deposit_mode']):
        if not accounts:
            checks.append({'source':'Rip Cars backend bridge','ok':False,'detail':'Connect a test Rip Cars account first; completeness and credentials cannot be checked without its authenticated snapshot.'})
        else:
            async def platform_check():
                bridge=Platform(bot.http,bot.env.get('PLATFORM_API_URL',''),bot.env.get('PLATFORM_API_KEY',''))
                async for page in bridge.pages(accounts[0]['identity'],cfg['snapshot_max_age']):
                    for enabled,key in ((cfg['platform_points'],'platform_points'),(cfg['balance_mode']=='platform','token_balance'),(cfg['asset_mode']=='platform','assets'),(cfg['deposit_mode']=='platform','deposits')):
                        if enabled and key not in page: raise ProviderError('Backend omitted requested field '+key+'.')
            await record('Rip Cars backend bridge',platform_check)
    if cfg['wallet_linking']:
        async def site_check():
            result=await bot.http.request('GET',bot.linking.origin+'/healthz')
            if not isinstance(result,dict) or result.get('ok') is not True or result.get('service')!='ripcars-verifier':
                raise ProviderError('Public HTTPS connection site is not serving this healthy verifier.')
        await record('Public HTTPS connection site',site_check)
    return checks
