import asyncio
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from ripcars_verifier.auth import Linking
from ripcars_verifier.config import MAINNET_GENESIS, TOKEN_PROGRAM, USDC, defaults
from ripcars_verifier.operations import Reporter
from ripcars_verifier.probes import probe_sources
from scripts.build_release import build
from scripts.preflight import read_env, validate_env
from .helpers import DBCase, FakeHttp


class DeploymentTests(unittest.TestCase):
    def env(self): return {'DISCORD_TOKEN':'x'*60,'PUBLIC_URL':'https://verify.example.com','WEB_HOST':'127.0.0.1','WEB_PORT':'8092','DATABASE_PATH':'/var/lib/ripcars-verifier/verifier.sqlite3','COORDINATION_PATH':'/var/lib/ripcars-bots/coordination.sqlite3','BACKUP_PATH':'/var/lib/ripcars-verifier/backups','BACKUP_PASSWORD':'a-safe-password-with-at-least-20-characters'}
    def test_preflight_valid_minimal_optional_features_skipped(self): self.assertEqual(validate_env(self.env()),[])
    def test_preflight_rejects_placeholder_and_insecure_binding(self):
        env=self.env(); env.update(DISCORD_TOKEN='replace_me',WEB_HOST='0.0.0.0',PUBLIC_URL='http://verify.example.com'); self.assertEqual(len(validate_env(env)),3)
    def test_private_and_shared_database_cannot_be_same(self):
        env=self.env(); env['DATABASE_PATH']=env['COORDINATION_PATH']; self.assertTrue(any('different files' in e for e in validate_env(env)))
    def test_env_parser_never_runs_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'env'; target.write_text('DISCORD_TOKEN="$(touch hacked)"\nPUBLIC_URL="https://verify.example.com"\n')
            env=read_env(target); self.assertEqual(env['DISCORD_TOKEN'],'$(touch hacked)'); self.assertFalse((Path(folder)/'hacked').exists())
    def test_env_parser_rejects_unknown_and_repeated_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'env'
            for data in ('UNKNOWN=x\n','PUBLIC_URL=x\nPUBLIC_URL=y\n'):
                target.write_text(data)
                with self.assertRaises(ValueError): read_env(target)
    def test_release_builder_excludes_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'source'; root.mkdir(); (root/'VERSION').write_text('1.0.0'); (root/'main.py').write_text('pass'); (root/'.env').write_text('SECRET=yes'); (root/'data').mkdir(); (root/'data/db.sqlite3').write_bytes(b'private')
            output=Path(folder)/'out'; build(root,output)
            with zipfile.ZipFile(output/'ripcars-verifier-1.0.0.zip') as archive: self.assertEqual(set(archive.namelist()),{'ripcars-verifier-1.0.0/VERSION','ripcars-verifier-1.0.0/main.py'})
    def test_release_builder_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'src'; root.mkdir(); (root/'VERSION').write_text('1.0.0'); (root/'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): build(root,Path(folder)/'out')
    def test_installer_dry_run_has_no_system_mutations(self):
        script=Path(__file__).resolve().parents[1]/'scripts/install.sh'
        result=subprocess.run(['bash',str(script),'--dry-run'],capture_output=True,text=True); self.assertEqual(result.returncode,0,result.stderr); self.assertIn('Dry run passed',result.stdout)


class ProbeTests(DBCase):
    def bot(self,http,env=None):
        bot=SimpleNamespace(db=self.store,http=http,env=env or {},linking=Linking(self.store,'https://verify.example.com')); bot.reporter=Reporter(bot); return bot
    async def test_site_probe_success_and_failure(self):
        for healthy in (True,False):
            cfg=defaults(); result=await probe_sources(self.bot(FakeHttp(lambda *a:{'ok':healthy,'service':'ripcars-verifier'})),1,cfg); self.assertEqual(result[0]['ok'],healthy)
    async def test_platform_probe_requires_proven_account(self):
        cfg=defaults(); cfg.update(wallet_linking=False,platform_points=True)
        result=await probe_sources(self.bot(None),1,cfg); self.assertFalse(result[0]['ok']); self.assertIn('Connect a test Rip Cars account',result[0]['detail'])
    async def test_rpc_probe_checks_real_mint_type(self):
        cfg=defaults(); cfg.update(wallet_linking=False,balance_mode='rpc',mint=USDC)
        def respond(method,url,payload,headers):
            values={'getGenesisHash':MAINNET_GENESIS,'getHealth':'ok','getAccountInfo':{'value':{'owner':TOKEN_PROGRAM,'data':{'parsed':{'type':'mint'}}}},'getTokenAccountsByOwner':{'value':[]}}
            return {'jsonrpc':'2.0','id':'ripcars-verifier','result':values[payload['method']]}
        result=await probe_sources(self.bot(FakeHttp(respond),{'RPC_URL':'https://rpc.example.com'}),1,cfg); self.assertTrue(result[0]['ok'])
    async def test_probe_wrong_mint_is_a_blocker(self):
        cfg=defaults(); cfg.update(wallet_linking=False,balance_mode='rpc',mint=USDC)
        def respond(m,u,p,h): return {'jsonrpc':'2.0','id':'ripcars-verifier','result':{'getGenesisHash':MAINNET_GENESIS,'getHealth':'ok','getAccountInfo':{'value':None}}[p['method']]}
        result=await probe_sources(self.bot(FakeHttp(respond),{'RPC_URL':'https://rpc.example.com'}),1,cfg); self.assertFalse(result[0]['ok'])
    async def test_disabled_external_features_do_not_probe(self):
        cfg=defaults(); cfg['wallet_linking']=False; self.assertEqual(await probe_sources(self.bot(None),1,cfg),[])
    async def test_member_refresh_is_rate_limited(self):
        await self.store.member_refresh(1,10,100)
        with self.assertRaises(ValueError): await self.store.member_refresh(1,10,101)
        await self.store.member_refresh(1,10,161)
    async def test_concurrent_session_limit_is_atomic(self):
        link=Linking(self.store,'https://verify.example.com'); results=await asyncio.gather(*(link.create(1,10) for _ in range(12)),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,str) for r in results),5); self.assertEqual(len(await self.store.query('SELECT * FROM sessions')),5)
