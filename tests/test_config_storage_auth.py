import asyncio
import base64
import copy
import hashlib
import hmac
import os
import unittest
from decimal import Decimal

from ripcars_verifier.auth import Linking, check_hmac, token_hash
from ripcars_verifier.config import address, canonical, decimal, defaults, role_metric, safe_url, usd_micros, validate
from ripcars_verifier.storage import Conflict
from .helpers import DBCase, WALLET, addr, keypair, rule, source


class ConfigTests(unittest.TestCase):
    def test_defaults_valid_and_isolated(self):
        cfg=defaults(); validate(cfg); cfg['sources'].append(source()); self.assertEqual(defaults()['sources'],[])
    def test_exact_usd_and_no_float(self):
        self.assertEqual(usd_micros('12.345678'),12345678)
        for v in ('NaN','Infinity','-1',True,0.1,'1e20','0.0000001'):
            with self.subTest(value=v),self.assertRaises(ValueError): usd_micros(v)
    def test_decimal_zero_and_integer(self): self.assertEqual(decimal(0),Decimal(0)); self.assertEqual(decimal('1000'),Decimal(1000))
    def test_invalid_addresses(self):
        self.assertEqual(address(WALLET),WALLET)
        for v in ('','0'*32,'1'*31,'1'*33,None):
            with self.subTest(value=v),self.assertRaises(ValueError): address(v)
    def test_urls_reject_internal_and_credentials(self):
        for url in ('http://example.com','https://localhost','https://127.0.0.1','https://10.0.0.1','https://169.254.169.254','https://foo.internal','https://x:y@example.com','https://example.com:8080','https://example.com/#secret'):
            with self.subTest(url=url),self.assertRaises(ValueError): safe_url(url)
        self.assertEqual(safe_url('https://rpc.example.com/?api-key=fake'),'https://rpc.example.com/?api-key=fake')
    def test_origin_only(self):
        with self.assertRaises(ValueError): safe_url('https://verify.example.com/a',True)
    def test_strict_schema(self):
        cfg=defaults(); cfg['secret']='bad'
        with self.assertRaises(ValueError): validate(cfg)
    def test_settings_ranges(self):
        for key,value in (('sync_seconds',299),('color',0x1000000),('max_wallets',False),('member_role',-1),('scan_pages',21),('wallet_linking','yes')):
            cfg=defaults(); cfg[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): validate(cfg)
    def test_multiple_sources_and_overlap_allowed(self):
        cfg=defaults(); a=source(); b=copy.deepcopy(a); b['id']='other'; cfg['sources']=[a,b]; validate(cfg)
    def test_duplicate_source_rejected(self):
        cfg=defaults(); cfg['sources']=[source(),source()]
        with self.assertRaises(ValueError): validate(cfg)
    def test_invalid_mint_valuation(self):
        cfg=defaults(); s=source(); next(iter(s['mints'].values()))['usd_per_token']='0'; cfg['sources']=[s]
        with self.assertRaises(ValueError): validate(cfg)
    def test_privileged_role_names_rejected(self):
        for name in ('OG','Rippers','Moderator','Admin','Team'):
            cfg=defaults(); cfg['rules']=[rule(name=name)]
            with self.subTest(name=name),self.assertRaises(ValueError): validate(cfg)
    def test_duplicate_role_id_rejected(self):
        cfg=defaults(); cfg['rules']=[rule(),rule(key='other')]
        with self.assertRaises(ValueError): validate(cfg)
    def test_selector_validation(self):
        for selector in ({'attribute':'Model'},{'asset_ids':[1]},{'attribute':5,'value':'Roxy'},{'arbitrary':True}):
            cfg=defaults(); cfg['rules']=[rule(kind='asset_count',selector=selector)]
            with self.subTest(selector=selector),self.assertRaises(ValueError): validate(cfg)
    def test_unknown_metric_preserves_state(self): self.assertIsNone(role_metric(rule(),{'complete':{}}))
    def test_lifetime_aggregate_can_exceed_single_receipt_bound(self): self.assertEqual(role_metric(rule(),{'complete':{'deposit_points':True},'deposit_points':'10000000000000'}),10000000000000)
    def test_asset_metric_counts_distinct_filtered_ids(self):
        snapshot={'complete':{'asset_count':True},'assets':[{'id':'a','attributes':{'Model':'Roxy'}},{'id':'a','attributes':{'Model':'Roxy'}},{'id':'b','attributes':{'Model':'Gulf'}}]}
        self.assertEqual(role_metric(rule(kind='asset_count',selector={'attribute':'Model','value':'Roxy'}),snapshot),1)
    def test_missing_required_trait_does_not_mean_zero(self):
        data={'complete':{'asset_count':True},'assets':[{'id':'a','attributes':{}}]}
        self.assertIsNone(role_metric(rule(kind='asset_count',threshold='1',selector={'attribute':'Model','value':'Roxy'}),data))
    def test_known_trait_match_can_prove_threshold_despite_other_unknowns(self):
        data={'complete':{'asset_count':True},'assets':[{'id':'a','attributes':{}},{'id':'b','attributes':{'Model':'Roxy'}}]}
        self.assertEqual(role_metric(rule(kind='asset_count',threshold='1',selector={'attribute':'Model','value':'Roxy'}),data),1)
    def test_canonical_nonfinite_rejected(self):
        with self.assertRaises(ValueError): canonical({'x':float('nan')})


