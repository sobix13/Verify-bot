import base64
import hashlib
import hmac
import io
import json
import sqlite3
import tarfile
import time

from aiohttp.test_utils import TestClient, TestServer

from ripcars_verifier.auth import Linking
from ripcars_verifier.backups import create_backup, decrypt, encrypt, restore_backup
from ripcars_verifier.web import build_app
from .helpers import DBCase, WALLET, keypair


class WebTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp(); self.secret='s'*40; self.linking=Linking(self.store,'https://verify.example.com')
        self.health_ok=True
        async def health(): return {'ok':self.health_ok,'database':True,'discord':self.health_ok,'worker':self.health_ok}
        self.app=build_app(self.store,self.linking,{'PLATFORM_WEBHOOK_SECRET':self.secret,'CHAIN_WEBHOOK_SECRET':self.secret},health)
        self.client=TestClient(TestServer(self.app)); await self.client.start_server()
    async def asyncTearDown(self): await self.client.close(); await super().asyncTearDown()
    async def signed(self,path,data,event='event1',secret=None):
        body=json.dumps(data,separators=(',',':')).encode(); stamp=str(int(time.time()))
        signature=hmac.new((secret or self.secret).encode(),stamp.encode()+b'.'+event.encode()+b'.'+body,hashlib.sha256).hexdigest()
        return await self.client.post(path,data=body,headers={'Content-Type':'application/json','X-Ripcars-Timestamp':stamp,'X-Ripcars-Event-Id':event,'X-Ripcars-Signature':signature})
    def rip(self): return {'schema_version':1,'type':'rip.opened','id':'pull1','guild_id':1,'car_name':'Roxy','asset_id':'asset1','occurred_at':time.time(),'image_url':'https://images.example.com/roxy.png'}
    async def test_static_site_headers_and_brand(self):
        response=await self.client.get('/link'); self.assertEqual(response.status,200); self.assertIn('Rip Cars',await response.text()); self.assertEqual(response.headers['Referrer-Policy'],'no-referrer'); self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
    async def test_cross_origin_request_rejected(self):
        response=await self.client.post('/api/challenge',json={'token':'x'*43,'wallet':WALLET},headers={'Origin':'https://evil.example.com'}); self.assertEqual(response.status,400)
    async def test_end_to_end_real_wallet_signature_over_http(self):
        key,wallet=keypair(); token=await self.linking.create(1,10)
        response=await self.client.post('/api/challenge',json={'token':token,'wallet':wallet},headers={'Origin':self.linking.origin}); message=(await response.json())['message']
        sig=base64.b64encode(key.sign(message.encode())).decode()
        response=await self.client.post('/api/verify',json={'token':token,'wallet':wallet,'signature':sig},headers={'Origin':self.linking.origin})
        self.assertEqual(response.status,200); self.assertEqual((await response.json())['discord_id'],'10')
    async def test_unauthenticated_account_link_fails(self):
        response=await self.client.post('/api/platform/link',json={'session_token':'x'*43,'account_id':'a'}); self.assertEqual(response.status,400)
    async def test_signed_backend_account_attestation(self):
        await self.settings(account_linking=True); token=await self.linking.create(1,10,'account')
        response=await self.signed('/api/platform/link',{'session_token':token,'account_id':'a'}); self.assertEqual(response.status,200)
        response=await self.signed('/api/platform/link',{'session_token':token,'account_id':'a'}); self.assertTrue((await response.json())['duplicate']); self.assertEqual(len(await self.store.links(1,10)),1)
    async def test_account_link_failure_releases_envelope(self):
        await self.settings(account_linking=True)
        response=await self.signed('/api/platform/link',{'session_token':'x'*43,'account_id':'a'}); self.assertEqual(response.status,400); self.assertEqual(await self.store.query('SELECT * FROM webhook_ids'),[])
    async def test_rip_event_atomic_dedup(self):
        await self.settings(rip_feed=True); data=self.rip()
        for event in ('event1','event1','event2'):
            response=await self.signed('/api/webhooks/rip',data,event); self.assertEqual(response.status,200)
        self.assertEqual(len(await self.store.query('SELECT * FROM deliveries')),1)
    async def test_rip_event_changed_content_rejected(self):
        await self.settings(rip_feed=True); data=self.rip(); await self.signed('/api/webhooks/rip',data)
        data['car_name']='Other'; response=await self.signed('/api/webhooks/rip',data,'event2'); self.assertEqual(response.status,400)
    async def test_reused_envelope_cannot_enqueue_new_rip(self):
        await self.settings(rip_feed=True); data=self.rip(); await self.signed('/api/webhooks/rip',data)
        data['id']='pull2'; response=await self.signed('/api/webhooks/rip',data); self.assertEqual(response.status,400); self.assertEqual(len(await self.store.query('SELECT * FROM deliveries')),1)
    async def test_skipped_rip_feed_rejects_events(self):
        response=await self.signed('/api/webhooks/rip',self.rip()); self.assertEqual(response.status,400)
    async def test_wrong_signature_creates_no_delivery(self):
        await self.settings(rip_feed=True); response=await self.signed('/api/webhooks/rip',self.rip(),secret='wrong'*8); self.assertEqual(response.status,400); self.assertEqual(await self.store.query('SELECT * FROM deliveries'),[])
    async def test_chain_hint_only_queues_and_never_credits(self):
        await self.settings(chain_webhook=True); await self.wallet(); response=await self.signed('/api/webhooks/chain',{'guild_id':1,'wallet':WALLET,'usd_amount':'999999'})
        self.assertEqual(response.status,200); self.assertEqual(len(await self.store.query('SELECT * FROM jobs')),1); self.assertEqual(await self.store.total(1,10),0)
    async def test_helius_native_hint_only(self):
        await self.settings(chain_webhook=True); await self.wallet()
        response=await self.client.post('/api/webhooks/helius',json=[{'accountData':[{'account':WALLET}],'amount':9999}],headers={'Authorization':'Bearer '+self.secret}); self.assertEqual(response.status,200); self.assertEqual(await self.store.total(1,10),0)
    async def test_health_reflects_unhealthy_worker(self):
        self.health_ok=False
        response=await self.client.get('/healthz'); self.assertEqual(response.status,503)
    async def test_nonfinite_json_rejected(self):
        response=await self.client.post('/api/challenge',data='{"value":NaN}',headers={'Origin':self.linking.origin,'Content-Type':'application/json'}); self.assertEqual(response.status,400)
    async def test_bounded_request_body(self):
        response=await self.client.post('/api/challenge',data=b'x'*270000,headers={'Origin':self.linking.origin,'Content-Type':'application/json'}); self.assertEqual(response.status,413)


