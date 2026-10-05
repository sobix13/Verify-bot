from __future__ import annotations

import copy
import json
import re

import discord

from .config import canonical
from .operations import attachment,authorized,doctor,reply

SECTIONS={
 'overview':('Overview','Choose channels and data sources, review role rules, publish the connection panel, then run Doctor and activate. Each feature stays optional.'),
 'connections':('Connections','Wallet links use a signed, expiring message. Rip Cars account links require a team backend attestation. The public URL and credentials live in the VPS environment file.'),
 'sources':('Data sources','Choose the source for current token balance, assets and platform points. RPC is for SPL balances. DAS is for verified NFT collections. Platform reads the documented backend bridge.'),
 'deposits':('Deposit tracking','Choose one authoritative ledger source. RPC counts finalized SPL transfers into verified destinations. Add multiple programs and treasuries in the source list. Fees, withdrawals, buybacks and internal transfers do not earn deposit points.'),
 'roles':('Assets and role rules','Create or bind separate zero-permission qualifying roles. Rules use deposit points, platform points, token balance or assets filtered by ID, collection or exact attributes. OG stays manual.'),
 'feed':('Rip announcements','The team backend sends signed rip.opened events with a car name and optional image. Enable this only after selecting its destination channel. It is independent of role checks.'),
 'channels':('Channels and access','Choose existing text channels and the Gate member role. Channel choices save by numeric ID and remain selected. Use Gate to expose its holder channel after an explicit holder handoff.'),
 'operations':('Health and backups','Doctor explains activation blockers. Encrypted backups include data and settings, not environment secrets. Failed providers retry; incomplete data preserves role state.'),
 'texts':('Public messages','Edit the public connection title, description and rip announcement title. Publish again to update the existing bot message.'),
 'guides':('Guide and revisions','Use /verifier guide for the setup sequence. Export settings to review or version them. Import validates the full schema and pauses the verifier. Each saved change has an audit revision.'),
}


