from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path

import discord
from aiohttp import web
from discord.ext import commands

from .auth import Linking
from .backups import create_backup
from .config import safe_url
from .coordination import Registry
from .engine import Engine,role_plan
from .member_ui import MemberPanel
from .network import Http
from .operations import Reporter, safe_role
from .storage import Store,Conflict
from .web import build_app
from . import __version__


def notify(value):
    path=os.getenv('NOTIFY_SOCKET')
    if not path: return
    if path.startswith('@'): path='\0'+path[1:]
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock: sock.connect(path); sock.send(value.encode())
    except OSError: pass


class Verifier(commands.Bot):
    def __init__(self,env=None):
        self.env=dict(os.environ if env is None else env)
        self.env.setdefault('DATABASE_PATH','data/verifier.sqlite3'); self.env.setdefault('COORDINATION_PATH','data/coordination.sqlite3'); self.env.setdefault('BACKUP_PATH','data/backups')
        intents=discord.Intents.default(); intents.members=True; intents.message_content=False
        super().__init__(command_prefix=commands.when_mentioned,intents=intents,allowed_mentions=discord.AllowedMentions.none())
        self.db=Store(self.env['DATABASE_PATH']); self.registry=Registry(self.env['COORDINATION_PATH']); self.http=Http(lambda:setattr(self,'worker_tick',time.monotonic()))
        self.linking=Linking(self.db,self.env.get('PUBLIC_URL','https://verify.example.com')); self.engine=Engine(self.db,self.http,self.env)
        self.reporter=Reporter(self); self.tasks=[]; self.runner=None; self.started=time.monotonic(); self.worker_tick=time.monotonic(); self.backup_day=''; self.bot_ready=False
        from .commands import VerifierCommands
        self.tree.add_command(VerifierCommands(self))
        self.tree.on_error=self.command_error

    async def setup_hook(self):
        await self.db.open(); await self.registry.open(); await self.http.open()
        await self.db.execute("UPDATE deliveries SET state='review' WHERE state='sending'")
        self.add_view(MemberPanel(self))
        app=build_app(self.db,self.linking,self.env,self.web_health)
        self.runner=web.AppRunner(app,access_log=None); await self.runner.setup()
        host=self.env.get('WEB_HOST','127.0.0.1')
        if host not in ('127.0.0.1','::1'): raise ValueError('WEB_HOST must be loopback. Serve HTTPS through nginx.')
        await web.TCPSite(self.runner,host,int(self.env.get('WEB_PORT','8092'))).start()
        test_id=self.env.get('TEST_GUILD_ID')
        if test_id:
            guild=discord.Object(id=int(test_id)); self.tree.copy_global_to(guild=guild); await self.tree.sync(guild=guild)
        else: await self.tree.sync()
        self.tasks=[asyncio.create_task(self.worker(),name='verifier-worker'),asyncio.create_task(self.heartbeat(),name='verifier-heartbeat')]

    async def on_ready(self):
        self.bot_ready=True; notify('READY=1\nSTATUS=Ripcars Verifier connected')
        logging.getLogger('ripcars.verifier').info('Online version %s in %s server(s)',__version__,len(self.guilds))

    async def on_disconnect(self): self.bot_ready=False
    async def on_resumed(self): self.bot_ready=True

    async def command_error(self,interaction,error):
        from .operations import reply
        original=getattr(error,'original',error)
        if isinstance(original,ValueError): await reply(interaction,str(original)); return
        error_id=await self.reporter.error(interaction.guild,'slash command',original)
        await reply(interaction,'This action failed. Error ID: '+error_id+'. Run /verifier doctor.')

    async def web_health(self):
        rows=await self.db.query('PRAGMA quick_check')
        db_ok=rows[0].get('quick_check')=='ok'
        healthy=db_ok and self.bot_ready and all(not t.done() for t in self.tasks) and time.monotonic()-self.worker_tick<600
        return {'ok':healthy,'version':__version__,'database':db_ok,'discord':self.bot_ready,'worker':all(not t.done() for t in self.tasks)}

    async def heartbeat(self):
        while True:
            if self.tasks and all(not t.done() for t in self.tasks) and time.monotonic()-self.worker_tick<180: notify('WATCHDOG=1')
            await asyncio.sleep(20)

    async def create_roles(self,guild,actor,revision):
        cfg,current=await self.db.settings(guild.id)
        if revision!=current: raise Conflict('Settings changed. Reopen the panel before creating roles.')
        if cfg['enabled']: raise ValueError('Pause Verifier before changing role bindings.')
        if not guild.me.guild_permissions.manage_roles: raise ValueError('Give Verifier Manage Roles first.')
        async with self.registry.lease(guild.id):
            for rule in cfg['rules']:
                role=guild.get_role(rule['role_id']) if rule['role_id'] else None
                if not role:
                    if rule['role_id']: raise Conflict('A bound role is missing. Review it; it is not recreated automatically.')
                    if any(r.name==rule['name'] for r in guild.roles): raise Conflict('A role with this name exists. Enter its ID to adopt it explicitly.')
                    await self.registry.ensure_lease(guild.id)
                    role=await guild.create_role(name=rule['name'],permissions=discord.Permissions.none(),reason='Rip Cars Verifier qualifying role setup')
                    rule['role_id']=role.id
                    # Save immediately, so a failed later step does not create a second role on retry.
                    current=await self.db.save_settings(guild.id,cfg,current,actor)
                if role.name!=rule['name']: raise Conflict('Bound role name differs from the rule. Review the rule before adoption.')
                baseline=safe_role(guild,role,cfg['member_role'])
                await self.registry.bind_role(guild.id,role.id,self.user.id,rule['key'],baseline)
        await self.db.audit(guild.id,actor,'role_setup','Created/bound qualifying roles only.')

    async def prepare_channels(self,guild,actor):
        cfg,revision=await self.db.settings(guild.id)
        if cfg['enabled']: raise ValueError('Pause Verifier before creating its channels.')
        member=guild.get_role(cfg['member_role'])
        dangerous=('administrator','manage_guild','manage_roles','manage_channels','manage_messages','ban_members','kick_members','moderate_members')
        if not member or member.is_default() or member.managed or any(getattr(member.permissions,k) for k in dangerous): raise ValueError('Choose the ordinary Gate member role without staff permissions first.')
        if not guild.me.guild_permissions.manage_channels: raise ValueError('Give Verifier Manage Channels for this optional setup action. You can remove it afterwards.')
        ordinary=discord.PermissionOverwrite(view_channel=True,read_message_history=True,use_application_commands=True,send_messages=False,add_reactions=False,create_public_threads=False,create_private_threads=False,send_messages_in_threads=False)
        bot_access=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=True,embed_links=True,attach_files=True)
        async with self.registry.lease(guild.id):
            for key,name,private in (('verification_channel','ripcars-connect',False),('rip_channel','ripcars-pulls',False),('log_channel','verifier-log',True)):
                if cfg[key]: continue
                if any(c.name==name for c in guild.channels): raise Conflict('A '+name+' channel exists. Select it explicitly instead of creating a duplicate.')
                overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),member:discord.PermissionOverwrite(view_channel=not private,read_message_history=True,send_messages=False),guild.me:bot_access}
                if not private: overwrites[member]=ordinary
                for role in guild.roles:
                    if role.permissions.manage_messages and not role.permissions.administrator:
                        overwrites[role]=discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=True)
                await self.registry.ensure_lease(guild.id)
                channel=await guild.create_text_channel(name,overwrites=overwrites,reason='Rip Cars Verifier optional channel setup')
                cfg[key]=channel.id
                revision=await self.db.save_settings(guild.id,cfg,revision,actor)
                await self.registry.bind_channel(guild.id,channel.id,self.user.id,key)
        await self.db.audit(guild.id,actor,'channel_setup','Created unset verifier channels only; existing channels preserved.')

    async def publish(self,guild,actor):
        cfg,revision=await self.db.settings(guild.id); channel=guild.get_channel(cfg['verification_channel'])
        if not isinstance(channel,discord.TextChannel): raise ValueError('Choose the connection panel text channel first.')
        perms=channel.permissions_for(guild.me)
        if not all(getattr(perms,x) for x in ('view_channel','read_message_history','send_messages','embed_links')): raise ValueError('Verifier cannot post in the selected channel. Fix its channel permissions.')
        embed=discord.Embed(title=cfg['texts']['title'],description=cfg['texts']['description'],color=cfg['color'])
        async with self.registry.lease(guild.id):
            view=MemberPanel(self)
            # The member view keeps persistent IDs, but optional buttons are hidden when skipped.
            for child in list(view.children):
                if child.custom_id=='rcv:wallet:v1' and not cfg['wallet_linking']: view.remove_item(child)
                if child.custom_id=='rcv:account:v1' and not cfg['account_linking']: view.remove_item(child)
            if cfg['public_message']:
                try: message=await channel.fetch_message(cfg['public_message'])
                except discord.NotFound: raise Conflict('The panel was deleted. Reset its message ID explicitly after reviewing the channel.') from None
                if message.author.id!=self.user.id: raise Conflict('The saved panel belongs to another bot.')
                await self.registry.ensure_lease(guild.id)
                await message.edit(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
            else:
                await self.registry.ensure_lease(guild.id)
                message=await channel.send(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
                cfg['public_message']=message.id
                await self.db.save_settings(guild.id,cfg,revision,actor)
        await self.db.audit(guild.id,actor,'panel_publish',str(message.id))

    async def apply_roles(self,guild,member,cfg,snapshot,revision):
        if cfg['member_role'] not in {r.id for r in member.roles}: return
        rows=await self.db.query('SELECT * FROM role_grants WHERE guild=? AND user=?',(guild.id,member.id))
        current={r.id for r in member.roles}; manual=set()
        for row in rows:
            if row['state']=='active' and row['role'] not in current:
                await self.db.execute("UPDATE role_grants SET state='manual' WHERE guild=? AND user=? AND role=?",(guild.id,member.id,row['role']))
                manual.add(row['role'])
            if row['state']=='manual': manual.add(row['role'])
        owned={r['role'] for r in rows if r['state'] in ('active','pending_add','pending_remove')}
        additions,removals,_=role_plan(cfg,snapshot,current,owned)
        async with self.registry.lease(guild.id):
            for rid,state,method in [(r,'pending_add',member.add_roles) for r in additions]+[(r,'pending_remove',member.remove_roles) for r in removals]:
                if rid in manual: continue
                latest,live_revision=await self.db.settings(guild.id)
                if not latest['enabled'] or live_revision!=revision: raise Conflict('Settings changed before role application.')
                rule=next(r for r in cfg['rules'] if r['role_id']==rid)
                role=guild.get_role(rid); baseline=safe_role(guild,role,cfg['member_role'])
                await self.registry.check_role(guild.id,rid,self.user.id,rule['key'],baseline)
                records=await self.registry.entries(guild.id)
                if f'verifier:role:{self.user.id}:{rule["key"]}' not in records: raise Conflict('Role has not been adopted. Use Create and bind qualifying roles first.')
                await self.registry.ensure_lease(guild.id)
                await self.db.execute('INSERT OR REPLACE INTO role_grants VALUES(?,?,?,?)',(guild.id,member.id,rid,state))
                await method(role,reason='Rip Cars verified role check',atomic=True)
                await self.db.execute('UPDATE role_grants SET state=? WHERE guild=? AND user=? AND role=?',('active' if state=='pending_add' else 'removed',guild.id,member.id,rid))
                await self.db.audit(guild.id,0,'role_update',f'{member.id}; {rid}; {state}')

    async def deliver(self,row):
        guild=self.get_guild(row['guild'])
        if not guild:
            await self.db.execute('UPDATE deliveries SET next_run=? WHERE guild=? AND event_id=?',(time.time()+3600,row['guild'],row['event_id'])); return
        cfg,_=await self.db.settings(guild.id)
        if not cfg['rip_feed']:
            await self.db.execute('UPDATE deliveries SET next_run=? WHERE guild=? AND event_id=?',(time.time()+3600,row['guild'],row['event_id'])); return
        channel=guild.get_channel(cfg['rip_channel'])
        if not isinstance(channel,discord.TextChannel): raise ValueError('Rip destination channel is missing.')
        event=json.loads(row['payload']); embed=discord.Embed(title=cfg['texts']['rip_title'],description=discord.utils.escape_markdown(event['car_name']),color=cfg['color'])
        embed.add_field(name='Asset',value=discord.utils.escape_markdown(event['asset_id'])[:1024],inline=False)
        if event.get('asset_url'): safe_url(event['asset_url']); embed.url=event['asset_url']
        if event.get('image_url'): safe_url(event['image_url']); embed.set_image(url=event['image_url'])
        embed.set_footer(text='Event: '+event['id'])
        await self.db.execute("UPDATE deliveries SET state='sending',attempts=attempts+1 WHERE guild=? AND event_id=?",(guild.id,row['event_id']))
        try:
            message=await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await self.db.execute("UPDATE deliveries SET state='pending',next_run=? WHERE guild=? AND event_id=?",(time.time()+300,guild.id,row['event_id'])); raise
        except BaseException:
            await self.db.execute("UPDATE deliveries SET state='review' WHERE guild=? AND event_id=?",(guild.id,row['event_id'])); raise
        await self.db.execute("UPDATE deliveries SET state='sent',message=? WHERE guild=? AND event_id=?",(message.id,guild.id,row['event_id']))

    async def worker(self):
        await self.wait_until_ready()
        while True:
            self.worker_tick=time.monotonic()
            try:
                jobs=await self.db.query('SELECT * FROM jobs WHERE next_run<=? ORDER BY next_run LIMIT 30',(time.time(),))
                for job in jobs:
                    guild=self.get_guild(job['guild'])
                    if not guild:
                        await self.db.execute('UPDATE jobs SET next_run=? WHERE guild=? AND user=?',(time.time()+3600,job['guild'],job['user'])); continue
                    cfg,_=await self.db.settings(guild.id)
                    if not cfg['enabled']:
                        await self.db.execute('UPDATE jobs SET next_run=? WHERE guild=? AND user=?',(time.time()+3600,guild.id,job['user'])); continue
                    try:
                        member=guild.get_member(job['user']) or await guild.fetch_member(job['user'])
                        snapshot,cfg,revision=await self.engine.refresh(guild.id,member.id)
                        await self.apply_roles(guild,member,cfg,snapshot,revision)
                        incomplete=any(not v for v in snapshot['complete'].values()) or bool(snapshot.get('incomplete_rules'))
                        await self.db.execute('UPDATE jobs SET next_run=?,failures=0 WHERE guild=? AND user=?',(time.time()+(cfg['retry_seconds'] if incomplete else cfg['sync_seconds']),guild.id,member.id))
                    except discord.NotFound:
                        await self.db.execute('DELETE FROM jobs WHERE guild=? AND user=?',(guild.id,job['user']))
                    except Exception as exc:
                        await self.reporter.error(guild,'member sync',exc)
                        delay=min(3600,cfg['retry_seconds']*2**min(job['failures'],4))
                        await self.db.execute('UPDATE jobs SET next_run=?,failures=failures+1 WHERE guild=? AND user=?',(time.time()+delay,guild.id,job['user']))
                    self.worker_tick=time.monotonic()
                for row in await self.db.query("SELECT * FROM deliveries WHERE state='pending' AND next_run<=? ORDER BY created LIMIT 10",(time.time(),)):
                    try: await self.deliver(row)
                    except Exception as exc:
                        await self.db.execute("UPDATE deliveries SET next_run=? WHERE guild=? AND event_id=? AND state='pending'",(time.time()+300,row['guild'],row['event_id']))
                        await self.reporter.error(self.get_guild(row['guild']),'rip announcement',exc)
                day=time.strftime('%Y%m%d',time.gmtime())
                if day!=self.backup_day and len(self.env.get('BACKUP_PASSWORD',''))>=20:
                    await create_backup(self.db,self.env['BACKUP_PATH'],self.env['BACKUP_PASSWORD']); self.backup_day=day
                    files=sorted(Path(self.env['BACKUP_PATH']).glob('*.rcvbackup'),key=lambda p:p.stat().st_mtime,reverse=True)
                    for old in files[14:]: old.unlink()
                await self.db.execute('DELETE FROM sessions WHERE expires<?',(time.time()-86400,))
                await self.db.execute('DELETE FROM webhook_ids WHERE expires<?',(time.time(),))
            except Exception as exc: await self.reporter.error(None,'worker',exc)
            await asyncio.sleep(15)

    async def close(self):
        for task in self.tasks: task.cancel()
        for task in self.tasks:
            try: await task
            except asyncio.CancelledError: pass
            except Exception: pass
        if self.runner: await self.runner.cleanup()
        await self.http.close(); await super().close()
