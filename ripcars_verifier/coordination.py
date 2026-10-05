from __future__ import annotations

import asyncio
import ripcars_coordination as protocol
from contextlib import asynccontextmanager

from .config import canonical
from .storage import Store, Conflict

SCHEMA='''
CREATE TABLE IF NOT EXISTS resources(guild INTEGER NOT NULL,key TEXT NOT NULL,object_id INTEGER NOT NULL,kind TEXT NOT NULL,owner TEXT NOT NULL,baseline TEXT NOT NULL,desired TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS locks(guild INTEGER NOT NULL,name TEXT NOT NULL,token TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(guild,name));
'''


class Registry(Store):
    def __init__(self,path):
        super().__init__(path); self.leases={}
    async def open(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        def init():
            with self.connect() as c:
                c.execute('PRAGMA journal_mode=WAL'); c.executescript(SCHEMA)
                protocol.initialize(c,Conflict)
        await asyncio.to_thread(init)
        protocol.shared_permissions(self.path)

    @asynccontextmanager
    async def lease(self,guild):
        token=await self.run(lambda c:protocol.claim(c,guild,error=Conflict))
        async def renew():
            while True:
                await asyncio.sleep(30)
                await self.run(lambda c:protocol.renew(c,guild,'server-setup',token,error=Conflict))
        task=asyncio.create_task(renew())
        self.leases[guild]=(token,task)
        try: yield token
        finally:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            except Exception: pass
            self.leases.pop(guild,None)
            await self.run(lambda c:protocol.release(c,guild,'server-setup',token))

    async def ensure_lease(self,guild):
        token,task=self.leases.get(guild,(None,None))
        if not token or task.done():
            raise Conflict('Shared setup lease was lost. The Discord change was stopped.')
        await self.run(lambda c:protocol.ensure(c,guild,'server-setup',token,error=Conflict))

    async def entries(self,guild):
        return {r['key']:r for r in await self.query('SELECT * FROM resources WHERE guild=?',(guild,))}

    async def gate_member(self,guild):
        row=(await self.entries(guild)).get('role:rippers')
        return row['object_id'] if row and row['state'] in ('active','pinned','external') else 0

    async def check_role(self,guild,role,bot_id,key,baseline):
        rows=await self.entries(guild); record_key=f'verifier:role:{bot_id}:{key}'
        for resource_key,row in rows.items():
            if row['kind']=='role' and row['object_id']==role and resource_key!=record_key:
                raise Conflict('This role belongs to another bot or a claim panel. Choose a dedicated qualifying role.')
        old=rows.get(record_key)
        value=canonical(baseline)
        if old and (old['owner']!=f'bot:{bot_id}' or old['object_id']!=role or old['state']!='active' or old['baseline']!=value):
            raise Conflict('Role binding changed or was pinned. Review it before resuming role updates.')

    async def bind_role(self,guild,role,bot_id,key,baseline):
        await self.ensure_lease(guild)
        await self.check_role(guild,role,bot_id,key,baseline)
        value=canonical(baseline)
        await self.execute("INSERT OR IGNORE INTO resources VALUES(?,?,?,'role',?,?,?,'active')",(guild,f'verifier:role:{bot_id}:{key}',role,f'bot:{bot_id}',value,value))

    async def bind_channel(self,guild,channel,bot_id,key):
        await self.ensure_lease(guild)
        records=await self.entries(guild); name=f'verifier:channel:{bot_id}:{key}'
        for other,row in records.items():
            if row['object_id']==channel and other!=name: raise Conflict('This channel is registered to another bot.')
        old=records.get(name)
        if old and (old['object_id']!=channel or old['owner']!=f'bot:{bot_id}' or old['state']!='active'): raise Conflict('Verifier channel binding was changed or pinned.')
        await self.execute("INSERT OR IGNORE INTO resources VALUES(?,?,?,'channel',?,'{}','{}','active')",(guild,name,channel,f'bot:{bot_id}'))