class AdminPanel(discord.ui.View):
    def __init__(self,bot,owner,cfg,revision,section='overview'):
        super().__init__(timeout=900); self.bot=bot; self.owner=owner; self.cfg=cfg; self.revision=revision; self.section=section
        menu=discord.ui.Select(placeholder='Choose a section',row=0,options=[discord.SelectOption(label=title,value=key,default=key==section,description=desc[:90]) for key,(title,desc) in SECTIONS.items()])
        async def navigate(i): await self.render(i,menu.values[0])
        menu.callback=navigate; self.add_item(menu)
        if section=='overview':
            self.button('Run Doctor',self.doctor,1); self.button('Publish connection panel',self.publish,1)
            self.button('Pause' if cfg['enabled'] else 'Activate',self.activate,2)
            self.button('Import Gate member role',self.import_gate,2)
            self.button('Create unset verifier channels',self.prepare_channels,3)
        elif section=='connections':
            self.toggle('wallet_linking','Wallet linking',1); self.toggle('account_linking','Account linking',1)
            self.button('Edit account connect URL',lambda i:self.modal(i,'platform_connect_url','Team account connect URL'),2)
            self.button('Edit wallet limit',lambda i:self.modal(i,'max_wallets','Maximum wallets per member'),2)
        elif section=='sources':
            self.mode('balance_mode',['off','rpc','platform'],'Token balance source',1)
            self.mode('asset_mode',['off','das','platform'],'Asset source',2)
            self.toggle('platform_points','Platform points',3)
            self.button('Edit $CARS mint',lambda i:self.modal(i,'mint','$CARS mint'),3)
            self.button('Edit verified collections',lambda i:self.modal(i,'collections','Verified collection IDs (JSON)'),4)
        elif section=='deposits':
            self.mode('deposit_mode',['off','rpc','platform'],'Deposit ledger source',1)
            self.button('Edit payment sources',lambda i:self.modal(i,'sources','Payment sources (JSON)'),2)
            self.button('Edit history start',lambda i:self.modal(i,'history_start','History start (Unix seconds, 0 = all)'),2)
            self.button('Add or edit a payment source',self.source_modal,2)
            self.button('Review ledger',self.ledger,3); self.button('Restart historical scan',self.backfill,3)
        elif section=='roles':
            self.button('Add or edit a role rule',self.rule_modal,1)
            self.button('Review role rules',self.rules,1)
            self.button('Create and bind qualifying roles',self.create_roles,2)
            self.button('Export settings',self.export,2)
        elif section=='feed':
            self.toggle('rip_feed','Rip announcements',1); self.toggle('chain_webhook','Chain webhook refresh hints',1)
            self.button('Review pending announcements',self.deliveries,2)
        elif section=='channels':
            for row,key,label in [(1,'member_role','Gate member role'),(2,'verification_channel','Connection panel channel'),(3,'rip_channel','Rip announcement channel'),(4,'log_channel','Staff log channel')]:
                control=discord.ui.RoleSelect if key=='member_role' else discord.ui.ChannelSelect
                kwargs={'placeholder':label,'row':row,'min_values':1,'max_values':1}
                if key!='member_role': kwargs['channel_types']=[discord.ChannelType.text]
                if cfg[key]: kwargs['default_values']=[discord.Object(id=cfg[key])]
                choice=control(**kwargs)
                async def selected(i,choice=choice,key=key):
                    await self.save(i,key,choice.values[0].id)
                choice.callback=selected; self.add_item(choice)
        elif section=='operations':
            self.button('Run Doctor',self.doctor,1); self.button('Create encrypted backup',self.backup,1)
            self.button('Refresh all connected members',self.refresh_all,2)
            self.button('Edit check interval',lambda i:self.modal(i,'sync_seconds','Check interval in seconds'),2)
            self.button('Export settings',self.export,3); self.button('Recent errors',self.errors,3)
            self.button('Probe live providers',self.probe,4)
        elif section=='texts':
            for row,key in enumerate(('title','description','rip_title'),1):
                self.button('Edit '+key.replace('_',' '),lambda i,key=key:self.text_modal(i,key),row)
            self.button('Update published panel',self.publish,4)
        elif section=='guides':
            self.button('Setup guide',self.guide,1); self.button('Export settings',self.export,1)
            self.button('Settings revisions',self.revisions,2)

    async def interaction_check(self,i):
        if i.user.id!=self.owner:
            await reply(i,'This panel belongs to another admin. Open /verifier panel for your own session.'); return False
        await authorized(self.bot,i,True); return True

    def button(self,label,callback,row):
        item=discord.ui.Button(label=label,row=row,style=discord.ButtonStyle.secondary)
        item.callback=callback; self.add_item(item)

    def toggle(self,key,label,row):
        async def change(i): await self.save(i,key,not self.cfg[key])
        self.button(label+(': on' if self.cfg[key] else ': off'),change,row)

    def mode(self,key,values,label,row):
        choice=discord.ui.Select(placeholder=label,row=row,options=[discord.SelectOption(label=label+': '+v,value=v,default=v==self.cfg[key]) for v in values])
        async def selected(i): await self.save(i,key,choice.values[0])
        choice.callback=selected; self.add_item(choice)

    def embed(self):
        title,description=SECTIONS[self.section]
        result=discord.Embed(title='Rip Cars Verifier · '+title,description=description,color=self.cfg['color'])
        result.add_field(name='Status',value=('Active' if self.cfg['enabled'] else 'Paused')+f' · settings revision {self.revision}',inline=False)
        if self.section=='overview':
            result.add_field(name='Methods',value=f'Token balance: {self.cfg["balance_mode"]}\nAssets: {self.cfg["asset_mode"]}\nDeposits: {self.cfg["deposit_mode"]}\nRole rules: {len(self.cfg["rules"])}',inline=False)
        if self.section=='channels':
            result.add_field(name='Selected IDs',value='\n'.join(f'{k}: {self.cfg[k] or "not selected"}' for k in ('member_role','verification_channel','rip_channel','log_channel')),inline=False)
        return result

    async def render(self,i,section=None):
        if not i.response.is_done(): await i.response.defer(ephemeral=True)
        cfg,revision=await authorized(self.bot,i,True)
        view=AdminPanel(self.bot,self.owner,cfg,revision,section or self.section)
        await i.edit_original_response(embed=view.embed(),view=view)

    async def save(self,i,key,value):
        await authorized(self.bot,i,True)
        cfg=copy.deepcopy(self.cfg); cfg[key]=value
        # A source change pauses live role application until the admin reviews Doctor.
        if key in ('member_role','verification_channel','log_channel','mint','sources','collections','rules','balance_mode','asset_mode','deposit_mode','platform_points','platform_connect_url'): cfg['enabled']=False
        await self.bot.db.save_settings(i.guild_id,cfg,self.revision,i.user.id)
        await self.render(i)

    async def modal(self,i,key,label):
        value=self.cfg[key]; value='\n'.join(value) if key=='collections' else canonical(value) if isinstance(value,(list,dict)) else str(value)
        if len(value)>4000:
            return await reply(i,'This setting exceeds one Discord form. Export settings, edit its JSON, then use /verifier import_settings with the file.')
        await i.response.send_modal(EditModal(self,key,label,value))
    async def text_modal(self,i,key): await i.response.send_modal(EditModal(self,'texts.'+key,'Public '+key,self.cfg['texts'][key]))

    async def doctor(self,i):
        await i.response.defer(ephemeral=True); cfg,_=await authorized(self.bot,i,True)
        blockers,notes=await doctor(self.bot,i.guild,cfg)
        value=('Ready for activation.' if not blockers else 'Fix before activating:\n'+'\n'.join('- '+b for b in blockers))+'\n\n'+'\n'.join(notes)
        await reply(i,value[:1900],file=attachment('verifier-doctor.json',{'blockers':blockers,'notes':notes}))
    async def activate(self,i):
        await i.response.defer(ephemeral=True)
        cfg,_=await authorized(self.bot,i,True)
        if not cfg['enabled']:
            blockers,_=await doctor(self.bot,i.guild,cfg)
            if blockers: return await reply(i,'Activation is blocked:\n'+'\n'.join(blockers)[:1750])
            from .probes import probe_sources
            checks=await probe_sources(self.bot,i.guild_id,cfg)
            failed=[c['source']+': '+c['detail'] for c in checks if not c['ok']]
            if failed: return await reply(i,'Provider checks blocked activation:\n'+'\n'.join(failed)[:1700])
        await self.save(i,'enabled',not cfg['enabled'])
    async def probe(self,i):
        await i.response.defer(ephemeral=True); cfg,_=await authorized(self.bot,i,True)
        from .probes import probe_sources
        checks=await probe_sources(self.bot,i.guild_id,cfg)
        await reply(i,file=attachment('verifier-provider-checks.json',checks))
    async def publish(self,i):
        await i.response.defer(ephemeral=True); await self.bot.publish(i.guild,i.user.id); await self.render(i)
    async def import_gate(self,i):
        role=await self.bot.registry.gate_member(i.guild_id)
        if not role: return await reply(i,'No active Gate member binding was found. Select the role under Channels and access.')
        await self.save(i,'member_role',role)
    async def create_roles(self,i):
        await i.response.defer(ephemeral=True); await self.bot.create_roles(i.guild,i.user.id,self.revision); await self.render(i)
    async def prepare_channels(self,i):
        await i.response.defer(ephemeral=True); await self.bot.prepare_channels(i.guild,i.user.id); await self.render(i)
    async def rule_modal(self,i): await i.response.send_modal(RuleModal(self))
    async def source_modal(self,i): await i.response.send_modal(SourceModal(self))
    async def rules(self,i): await reply(i,file=attachment('verifier-role-rules.json',self.cfg['rules']))
    async def export(self,i): await reply(i,file=attachment('verifier-settings.json',self.cfg))
    async def backup(self,i):
        await i.response.defer(ephemeral=True)
        from .backups import create_backup
        path=await create_backup(self.bot.db,self.bot.env['BACKUP_PATH'],self.bot.env.get('BACKUP_PASSWORD',''),i.guild_id)
        await self.bot.db.audit(i.guild_id,i.user.id,'backup_export',path.name)
        await reply(i,'Encrypted verifier backup. Restore it offline using scripts/restore_backup.py.',file=attachment(path.name,path.read_bytes()))
    async def ledger(self,i):
        rows=await self.bot.db.query('SELECT event_id,user,usd_micros,source,created FROM ledger WHERE guild=? ORDER BY created DESC LIMIT 2000',(i.guild_id,))
        await reply(i,'Latest 2,000 deposits. Full history is in the encrypted backup.',file=attachment('verifier-deposits.json',rows))
    async def backfill(self,i):
        await self.bot.db.execute('DELETE FROM checkpoints WHERE guild=?',(i.guild_id,)); await self.refresh_all(i)
    async def refresh_all(self,i):
        await self.bot.db.execute('UPDATE jobs SET next_run=0 WHERE guild=?',(i.guild_id,)); await reply(i,'All connected members are queued. Provider rate limits still apply.')
    async def deliveries(self,i):
        rows=await self.bot.db.query('SELECT event_id,state,message,attempts,created FROM deliveries WHERE guild=? ORDER BY created DESC LIMIT 100',(i.guild_id,))
        await reply(i,file=attachment('verifier-rip-deliveries.json',rows))
    async def errors(self,i):
        rows=await self.bot.db.query("SELECT action,detail,created FROM audit WHERE guild=? AND action='error' ORDER BY id DESC LIMIT 30",(i.guild_id,))
        await reply(i,file=attachment('verifier-errors.json',rows))
    async def revisions(self,i):
        rows=await self.bot.db.query("SELECT id,actor,created FROM audit WHERE guild=? AND action='settings' ORDER BY id DESC LIMIT 30",(i.guild_id,))
        await reply(i,file=attachment('verifier-revisions.json',rows))
    async def guide(self,i):
        await reply(i,'1. Deploy Verifier and its HTTPS link page.\n2. Select the Gate role and text channels.\n3. Enable only the connections and data sources you need.\n4. Add verified mints, collections and deposit sources.\n5. Define and create/bind separate qualifying roles.\n6. Publish the connection panel.\n7. Run Doctor, fix its blockers, and activate.\n8. Test with a non-admin member.\n\nUse /verifier health, /verifier doctor, /verifier import_settings and /verifier review_delivery for operations. Full install and API contracts are included in the release guides.')
    async def on_error(self,i,error,item):
        await reply(i,str(error) if isinstance(error,ValueError) else 'This action failed. Its error ID is in the private log. Reopen /verifier panel before retrying.')
        if not isinstance(error,ValueError): await self.bot.reporter.error(i.guild,'admin panel',error)


