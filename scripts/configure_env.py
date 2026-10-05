#!/usr/bin/env python3
"""Prompt for VPS-only credentials; no secret values are echoed or printed."""
import argparse
import getpass
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.preflight import KEYS, read_env, validate_env


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--file',default='/etc/ripcars-verifier.env'); args=parser.parse_args()
    target=Path(args.file); env=read_env(target) if target.exists() else {}
    for key,value in {'WEB_HOST':'127.0.0.1','WEB_PORT':'8092','DATABASE_PATH':'/var/lib/ripcars-verifier/verifier.sqlite3','COORDINATION_PATH':'/var/lib/ripcars-bots/coordination.sqlite3','BACKUP_PATH':'/var/lib/ripcars-verifier/backups'}.items(): env.setdefault(key,value)
    print('Press Enter to keep an existing value. Enter - to clear an optional integration. Secret prompts do not echo.')
    for key in ('DISCORD_TOKEN','PUBLIC_URL','RPC_URL','RPC_FALLBACK_URL','DAS_URL','PLATFORM_API_URL','PLATFORM_API_KEY','PLATFORM_WEBHOOK_SECRET','CHAIN_WEBHOOK_SECRET','BACKUP_PASSWORD','TEST_GUILD_ID'):
        hidden=key not in ('PUBLIC_URL','TEST_GUILD_ID')
        generated=key in ('PLATFORM_WEBHOOK_SECRET','CHAIN_WEBHOOK_SECRET','BACKUP_PASSWORD')
        state='set' if env.get(key) else 'not set'
        prompt=key+' ['+state+(' ; Enter generates a value' if generated and not env.get(key) else '')+']: '
        value=getpass.getpass(prompt) if hidden else input(prompt).strip()
        if value=='-': env[key]=''
        elif value: env[key]=value
        elif generated and not env.get(key): env[key]=secrets.token_urlsafe(48)
    errors=validate_env(env)
    if errors: print('\n'.join(errors)); return 1
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='verifier-env-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as file:
            for key in KEYS: file.write(key+'='+json.dumps(env.get(key,''),ensure_ascii=False)+'\n')
            file.flush(); os.fsync(file.fileno())
        os.chmod(temporary,0o600); os.replace(temporary,target)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()
    print('Environment file saved with mode 600. Keep a separate secure copy of the backup password and share integration secrets only through your team secret manager.')
    return 0


if __name__=='__main__': raise SystemExit(main())
