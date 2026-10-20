import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from ripcars_verifier.config import MAINNET_GENESIS,TOKEN_PROGRAM,USDC,canonical,defaults,validate
from ripcars_verifier.member_ui import MemberPanel,WalletTransferModal,TransactionModal,TransferRequest
from ripcars_verifier.network import ProviderError
from ripcars_verifier.transfer_proof import TransferProof,amount_text,check_transfer,mint_details
from scripts.configure_env import write_env
from scripts.configure_rpc import check_endpoints
from scripts.configure_rpc import main as configure_rpc
from scripts.preflight import read_env
from .helpers import DBCase,FakeHttp,FakeRpc,WALLET,SRC,DST,addr,encode58,fake_guild,interaction


SIGNATURE=encode58(bytes([9])*64)


def mint():
    return {'value':{'owner':TOKEN_PROGRAM,'data':{'parsed':{'type':'mint','info':{'isInitialized':True,'decimals':6}}}}}


def proof_transaction(proof,checked=True,inner=False,destination=SRC):
    keys=[{'pubkey':WALLET,'signer':True,'writable':True},{'pubkey':SRC,'signer':False,'writable':True}]
    if destination!=SRC: keys.append({'pubkey':destination,'signer':False,'writable':True})
    balances=[{'accountIndex':n,'owner':WALLET,'mint':USDC,'programId':TOKEN_PROGRAM,'uiTokenAmount':{'decimals':6,'amount':'2000000000'}} for n in range(1,len(keys))]
    info={'source':SRC,'destination':destination,'authority':WALLET}
    if checked: info.update(mint=USDC,tokenAmount={'amount':proof['raw_amount'],'decimals':6})
    else: info['amount']=proof['raw_amount']
    ins={'programId':TOKEN_PROGRAM,'parsed':{'type':'transferChecked' if checked else 'transfer','info':info}}
    return {'slot':proof['slot']+10,'blockTime':int(proof['created'])+1,
            'transaction':{'signatures':[SIGNATURE],'message':{'accountKeys':keys,'instructions':[] if inner else [ins]}},
            'meta':{'err':None,'preTokenBalances':copy.deepcopy(balances),'postTokenBalances':balances,
                    'innerInstructions':[{'index':0,'instructions':[ins]}] if inner else []}}


class TransferProofTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.settings(wallet_transfer=True,balance_mode='rpc',mint=USDC)
        self.tx=None
        def respond(method,url,payload,headers):
            name=payload['method']
            result={'getGenesisHash':MAINNET_GENESIS,'getAccountInfo':mint(),'getSlot':100,
                    'getTransaction':self.tx,'getHealth':'ok'}[name]
            if isinstance(result,Exception): raise result
            return {'jsonrpc':'2.0','id':'ripcars-verifier','result':result}
        self.http=FakeHttp(respond)
        self.service=TransferProof(self.store,self.http,{'RPC_URL':'https://rpc.example.com'})

    async def issue(self): return await self.service.create(1,10,WALLET)

    async def test_signed_self_transfer_links_and_queues_only_wallet_atomically(self):
        proof=await self.issue(); self.tx=proof_transaction(proof)
        await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual((await self.store.links(1,10))[0]['identity'],WALLET)
        self.assertEqual(json.loads((await self.store.query('SELECT kinds FROM job_requests'))[0]['kinds']),['token_balance'])
        self.assertEqual((await self.store.query('SELECT used,signature FROM transfer_proofs'))[0],{'used':1,'signature':SIGNATURE})
        self.assertTrue(all(call[1]=='https://rpc.example.com' for call in self.http.calls))

    async def test_raw_transfer_inner_instruction_and_second_own_account(self):
        proof=await self.issue()
        for checked,inner,destination in [(False,False,SRC),(True,True,SRC),(True,False,DST)]:
            with self.subTest(checked=checked,inner=inner,destination=destination):
                check_transfer(proof_transaction(proof,checked,inner,destination),SIGNATURE,proof)

    async def test_rejects_missing_wallet_signature_even_when_transfer_succeeds(self):
        proof=await self.issue(); tx=proof_transaction(proof)
        tx['transaction']['message']['accountKeys'][0]['signer']=False
        with self.assertRaisesRegex(ValueError,'did not sign'): check_transfer(tx,SIGNATURE,proof)

    async def test_rejects_wrong_owner_mint_amount_authority_program_and_decimals(self):
        proof=await self.issue()
        for key,value in [('authority',addr(8)),('source',addr(8)),('destination',addr(8)),('mint',addr(8)),('amount','999999999'),('decimals',5),('program',addr(8)),('owner',addr(8))]:
            tx=proof_transaction(proof); ins=tx['transaction']['message']['instructions'][0]
            if key=='program': ins['programId']=value
            elif key=='owner':
                for name in ('preTokenBalances','postTokenBalances'): tx['meta'][name][0]['owner']=value
            elif key in ('amount','decimals'): ins['parsed']['info']['tokenAmount'][key]=value
            else: ins['parsed']['info'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):check_transfer(tx,SIGNATURE,proof)

    async def test_rejects_old_failed_late_missing_and_wrong_signature_transactions(self):
        proof=await self.issue()
        for change in ('oldslot','oldtime','late','failed','signature','missingmeta','null'):
            tx=proof_transaction(proof)
            if change=='oldslot':tx['slot']=proof['slot']
            elif change=='oldtime':tx['blockTime']=int(proof['created'])-60
            elif change=='late':tx['blockTime']=int(proof['expires'])+1
            elif change=='failed':tx['meta']['err']={'InstructionError':[0,'Failed']}
            elif change=='signature':tx['transaction']['signatures']=[]
            elif change=='missingmeta':tx.pop('meta')
            else:tx=None
            with self.subTest(change=change),self.assertRaises((ValueError,ProviderError)):check_transfer(tx,SIGNATURE,proof)

    async def test_token_account_owner_change_is_rejected(self):
        proof=await self.issue(); tx=proof_transaction(proof)
        tx['meta']['postTokenBalances'][0]['owner']=addr(8)
        with self.assertRaisesRegex(ValueError,'changed'):check_transfer(tx,SIGNATURE,proof)

    async def test_reopen_reuses_exact_request_and_restart_keeps_it(self):
        proof=await self.issue(); await self.store.open()
        other=await self.issue()
        self.assertEqual(proof['id'],other['id']); self.assertEqual(amount_text(proof),amount_text(other))
        self.assertGreater(float(amount_text(proof)),0);self.assertLess(float(amount_text(proof)),1)

    async def test_duplicate_concurrent_requests_share_one_challenge(self):
        values=await asyncio.gather(*(self.issue() for _ in range(5)),return_exceptions=True)
        self.assertEqual(len([v for v in values if isinstance(v,dict)]),1)
        self.assertEqual(len(await self.store.query('SELECT * FROM transfer_proofs')),1)
        self.assertEqual((await self.issue())['id'],next(v['id'] for v in values if isinstance(v,dict)))

    async def test_foreign_user_and_server_cannot_use_or_cancel_request(self):
        proof=await self.issue(); self.tx=proof_transaction(proof)
        for guild,user in [(1,11),(2,10)]:
            with self.assertRaises(ValueError):await self.service.verify(guild,user,proof['id'],SIGNATURE)
            await self.service.cancel(guild,user,proof['id'])
        self.assertEqual((await self.store.query('SELECT used FROM transfer_proofs'))[0]['used'],0)

    async def test_pending_and_linked_wallet_cannot_be_claimed_by_another_member(self):
        await self.issue()
        with self.assertRaisesRegex(ValueError,'already'):await self.service.create(1,11,WALLET)

    async def test_used_proof_and_same_signature_cannot_be_replayed(self):
        proof=await self.issue();self.tx=proof_transaction(proof)
        await self.service.verify(1,10,proof['id'],SIGNATURE)
        with self.assertRaises(ValueError):await self.service.verify(1,10,proof['id'],SIGNATURE)
        await self.store.execute("UPDATE links SET active=0 WHERE guild=1 AND user=10")
        await self.store.execute('UPDATE transfer_proofs SET created=created-120')
        await self.store.execute("UPDATE audit SET created=created-120 WHERE action='self_transfer_start'")
        next_proof=await self.issue();self.tx=proof_transaction(next_proof)
        with self.assertRaisesRegex(ValueError,'already been used'):await self.service.verify(1,10,next_proof['id'],SIGNATURE)

    async def test_cancel_and_expiry_cannot_bind(self):
        proof=await self.issue();self.tx=proof_transaction(proof)
        await self.service.cancel(1,10,proof['id'])
        with self.assertRaises(ValueError):await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual(await self.store.links(1,10),[])
        await self.store.execute('UPDATE transfer_proofs SET used=0,expires=0')
        with self.assertRaisesRegex(ValueError,'expired'):await self.service.verify(1,10,proof['id'],SIGNATURE)

    async def test_existing_proven_wallet_needs_no_new_transaction(self):
        await self.wallet()
        with self.assertRaisesRegex(ValueError,'no new transfer'):await self.issue()
        self.assertEqual(await self.store.query('SELECT * FROM transfer_proofs'),[])

    async def test_rpc_failure_keeps_roles_and_request_retryable(self):
        proof=await self.issue();self.tx=ProviderError('unavailable')
        await self.store.execute("INSERT INTO role_grants VALUES(1,10,20,'active')")
        with self.assertRaises(ProviderError):await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual((await self.store.query('SELECT state FROM role_grants'))[0]['state'],'active')
        self.assertEqual((await self.store.query('SELECT used FROM transfer_proofs'))[0]['used'],0)
        self.assertEqual(await self.store.links(1,10),[])
        with self.assertRaisesRegex(ValueError,'30 seconds'):await self.service.verify(1,10,proof['id'],SIGNATURE)
        await self.store.execute('UPDATE transfer_proofs SET next_check=0')
        self.tx=proof_transaction(proof);await self.service.verify(1,10,proof['id'],SIGNATURE)

    async def test_cancellation_during_rpc_prevents_late_bind(self):
        proof=await self.issue()
        async def call(method,params):
            await self.service.cancel(1,10,proof['id'])
            return proof_transaction(proof)
        with patch.object(self.service,'rpc',return_value=SimpleNamespace(call=call)):
            with self.assertRaisesRegex(ValueError,'changed'):await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual(await self.store.links(1,10),[])

    async def test_token_change_during_check_prevents_bind(self):
        proof=await self.issue()
        async def call(method,params):
            await self.settings(mint=addr(8))
            return proof_transaction(proof)
        with patch.object(self.service,'rpc',return_value=SimpleNamespace(call=call)):
            with self.assertRaises(ValueError):await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual(await self.store.links(1,10),[])

    async def test_queue_failure_rolls_back_link_and_proof(self):
        proof=await self.issue();self.tx=proof_transaction(proof)
        with patch.object(self.store,'queue_check',side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):await self.service.verify(1,10,proof['id'],SIGNATURE)
        self.assertEqual(await self.store.links(1,10),[])
        self.assertEqual((await self.store.query('SELECT used FROM transfer_proofs'))[0]['used'],0)

    async def test_old_database_settings_and_exports_migrate_without_enabling_method(self):
        cfg=defaults();cfg.pop('wallet_transfer');validate(cfg);self.assertFalse(cfg['wallet_transfer'])
        cfg.pop('wallet_transfer')
        await self.store.execute('UPDATE settings SET value=? WHERE guild=1',(canonical(cfg),))
        await self.store.open();self.assertFalse((await self.store.settings(1))[0]['wallet_transfer'])
        stored=json.loads((await self.store.query('SELECT value FROM settings WHERE guild=1'))[0]['value'])
        self.assertNotIn('wallet_transfer',stored)

    async def test_backup_restore_cancels_pending_proofs(self):
        import sqlite3
        from ripcars_verifier.backups import create_backup,restore_backup
        await self.issue()
        password='self-transfer-test-password-over-20-characters'
        archive=await create_backup(self.store,self.root/'backups',password)
        restored=restore_backup(archive,self.root/'restored.sqlite3',password)
        with sqlite3.connect(restored) as c:
            self.assertEqual(c.execute('SELECT used FROM transfer_proofs').fetchone()[0],-1)

    async def test_real_discord_controls_fit_component_limits(self):
        bot=SimpleNamespace()
        panel=MemberPanel(bot)
        self.assertTrue(any(c.custom_id=='rcv:transfer:v1' for c in panel.children))
        self.assertTrue(WalletTransferModal(bot).to_dict())
        self.assertTrue(TransactionModal(bot,'request').to_dict())
        self.assertEqual(len(TransferRequest(bot,10,'request').children),2)

    async def test_actual_member_modals_issue_and_verify_without_a_website(self):
        await self.settings(member_role=2)
        bot=SimpleNamespace(db=self.store,transfer_proof=self.service,reporter=SimpleNamespace(error=AsyncMock()))
        guild,member=fake_guild();i=interaction(guild,member);i.response.is_done=lambda:True
        modal=WalletTransferModal(bot);modal.wallet._value=WALLET
        await modal.on_submit(i)
        view=i.followup.send.call_args.kwargs['view']
        proof=(await self.store.query('SELECT * FROM transfer_proofs'))[0]
        self.assertIn(amount_text(proof),i.followup.send.call_args.args[0])
        self.tx=proof_transaction(proof)
        submit=TransactionModal(bot,view.pid);submit.signature._value=SIGNATURE
        j=interaction(guild,member);j.response.is_done=lambda:True
        await submit.on_submit(j)
        self.assertIn('ownership verified',j.followup.send.call_args.args[0])
        bot.reporter.error.assert_not_awaited()