class EditModal(discord.ui.Modal):
    def __init__(self,panel,key,label,value):
        super().__init__(title=label[:45]); self.panel=panel; self.key=key
        self.value=discord.ui.TextInput(label=label[:45],default=value,required=False,max_length=4000,style=discord.TextStyle.paragraph)
        self.add_item(self.value)
    async def on_submit(self,i):
        await authorized(self.panel.bot,i,True)
        if i.user.id!=self.panel.owner: raise ValueError('This form belongs to another admin.')
        key=self.key; text=self.value.value
        if key.startswith('texts.'):
            cfg=copy.deepcopy(self.panel.cfg); cfg['texts'][key.split('.')[1]]=text
            await self.panel.bot.db.save_settings(i.guild_id,cfg,self.panel.revision,i.user.id); return await self.panel.render(i)
        old=self.panel.cfg[key]
        value=([v for v in re.split(r'[\s,]+',text.strip()) if v] if key=='collections' and not text.lstrip().startswith('[') else json.loads(text)) if isinstance(old,(list,dict)) else int(text) if type(old) is int else text
        await self.panel.save(i,key,value)
    async def on_error(self,i,error): await reply(i,str(error) if isinstance(error,ValueError) else 'The form could not be saved. Reopen the panel and retry.')


class RuleModal(discord.ui.Modal):
    def __init__(self,panel):
        super().__init__(title='Add or edit a qualifying rule'); self.panel=panel
        self.key=discord.ui.TextInput(label='Unique rule key (reuse it to edit)',max_length=40)
        self.name=discord.ui.TextInput(label='Role name',max_length=100)
        self.metric=discord.ui.TextInput(label='Metric: deposit_points / token_balance / etc.',max_length=40)
        self.threshold=discord.ui.TextInput(label='Threshold',max_length=40)
        self.selector=discord.ui.TextInput(label='Selector JSON and optional existing role ID',default='{"selector":{},"role_id":0,"enabled":true}',style=discord.TextStyle.paragraph,max_length=2000)
        for item in (self.key,self.name,self.metric,self.threshold,self.selector): self.add_item(item)
    async def on_submit(self,i):
        await authorized(self.panel.bot,i,True)
        if i.user.id!=self.panel.owner: raise ValueError('This form belongs to another admin.')
        options=json.loads(self.selector.value)
        if not isinstance(options,dict) or not set(options)<={'selector','role_id','enabled'}: raise ValueError('Rule options contain unsupported fields.')
        rule={'key':self.key.value,'name':self.name.value,'kind':self.metric.value,'threshold':self.threshold.value,'selector':options.get('selector',{}),'role_id':options.get('role_id',0),'enabled':options.get('enabled',True)}
        rules=[r for r in self.panel.cfg['rules'] if r['key']!=rule['key']]+[rule]
        await self.panel.save(i,'rules',rules)
    async def on_error(self,i,error): await reply(i,str(error) if isinstance(error,ValueError) else 'The role rule could not be saved.')


