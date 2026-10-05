import copy
import time
import unittest
from unittest.mock import patch

from ripcars_verifier.chain import assets_by_owner, scan_deposits, token_balance, transfers
from ripcars_verifier.config import MAINNET_GENESIS, TOKEN_2022, TOKEN_PROGRAM, USDC, canonical, defaults
from ripcars_verifier.engine import Engine, readiness, role_plan
from ripcars_verifier.network import ProviderError, Rpc
from ripcars_verifier.platform import Platform, rip_event, validate_snapshot
from ripcars_verifier.storage import Conflict
from .helpers import DBCase, COLLECTION, PROGRAM, TREASURY, WALLET, FakeHttp, FakeRpc, asset, rule, source, transaction


class TransferTests(unittest.TestCase):
    def count(self,tx): return transfers(tx,WALLET,[source()],'sig1')
    def test_exact_finalized_spl_deposit(self):
        results=self.count(transaction()); self.assertEqual(results[0]['usd_micros'],12345678); self.assertEqual(results[0]['event_id'],'solana:sig1:1:root')
    def test_inner_cpi_deposit(self): self.assertEqual(self.count(transaction(inner=True))[0]['event_id'],'solana:sig1:0:0')
    def test_failed_transaction_never_credits(self):
        tx=transaction(); tx['meta']['err']={'InstructionError':[0,'failed']}; self.assertEqual(self.count(tx),[])
    def test_signature_mismatch_fails(self):
        with self.assertRaises(ProviderError): self.count(transaction('another'))
    def test_missing_metadata_fails(self):
        with self.assertRaises(ProviderError): self.count({})
    def test_program_required(self):
        tx=transaction(); tx['transaction']['message']['instructions'].pop(0); self.assertEqual(self.count(tx),[])
    def test_wrong_destination_never_credits(self):
        tx=transaction(); tx['meta']['postTokenBalances'][0]['owner']=PROGRAM; self.assertEqual(self.count(tx),[])
    def test_self_transfer_never_credits(self):
        tx=transaction(); tx['meta']['postTokenBalances'][0]['owner']=WALLET; self.assertEqual(self.count(tx),[])
    def test_other_wallet_never_credits(self):
        tx=transaction(); tx['meta']['preTokenBalances'][0]['owner']=PROGRAM; self.assertEqual(self.count(tx),[])
    def test_mint_and_decimals_must_match(self):
        for field,value in (('mint',PROGRAM),('owner',WALLET)):
            tx=transaction(); tx['meta']['postTokenBalances'][0][field]=value
            with self.subTest(field=field): self.assertEqual(self.count(tx),[])
        tx=transaction(); tx['meta']['postTokenBalances'][0]['uiTokenAmount']['decimals']=9; self.assertEqual(self.count(tx),[])
    def test_unchecked_transfer_uses_balance_metadata(self):
        tx=transaction(); ins=tx['transaction']['message']['instructions'][1]['parsed']; ins['type']='transfer'; ins['info']['amount']='2000000'; self.assertEqual(self.count(tx)[0]['usd_micros'],2000000)
    def test_token2022_fee_transfer_requires_receipts(self):
        tx=transaction(); tx['transaction']['message']['instructions'][1]['programId']=TOKEN_2022; self.assertEqual(self.count(tx),[])
    def test_overlapping_sources_count_once(self):
        second=source(); second['id']='second'; self.assertEqual(len(transfers(transaction(),WALLET,[source(),second],'sig1')),1)
    def test_no_native_sol_guessing(self):
        tx=transaction(); tx['transaction']['message']['instructions'][1]={'programId':'11111111111111111111111111111111','parsed':{'type':'transfer','info':{'source':WALLET,'destination':TREASURY,'lamports':1000000000}}}; self.assertEqual(self.count(tx),[])
    def test_zero_and_invalid_amounts(self):
        for value in ('0','-1','1.0','nan',True):
            with self.subTest(value=value): self.assertEqual(self.count(transaction(amount=value)),[])
    def test_fractional_micro_usd_rounds_down_without_blocking(self):
        spec=source(); spec['mints'][USDC]['usd_per_token']='0.5'
        self.assertEqual(transfers(transaction(amount='1'),WALLET,[spec],'sig1'),[])
        self.assertEqual(transfers(transaction(amount='3'),WALLET,[spec],'sig1')[0]['usd_micros'],1)


