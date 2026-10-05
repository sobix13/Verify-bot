import copy
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

import discord
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ripcars_verifier.config import ALPHABET, USDC, TOKEN_PROGRAM
from ripcars_verifier.storage import Store


def encode58(raw):
    number=int.from_bytes(raw,'big'); out=''
    while number:
        number,index=divmod(number,58); out=ALPHABET[index]+out
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+out


def addr(number): return encode58(bytes([number])*32)


def keypair():
    key=Ed25519PrivateKey.generate()
    return key,encode58(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))


WALLET=addr(1); TREASURY=addr(2); SRC=addr(3); DST=addr(4); PROGRAM=addr(5); COLLECTION=addr(6)


def source(): return {'id':'payments','program_ids':[PROGRAM],'treasury_owners':[TREASURY],'token_accounts':[],'mints':{USDC:{'decimals':6,'usd_per_token':'1'}}}


def transaction(signature='sig1',amount='12345678',inner=False):
    instruction={'programId':TOKEN_PROGRAM,'parsed':{'type':'transferChecked','info':{'source':SRC,'destination':DST,'mint':USDC,'tokenAmount':{'amount':amount,'decimals':6}}}}
    tx={'slot':123,'blockTime':1700000000,'transaction':{'signatures':[signature],'message':{'accountKeys':[SRC,DST,PROGRAM],'instructions':[{'programId':PROGRAM}]}},'meta':{'err':None,'preTokenBalances':[{'accountIndex':0,'owner':WALLET,'mint':USDC,'uiTokenAmount':{'decimals':6}}],'postTokenBalances':[{'accountIndex':1,'owner':TREASURY,'mint':USDC,'uiTokenAmount':{'decimals':6}}],'innerInstructions':[]}}
    if inner: tx['meta']['innerInstructions']=[{'index':0,'instructions':[instruction]}]
    else: tx['transaction']['message']['instructions'].append(instruction)
    return tx


def asset(identity='asset1',**updates):
    data={'id':identity,'interface':'MplCoreAsset','burnt':False,'ownership':{'owner':WALLET},'grouping':[{'group_key':'collection','group_value':COLLECTION,'verified':True}],'content':{'metadata':{'name':'Roxy','attributes':[{'trait_type':'Model','value':'Roxy'}]},'links':{'image':'https://images.example.com/car.png'}}}
    data.update(updates); return data


def rule(kind='deposit_points',rid=20,threshold='10',**updates):
    data={'key':'tier_10','name':'Deposit 10','role_id':rid,'kind':kind,'threshold':threshold,'selector':{},'enabled':True}; data.update(updates); return data


class DBCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.store=Store(self.root/'db.sqlite3'); await self.store.open()
    async def asyncTearDown(self): self.temp.cleanup()
    async def settings(self,**changes):
        cfg,rev=await self.store.settings(1); cfg.update(changes); await self.store.save_settings(1,cfg,rev,7); return cfg
    async def wallet(self,user=10,wallet=WALLET):
        await self.store.execute("INSERT INTO links VALUES(?,?,'wallet',?,1,?)",(1,user,wallet,time.time()))


class FakeRpc:
    def __init__(self,results): self.results=results; self.calls=[]
    async def call(self,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        result=self.results[method]
        if isinstance(result,Exception): raise result
        if callable(result): return result(params)
        return copy.deepcopy(result)


class FakeHttp:
    def __init__(self,responder): self.responder=responder; self.calls=[]
    async def request(self,method,url,payload=None,headers=None):
        self.calls.append((method,url,payload,headers))
        result=self.responder(method,url,payload,headers)
        if isinstance(result,Exception): raise result
        return copy.deepcopy(result)


def roles():
    class Role:
        def __init__(self,id,name,position=1): self.id=id; self.name=name; self.position=position; self.permissions=discord.Permissions.none(); self.managed=False
        def is_default(self): return self.id==1
        def is_bot_managed(self): return False
        def __ge__(self,other): return self.position>=other.position
    return [Role(1,'@everyone',0),Role(2,'Rippers',1),Role(20,'Deposit 10',2),Role(900,'Verifier',10)]


def fake_guild():
    all_roles=roles(); me=Mock(spec=discord.Member); me.id=900; me.bot=True; me.top_role=all_roles[-1]
    me.guild_permissions=discord.Permissions(manage_roles=True)
    member=Mock(spec=discord.Member); member.id=10; member.bot=False; member.roles=all_roles[:2]; member.guild_permissions=discord.Permissions.none()
    member.add_roles=AsyncMock(side_effect=lambda role,**kwargs:member.roles.append(role))
    member.remove_roles=AsyncMock(side_effect=lambda role,**kwargs:member.roles.remove(role))
    channels={}
    for cid in (30,31,32):
        channel=Mock(spec=discord.TextChannel); channel.id=cid; channel.overwrites={}
        def permissions_for(target,cid=cid):
            staff=getattr(target,'id',0)==900
            return discord.Permissions(view_channel=staff or (cid!=32 and getattr(target,'id',0)==2),read_message_history=True,send_messages=staff,embed_links=staff,attach_files=staff)
        channel.permissions_for=permissions_for; channel.send=AsyncMock(return_value=SimpleNamespace(id=500,author=SimpleNamespace(id=900)))
        channel.fetch_message=AsyncMock(); channels[cid]=channel
    all_roles[-1].managed=True; all_roles[-1].is_bot_managed=lambda:True
    guild=SimpleNamespace(id=1,roles=all_roles,me=me,get_role=lambda rid:next((r for r in all_roles if r.id==rid),None),get_channel=lambda cid:channels.get(cid),get_member=lambda uid:member if uid==10 else None,channels=channels)
    return guild,member


def interaction(guild,user):
    i=SimpleNamespace(guild=guild,guild_id=guild.id,user=user,response=SimpleNamespace(is_done=lambda:False,defer=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock(),edit_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
    return i
