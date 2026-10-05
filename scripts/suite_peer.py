#!/usr/bin/env python3
"""Temporary integration-test peer. Never accepts a production registry path."""
import argparse
import asyncio
import json
import sys
from pathlib import Path


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('gate','crew','raffle','verifier'))
    parser.add_argument('source',type=Path)
    parser.add_argument('temporary',type=Path)
    args=parser.parse_args()
    marker=args.temporary/'RIPCARS_SUITE_TEMPORARY'
    if not marker.is_file() or marker.read_text()!='isolated compatibility fixture\n':parser.error('Only a marked temporary test directory is accepted.')
    source=args.source.resolve()
    sys.path.insert(0,str(source))
    if args.kind=='gate':
        from db import Database
        db=Database(args.temporary/'gate.sqlite3',args.temporary/'coordination.sqlite3')
        await db.connect()
        async def acquire(guild):return await db.lease(guild,'server-setup')
        async def release(guild,token):await db.release(guild,'server-setup',token)
        async def records(guild):return await db.resources(guild)
        query=db.query
        close=db.close
    elif args.kind=='crew':
        from ripcars_crew.storage import Database
        import ripcars_coordination as protocol
        db=Database(args.temporary/'crew.sqlite3',args.temporary/'coordination.sqlite3')
        await db.open()
        async def acquire(guild):return await db.acquire(guild)
        async def release(guild,token):await db.coord.run(lambda c:protocol.release(c,guild,'server-setup',token))
        async def records(guild):return await db.resources(guild)
        query=db.coord.query
        close=db.close
    else:
        if args.kind=='raffle':from ripcars_raffle.coordination import Registry
        else:from ripcars_verifier.coordination import Registry
        db=Registry(args.temporary/'coordination.sqlite3')
        await db.open()
        contexts={}
        async def acquire(guild):
            context=db.lease(guild)
            token=await context.__aenter__()
            contexts[guild]=context
            return token
        async def release(guild,token):await contexts.pop(guild).__aexit__(None,None,None)
        async def records(guild):return await (db.resources(guild) if args.kind=='raffle' else db.entries(guild))
        query=db.query
        async def close():pass
    print(json.dumps({'ready':True,'kind':args.kind}),flush=True)
    held={}
    try:
        while True:
            line=await asyncio.to_thread(sys.stdin.readline)
            if not line:break
            try:
                command=json.loads(line); action=command['action']; guild=command.get('guild',101)
                if action=='acquire':
                    held[guild]=await acquire(guild); result={'acquired':True}
                elif action=='release':
                    await release(guild,held.pop(guild)); result={'released':True}
                elif action=='resources':result={'resources':await records(guild)}
                elif action=='schema':result={'tables':[r['name'] for r in await query("SELECT name FROM sqlite_master WHERE type='table'")]}
                elif action=='bind_role' and args.kind=='verifier':
                    async with db.lease(guild):await db.bind_role(guild,401,703,'deposit_1k',{'name':'Deposit 1k','permissions':0})
                    result={'bound':True}
                elif action in ('eligible','ineligible') and args.kind=='raffle':
                    from datetime import datetime,timezone
                    from types import SimpleNamespace
                    from ripcars_raffle.config import rules
                    from ripcars_raffle.eligibility import check
                    rule=rules();rule['all_roles']=[401]
                    member=SimpleNamespace(id=601,bot=False,roles=[SimpleNamespace(id=201)]+([SimpleNamespace(id=401)] if action=='eligible' else []),guild_permissions=SimpleNamespace(administrator=False,manage_guild=False,manage_messages=False),created_at=datetime(2020,1,1,tzinfo=timezone.utc),joined_at=datetime(2020,1,1,tzinfo=timezone.utc))
                    ok,reason,weight=check(member,rule,201)
                    result={'eligible':ok,'weight':weight}
                elif action=='stop':print(json.dumps({'stopped':True}),flush=True);break
                else:raise ValueError('Unknown test action.')
                print(json.dumps(result),flush=True)
            except ValueError as exc:print(json.dumps({'blocked':True,'reason':str(exc)}),flush=True)
    finally:
        for guild,token in held.items():await release(guild,token)
        await close()


if __name__=='__main__':asyncio.run(main())