class ChainReadTests(DBCase):
    async def test_token_balances_sum_raw_units(self):
        account=lambda amount:{'account':{'owner':TOKEN_PROGRAM,'data':{'parsed':{'info':{'owner':WALLET,'mint':USDC,'tokenAmount':{'amount':amount,'decimals':6}}}}}}
        rpc=FakeRpc({'getTokenAccountsByOwner':{'value':[account('1234567'),account('2000000')]}})
        self.assertEqual(await token_balance(rpc,WALLET,USDC),'3.234567')
    async def test_missing_token_data_raises_instead_of_zero(self):
        with self.assertRaises(ProviderError): await token_balance(FakeRpc({'getTokenAccountsByOwner':{}}),WALLET,USDC)
    async def test_wrong_token_owner_fails(self):
        rpc=FakeRpc({'getTokenAccountsByOwner':{'value':[{'account':{'owner':PROGRAM}}]}})
        with self.assertRaises(ProviderError): await token_balance(rpc,WALLET,USDC)
    async def test_asset_owner_collection_and_burn_filters(self):
        batch=[asset('yes'),asset('wrong_owner',ownership={'owner':PROGRAM}),asset('burnt',burnt=True),asset('wrong_collection',grouping=[{'group_key':'collection','group_value':PROGRAM}]),asset('unverified',grouping=[{'group_key':'collection','group_value':COLLECTION,'verified':False}]),asset('fungible',interface='FungibleToken')]
        found=await assets_by_owner(FakeRpc({'getAssetsByOwner':{'items':batch}}),WALLET,[COLLECTION]); self.assertEqual([a['id'] for a in found],['yes'])
    async def test_asset_pagination_and_deduplication(self):
        rpc=FakeRpc({'getAssetsByOwner':lambda p:{'items':[asset(str(n)) for n in range(100)] if p['page']==1 else [asset('99'),asset('100')]}})
        found=await assets_by_owner(rpc,WALLET,[COLLECTION]); self.assertEqual(len(found),101)
    async def test_repeated_asset_page_fails(self):
        rpc=FakeRpc({'getAssetsByOwner':{'items':[asset(str(n)) for n in range(100)]}})
        with self.assertRaises(ProviderError): await assets_by_owner(rpc,WALLET,[COLLECTION])
    async def test_deposit_scan_resumes_and_deduplicates(self):
        cfg=defaults(); cfg.update(sources=[source()],scan_transactions=1)
        rows=[{'signature':s,'err':None,'blockTime':1700000000,'confirmationStatus':'finalized'} for s in ('sig3','sig2','sig1')]
        def history(p):
            before=p[1].get('before'); remaining=rows[rows.index(next(r for r in rows if r['signature']==before))+1:] if before else rows
            until=p[1].get('until'); return [r for r in remaining if r['signature']!=until] if until else remaining
        rpc=FakeRpc({'getSignaturesForAddress':history,'getTransaction':lambda p:transaction(p[0],amount='1000000')})
        self.assertFalse(await scan_deposits(self.store,rpc,1,10,WALLET,cfg))
        self.assertFalse(await scan_deposits(self.store,rpc,1,10,WALLET,cfg))
        self.assertFalse(await scan_deposits(self.store,rpc,1,10,WALLET,cfg))
        self.assertTrue(await scan_deposits(self.store,rpc,1,10,WALLET,cfg)); self.assertEqual(await self.store.total(1,10),3000000)
        await self.store.execute('DELETE FROM checkpoints');
        for _ in range(4): await scan_deposits(self.store,rpc,1,10,WALLET,cfg)
        self.assertEqual(await self.store.total(1,10),3000000)
    async def test_missing_archive_tx_does_not_advance_cursor(self):
        cfg=defaults(); cfg['sources']=[source()]
        rpc=FakeRpc({'getSignaturesForAddress':[{'signature':'old','err':None,'blockTime':1,'confirmationStatus':'finalized'}],'getTransaction':None})
        with self.assertRaises(ProviderError): await scan_deposits(self.store,rpc,1,10,WALLET,cfg)
        self.assertEqual(await self.store.query('SELECT * FROM checkpoints'),[])
    async def test_nonfinalized_history_fails(self):
        rpc=FakeRpc({'getSignaturesForAddress':[{'signature':'s','confirmationStatus':'confirmed'}]})
        with self.assertRaises(ProviderError): await scan_deposits(self.store,rpc,1,10,WALLET,defaults())
    async def test_history_cutoff(self):
        cfg=defaults(); cfg['history_start']=100
        rpc=FakeRpc({'getSignaturesForAddress':[{'signature':'old','confirmationStatus':'finalized','blockTime':99}]})
        self.assertTrue(await scan_deposits(self.store,rpc,1,10,WALLET,cfg)); self.assertEqual(await self.store.total(1,10),0)
    async def test_failed_signature_never_fetches_transaction(self):
        cfg=defaults()
        rpc=FakeRpc({'getSignaturesForAddress':lambda p:[] if p[1].get('before') else [{'signature':'bad','err':'failed','confirmationStatus':'finalized','blockTime':1}]})
        self.assertTrue(await scan_deposits(self.store,rpc,1,10,WALLET,cfg)); self.assertFalse(any(c[0]=='getTransaction' for c in rpc.calls))


