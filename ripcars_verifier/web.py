from __future__ import annotations

import hmac
import json
import time
from pathlib import Path

from aiohttp import web

from .auth import check_hmac
from .config import canonical, discord_id
from .platform import rip_event
from .storage import Conflict

REQUESTS=web.AppKey('requests',list)
HEALTH=web.AppKey('health_callback',object)


async def strict_json(request):
    if request.content_type!='application/json': raise ValueError('Use application/json.')
    body=await request.read()
    if len(body)>262144: raise ValueError('Request exceeds the 256 KiB limit.')
    data=json.loads(body,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Invalid JSON number.')))
    if not isinstance(data,dict): raise ValueError('Use a JSON object.')
    return body,data


def build_app(store,linking,env,health_callback=None):
    files=Path(__file__).resolve().parent.parent/'web'
    async def rate_check(request):
        # This listener is behind nginx, which owns IP-level rate limiting.
        # A process-wide ceiling also limits unauthenticated request work.
        now=time.monotonic(); values=request.app[REQUESTS]
        while values and values[0]<now-1: values.pop(0)
        if len(values)>=30: raise web.HTTPTooManyRequests(text='Try again shortly.')
        values.append(now)

    @web.middleware
    async def security(request,handler):
        await rate_check(request)
        try: response=await handler(request)
        except (ValueError,KeyError,TypeError,Conflict,json.JSONDecodeError):
            response=web.json_response({'error':'The request was rejected. Check the connection link, input and credentials.'},status=400)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    app=web.Application(middlewares=[security],client_max_size=262144); app[REQUESTS]=[]
    if health_callback: app[HEALTH]=health_callback

    def origin(request):
        if request.headers.get('Origin')!=linking.origin: raise ValueError('Origin did not match the connection site.')

    async def page(request): return web.FileResponse(files/'index.html')
    async def script(request): return web.FileResponse(files/'connect.js')
    async def style(request): return web.FileResponse(files/'style.css')
    async def challenge(request):
        origin(request); _,data=await strict_json(request)
        message=await linking.challenge(data['token'],data['wallet'])
        return web.json_response({'message':message})
    async def verify(request):
        origin(request); _,data=await strict_json(request)
        guild,user=await linking.verify(data['token'],data['wallet'],data['signature'])
        return web.json_response({'ok':True,'guild_id':str(guild),'discord_id':str(user)})

    async def authenticate(request,scope,secret_name,body):
        event_id=request.headers.get('X-Ripcars-Event-Id','')
        check_hmac(env.get(secret_name,''),request.headers.get('X-Ripcars-Timestamp'),event_id,body,request.headers.get('X-Ripcars-Signature'))
        def reserve(c):
            old=c.execute('SELECT * FROM webhook_ids WHERE scope=? AND event_id=?',(scope,event_id)).fetchone()
            if old: return False
            c.execute('INSERT INTO webhook_ids VALUES(?,?,?)',(scope,event_id,time.time()+86400)); return True
        return event_id,await store.run(reserve)

    async def account(request):
        body,data=await strict_json(request); event_id,fresh=await authenticate(request,'account','PLATFORM_WEBHOOK_SECRET',body)
        if not fresh: return web.json_response({'ok':True,'duplicate':True})
        try:
            # Only the trusted Rip Cars backend calls this route after authenticating its account.
            from .auth import token_hash
            rows=await store.query('SELECT guild FROM sessions WHERE token=?',(token_hash(data['session_token']),))
            if not rows: raise ValueError('Unknown session.')
            cfg,_=await store.settings(rows[0]['guild'])
            if not cfg['account_linking']: raise ValueError('Account linking is paused.')
            guild,user=await linking.attest_account(data['session_token'],data['account_id'])
            return web.json_response({'ok':True,'guild_id':str(guild),'discord_id':str(user)})
        except BaseException:
            await store.execute('DELETE FROM webhook_ids WHERE scope=? AND event_id=?',('account',event_id)); raise

    async def rip(request):
        body,data=await strict_json(request); event=rip_event(data); guild=event['guild_id']; cfg,_=await store.settings(guild)
        if not cfg['rip_feed']: raise ValueError('Rip announcements are not enabled for this server.')
        event_id=request.headers.get('X-Ripcars-Event-Id','')
        check_hmac(env.get('PLATFORM_WEBHOOK_SECRET',''),request.headers.get('X-Ripcars-Timestamp'),event_id,body,request.headers.get('X-Ripcars-Signature'))
        value=canonical(event)
        def enqueue(c):
            old=c.execute('SELECT payload FROM deliveries WHERE guild=? AND event_id=?',(guild,'rip:'+event['id'])).fetchone()
            if old and old['payload']!=value: raise Conflict('The rip event ID was reused with different content.')
            duplicate=bool(c.execute("SELECT 1 FROM webhook_ids WHERE scope='rip' AND event_id=?",(event_id,)).fetchone())
            if duplicate and not old: raise Conflict('The webhook envelope ID was already used for another rip event.')
            c.execute("INSERT OR IGNORE INTO webhook_ids VALUES('rip',?,?)",(event_id,time.time()+86400))
            c.execute("INSERT OR IGNORE INTO deliveries(guild,event_id,kind,payload,state,next_run,created) VALUES(?,?,'rip',?,'pending',?,?)",(guild,'rip:'+event['id'],value,time.time(),time.time()))
            return duplicate or bool(old)
        duplicate=await store.run(enqueue)
        return web.json_response({'ok':True,'duplicate':duplicate})

    async def wake(data):
        guild=discord_id(data.get('guild_id'))
        if not isinstance(data.get('wallet'),str): raise ValueError('Invalid chain hint.')
        cfg,_=await store.settings(guild)
        if not cfg['chain_webhook']: raise ValueError('Chain webhook is paused.')
        rows=await store.query("SELECT user FROM links WHERE guild=? AND kind='wallet' AND identity=? AND active=1",(guild,data['wallet']))
        for row in rows: await store.enqueue(guild,row['user'])
    async def chain(request):
        body,data=await strict_json(request); event_id,fresh=await authenticate(request,'chain','CHAIN_WEBHOOK_SECRET',body)
        if fresh:
            try: await wake(data)
            except BaseException:
                await store.execute('DELETE FROM webhook_ids WHERE scope=? AND event_id=?',('chain',event_id)); raise
        return web.json_response({'ok':True,'duplicate':not fresh})
    async def helius(request):
        expected=env.get('CHAIN_WEBHOOK_SECRET','')
        if len(expected)<32 or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+expected): raise ValueError('Helius webhook authentication failed.')
        data=await request.json()
        if not isinstance(data,list) or len(data)>100: raise ValueError('Helius events must be a bounded array.')
        for event in data:
            if not isinstance(event,dict): raise ValueError('Invalid Helius event.')
            accounts={a.get('account') for a in event.get('accountData',[]) if isinstance(a,dict)}
            for row in await store.query("SELECT guild,user,identity FROM links WHERE kind='wallet' AND active=1"):
                if row['identity'] in accounts:
                    cfg,_=await store.settings(row['guild'])
                    if cfg['chain_webhook']: await store.enqueue(row['guild'],row['user'])
        # Hints only. A finalized RPC check, never this body, produces deposit points.
        return web.json_response({'ok':True})
    async def health(request):
        callback=request.app.get(HEALTH)
        result=await callback() if callback else {'ok':True}
        return web.json_response({**result,'service':'ripcars-verifier','schema':1},status=200 if result['ok'] else 503)

    app.add_routes([web.get('/link',page),web.get('/connect.js',script),web.get('/style.css',style),web.get('/healthz',health),
                    web.post('/api/challenge',challenge),web.post('/api/verify',verify),web.post('/api/platform/link',account),
                    web.post('/api/webhooks/rip',rip),web.post('/api/webhooks/chain',chain),web.post('/api/webhooks/helius',helius)])
    return app
