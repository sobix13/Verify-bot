import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ripcars_verifier.admin_ui import AdminPanel, SECTIONS, SourceModal
from ripcars_verifier.bot import Verifier
from ripcars_verifier.config import canonical
from ripcars_verifier.coordination import Registry
from ripcars_verifier.member_ui import MemberPanel
from ripcars_verifier.operations import authorized, doctor, private_channel, safe_role
from ripcars_verifier.storage import Conflict
from .helpers import DBCase, fake_guild, interaction, rule


class RegistryTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp(); self.registry=Registry(self.root/'shared.sqlite3'); await self.registry.open()
    async def test_shared_schema_reads_gate_role(self):
        await self.registry.execute("INSERT INTO resources VALUES(1,'role:rippers',2,'role','bot:100','{}','{}','active')")
        self.assertEqual(await self.registry.gate_member(1),2)
    async def test_setup_lease_blocks_peer_then_releases(self):
        peer=Registry(self.registry.path)
        async with self.registry.lease(1):
            with self.assertRaises(Conflict):
                async with peer.lease(1): pass
        async with peer.lease(1): await peer.ensure_lease(1)
    async def test_role_cannot_be_claim_role(self):
        await self.registry.execute("INSERT INTO resources VALUES(1,'claim:collectors',20,'role','bot:100','{}','{}','active')")
        with self.assertRaises(Conflict): await self.registry.check_role(1,20,900,'tier_10',{'name':'Deposit 10','permissions':0})
    async def test_pinned_or_manually_renamed_role_is_preserved(self):
        async with self.registry.lease(1): await self.registry.bind_role(1,20,900,'tier_10',{'name':'Deposit 10','permissions':0})
        with self.assertRaises(Conflict): await self.registry.check_role(1,20,900,'tier_10',{'name':'Changed','permissions':0})
        await self.registry.execute("UPDATE resources SET state='pinned'")
        with self.assertRaises(Conflict): await self.registry.check_role(1,20,900,'tier_10',{'name':'Deposit 10','permissions':0})
    async def test_lost_lease_stops_change(self):
        async with self.registry.lease(1):
            await self.registry.execute("UPDATE locks SET token='another'")
            with self.assertRaises(Conflict): await self.registry.ensure_lease(1)
        self.assertEqual((await self.registry.query('SELECT token FROM locks'))[0]['token'],'another')
    async def test_gate_text_channel_cannot_be_claimed_as_verifier_channel(self):
        await self.registry.execute("INSERT INTO resources VALUES(1,'channel:general',30,'text','ripcars-gate','{}','{}','active')")
        async with self.registry.lease(1):
            with self.assertRaises(Conflict):await self.registry.bind_channel(1,30,900,'verification_channel')
        self.assertEqual(len(await self.registry.entries(1)),1)


class DiscordTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        env={'DISCORD_TOKEN':'offline-fixture','PUBLIC_URL':'https://verify.example.com','DATABASE_PATH':str(self.store.path),'COORDINATION_PATH':str(self.root/'shared.sqlite3'),'BACKUP_PATH':str(self.root/'backups')}
        self.bot=Verifier(env); self.bot._connection.user=SimpleNamespace(id=900); await self.bot.registry.open()
        self.bot.db=self.store; self.guild,self.member=fake_guild(); self.bot.get_guild=lambda gid:self.guild if gid==1 else None
        cfg,rev=await self.store.settings(1); cfg.update(member_role=2,verification_channel=30,rip_channel=31,log_channel=32,public_message=500,rules=[rule()],enabled=True,deposit_mode='rpc'); self.rev=await self.store.save_settings(1,cfg,rev,7); self.cfg=cfg
        async with self.bot.registry.lease(1): await self.bot.registry.bind_role(1,20,900,'tier_10',{'name':'Deposit 10','permissions':0})
    async def asyncTearDown(self): await self.bot.close(); await super().asyncTearDown()
    async def test_role_grant_and_removal(self):
        await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'12','complete':{'deposit_points':True}},self.rev)
        self.member.add_roles.assert_awaited_once(); self.assertIn(20,{r.id for r in self.member.roles})
        await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'0','complete':{'deposit_points':True}},self.rev)
        self.member.remove_roles.assert_awaited_once()
    async def test_existing_manual_role_is_not_removed(self):
        self.member.roles.append(self.guild.get_role(20)); await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'0','complete':{'deposit_points':True}},self.rev); self.member.remove_roles.assert_not_awaited()
    async def test_manual_removal_suspends_automation(self):
        await self.store.execute("INSERT INTO role_grants VALUES(1,10,20,'active')")
        await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'999','complete':{'deposit_points':True}},self.rev)
        self.member.add_roles.assert_not_awaited(); self.assertEqual((await self.store.query('SELECT state FROM role_grants'))[0]['state'],'manual')
    async def test_no_gate_role_means_no_qualifying_grant(self):
        self.member.roles=[self.guild.roles[0]]; await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'999','complete':{'deposit_points':True}},self.rev); self.member.add_roles.assert_not_awaited()
    async def test_privileged_role_is_rejected(self):
        role=self.guild.get_role(20); role.permissions=discord.Permissions(manage_messages=True)
        with self.assertRaises(ValueError): safe_role(self.guild,role,2)
    async def test_changed_revision_blocks_discord_mutation(self):
        await self.settings(brand='Changed')
        with self.assertRaises(Conflict): await self.bot.apply_roles(self.guild,self.member,self.cfg,{'deposit_points':'999','complete':{'deposit_points':True}},self.rev)
        self.member.add_roles.assert_not_awaited()
    async def test_admin_panel_all_sections_serialize(self):
        for section in SECTIONS:
            view=AdminPanel(self.bot,10,self.cfg,self.rev,section); self.assertLessEqual(len(view.to_components()),5); self.assertIn('Rip Cars Verifier',view.embed().title)
        self.assertEqual(len(SourceModal(AdminPanel(self.bot,10,self.cfg,self.rev)).children),5)
    async def test_channel_selector_keeps_selected_ids(self):
        view=AdminPanel(self.bot,10,self.cfg,self.rev,'channels')
        defaults_by_row={item.row:item.default_values[0].id for item in view.children if isinstance(item,(discord.ui.ChannelSelect,discord.ui.RoleSelect))}; self.assertEqual(defaults_by_row,{1:2,2:30,3:31,4:32})
    async def test_channel_selection_callback_saves_and_renders_new_default(self):
        self.member.guild_permissions.manage_guild=True; view=AdminPanel(self.bot,10,self.cfg,self.rev,'channels'); item=next(c for c in view.children if c.row==2)
        item._values=[SimpleNamespace(id=35)]; i=interaction(self.guild,self.member); await item.callback(i)
        saved,_=await self.store.settings(1); self.assertEqual(saved['verification_channel'],35); self.assertFalse(saved['enabled'])
        rendered=i.edit_original_response.call_args.kwargs['view']; selected=next(c for c in rendered.children if c.row==2); self.assertEqual(selected.default_values[0].id,35)
    async def test_panel_owner_bound(self):
        i=interaction(self.guild,self.member); view=AdminPanel(self.bot,99,self.cfg,self.rev)
        self.assertFalse(await view.interaction_check(i)); i.response.send_message.assert_awaited_once()
    async def test_member_authorization_gate_and_no_bot(self):
        i=interaction(self.guild,self.member); await authorized(self.bot,i)
        self.member.bot=True
        with self.assertRaises(ValueError): await authorized(self.bot,i)
    async def test_admin_authorization_checked_server_side(self):
        with self.assertRaises(ValueError): await authorized(self.bot,interaction(self.guild,self.member),True)
    async def test_staff_log_is_private(self): self.assertTrue(private_channel(self.guild,self.guild.get_channel(32))); self.assertFalse(private_channel(self.guild,self.guild.get_channel(30)))
    async def test_doctor_finds_lost_role_binding(self):
        await self.bot.registry.execute("DELETE FROM resources WHERE key LIKE 'verifier:%'")
        blockers,notes=await doctor(self.bot,self.guild); self.assertTrue(any('bind qualifying roles' in b for b in blockers))
    async def test_persistent_ids_are_namespaced(self):
        view=MemberPanel(self.bot); self.assertTrue(view.is_persistent()); self.assertTrue(all(item.custom_id.startswith('rcv:') for item in view.children))
    async def test_member_status_command_runs_button_callback(self):
        i=interaction(self.guild,self.member); group=self.bot.tree.get_command('verifier'); await group.get_command('status').callback(group,i); i.response.send_message.assert_awaited_once()
    async def test_rip_send_and_footer(self):
        await self.settings(rip_feed=True)
        payload={'id':'pull1','car_name':'Roxy','asset_id':'asset1','image_url':'https://images.example.com/car.png'}
        await self.store.execute("INSERT INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(1,'rip:pull1','rip',?,'pending',0,0)",(canonical(payload),))
        row=(await self.store.query('SELECT * FROM deliveries'))[0]; await self.bot.deliver(row)
        self.assertEqual((await self.store.query('SELECT state,message FROM deliveries'))[0],{'state':'sent','message':500}); embed=self.guild.get_channel(31).send.call_args.kwargs['embed']; self.assertEqual(embed.footer.text,'Event: pull1')
    async def test_uncertain_send_goes_to_review_without_blind_retry(self):
        await self.settings(rip_feed=True); self.guild.get_channel(31).send.side_effect=TimeoutError('unknown delivery')
        await self.store.execute("INSERT INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(1,'rip:a','rip',?,'pending',0,0)",(canonical({'id':'a','car_name':'Roxy','asset_id':'asset1'}),))
        with self.assertRaises(TimeoutError): await self.bot.deliver((await self.store.query('SELECT * FROM deliveries'))[0])
        self.assertEqual((await self.store.query('SELECT state FROM deliveries'))[0]['state'],'review')
    async def test_secrets_redacted_from_errors(self):
        self.bot.env['RPC_URL']='https://rpc.example.com/?api-key=private'; self.bot.env['PLATFORM_API_KEY']='secretlongvalue'
        text=self.bot.reporter.redact('https://rpc.example.com/?api-key=private secretlongvalue token=abc')
        self.assertNotIn('private',text); self.assertNotIn('secretlongvalue',text); self.assertNotIn('abc',text)
    async def test_publish_updates_existing_panel_without_permission_changes(self):
        message=SimpleNamespace(id=500,author=SimpleNamespace(id=900),edit=AsyncMock())
        self.guild.get_channel(30).fetch_message.return_value=message
        await self.bot.publish(self.guild,7); message.edit.assert_awaited_once(); self.guild.get_channel(30).send.assert_not_awaited()
        for channel in self.guild.channels.values(): channel.set_permissions.assert_not_called()
    async def test_deleted_panel_is_not_duplicated(self):
        self.guild.get_channel(30).fetch_message.side_effect=discord.NotFound(SimpleNamespace(status=404,reason='Not Found'),{'code':10008,'message':'Unknown Message'})
        with self.assertRaises(Conflict): await self.bot.publish(self.guild,7)
        self.guild.get_channel(30).send.assert_not_awaited()
    async def test_foreign_public_message_is_preserved(self):
        self.guild.get_channel(30).fetch_message.return_value=SimpleNamespace(id=500,author=SimpleNamespace(id=100))
        with self.assertRaises(Conflict): await self.bot.publish(self.guild,7)
    async def test_permission_failure_rip_remains_pending_for_delayed_retry(self):
        await self.settings(rip_feed=True); self.guild.get_channel(31).send.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),{'code':50013,'message':'Missing Permissions'})
        await self.store.execute("INSERT INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(1,'rip:a','rip',?,'pending',0,0)",(canonical({'id':'a','car_name':'Roxy','asset_id':'asset1'}),))
        with self.assertRaises(discord.Forbidden): await self.bot.deliver((await self.store.query('SELECT * FROM deliveries'))[0])
        row=(await self.store.query('SELECT state,next_run FROM deliveries'))[0]; self.assertEqual(row['state'],'pending'); self.assertGreater(row['next_run'],time.time()+290)
    async def test_bind_existing_role_requires_paused_setup(self):
        with self.assertRaises(ValueError): await self.bot.create_roles(self.guild,7,self.rev)
    async def test_existing_name_is_not_guessed_on_creation(self):
        cfg,revision=await self.store.settings(1); cfg.update(enabled=False,rules=[rule(rid=0)]); revision=await self.store.save_settings(1,cfg,revision,7)
        self.guild.create_role=AsyncMock()
        with self.assertRaises(Conflict): await self.bot.create_roles(self.guild,7,revision)
        self.guild.create_role.assert_not_awaited()
    async def test_role_creation_saved_before_later_binding_failure(self):
        cfg,revision=await self.store.settings(1); cfg.update(enabled=False,rules=[rule(rid=0,name='New Tier',key='new_tier')]); revision=await self.store.save_settings(1,cfg,revision,7)
        role=type(self.guild.roles[0])(99,'New Tier',3)
        async def create(**kwargs): self.guild.roles.append(role); return role
        self.guild.create_role=AsyncMock(side_effect=create)
        with patch.object(self.bot.registry,'bind_role',side_effect=Conflict('binding failure')):
            with self.assertRaises(Conflict): await self.bot.create_roles(self.guild,7,revision)
        saved,revision=await self.store.settings(1); self.assertEqual(saved['rules'][0]['role_id'],99)
        await self.bot.create_roles(self.guild,7,revision); self.guild.create_role.assert_awaited_once()
    async def test_optional_channel_setup_creates_only_unset_channels(self):
        cfg,revision=await self.store.settings(1); cfg.update(enabled=False,verification_channel=0,rip_channel=0,log_channel=0); await self.store.save_settings(1,cfg,revision,7)
        self.guild.default_role=self.guild.roles[0]; self.guild.me.guild_permissions.manage_channels=True
        original=list(self.guild.channels.values()); self.guild.channels=list(original); next_id=100
        async def create(name,**kwargs):
            nonlocal next_id
            channel=Mock(spec=discord.TextChannel); channel.id=next_id; next_id+=1; channel.name=name; channel.overwrites=kwargs['overwrites']; self.guild.channels.append(channel); return channel
        self.guild.create_text_channel=AsyncMock(side_effect=create)
        await self.bot.prepare_channels(self.guild,7); self.assertEqual(self.guild.create_text_channel.await_count,3)
        saved,_=await self.store.settings(1); self.assertEqual((saved['verification_channel'],saved['rip_channel'],saved['log_channel']),(100,101,102))
        log=self.guild.channels[-1]; self.assertFalse(log.overwrites[self.guild.get_role(2)].view_channel)
        await self.bot.prepare_channels(self.guild,7); self.assertEqual(self.guild.create_text_channel.await_count,3)
        self.assertEqual(self.guild.channels[:3],original)
    async def test_optional_channel_setup_refuses_existing_name_collision(self):
        cfg,revision=await self.store.settings(1); cfg.update(enabled=False,verification_channel=0); await self.store.save_settings(1,cfg,revision,7)
        self.guild.channels=list(self.guild.channels.values()); self.guild.channels[0].name='ripcars-connect'; self.guild.me.guild_permissions.manage_channels=True; self.guild.create_text_channel=AsyncMock()
        with self.assertRaises(Conflict): await self.bot.prepare_channels(self.guild,7)
        self.guild.create_text_channel.assert_not_awaited()
    async def test_review_role_accepts_only_same_safe_owned_role(self):
        cfg,_=await self.store.settings(1); cfg['rules'][0]['name']='Renamed Tier'; await self.settings(enabled=False,rules=cfg['rules']); self.guild.get_role(20).name='Renamed Tier'; self.member.guild_permissions.manage_guild=True
        group=self.bot.tree.get_command('verifier'); i=interaction(self.guild,self.member)
        await group.get_command('review_role').callback(group,i,'tier_10',True)
        record=(await self.bot.registry.entries(1))['verifier:role:900:tier_10']; self.assertEqual(json.loads(record['baseline'])['name'],'Renamed Tier')
    async def test_role_review_does_not_reclaim_foreign_owner(self):
        await self.settings(enabled=False); self.member.guild_permissions.manage_guild=True; await self.bot.registry.execute("UPDATE resources SET owner='bot:100'")
        group=self.bot.tree.get_command('verifier')
        with self.assertRaises(ValueError): await group.get_command('review_role').callback(group,interaction(self.guild,self.member),'tier_10',True)
        self.assertEqual((await self.bot.registry.entries(1))['verifier:role:900:tier_10']['owner'],'bot:100')
    async def test_role_review_requires_explicit_acceptance(self):
        await self.settings(enabled=False); self.member.guild_permissions.manage_guild=True; await self.bot.registry.execute("UPDATE resources SET state='pinned'")
        group=self.bot.tree.get_command('verifier'); await group.get_command('review_role').callback(group,interaction(self.guild,self.member),'tier_10',False)
        self.assertEqual((await self.bot.registry.entries(1))['verifier:role:900:tier_10']['state'],'pinned')
    async def test_guided_payment_form_saves_verified_source_and_pauses(self):
        from .helpers import PROGRAM,TREASURY
        from ripcars_verifier.config import USDC
        self.member.guild_permissions.manage_guild=True; panel=AdminPanel(self.bot,10,self.cfg,self.rev,'deposits'); modal=SourceModal(panel)
        modal.key._value='payments'; modal.programs._value=PROGRAM; modal.owners._value=TREASURY; modal.accounts._value=''; modal.mints._value=USDC+',6,1'
        await modal.on_submit(interaction(self.guild,self.member)); saved,_=await self.store.settings(1)
        self.assertFalse(saved['enabled']); self.assertEqual(saved['sources'][0]['mints'][USDC]['usd_per_token'],'1')