class ProviderTests(DBCase):
    async def test_mainnet_identity_checked_before_data(self):
        http=FakeHttp(lambda m,u,p,h:{'jsonrpc':'2.0','id':'ripcars-verifier','result':MAINNET_GENESIS if p['method']=='getGenesisHash' else 'ok'})
        rpc=Rpc(http,'https://rpc.example.com'); self.assertEqual(await rpc.call('getHealth',[]),'ok'); self.assertEqual(http.calls[0][2]['method'],'getGenesisHash')
    async def test_wrong_cluster_is_rejected(self):
        http=FakeHttp(lambda m,u,p,h:{'jsonrpc':'2.0','id':'ripcars-verifier','result':'devnet'})
        with self.assertRaises(ProviderError): await Rpc(http,'https://rpc.example.com').call('getHealth',[])
        self.assertEqual(len(http.calls),1)
    async def test_rpc_fallback_verifies_both_endpoints(self):
        def respond(m,u,p,h):
            if 'primary' in u: return ProviderError('down')
            return {'jsonrpc':'2.0','id':'ripcars-verifier','result':MAINNET_GENESIS if p['method']=='getGenesisHash' else 'ok'}
        http=FakeHttp(respond); self.assertEqual(await Rpc(http,'https://primary.example.com','https://secondary.example.com').call('getHealth',[]),'ok')
    async def test_rpc_error_does_not_return_empty_result(self):
        http=FakeHttp(lambda m,u,p,h:{'jsonrpc':'2.0','id':'ripcars-verifier','result':MAINNET_GENESIS} if p['method']=='getGenesisHash' else {'jsonrpc':'2.0','id':'ripcars-verifier','error':{'code':-1}})
        with self.assertRaises(ProviderError): await Rpc(http,'https://rpc.example.com').call('getHealth',[])


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    def page(self): return {'schema_version':1,'account_id':'a','complete':True,'as_of':time.time(),'platform_points':'5','token_balance':'10','assets':[],'deposits':[],'next_cursor':None}
    def test_stale_and_incomplete_snapshots(self):
        for changes in ({'as_of':1},{'complete':False},{'account_id':'b'},{'schema_version':2},{'token_balance':0.1}):
            data=self.page(); data.update(changes)
            with self.subTest(changes=changes),self.assertRaises((ValueError,ProviderError)): validate_snapshot(data,'a',3600)
    def test_only_settled_actual_deposits(self):
        for changes in ({'kind':'withdrawal'},{'status':'pending'},{'usd_amount':'NaN'}):
            data=self.page(); deposit={'id':'d','kind':'deposit','status':'settled','usd_amount':'1'}; deposit.update(changes); data['deposits']=[deposit]
            with self.subTest(changes=changes),self.assertRaises((ValueError,ProviderError)): validate_snapshot(data,'a',3600)
    async def test_snapshot_changes_during_pagination_fail(self):
        page=self.page(); page['next_cursor']='c'; nextpage=copy.deepcopy(page); nextpage.update(as_of=page['as_of']+1,next_cursor=None)
        http=FakeHttp(lambda m,u,p,h:nextpage if '?cursor=' in u else page)
        with self.assertRaises(ProviderError): [p async for p in Platform(http,'https://bridge.example.com','k'*32).pages('a',3600)]
    async def test_platform_cursor_loop_fails(self):
        page=self.page(); page['next_cursor']='loop'
        with self.assertRaises(ProviderError): [p async for p in Platform(FakeHttp(lambda *a:page),'https://bridge.example.com','k'*32).pages('a',3600)]
    def test_rip_payload_no_arbitrary_forwarding(self):
        data={'schema_version':1,'type':'rip.opened','id':'r','guild_id':1,'car_name':'Roxy','asset_id':'a','occurred_at':time.time(),'role_id':999,'amount':1000}
        self.assertNotIn('role_id',rip_event(data)); self.assertNotIn('amount',rip_event(data))
    def test_rip_image_rejects_internal_url(self):
        data={'schema_version':1,'type':'rip.opened','id':'r','guild_id':1,'car_name':'Roxy','asset_id':'a','occurred_at':time.time(),'image_url':'https://127.0.0.1/image'}
        with self.assertRaises(ValueError): rip_event(data)
    def test_string_snowflake_is_exact(self):
        data={'schema_version':1,'type':'rip.opened','id':'r','guild_id':'123456789012345678','car_name':'Roxy','asset_id':'a','occurred_at':time.time()}
        self.assertEqual(rip_event(data)['guild_id'],123456789012345678)


