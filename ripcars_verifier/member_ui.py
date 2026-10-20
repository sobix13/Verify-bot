from __future__ import annotations

from urllib.parse import urlencode,urlsplit,urlunsplit

import discord
from .config import wallet_kinds

from .operations import authorized,reply


class MemberPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label='Verify wallet without connecting',style=discord.ButtonStyle.primary,custom_id='rcv:transfer:v1')
    async def transfer(self,interaction,button):
        cfg,_=await authorized(self.bot,interaction)
        self.bot.transfer_proof.require_enabled(cfg)
        await interaction.response.send_modal(WalletTransferModal(self.bot))

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
            for kind,label in [('token_balance','Token balance updated'),('platform_points','Platform points updated')]:
                when=data.get('last_success',{}).get(kind)
                if when:text+=f'\n{label}: <t:{int(when)}:R>'
            incomplete=[k for k,v in data['complete'].items() if not v]
            if incomplete: text+='\nHistory still syncing: '+', '.join(incomplete)
            if data.get('incomplete_rules'): text+='\nRules waiting for complete evidence: '+', '.join(data['incomplete_rules'])[:500]
        else: text+='\nYour first check is queued after connecting.'
        await reply(interaction,text)

    @discord.ui.button(label='Refresh',style=discord.ButtonStyle.secondary,custom_id='rcv:refresh:v1')
    async def refresh(self,interaction,button):
        cfg,_=await authorized(self.bot,interaction)
        if not cfg['enabled']: raise ValueError('Verifier is paused.')
        links=await self.bot.db.links(interaction.guild_id,interaction.user.id)
        if not any(r['kind']=='wallet' for r in links) or not wallet_kinds(cfg):
            return await reply(interaction,'No active wallet check is configured. Platform points update when a server admin starts an update. Use My status for your saved result.')
        await self.bot.db.member_refresh(interaction.guild_id,interaction.user.id)
        await reply(interaction,'Wallet refresh queued. Platform points update when a server admin starts an update. Open My status for your saved results.')

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


class WalletTransferModal(discord.ui.Modal,title='Verify your own wallet'):
    wallet=discord.ui.TextInput(label='Your public Solana wallet address',placeholder='Public address only. Never enter a seed phrase.',max_length=44)

    def __init__(self,bot):
        super().__init__(timeout=600); self.bot=bot

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        try:
            proof=await self.bot.transfer_proof.create(i.guild_id,i.user.id,self.wallet.value)
            from .transfer_proof import amount_text
            text=(f'Verify ownership with one self-transfer.\n\nWallet / recipient:\n```{proof["wallet"]}```'
                  f'Token mint:\n```{proof["mint"]}```Exact amount (copy every digit):\n```{amount_text(proof)}```'
                  f'Send this token amount from the wallet above back to the same wallet. '
                  f'Do not send it to the team or bot. Your wallet charges a network fee in SOL.\n'
                  f'Expires: <t:{int(proof["expires"])}:R>. After sending, select Check transaction and paste its signature. '
                  f'If your wallet blocks self-transfers, cancel and contact staff; do not send to another person. '
                  f'No website connection, seed phrase or token approval is requested.')
            await reply(i,text,view=TransferRequest(self.bot,i.user.id,proof['id']))
        except ValueError as exc: await reply(i,str(exc))
        except Exception as exc:
            code=await self.bot.reporter.error(i.guild,f'self-transfer request user={i.user.id}',exc)
            await reply(i,'The network check could not start. No wallet was linked. Try again later. Reference: '+code)


class TransferRequest(discord.ui.View):
    def __init__(self,bot,user,pid):
        super().__init__(timeout=1200); self.bot,self.user,self.pid=bot,user,pid
        check=discord.ui.Button(label='Check transaction',style=discord.ButtonStyle.primary)
        async def run(i):
            await i.response.send_modal(TransactionModal(bot,pid))
        check.callback=run; self.add_item(check)
        cancel=discord.ui.Button(label='Cancel request')
        async def stop(i):
            await i.response.defer(ephemeral=True)
            await bot.transfer_proof.cancel(i.guild_id,i.user.id,pid)
            await reply(i,'Request cancelled. Your existing verified wallets and roles are unchanged.')
        cancel.callback=stop; self.add_item(cancel)

    async def interaction_check(self,i):
        if i.user.id!=self.user:
            await reply(i,'Open your own verification request.'); return False
        await authorized(self.bot,i); return True


class TransactionModal(discord.ui.Modal,title='Check your self-transfer'):
    signature=discord.ui.TextInput(label='Transaction signature',placeholder='Copy the signature from your wallet transaction.',min_length=64,max_length=88)

    def __init__(self,bot,pid):
        super().__init__(timeout=600); self.bot,self.pid=bot,pid

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        try:
            await self.bot.transfer_proof.verify(i.guild_id,i.user.id,self.pid,self.signature.value)
            await reply(i,'Wallet ownership verified. Your current balance check is queued. Use My status to see the result. Roles are awarded only when you meet their minimum balance.')
        except ValueError as exc: await reply(i,str(exc))
        except Exception as exc:
            code=await self.bot.reporter.error(i.guild,f'self-transfer check user={i.user.id}',exc)
            await reply(i,'The provider could not complete the check. No wallet was verified by this attempt and existing roles are unchanged. Retry the same transaction after 30 seconds. Reference: '+code)


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
