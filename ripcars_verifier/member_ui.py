from __future__ import annotations

from urllib.parse import urlencode,urlsplit,urlunsplit

import discord

from .operations import authorized,reply


class MemberPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label='Connect wallet',style=discord.ButtonStyle.primary,custom_id='rcv:wallet:v1')
    async def wallet(self,interaction,button):
        cfg,_=await authorized(self.bot,interaction)
        if not cfg['wallet_linking']: raise ValueError('Wallet linking is paused.')
        token=await self.bot.linking.create(interaction.guild_id,interaction.user.id)
        view=discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label='Open wallet connection',url=self.bot.linking.origin+'/link#'+token))
        await reply(interaction,'This private link expires in ten minutes. Sign the message, then return here.',view=view)

    @discord.ui.button(label='Connect Rip Cars account',style=discord.ButtonStyle.secondary,custom_id='rcv:account:v1')
    async def account(self,interaction,button):
        cfg,_=await authorized(self.bot,interaction)
        if not cfg['account_linking'] or not cfg['platform_connect_url']: raise ValueError('Account linking is not configured yet.')
        token=await self.bot.linking.create(interaction.guild_id,interaction.user.id,'account')
        p=urlsplit(cfg['platform_connect_url']); query=p.query+('&' if p.query else '')+urlencode({'discord_link_token':token})
        view=discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label='Open Rip Cars account connection',url=urlunsplit((p.scheme,p.netloc,p.path,query,''))))
        await reply(interaction,'Authenticate on the team-approved Rip Cars page. The platform will confirm your account to Verifier.',view=view)

    @discord.ui.button(label='My status',style=discord.ButtonStyle.secondary,custom_id='rcv:status:v1')
    async def status(self,interaction,button):
        await authorized(self.bot,interaction)
        data=await self.bot.engine.status(interaction.guild_id,interaction.user.id)
        links=await self.bot.db.links(interaction.guild_id,interaction.user.id)
        text='Connections: '+str(len(links))
        if data:
            value=lambda key:str(len(data['assets'])) if key=='asset_count' and key in data['complete'] else str(data[key]) if key in data['complete'] else 'Not enabled'
            text+=f'\nLast check: <t:{int(data["checked_at"])}:R>\nDeposit points: {value("deposit_points")}\nPlatform points: {value("platform_points")}\nToken balance: {value("token_balance")}\nQualified assets: {value("asset_count")}'
            incomplete=[k for k,v in data['complete'].items() if not v]
            if incomplete: text+='\nHistory still syncing: '+', '.join(incomplete)
            if data.get('incomplete_rules'): text+='\nRules waiting for complete evidence: '+', '.join(data['incomplete_rules'])[:500]
        else: text+='\nYour first check is queued after connecting.'
        await reply(interaction,text)

    @discord.ui.button(label='Refresh',style=discord.ButtonStyle.secondary,custom_id='rcv:refresh:v1')
    async def refresh(self,interaction,button):
        cfg,_=await authorized(self.bot,interaction)
        if not cfg['enabled']: raise ValueError('Verifier is paused.')
        await self.bot.db.member_refresh(interaction.guild_id,interaction.user.id)
        await reply(interaction,'Refresh queued. Open My status after the next worker pass.')

    @discord.ui.button(label='Disconnect',style=discord.ButtonStyle.secondary,custom_id='rcv:disconnect:v1')
    async def disconnect(self,interaction,button):
        await authorized(self.bot,interaction)
        links=await self.bot.db.links(interaction.guild_id,interaction.user.id)
        if not links: return await reply(interaction,'There are no active connections.')
        view=DisconnectView(self.bot,interaction.user.id,links)
        await reply(interaction,'Choose the connection to disconnect. Earned deposit history stays with your Discord account.',view=view)

    async def on_error(self,interaction,error,item):
        await reply(interaction,str(error) if isinstance(error,ValueError) else 'The action failed. Open /verifier guide or contact a server admin.')
        if not isinstance(error,ValueError): await self.bot.reporter.error(interaction.guild,'member panel',error)


class DisconnectView(discord.ui.View):
    def __init__(self,bot,user,links):
        super().__init__(timeout=180); self.bot=bot; self.user=user; self.links=links
        select=discord.ui.Select(placeholder='Choose a connection',options=[discord.SelectOption(label=r['kind']+': '+r['identity'][:80],value=str(i)) for i,r in enumerate(links)])
        async def chosen(interaction):
            if interaction.user.id!=self.user: return await reply(interaction,'This connection menu belongs to another member.')
            await authorized(bot,interaction)
            item=links[int(select.values[0])]
            await bot.linking.disconnect(interaction.guild_id,self.user,item['kind'],item['identity'])
            await interaction.response.edit_message(content='Disconnected. Qualifying roles refresh on the next check.',view=None)
        select.callback=chosen; self.add_item(select)