class EngineTests(DBCase):
    async def test_missing_connections_known_zero(self):
        await self.settings(enabled=True,balance_mode='rpc',mint=USDC)
        snapshot,_,_=await Engine(self.store,None,{'RPC_URL':'https://rpc.example.com'}).refresh(1,10)
        self.assertEqual(snapshot['token_balance'],'0'); self.assertTrue(snapshot['complete']['token_balance'])
    async def test_provider_failure_keeps_last_snapshot(self):
        await self.settings(enabled=True,balance_mode='rpc',mint=USDC); await self.wallet()
        await self.store.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(1,10,canonical({'token_balance':'99'}),time.time()))
        engine=Engine(self.store,None,{'RPC_URL':'https://rpc.example.com'})
        with patch('ripcars_verifier.engine.token_balance',side_effect=ProviderError('down')):
            with self.assertRaises(ProviderError): await engine.refresh(1,10)
        self.assertEqual((await engine.status(1,10))['token_balance'],'99')
    async def test_settings_change_during_snapshot_rejects_write(self):
        await self.settings(enabled=True); engine=Engine(self.store,None,{})
        async def changed(g,u,cfg):
            current,rev=await self.store.settings(1); current['brand']='changed'; await self.store.save_settings(1,current,rev,7); return {}
        with patch.object(engine,'snapshot',side_effect=changed):
            with self.assertRaises(Conflict): await engine.refresh(1,10)
        self.assertIsNone(await engine.status(1,10))
    async def test_platform_receipts_credit_once(self):
        await self.settings(enabled=True,deposit_mode='platform',platform_points=True)
        await self.store.execute("INSERT INTO links VALUES(1,10,'account','a',1,?)",(time.time(),))
        page={'schema_version':1,'account_id':'a','complete':True,'as_of':time.time(),'platform_points':'25','deposits':[{'id':'d1','kind':'deposit','status':'settled','usd_amount':'12.5'}],'next_cursor':None}
        engine=Engine(self.store,FakeHttp(lambda *a:page),{'PLATFORM_API_URL':'https://bridge.example.com','PLATFORM_API_KEY':'k'*32})
        for _ in range(2): data,_,_=await engine.refresh(1,10)
        self.assertEqual(data['deposit_points'],'12.5'); self.assertEqual(data['platform_points'],'25')
    async def test_paused_engine_does_no_work(self):
        with self.assertRaises(ValueError): await Engine(self.store,None,{}).refresh(1,10)
    def test_role_removal_only_for_owned_grants(self):
        cfg=defaults(); cfg['rules']=[rule()]; snapshot={'deposit_points':'0','complete':{'deposit_points':True}}
        self.assertEqual(role_plan(cfg,snapshot,{20},set())[1],[]); self.assertEqual(role_plan(cfg,snapshot,{20},{20})[1],[20])
    def test_unknown_or_partial_metric_preserves_role(self):
        cfg=defaults(); cfg['rules']=[rule()]; result=role_plan(cfg,{'complete':{'deposit_points':False}},{20},{20}); self.assertEqual(result,([],[],[20]))
    def test_threshold_boundary(self):
        cfg=defaults(); cfg['rules']=[rule()]; self.assertEqual(role_plan(cfg,{'deposit_points':'10','complete':{'deposit_points':True}},set(),set())[0],[20])
    def test_disabled_rule_removes_owned_role_only(self):
        cfg=defaults(); cfg['rules']=[rule(enabled=False)]; self.assertEqual(role_plan(cfg,{}, {20},{20})[1],[20])
    def test_activation_blockers_are_actionable(self):
        blockers=readiness(defaults(),{}); self.assertTrue(any('PUBLIC_URL' in b for b in blockers)); self.assertTrue(any('Publish' in b for b in blockers))