class RpcSetupTests(DBCase):
    async def test_checks_both_mainnet_endpoints_and_lookup_method(self):
        def respond(method,url,payload,headers):
            values={'getGenesisHash':MAINNET_GENESIS,'getHealth':'ok','getSlot':123,'getTransaction':None}
            return {'jsonrpc':'2.0','id':'ripcars-verifier','result':values[payload['method']]}
        http=FakeHttp(respond)
        await check_endpoints({'RPC_URL':'https://primary.example.com','RPC_FALLBACK_URL':'https://backup.example.com'},http)
        self.assertEqual(len([c for c in http.calls if c[2]['method']=='getTransaction']),2)

    async def test_wrong_network_and_unsupported_lookup_fail(self):
        for result in [{'jsonrpc':'2.0','id':'ripcars-verifier','result':'devnet'}, {'jsonrpc':'2.0','id':'ripcars-verifier','error':{'code':-32601}}]:
            with self.assertRaises(ProviderError):await check_endpoints({'RPC_URL':'https://rpc.example.com'},FakeHttp(lambda *args:result))

    async def test_mint_precision_and_extensions_fail_closed(self):
        for change in ('precision','extension','uninitialized'):
            data=mint();info=data['value']['data']['parsed']['info']
            if change=='precision':info['decimals']=0
            elif change=='extension':info['extensions']=[{'extension':'transferFeeConfig'}]
            else:info['isInitialized']=False
            with self.assertRaises(ValueError):await mint_details(FakeRpc({'getAccountInfo':data}),USDC)

    async def test_atomic_env_writer_preserves_other_credentials_and_mode(self):
        target=self.root/'env'
        env={'DISCORD_TOKEN':'original','BACKUP_PASSWORD':'original-password','RPC_URL':'https://rpc.example.com/?api-key=private'}
        write_env(target,env);read=read_env(target)
        self.assertEqual(read['DISCORD_TOKEN'],'original');self.assertEqual(read['BACKUP_PASSWORD'],'original-password')
        self.assertEqual(target.stat().st_mode&0o777,0o600)


