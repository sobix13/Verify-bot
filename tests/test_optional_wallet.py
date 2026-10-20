import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiohttp
import discord
from aiohttp.test_utils import TestClient, TestServer

import main as entry
from ripcars_verifier.auth import Linking
from ripcars_verifier.bot import Verifier
from ripcars_verifier.config import defaults
from ripcars_verifier.engine import Engine, readiness
from ripcars_verifier.web import build_app
from scripts.preflight import validate_env
from .helpers import DBCase, FakeHttp


class OptionalWalletTests(DBCase):
    def env(self):
        return {'DISCORD_TOKEN':'x'*60,'PUBLIC_URL':'','DATABASE_PATH':str(self.root/'db.sqlite3'),
                'COORDINATION_PATH':str(self.root/'coord.sqlite3'),'BACKUP_PATH':str(self.root/'backups'),
                'BACKUP_PASSWORD':'test-backup-password-with-20-characters'}

    async def test_install_preflight_accepts_no_wallet_site_but_rejects_bad_supplied_url(self):
        env=self.env()
        self.assertEqual(validate_env(env),[])
        env['PUBLIC_URL']='http://127.0.0.1'
        self.assertTrue(any('PUBLIC_URL' in error for error in validate_env(env)))

    async def test_wallet_connection_is_opt_in_for_new_servers(self):
        cfg,_=await self.store.settings(1)
        self.assertFalse(cfg['wallet_linking'])
        self.assertFalse(cfg['enabled'])
        await self.settings(wallet_linking=True)
        self.assertTrue((await self.store.settings(1))[0]['wallet_linking'])

    async def test_wallet_site_required_only_when_wallet_method_selected(self):
        cfg=defaults()
        cfg.update(member_role=2,verification_channel=3,public_message=4,log_channel=5,
                   account_linking=True,balance_mode='platform',platform_connect_url='https://app.example.com/discord')
        env={'PLATFORM_API_URL':'https://api.example.com','PLATFORM_API_KEY':'k'*32,'PLATFORM_WEBHOOK_SECRET':'s'*40}
        self.assertEqual(readiness(cfg,env),[])
        cfg['wallet_linking']=True
        self.assertTrue(any('PUBLIC_URL' in error for error in readiness(cfg,env)))

    async def test_no_site_cannot_issue_wallet_session(self):
        link=Linking(self.store,'')
        with self.assertRaisesRegex(ValueError,'HTTPS'):
            await link.create(1,10)
        self.assertEqual(await self.store.query('SELECT * FROM sessions'),[])

    async def test_signed_account_link_and_platform_token_balance_without_wallet(self):
        link=Linking(self.store,'')
        secret='s'*40
        await self.settings(account_linking=True,balance_mode='platform')
        app=build_app(self.store,link,{'PLATFORM_WEBHOOK_SECRET':secret})
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get('/link')).status,503)
            token=await link.create(1,10,'account')
            payload={'session_token':token,'account_id':'account-10'}
            self.assertEqual((await client.post('/api/platform/link',json=payload)).status,400)
            body=json.dumps(payload).encode()
            stamp=str(int(time.time()))
            signature=hmac.new(secret.encode(),stamp.encode()+b'.account-event.'+body,hashlib.sha256).hexdigest()
            response=await client.post('/api/platform/link',data=body,headers={
                'Content-Type':'application/json','X-Ripcars-Timestamp':stamp,
                'X-Ripcars-Event-Id':'account-event','X-Ripcars-Signature':signature})
            self.assertEqual(response.status,200,await response.text())
        provider=FakeHttp(lambda *args:{'schema_version':1,'account_id':'account-10','complete':True,
            'as_of':time.time(),'token_balance':'1500','next_cursor':None})
        cfg,_=await self.store.settings(1)
        snapshot=await Engine(self.store,provider,{'PLATFORM_API_URL':'https://api.example.com','PLATFORM_API_KEY':'k'*32}).snapshot(1,10,cfg)
        self.assertEqual(snapshot['wallets'],[])
        self.assertEqual(snapshot['token_balance'],'1500')
        self.assertTrue(snapshot['complete']['token_balance'])

    async def test_main_entry_starts_without_wallet_origin(self):
        with patch.dict(os.environ,self.env(),clear=True), patch.object(Verifier,'start',new_callable=AsyncMock) as start:
            await entry.main()
            start.assert_awaited_once_with('x'*60)


class VerifierLoginTests(IsolatedAsyncioTestCase):
    async def test_real_sdk_login_and_local_listener_without_wallet_site(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            env={'WEB_HOST':'127.0.0.1','WEB_PORT':'0','DATABASE_PATH':str(root/'db.sqlite3'),
                 'COORDINATION_PATH':str(root/'shared.sqlite3'),'BACKUP_PATH':str(root/'backups')}
            async with Verifier(env) as bot:
                bot.tree.sync=AsyncMock(return_value=[])
                bot.application_info=AsyncMock(return_value=SimpleNamespace(id=900,interactions_endpoint_url=None,flags=discord.ApplicationFlags._from_value(0)))
                with patch.object(discord.http.HTTPClient,'static_login',new_callable=AsyncMock,return_value={
                        'id':'900','username':'Verifier','discriminator':'0','avatar':None,'bot':True}) as login:
                    await bot.login('test-only-token')
                    login.assert_awaited_once_with('test-only-token')
                self.assertIs(bot.http,bot._connection.http)
                self.assertIsNot(bot.engine.http,bot.http)
                address=bot.runner.addresses[0]
                async with aiohttp.ClientSession() as client:
                    self.assertEqual((await client.get(f'http://127.0.0.1:{address[1]}/link')).status,503)
                    health=await client.get(f'http://127.0.0.1:{address[1]}/healthz')
                    self.assertIn('database',await health.json())
