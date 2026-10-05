"""Server-side integration example. Implement authentication and accounting in YOUR backend.

Never expose either secret to a browser. This module does not authenticate a Rip Cars
account itself and does not invent a public Rip Cars API. See API_CONTRACT.md.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.request import Request, urlopen


def send_signed(path,payload,event_id=None):
    origin=os.environ['VERIFIER_PUBLIC_URL'].rstrip('/')
    if not origin.startswith('https://'): raise ValueError('Use the verifier HTTPS origin.')
    secret=os.environ['VERIFIER_PLATFORM_WEBHOOK_SECRET']
    if len(secret)<32: raise ValueError('Missing integration secret.')
    body=json.dumps(payload,separators=(',',':'),allow_nan=False).encode(); stamp=str(int(time.time()))
    event_id=event_id or secrets.token_urlsafe(24)
    signature=hmac.new(secret.encode(),stamp.encode()+b'.'+event_id.encode()+b'.'+body,hashlib.sha256).hexdigest()
    request=Request(origin+path,data=body,method='POST',headers={'Content-Type':'application/json','X-Ripcars-Timestamp':stamp,'X-Ripcars-Event-Id':event_id,'X-Ripcars-Signature':signature})
    with urlopen(request,timeout=15) as response: return json.load(response)


def confirm_authenticated_account(discord_link_token,authenticated_account_id):
    # authenticated_account_id MUST come from the backend's authenticated session.
    # Never copy an unverified account ID, wallet, or user ID from a browser request.
    return send_signed('/api/platform/link',{'session_token':discord_link_token,'account_id':authenticated_account_id},event_id='link-'+hashlib.sha256(discord_link_token.encode()).hexdigest())


def announce_finalized_pull(guild_id,event_id,car_name,asset_id,occurred_at,image_url='',asset_url=''):
    # Call from the settled pack-opening transaction/job, not a client-side click.
    # Persist occurred_at and the business payload in your job for identical retries.
    payload={'schema_version':1,'type':'rip.opened','id':event_id,'guild_id':guild_id,'car_name':car_name,'asset_id':asset_id,'occurred_at':occurred_at,'image_url':image_url,'asset_url':asset_url}
    return send_signed('/api/webhooks/rip',payload,event_id='pull-'+event_id)