class RpcToolTests(unittest.TestCase):
    def test_only_rpc_changes_and_failed_probe_saves_nothing(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'env'
            original={'DISCORD_TOKEN':'x'*60,'BACKUP_PASSWORD':'backup-password-of-at-least-20-characters',
                      'WEB_HOST':'127.0.0.1','WEB_PORT':'8092',
                      'DATABASE_PATH':'/var/lib/verifier/db','COORDINATION_PATH':'/var/lib/shared/db','BACKUP_PATH':'/var/lib/verifier/backups'}
            write_env(target,original);before=target.read_bytes()
            for failure in (True,False):
                http=SimpleNamespace(open=AsyncMock(),close=AsyncMock())
                with patch('sys.argv',['configure_rpc.py','--file',str(target)]),patch('scripts.configure_rpc.os.geteuid',return_value=0),\
                     patch('scripts.configure_rpc.getpass.getpass',side_effect=['https://rpc.example.com','']),\
                     patch('scripts.configure_rpc.Http',return_value=http),\
                     patch('scripts.configure_rpc.check_endpoints',new=AsyncMock(side_effect=ProviderError('unavailable') if failure else None)):
                    if failure:
                        with self.assertRaises(SystemExit):configure_rpc()
                        self.assertEqual(target.read_bytes(),before)
                        self.assertEqual(list(Path(folder).glob('env.before-rpc-*')),[])
                    else:self.assertEqual(configure_rpc(),0)
                http.close.assert_awaited_once()
            saved=read_env(target)
            self.assertEqual(saved['DISCORD_TOKEN'],original['DISCORD_TOKEN']);self.assertEqual(saved['BACKUP_PASSWORD'],original['BACKUP_PASSWORD'])
            self.assertEqual(saved['RPC_URL'],'https://rpc.example.com')
            self.assertEqual(next(Path(folder).glob('env.before-rpc-*')).read_bytes(),before)


if __name__=='__main__':unittest.main()