class SourceModal(discord.ui.Modal):
    def __init__(self,panel):
        super().__init__(title='Add or edit a deposit source'); self.panel=panel
        self.key=discord.ui.TextInput(label='Source ID (reuse it to edit)',max_length=40,placeholder='payments')
        self.programs=discord.ui.TextInput(label='Program IDs (optional, one per line)',required=False,style=discord.TextStyle.paragraph,max_length=4000)
        self.owners=discord.ui.TextInput(label='Treasury owner addresses (one per line)',required=False,style=discord.TextStyle.paragraph,max_length=4000)
        self.accounts=discord.ui.TextInput(label='Destination token accounts (one per line)',required=False,style=discord.TextStyle.paragraph,max_length=4000)
        self.mints=discord.ui.TextInput(label='Payment mints: address,decimals,USD per token',placeholder='One accepted mint per line; verified addresses only',style=discord.TextStyle.paragraph,max_length=4000)
        for item in (self.key,self.programs,self.owners,self.accounts,self.mints): self.add_item(item)
    async def on_submit(self,i):
        await authorized(self.panel.bot,i,True)
        if i.user.id!=self.panel.owner: raise ValueError('This form belongs to another admin.')
        addresses=lambda text:[v for v in re.split(r'[\s,]+',text.strip()) if v]
        accepted={}
        for line in self.mints.value.splitlines():
            if not line.strip(): continue
            values=[v.strip() for v in line.split(',')]
            if len(values)!=3: raise ValueError('Each payment mint line must be address,decimals,USD-per-token, for example the verified USDC mint,6,1.')
            mint,places,price=values
            if mint in accepted: raise ValueError('This payment mint appears twice.')
            accepted[mint]={'decimals':int(places),'usd_per_token':price}
        data={'id':self.key.value,'program_ids':addresses(self.programs.value),'treasury_owners':addresses(self.owners.value),'token_accounts':addresses(self.accounts.value),'mints':accepted}
        sources=[s for s in self.panel.cfg['sources'] if s['id']!=data['id']]+[data]
        await self.panel.save(i,'sources',sources)
    async def on_error(self,i,error): await reply(i,str(error) if isinstance(error,ValueError) else 'The deposit source could not be saved.')
