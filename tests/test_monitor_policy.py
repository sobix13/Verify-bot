import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from ripcars_verifier.bot import Verifier
from ripcars_verifier.config import defaults,wallet_kinds
from ripcars_verifier.engine import Engine
from ripcars_verifier.storage import Store
from ripcars_verifier.network import RateLimited
from tests.helpers import DBCase,FakeHttp,fake_guild,rule,WALLET


class MonitorPolicyTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot=Verifier({'DATABASE_PATH':str(self.store.path),'COORDINATION_PATH':str(self.root/'shared.sqlite3'),'BACKUP_PATH':str(self.root/'backups')})
        self.bot._connection.user=SimpleNamespace(id=900)
        self.bot.db=self.store;await self.bot.registry.open()
        self.guild,self.member=fake_guild();self.bot.get_guild=lambda gid:self.guild if gid==1 else None
        self.cfg=await self.settings(enabled=True,member_role=2,verification_channel=30,public_message=500,log_channel=32,balance_mode='rpc',mint=WALLET,rules=[rule(kind='token_balance')])
        self.rev=(await self.store.settings(1))[1]
        async with self.bot.registry.lease(1):await self.bot.registry.bind_role(1,20,900,'tier_10',{'name':'Deposit 10','permissions':0})
        await self.wallet()
    async def asyncTearDown(self):
        await self.bot.close();await super().asyncTearDown()
    def snapshot(self,value):return {'wallets':[WALLET],'token_balance':str(value),'complete':{'token_balance':True},'_checked_kinds':['token_balance'],'checked_at':time.time()}
    async def cycle(self,*,request=True,error=None,value=0):
        self.bot.wait_until_ready=AsyncMock()
        self.bot.engine.refresh=AsyncMock(side_effect=error,return_value=(self.snapshot(value),self.cfg,self.rev))
        if request:await self.store.enqueue(1,10,scope='wallet')
        else:await self.store.execute('UPDATE jobs SET next_run=0')
        with patch('ripcars_verifier.bot.asyncio',SimpleNamespace(sleep=AsyncMock(side_effect=asyncio.CancelledError))):
            with self.assertRaises(asyncio.CancelledError):await self.bot.worker()
    async def test_rejected_first_check_does_not_repeat(self):
        await self.cycle(value=0)
        self.assertEqual(await self.store.query('SELECT * FROM jobs'),[])
        self.assertEqual(len(await self.store.links(1,10)),1)
    async def test_qualified_member_runs_twelve_hour_cycle_and_leaves_after_last_role(self):
        await self.cycle(value=20)
        jobs=await self.store.query('SELECT * FROM jobs')
        self.assertEqual(len(jobs),1);self.assertGreater(jobs[0]['next_run'],time.time()+43000)
        self.assertIn(20,{r.id for r in self.member.roles})
        await self.cycle(request=False,value=0)
        self.assertNotIn(20,{r.id for r in self.member.roles})
        self.assertEqual(await self.store.query('SELECT * FROM jobs'),[])
    async def test_provider_failure_preserves_qualified_monitoring_and_role(self):
        await self.cycle(value=20)
        with self.assertLogs('ripcars.verifier',level='ERROR'):
            await self.cycle(request=False,error=RuntimeError('Provider unavailable'))
        self.assertIn(20,{r.id for r in self.member.roles})
        self.assertEqual(len(await self.store.query('SELECT * FROM jobs')),1)
    async def test_webhook_does_not_reenroll_inactive_member(self):
        self.assertFalse(await self.store.enqueue(1,10,scope='hint'))
        await self.cycle(value=20)
        self.assertTrue(await self.store.enqueue(1,10,scope='hint'))
        request=(await self.store.query('SELECT * FROM job_requests'))[0]
        self.assertNotIn('platform_points',request['kinds'])
    async def test_manual_role_removal_stops_periodic_network_check(self):
        await self.cycle(value=20)
        self.member.roles=[r for r in self.member.roles if r.id!=20]
        await self.cycle(request=False,value=20)
        self.bot.engine.refresh.assert_not_awaited()
        self.assertEqual(await self.store.query('SELECT * FROM jobs'),[])
    async def test_request_arriving_during_check_is_not_lost(self):
        await self.store.enqueue(1,10,scope='wallet')
        version=(await self.store.query('SELECT version FROM job_requests'))[0]['version']
        await self.store.enqueue(1,10,scope='account')
        self.assertFalse(await self.store.finish_check(1,10,version,False,0))
        self.assertEqual(len(await self.store.query('SELECT * FROM jobs')),1)
    async def test_wallet_refresh_does_not_call_platform_or_discard_cached_points(self):
        self.cfg=await self.settings(platform_points=True)
        await self.store.execute("INSERT INTO snapshots VALUES(1,10,?,?)",('{"platform_points":"100","complete":{"platform_points":true}}',time.time()))
        http=FakeHttp(lambda *args:(_ for _ in ()).throw(AssertionError('Unexpected platform request')))
        with patch('ripcars_verifier.engine.token_balance',AsyncMock(return_value='20')):
            data,_,_=await Engine(self.store,http,{'RPC_URL':'https://rpc.example.com'}).refresh(1,10,wallet_kinds(self.cfg))
        self.assertEqual(data['platform_points'],'100');self.assertEqual(data['_checked_kinds'],['token_balance']);self.assertEqual(http.calls,[])
    async def test_platform_refresh_does_not_call_rpc(self):
        self.cfg=await self.settings(platform_points=True)
        await self.store.execute("INSERT INTO links VALUES(1,10,'account','account-a',1,?)",(time.time(),))
        page={'schema_version':1,'account_id':'account-a','complete':True,'as_of':time.time(),'platform_points':'100'}
        http=FakeHttp(lambda *args:page)
        with patch('ripcars_verifier.engine.token_balance',AsyncMock(side_effect=AssertionError('Unexpected RPC'))) as token:
            data,_,_=await Engine(self.store,http,{'PLATFORM_API_URL':'https://platform.example.com','PLATFORM_API_KEY':'k'*32}).refresh(1,10,{'platform_points'})
        token.assert_not_awaited();self.assertEqual(data['platform_points'],'100');self.assertEqual(len(http.calls),1)
    async def test_platform_batch_deduplicates_resumes_and_skips_done_accounts(self):
        await self.settings(platform_points=True)
        await self.store.execute("INSERT INTO links VALUES(1,10,'account','account-a',1,?)",(time.time(),))
        bid=await self.store.start_point_batch(1,7)
        self.assertEqual(await self.store.start_point_batch(1,7),bid)
        reopened=Store(self.store.path);await reopened.open()
        self.assertEqual((await reopened.point_progress(1))['counts'],{'pending':1})
        cfg,rev=await self.store.settings(1)
        self.bot.engine.refresh=AsyncMock(return_value=({'platform_points':'100','complete':{'platform_points':True},'_checked_kinds':['platform_points']},cfg,rev))
        with patch('ripcars_verifier.bot.asyncio',SimpleNamespace(sleep=AsyncMock())):
            await self.bot.point_batch_tick();await self.bot.point_batch_tick()
        self.bot.engine.refresh.assert_awaited_once()
        self.assertEqual((await self.store.point_progress(1))['counts'],{'done':1})
    async def test_empty_defaults_use_twelve_hours(self):
        self.assertEqual(defaults()['sync_seconds'],43200)
    async def test_upgrade_keeps_existing_validated_wallet_monitors(self):
        await self.store.execute("INSERT INTO role_grants VALUES(1,10,20,'active')")
        await self.store.execute("DELETE FROM meta WHERE key='role_monitor_policy'")
        reopened=Store(self.store.path);await reopened.open()
        self.assertEqual((await reopened.query('SELECT active FROM monitoring WHERE guild=1 AND user=10'))[0]['active'],1)
        self.assertEqual((await reopened.settings(1))[0]['sync_seconds'],43200)
    async def test_platform_limit_pauses_batch_without_reading_other_accounts(self):
        await self.settings(platform_points=True)
        for uid,identity in [(10,'account-a'),(11,'account-b')]:
            await self.store.execute("INSERT INTO links VALUES(1,?,'account',?,1,?)",(uid,identity,time.time()))
        bid=await self.store.start_point_batch(1,7)
        self.bot.engine.refresh=AsyncMock(side_effect=RateLimited('Limit reached'))
        with self.assertLogs('ripcars.verifier',level='ERROR'):await self.bot.point_batch_tick()
        progress=await self.store.point_progress(1)
        self.assertEqual(progress['status'],'paused');self.assertEqual(progress['counts'],{'failed':1,'pending':1})
        self.assertEqual(await self.store.start_point_batch(1,7),bid)
        await self.bot.point_batch_tick();self.bot.engine.refresh.assert_awaited_once()
    async def test_disconnection_during_provider_request_cannot_save_old_identity(self):
        engine=Engine(self.store,FakeHttp(lambda *args:None))
        async def disconnect(*args):
            await self.store.execute("UPDATE links SET active=0 WHERE guild=1 AND user=10 AND kind='wallet'")
            return self.snapshot(20)
        with patch.object(engine,'snapshot',side_effect=disconnect):
            with self.assertRaisesRegex(ValueError,'Connections changed'):await engine.refresh(1,10,{'token_balance'})
        self.assertIsNone(await engine.status(1,10))
    async def test_upgrade_preserves_pending_first_verification(self):
        await self.store.execute('INSERT INTO jobs(guild,user,next_run) VALUES(1,10,0)')
        await self.store.execute("DELETE FROM meta WHERE key='role_monitor_policy'")
        reopened=Store(self.store.path);await reopened.open()
        request=(await reopened.query('SELECT kinds FROM job_requests WHERE guild=1 AND user=10'))[0]
        self.assertIn('token_balance',request['kinds']);self.assertNotIn('platform_points',request['kinds'])
