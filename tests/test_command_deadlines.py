import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.webhook.async_ import async_context

from ripcars_verifier.bot import Verifier
from ripcars_verifier.operations import authorized
from .helpers import DBCase, fake_guild


class CommandDeadlineTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot=Verifier({'DATABASE_PATH':str(self.store.path),'COORDINATION_PATH':str(self.root/'shared.sqlite3')})
        await self.bot.__aenter__()
        self.bot.db=self.store
        await self.bot.registry.open()
        self.guild,self.member=fake_guild()
        self.member.guild=self.guild
        self.member.guild_permissions=discord.Permissions(manage_guild=True)
        await self.settings(member_role=2,verification_channel=30,log_channel=32)
        self.acknowledged=asyncio.Event()
        async def respond(*args,**kwargs):
            self.acknowledged.set()
            return {'interaction':{'id':str(args[0])}}
        self.adapter=SimpleNamespace(create_interaction_response=AsyncMock(side_effect=respond))
        self.context_token=async_context.set(self.adapter)
        self.webhook_patch=patch.object(discord.Webhook,'send',new_callable=AsyncMock)
        self.followup=self.webhook_patch.start()

    async def asyncTearDown(self):
        self.webhook_patch.stop()
        async_context.reset(self.context_token)
        await self.bot.__aexit__(None,None,None)
        await super().asyncTearDown()

    def interaction(self,name,kind=2):
        i=discord.Interaction(state=self.bot._connection,data={
            'id':str(discord.utils.time_snowflake(discord.utils.utcnow())),
            'application_id':'900','type':kind,'token':'test-only-interaction-token',
            'version':1,'attachment_size_limit':10000000,
            'data':{'name':'verifier','type':1,'options':[{'name':name,'type':1}]}})
        i.guild_id,i.user,i.channel=1,self.member,self.guild.get_channel(30)
        return i

    async def test_panel_health_and_doctor_ack_before_sqlite_writer_wait(self):
        for name in ('panel','health','doctor'):
            with self.subTest(command=name):
                self.acknowledged.clear()
                self.adapter.create_interaction_response.reset_mock()
                self.followup.reset_mock()
                writer=sqlite3.connect(self.store.path)
                writer.execute('BEGIN IMMEDIATE')
                task=asyncio.create_task(self.bot.tree._call(self.interaction(name)))
                try:
                    await asyncio.wait_for(self.acknowledged.wait(),0.5)
                    self.assertFalse(task.done())
                finally:
                    writer.rollback()
                    writer.close()
                    await asyncio.wait_for(task,3)
                self.adapter.create_interaction_response.assert_awaited_once()
                self.assertEqual(self.adapter.create_interaction_response.await_args.kwargs['params'].payload['data']['flags'],64)
                self.followup.assert_awaited_once()

    async def test_early_ack_preserves_admin_check(self):
        self.member.guild_permissions=discord.Permissions.none()
        await self.bot.tree._call(self.interaction('panel'))
        self.followup.assert_awaited_once()
        self.assertIn('requires Manage Server',self.followup.await_args.args[0])

    async def test_component_authorization_keeps_modal_response_available(self):
        i=self.interaction('panel',3)
        await authorized(self.bot,i,True)
        self.adapter.create_interaction_response.assert_not_awaited()
        self.assertFalse(i.response.is_done())

    async def test_expired_ack_does_not_read_database_or_retry_response(self):
        self.adapter.create_interaction_response.side_effect=discord.NotFound(
            SimpleNamespace(status=404,reason='Not Found'),{'code':10062,'message':'Unknown interaction'})
        with patch.object(self.store,'settings',new_callable=AsyncMock) as settings, patch.object(self.bot.reporter,'error',new_callable=AsyncMock) as report:
            await self.bot.tree._call(self.interaction('panel'))
            settings.assert_not_awaited()
            report.assert_not_awaited()
        self.adapter.create_interaction_response.assert_awaited_once()
        self.followup.assert_not_awaited()
