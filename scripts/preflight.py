#!/usr/bin/env python3
"""Validate environment values without printing secret values or contacting Discord."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ripcars_verifier.config import safe_url

KEYS=('DISCORD_TOKEN','PUBLIC_URL','WEB_HOST','WEB_PORT','DATABASE_PATH','COORDINATION_PATH','BACKUP_PATH','RPC_URL','RPC_FALLBACK_URL','DAS_URL','PLATFORM_API_URL','PLATFORM_API_KEY','PLATFORM_WEBHOOK_SECRET','CHAIN_WEBHOOK_SECRET','BACKUP_PASSWORD','TEST_GUILD_ID')


def read_env(path):
    values={}
    for number,line in enumerate(Path(path).read_text().splitlines(),1):
        if not line.strip() or line.lstrip().startswith('#'): continue
        match=re.fullmatch(r'([A-Z][A-Z0-9_]*)=(.*)',line)
        if not match or match[1] not in KEYS or match[1] in values: raise ValueError(f'Invalid or repeated environment key on line {number}.')
        value=match[2]
        if value.startswith('"'):
            value=json.loads(value)
        if not isinstance(value,str) or '\n' in value or '\r' in value or '\0' in value: raise ValueError(f'Invalid environment value on line {number}.')
        values[match[1]]=value
    return values


def validate_env(env):
    errors=[]
    if sys.version_info<(3,11): errors.append('Python 3.11 or newer is required.')
    token=env.get('DISCORD_TOKEN','')
    if len(token)<40 or token.startswith(('replace_','offline-')): errors.append('Set the actual Discord bot token.')
    try: safe_url(env.get('PUBLIC_URL',''),origin_only=True)
    except ValueError: errors.append('PUBLIC_URL must be the real public HTTPS origin, without a path.')
    if env.get('WEB_HOST','127.0.0.1') not in ('127.0.0.1','::1'): errors.append('WEB_HOST must be loopback.')
    try:
        if not 1024<=int(env.get('WEB_PORT','8092'))<=65535: raise ValueError()
    except ValueError: errors.append('WEB_PORT must be an unprivileged port from 1024 to 65535.')
    for key in ('DATABASE_PATH','COORDINATION_PATH','BACKUP_PATH'):
        if not env.get(key) or not Path(env[key]).is_absolute(): errors.append(key+' must be an absolute path.')
    if env.get('DATABASE_PATH')==env.get('COORDINATION_PATH'): errors.append('Private database and shared coordination must be different files.')
    if len(env.get('BACKUP_PASSWORD',''))<20: errors.append('Set a backup password of at least 20 characters.')
    for key in ('RPC_URL','RPC_FALLBACK_URL','DAS_URL','PLATFORM_API_URL'):
        if env.get(key):
            try: safe_url(env[key])
            except ValueError: errors.append(key+' must be a public HTTPS provider URL.')
    for key in ('PLATFORM_WEBHOOK_SECRET','CHAIN_WEBHOOK_SECRET'):
        if env.get(key) and len(env[key])<32: errors.append(key+' requires at least 32 characters.')
    if env.get('PLATFORM_API_KEY') and len(env['PLATFORM_API_KEY'])<16: errors.append('PLATFORM_API_KEY requires at least 16 characters.')
    if env.get('TEST_GUILD_ID') and not re.fullmatch(r'[1-9][0-9]{5,19}',env['TEST_GUILD_ID']): errors.append('TEST_GUILD_ID must be a numeric Discord server ID.')
    return errors


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--env-file'); parser.add_argument('--json',action='store_true'); args=parser.parse_args()
    try: env=read_env(args.env_file) if args.env_file else dict(os.environ); errors=validate_env(env)
    except (ValueError,OSError): errors=['Cannot parse the environment file. Check its documented key=value format.']
    result={'ok':not errors,'errors':errors,'python':sys.version.split()[0],'checks':'Offline configuration checks only; run /verifier probe for live providers.'}
    print(json.dumps(result,indent=2) if args.json else ('Preflight passed.' if not errors else '\n'.join(errors)))
    return int(bool(errors))


if __name__=='__main__': raise SystemExit(main())
