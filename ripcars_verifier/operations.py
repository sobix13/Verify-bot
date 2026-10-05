from __future__ import annotations

import io
import json
import logging
import re
import secrets
import time

import discord

from .engine import readiness


def admin(member):
    return isinstance(member,discord.Member) and not member.bot and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)


async def authorized(bot,interaction,require_admin=False):
    if not interaction.guild or not isinstance(interaction.user,discord.Member) or interaction.user.bot:
        raise ValueError('Use this command in your Discord server.')
    cfg,revision=await bot.db.settings(interaction.guild_id)
    if require_admin:
        if not admin(interaction.user): raise ValueError('This action requires Manage Server or Administrator.')
    elif cfg['member_role'] not in {r.id for r in interaction.user.roles} and not admin(interaction.user):
        raise ValueError('Complete Gate entry before connecting a wallet or account.')
    return cfg,revision


async def reply(interaction,content=None,**kwargs):
    kwargs.setdefault('ephemeral',True); kwargs.setdefault('allowed_mentions',discord.AllowedMentions.none())
    if interaction.response.is_done(): return await interaction.followup.send(content,**kwargs)
    return await interaction.response.send_message(content,**kwargs)


def attachment(name,value):
    raw=value if isinstance(value,bytes) else json.dumps(value,indent=2,allow_nan=False).encode()
    if len(raw)>7*1024*1024: raise ValueError('This export exceeds the safe Discord attachment size. Use the encrypted VPS backup file.')
    return discord.File(io.BytesIO(raw),filename=name)


def private_channel(guild,channel):
    if not isinstance(channel,discord.TextChannel): return False
    for role in guild.roles:
        if role.is_bot_managed() or role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_messages: continue
        if channel.permissions_for(role).view_channel: return False
    for target,over in channel.overwrites.items():
        if isinstance(target,discord.Object) and over.view_channel is True: return False
        if isinstance(target,discord.Member) and over.view_channel is True and target.id!=guild.me.id and not admin(target) and not target.guild_permissions.manage_messages:
            return False
    return True


def safe_role(guild,role,member_role):
    if not role or role.is_default() or role.managed or role.id==member_role or role.name.casefold() in ('og','ripper','rippers','admin','team','moderator') or role.permissions.value!=0:
        raise ValueError('Use a dedicated qualifying role with zero server permission bits.')
    if not guild.me.guild_permissions.manage_roles or role>=guild.me.top_role:
        raise ValueError('Give Verifier Manage Roles and place its bot role above every qualifying role.')
    return {'name':role.name,'permissions':role.permissions.value}


async def doctor(bot,guild,cfg=None):
    cfg=cfg or (await bot.db.settings(guild.id))[0]; blockers=readiness(cfg,bot.env); notes=[]
    member=guild.get_role(cfg['member_role'])
    dangerous=('administrator','manage_guild','manage_roles','manage_channels','manage_messages','ban_members','kick_members','moderate_members')
    if not member or member.managed or member.is_default() or any(getattr(member.permissions,k) for k in dangerous): blockers.append('Select the ordinary Gate member role without staff permissions.')
    if not bot.intents.members: blockers.append('Enable Server Members Intent.')
    for key in ('verification_channel','log_channel','rip_channel'):
        if key=='rip_channel' and not cfg['rip_feed']: continue
        channel=guild.get_channel(cfg[key])
        if not isinstance(channel,discord.TextChannel): blockers.append(key+': choose an existing text channel.'); continue
        flags=channel.permissions_for(guild.me)
        needed=['view_channel','read_message_history','send_messages','embed_links']
        if key=='log_channel': needed.append('attach_files')
        missing=[f for f in needed if not getattr(flags,f)]
        if missing: blockers.append(key+': missing '+', '.join(missing))
        if key=='log_channel' and not private_channel(guild,channel): blockers.append('Log channel must be staff-only.')
        if key!='log_channel' and member:
            access=channel.permissions_for(member)
            if not access.view_channel or not access.read_message_history: blockers.append(key+': Gate members need View Channel and Read Message History.')
    for rule in cfg['rules']:
        if rule['role_id']:
            try:
                role=guild.get_role(rule['role_id']); baseline=safe_role(guild,role,cfg['member_role'])
                await bot.registry.check_role(guild.id,role.id,bot.user.id,rule['key'],baseline)
                if f'verifier:role:{bot.user.id}:{rule["key"]}' not in await bot.registry.entries(guild.id):
                    blockers.append(rule['key']+': run Create and bind qualifying roles before activation.')
            except ValueError as exc: blockers.append(rule['key']+': '+str(exc))
    if guild.me.guild_permissions.administrator: notes.append('The bot operates with Manage Roles and selected channel access; Administrator is unnecessary.')
    pending=await bot.db.query("SELECT COUNT(*) AS n FROM deliveries WHERE guild=? AND state='review'",(guild.id,))
    if pending[0]['n']: notes.append(f'{pending[0]["n"]} rip deliveries need review after an uncertain send.')
    checks=await bot.db.query("SELECT wallet,value FROM checkpoints WHERE guild=?",(guild.id,))
    if any(not json.loads(r['value']).get('complete') for r in checks): notes.append('Historical deposit backfill is in progress. Deposit-role changes wait for completeness.')
    notes.append('Wallet balances and assets refresh every '+str(cfg['sync_seconds']//3600)+' hour(s). Provider failures preserve the last known role state.')
    return blockers,notes


class Reporter:
    def __init__(self,bot): self.bot=bot; self.last={}
    def redact(self,value):
        result=str(value)
        for key in ('DISCORD_TOKEN','PLATFORM_API_KEY','PLATFORM_WEBHOOK_SECRET','CHAIN_WEBHOOK_SECRET','BACKUP_PASSWORD','RPC_URL','RPC_FALLBACK_URL','DAS_URL'):
            secret=self.bot.env.get(key,'')
            if len(secret)>8: result=result.replace(secret,'[redacted]')
        result=re.sub(r'(?i)(api-key|token|key|secret)=[^\s&]+',r'\1=[redacted]',result)
        return result[:1500]
    async def error(self,guild,area,exc):
        error_id='RCV-'+time.strftime('%y%m%d',time.gmtime())+'-'+secrets.token_hex(3).upper()
        detail=self.redact(exc)
        logging.getLogger('ripcars.verifier').error('%s %s %s: %s',error_id,area,type(exc).__name__,detail)
        gid=guild.id if guild else 0
        await self.bot.db.audit(gid,0,'error',f'{error_id}; {area}; {type(exc).__name__}; {detail}')
        if guild:
            cfg,_=await self.bot.db.settings(gid); channel=guild.get_channel(cfg['log_channel'])
            if channel and private_channel(guild,channel) and time.monotonic()-self.last.get(gid,-1e9)>=cfg['error_alert_seconds']:
                self.last[gid]=time.monotonic()
                embed=discord.Embed(title='Verifier needs attention',description=f'{error_id}\nArea: {area}\nType: {type(exc).__name__}\nOpen /verifier doctor for the next steps.',color=0x800020)
                try: await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException: pass
        return error_id