class StorageTests(DBCase):
    async def test_settings_compare_and_swap(self):
        cfg,rev=await self.store.settings(1); cfg['brand']='Rip Cars'; new=await self.store.save_settings(1,cfg,rev,7)
        self.assertEqual(new,2)
        with self.assertRaises(Conflict): await self.store.save_settings(1,cfg,rev,7)
    async def test_credit_deduplicates_concurrent_delivery(self):
        results=await asyncio.gather(*(self.store.credit(1,10,WALLET,'solana:a:0:root',12000000,'payments',{}) for _ in range(8)))
        self.assertEqual(sum(results),1); self.assertEqual(await self.store.total(1,10),12000000)
    async def test_credit_refuses_valuation_or_owner_change(self):
        await self.store.credit(1,10,WALLET,'solana:a:0:root',1000000,'payments',{})
        for user,amount in ((11,1000000),(10,2000000)):
            with self.assertRaises(Conflict): await self.store.credit(1,user,WALLET,'solana:a:0:root',amount,'payments',{})
    async def test_deposit_source_switch_is_blocked_after_pause(self):
        await self.settings(deposit_mode='rpc'); await self.store.credit(1,10,WALLET,'solana:a:0:root',1000000,'payments',{})
        await self.settings(deposit_mode='off')
        with self.assertRaises(Conflict): await self.settings(deposit_mode='platform')
    async def test_mixed_source_receipts_blocked(self):
        await self.store.credit(1,10,'','platform:account:1',1000000,'platform',{})
        with self.assertRaises(Conflict): await self.store.credit(1,10,WALLET,'solana:a:0:root',1000000,'payments',{})
    async def test_integer_totals_do_not_overflow_sqlite(self):
        for n in range(10): await self.store.credit(1,10,WALLET,f'solana:{n}:root',10**18,'payments',{})
        self.assertEqual(await self.store.total(1,10),10**19)
    async def test_tenant_separation(self):
        await self.store.credit(1,10,WALLET,'solana:a:root',1000000,'p',{}); self.assertEqual(await self.store.total(2,10),0)
    async def test_enqueue_coalesces_earliest_time(self):
        await self.store.enqueue(1,10,100); await self.store.enqueue(1,10,200)
        rows=await self.store.query('SELECT next_run FROM jobs'); self.assertEqual(rows,[{'next_run':100.0}])
    async def test_transaction_rolls_back(self):
        def bad(c): c.execute('INSERT INTO jobs VALUES(1,10,1,0)'); raise ValueError('abort')
        with self.assertRaises(ValueError): await self.store.run(bad)
        self.assertEqual(await self.store.query('SELECT * FROM jobs'),[])
    async def test_database_file_is_private(self): self.assertEqual(os.stat(self.store.path).st_mode&0o777,0o600)
    async def test_unsupported_schema_is_not_mutated_on_open(self):
        await self.store.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        await self.store.execute('DROP TABLE deliveries')
        with self.assertRaises(Conflict): await self.store.open()
        self.assertEqual(await self.store.query("SELECT name FROM sqlite_master WHERE name='deliveries'"),[])


class LinkingTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp(); self.linking=Linking(self.store,'https://verify.example.com'); self.key,self.wallet=keypair()
    async def sign(self,token,wallet=None,key=None,now=None):
        wallet=wallet or self.wallet; message=await self.linking.challenge(token,wallet,now)
        return base64.b64encode((key or self.key).sign(message.encode())).decode(),message
    async def test_real_signature_links_and_queues(self):
        token=await self.linking.create(1,10); sig,message=await self.sign(token)
        self.assertIn('Discord user 10',message); self.assertEqual(await self.linking.verify(token,self.wallet,sig),(1,10))
        self.assertEqual(len(await self.store.links(1,10)),1); self.assertEqual(len(await self.store.query('SELECT * FROM jobs')),1)
    async def test_session_tokens_are_hashed(self):
        token=await self.linking.create(1,10); rows=await self.store.query('SELECT token FROM sessions'); self.assertEqual(rows[0]['token'],token_hash(token)); self.assertNotIn(token,str(rows))
    async def test_signature_replay_fails(self):
        token=await self.linking.create(1,10); sig,_=await self.sign(token); await self.linking.verify(token,self.wallet,sig)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig)
    async def test_other_wallet_signature_fails(self):
        token=await self.linking.create(1,10); wrong,_=keypair(); sig,_=await self.sign(token,key=wrong)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig)
    async def test_expired_session_fails(self):
        token=await self.linking.create(1,10,now=100); sig,_=await self.sign(token,now=101)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig,now=701)
    async def test_old_challenge_fails_after_new_nonce(self):
        token=await self.linking.create(1,10); sig,_=await self.sign(token); await self.linking.challenge(token,self.wallet)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig)
    async def test_wallet_not_reassigned_after_disconnect(self):
        token=await self.linking.create(1,10); sig,_=await self.sign(token); await self.linking.verify(token,self.wallet,sig)
        await self.linking.disconnect(1,10,'wallet',self.wallet)
        other=await self.linking.create(1,11); sig,_=await self.sign(other)
        with self.assertRaises(Conflict): await self.linking.verify(other,self.wallet,sig)
    async def test_same_owner_can_reconnect(self):
        for _ in range(2):
            token=await self.linking.create(1,10); sig,_=await self.sign(token); await self.linking.verify(token,self.wallet,sig); await self.linking.disconnect(1,10,'wallet',self.wallet)
    async def test_wallet_limit(self):
        await self.settings(max_wallets=1); await DBCase.wallet(self,wallet=addr(9))
        token=await self.linking.create(1,10); sig,_=await self.sign(token)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig)
    async def test_paused_wallet_linking(self):
        token=await self.linking.create(1,10); sig,_=await self.sign(token); await self.settings(wallet_linking=False)
        with self.assertRaises(ValueError): await self.linking.verify(token,self.wallet,sig)
    async def test_account_link_requires_account_session(self):
        token=await self.linking.create(1,10)
        with self.assertRaises(ValueError): await self.linking.attest_account(token,'account1')
    async def test_only_one_account_per_user(self):
        token=await self.linking.create(1,10,'account'); await self.linking.attest_account(token,'a')
        token=await self.linking.create(1,10,'account')
        with self.assertRaises(Conflict): await self.linking.attest_account(token,'b')
    async def test_account_cannot_be_shared(self):
        token=await self.linking.create(1,10,'account'); await self.linking.attest_account(token,'a')
        token=await self.linking.create(1,11,'account')
        with self.assertRaises(Conflict): await self.linking.attest_account(token,'a')
    async def test_session_creation_rate_bound(self):
        for _ in range(5): await self.linking.create(1,10)
        with self.assertRaises(ValueError): await self.linking.create(1,10)


class HmacTests(unittest.TestCase):
    def test_real_hmac_and_tampering(self):
        secret='s'*40; body=b'{"x":1}'; value=hmac.new(secret.encode(),b'100.evt.'+body,hashlib.sha256).hexdigest()
        self.assertEqual(check_hmac(secret,'100','evt',body,value,now=101),100)
        with self.assertRaises(ValueError): check_hmac(secret,'100','evt',b'changed',value,now=101)
    def test_expiry(self):
        with self.assertRaises(ValueError): check_hmac('s'*40,'100','evt',b'','',now=401)
    def test_missing_secret(self):
        with self.assertRaises(ValueError): check_hmac('','100','evt',b'','',now=100)