class BackupTests(DBCase):
    async def test_encrypted_backup_and_safe_restore(self):
        await self.settings(enabled=True); await self.wallet(); await self.store.credit(1,10,WALLET,'solana:a:root',1500000,'p',{})
        await self.store.execute("INSERT INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(1,'rip:a','rip','{}','pending',0,0)")
        await self.store.execute("INSERT INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(1,'rip:b','rip','{}','discarded',0,0)")
        file=await create_backup(self.store,self.root/'backups','password-with-at-least-20-chars')
        target=restore_backup(file,self.root/'restored.sqlite3','password-with-at-least-20-chars')
        with sqlite3.connect(target) as c:
            cfg=json.loads(c.execute('SELECT value FROM settings').fetchone()[0]); self.assertFalse(cfg['enabled']); self.assertEqual(c.execute('SELECT SUM(usd_micros) FROM ledger').fetchone()[0],1500000)
            self.assertEqual(dict(c.execute('SELECT event_id,state FROM deliveries')),{ 'rip:a':'review','rip:b':'discarded'})
    async def test_backup_is_not_plain_database(self):
        file=await create_backup(self.store,self.root/'backups','password-with-at-least-20-chars'); self.assertNotIn(b'SQLite format 3',file.read_bytes())
    async def test_server_backup_excludes_other_servers(self):
        await self.store.settings(1); await self.store.settings(2)
        await self.store.credit(2,99,WALLET,'solana:other:root',1000000,'p',{})
        file=await create_backup(self.store,self.root/'backups','password-with-at-least-20-chars',guild=1)
        target=restore_backup(file,self.root/'scoped.sqlite3','password-with-at-least-20-chars')
        with sqlite3.connect(target) as c:
            self.assertEqual(c.execute('SELECT guild FROM settings').fetchall(),[(1,)]); self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger').fetchone()[0],0)
    def test_wrong_password_and_corruption_fail(self):
        raw=encrypt(b'private','password-with-at-least-20-chars')
        for data,password in ((raw,'wrong'),(raw[:-1]+bytes([raw[-1]^1]),'password-with-at-least-20-chars')):
            with self.assertRaises(ValueError): decrypt(data,password)
    async def test_restore_does_not_overwrite_database(self):
        file=await create_backup(self.store,self.root/'backups','password-with-at-least-20-chars')
        with self.assertRaises(ValueError): restore_backup(file,self.store.path,'password-with-at-least-20-chars')
    def test_archive_traversal_is_rejected(self):
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w:gz') as archive:
            value=b'evil'; info=tarfile.TarInfo('../outside'); info.size=len(value); archive.addfile(info,io.BytesIO(value))
        path=self.root/'bad.rcvbackup'; path.write_bytes(encrypt(payload.getvalue(),'password-with-at-least-20-chars'))
        with self.assertRaises(ValueError): restore_backup(path,self.root/'new.db','password-with-at-least-20-chars')
        self.assertFalse((self.root/'new.db').exists())
