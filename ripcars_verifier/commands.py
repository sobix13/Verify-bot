from __future__ import annotations

import json
import time

import discord
from discord import app_commands

from .admin_ui import AdminPanel
from .config import validate, canonical
from .member_ui import MemberPanel
from .operations import attachment,authorized,doctor,reply,safe_role


@app_commands.guild_only()
class VerifierCommands(app_commands.Group):
    def __init__(self,bot): super().__init__(name='verifier',description='Rip Cars connections, roles and operations'); self.bot=bot

    @app_commands.command(description='Open the verifier admin center')
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self,i:discord.Interaction):
        cfg,revision=await authorized(self.bot,i,True); view=AdminPanel(self.bot,i.user.id,cfg,revision)
        await reply(i,embed=view.embed(),view=view)

    @app_commands.command(description='Create only unset verifier channels with Gate member and staff access')
    @app_commands.default_permissions(manage_guild=True)
    async def prepare_channels(self,i:discord.Interaction):
        await authorized(self.bot,i,True); await i.response.defer(ephemeral=True)
        await self.bot.prepare_channels(i.guild,i.user.id)
        await reply(i,'Unset verifier channels were created. Existing selected channels were preserved. Review Channels and Doctor before activation.')

    @app_commands.command(description='Get connection help and the admin setup sequence')
    async def guide(self,i:discord.Interaction):
        await reply(i,'Members: complete Gate, use Connect wallet or Connect Rip Cars account, then My status.\nAdmins: open /verifier panel, select channels, enable the required data sources, define separate qualifying roles, run Doctor, publish the connection panel and activate.\nDeposit points are earned from verified deposits at one point per USD. Platform points and current holdings use their own rules. Full installation and integration contracts are included in DEPLOYMENT.md and API_CONTRACT.md.')

    @app_commands.command(description='Check permissions, configuration and activation blockers')
    @app_commands.default_permissions(manage_guild=True)
    async def doctor(self,i:discord.Interaction):
        await authorized(self.bot,i,True); await i.response.defer(ephemeral=True)
        blockers,notes=await doctor(self.bot,i.guild)
        await reply(i,('Ready.' if not blockers else 'Fix before activating:\n'+'\n'.join(blockers))[:1900],file=attachment('verifier-doctor.json',{'blockers':blockers,'notes':notes}))

    @app_commands.command(description='Read configured providers and check the public HTTPS connection site')
    @app_commands.default_permissions(manage_guild=True)
    async def probe(self,i:discord.Interaction):
        cfg,_=await authorized(self.bot,i,True); await i.response.defer(ephemeral=True)
        from .probes import probe_sources
        checks=await probe_sources(self.bot,i.guild_id,cfg)
        await reply(i,'\n'.join(c['source']+': '+('passed' if c['ok'] else c['detail']) for c in checks)[:1900] or 'No external data sources are enabled.',file=attachment('verifier-provider-checks.json',checks))

    @app_commands.command(description='Check process, database, queue and recent failures')
    @app_commands.default_permissions(manage_guild=True)
    async def health(self,i:discord.Interaction):
        cfg,_=await authorized(self.bot,i,True); status=await self.bot.web_health()
        jobs=await self.bot.db.query('SELECT COUNT(*) AS n FROM jobs WHERE guild=?',(i.guild_id,))
        failures=await self.bot.db.query("SELECT COUNT(*) AS n FROM audit WHERE guild=? AND action='error' AND created>?",(i.guild_id,time.time()-86400))
        pending=await self.bot.db.query("SELECT state,COUNT(*) AS n FROM deliveries WHERE guild=? GROUP BY state",(i.guild_id,))
        await reply(i,f'Verifier: {"active" if cfg["enabled"] else "paused"}\nVersion: {status["version"]}\nUptime: {int((time.monotonic()-self.bot.started)/60)} minutes\nDatabase: {status["database"]}\nDiscord: {status["discord"]}\nWorker: {status["worker"]}\nMembers scheduled: {jobs[0]["n"]}\nErrors in 24h: {failures[0]["n"]}\nRip queue: '+', '.join(f'{r["state"]}={r["n"]}' for r in pending))

    @app_commands.command(description='Show your private account and wallet status')
    async def status(self,i:discord.Interaction):
        await MemberPanel(self.bot).status.callback(i)

    @app_commands.command(description='Queue a fresh check of your verified connections')
    async def refresh(self,i:discord.Interaction):
        cfg,_=await authorized(self.bot,i)
        if not cfg['enabled']: raise ValueError('Verifier is paused.')
        await self.bot.db.member_refresh(i.guild_id,i.user.id)
        await reply(i,'Your check is queued. Open /verifier status after the next worker pass.')

    @app_commands.command(description='Validate and import a settings file, leaving Verifier paused')
    @app_commands.default_permissions(manage_guild=True)
    async def import_settings(self,i:discord.Interaction,file:discord.Attachment):
        old,revision=await authorized(self.bot,i,True)
        if file.size>262144: raise ValueError('Settings file exceeds 256 KiB.')
        await i.response.defer(ephemeral=True); cfg=json.loads(await file.read()); validate(cfg)
        cfg['enabled']=False; cfg['public_message']=old['public_message']
        await self.bot.db.save_settings(i.guild_id,cfg,revision,i.user.id)
        await reply(i,'Settings imported and paused. Review Channels, Roles and Doctor before reactivating.')

    @app_commands.command(description='Restore a recorded settings revision and pause Verifier')
    @app_commands.default_permissions(manage_guild=True)
    async def restore_settings(self,i:discord.Interaction,revision_id:int):
        old,revision=await authorized(self.bot,i,True)
        rows=await self.bot.db.query("SELECT detail FROM audit WHERE id=? AND guild=? AND action='settings'",(revision_id,i.guild_id))
        if not rows: raise ValueError('This server has no settings record with that ID.')
        cfg=json.loads(rows[0]['detail']); validate(cfg); cfg['enabled']=False; cfg['public_message']=old['public_message']
        await self.bot.db.save_settings(i.guild_id,cfg,revision,i.user.id)
        await reply(i,'Settings restored and paused. Discord objects and the deposit ledger were not rolled back.')

    @app_commands.command(description='Review a member and optionally resume roles after a manual override')
    @app_commands.default_permissions(manage_guild=True)
    async def review_member(self,i:discord.Interaction,member:discord.Member,resume_automation:bool=False):
        await authorized(self.bot,i,True)
        if resume_automation:
            await self.bot.db.execute("UPDATE role_grants SET state='removed' WHERE guild=? AND user=? AND state='manual'",(i.guild_id,member.id))
            await self.bot.db.enqueue(i.guild_id,member.id); await self.bot.db.audit(i.guild_id,i.user.id,'resume_member',str(member.id))
        data={'links':await self.bot.db.links(i.guild_id,member.id),'snapshot':await self.bot.engine.status(i.guild_id,member.id),'role_grants':await self.bot.db.query('SELECT role,state FROM role_grants WHERE guild=? AND user=?',(i.guild_id,member.id))}
        await reply(i,file=attachment('verifier-member-review.json',data))

    @app_commands.command(description='Review an owned role baseline and explicitly accept a safe manual rename or unpin')
    @app_commands.default_permissions(manage_guild=True)
    async def review_role(self,i:discord.Interaction,rule_key:str,accept_changes:bool=False):
        cfg,_=await authorized(self.bot,i,True)
        if cfg['enabled']: raise ValueError('Pause Verifier before reviewing a role binding.')
        rule=next((r for r in cfg['rules'] if r['key']==rule_key),None)
        if not rule or not rule['role_id']: raise ValueError('Choose an existing bound rule key.')
        role=i.guild.get_role(rule['role_id']); baseline=safe_role(i.guild,role,cfg['member_role'])
        if role.name!=rule['name']: raise ValueError('Update the rule name to match the actual Discord role before accepting a rename.')
        key=f'verifier:role:{self.bot.user.id}:{rule_key}'
        async with self.bot.registry.lease(i.guild_id):
            entries=await self.bot.registry.entries(i.guild_id); old=entries.get(key)
            if not old or old['owner']!=f'bot:{self.bot.user.id}' or old['object_id']!=role.id or old['state'] not in ('active','pinned'):
                raise ValueError('This is not the same owned active/pinned role. Use a new rule key for a new role; foreign ownership requires an explicit handoff.')
            if any(v['kind']=='role' and v['object_id']==role.id and k!=key for k,v in entries.items()): raise ValueError('Another resource claims this role. Resolve that ownership first.')
            if accept_changes:
                await self.bot.registry.ensure_lease(i.guild_id)
                await self.bot.registry.execute("UPDATE resources SET baseline=?,desired=?,state='active' WHERE guild=? AND key=? AND owner=? AND object_id=?",(canonical(baseline),canonical(baseline),i.guild_id,key,f'bot:{self.bot.user.id}',role.id))
                await self.bot.db.audit(i.guild_id,i.user.id,'role_baseline_accept',canonical({'key':rule_key,'previous':old,'current':baseline}))
        await reply(i,'Baseline '+('accepted; run Doctor before reactivation.' if accept_changes else 'review only; use accept_changes:true after inspecting the attachment.'),file=attachment('verifier-role-baseline.json',{'previous':old,'current':baseline}))

    @app_commands.command(description='Resolve an uncertain rip send after reviewing the destination channel')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(decision=[app_commands.Choice(name='Retry sending',value='retry'),app_commands.Choice(name='Mark an existing message as sent',value='sent'),app_commands.Choice(name='Discard this announcement',value='discard')])
    async def review_delivery(self,i:discord.Interaction,event_id:str,decision:app_commands.Choice[str],message_id:str=''):
        cfg,_=await authorized(self.bot,i,True)
        rows=await self.bot.db.query('SELECT * FROM deliveries WHERE guild=? AND event_id=?',(i.guild_id,event_id))
        if not rows or rows[0]['state']!='review': raise ValueError('Only a delivery in review state accepts this action.')
        state={'retry':'pending','sent':'sent','discard':'discarded'}[decision.value]; mid=0
        if state=='sent':
            channel=i.guild.get_channel(cfg['rip_channel'])
            mid=int(message_id); message=await channel.fetch_message(mid)
            expected='Event: '+json.loads(rows[0]['payload'])['id']
            if message.author.id!=self.bot.user.id or not any(e.footer.text==expected for e in message.embeds): raise ValueError('The existing message does not match this rip event.')
        await self.bot.db.execute('UPDATE deliveries SET state=?,message=?,next_run=0 WHERE guild=? AND event_id=?',(state,mid,i.guild_id,event_id))
        await self.bot.db.audit(i.guild_id,i.user.id,'delivery_review',event_id+'; '+state)
        await reply(i,'Delivery review saved: '+state)

    @app_commands.command(description='Reset a missing connection panel after reviewing the channel')
    @app_commands.default_permissions(manage_guild=True)
    async def reset_panel(self,i:discord.Interaction):
        cfg,revision=await authorized(self.bot,i,True)
        cfg['public_message']=0; await self.bot.db.save_settings(i.guild_id,cfg,revision,i.user.id)
        await reply(i,'The panel binding was reset. Use Publish connection panel to create its replacement.')
