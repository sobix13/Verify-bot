import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ripcars_verifier.engine import Engine
from ripcars_verifier.network import PublicResolver, ProviderError
from .helpers import DBCase, FakeHttp


class NetworkBoundaryTests(DBCase):
    async def test_dns_private_public_and_mixed_resolution(self):
        resolver=PublicResolver(); original=resolver.resolver
        try:
            for hosts,accepted in ((['8.8.8.8'],True),(['127.0.0.1'],False),(['8.8.8.8','10.0.0.1'],False),([],False)):
                resolver.resolver=SimpleNamespace(resolve=AsyncMock(return_value=[{'host':h} for h in hosts]),close=AsyncMock())
                if accepted: self.assertEqual(len(await resolver.resolve('rpc.example.com',443)),1)
                else:
                    with self.assertRaises(OSError): await resolver.resolve('rpc.example.com',443)
        finally: await original.close()


class ReceiptTests(DBCase):
    async def test_platform_historical_cutoff_and_zero_receipts(self):
        await self.settings(enabled=True,deposit_mode='platform',history_start=100)
        await self.store.execute("INSERT INTO links VALUES(1,10,'account','a',1,?)",(time.time(),))
        page={'schema_version':1,'account_id':'a','complete':True,'as_of':time.time(),'deposits':[{'id':'old','kind':'deposit','status':'settled','usd_amount':'100','settled_at':99},{'id':'new','kind':'deposit','status':'settled','usd_amount':'12.5','settled_at':100},{'id':'zero','kind':'deposit','status':'settled','usd_amount':'0','settled_at':100}],'next_cursor':None}
        data,_,_=await Engine(self.store,FakeHttp(lambda *a:page),{'PLATFORM_API_URL':'https://bridge.example.com','PLATFORM_API_KEY':'k'*32}).refresh(1,10)
        self.assertEqual(data['deposit_points'],'12.5'); self.assertEqual(len(await self.store.query('SELECT * FROM ledger')),1)
    async def test_platform_cutoff_missing_timestamp_fails(self):
        await self.settings(enabled=True,deposit_mode='platform',history_start=100)
        await self.store.execute("INSERT INTO links VALUES(1,10,'account','a',1,?)",(time.time(),))
        page={'schema_version':1,'account_id':'a','complete':True,'as_of':time.time(),'deposits':[{'id':'d','kind':'deposit','status':'settled','usd_amount':'1'}],'next_cursor':None}
        with self.assertRaises(ProviderError): await Engine(self.store,FakeHttp(lambda *a:page),{'PLATFORM_API_URL':'https://bridge.example.com','PLATFORM_API_KEY':'k'*32}).refresh(1,10)
        self.assertEqual(await self.store.total(1,10),0)
