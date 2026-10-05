#!/usr/bin/env python3
"""Create a full encrypted VPS backup using the protected environment file."""
import argparse
import asyncio
import os
import pwd
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.preflight import read_env
from ripcars_verifier.backups import create_backup
from ripcars_verifier.storage import Store


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--env-file',default='/etc/ripcars-verifier.env'); p.add_argument('--service-user'); args=p.parse_args()
    env=read_env(args.env_file)
    if args.service_user:
        if os.getuid()!=0: raise SystemExit('Dropping to the service account requires root.')
        account=pwd.getpwnam(args.service_user); os.initgroups(account.pw_name,account.pw_gid); os.setgid(account.pw_gid); os.setuid(account.pw_uid)
    os.umask(0o007)
    if not Path(env['DATABASE_PATH']).exists(): raise SystemExit('No verifier database exists yet. Nothing was overwritten.')
    path=asyncio.run(create_backup(Store(env['DATABASE_PATH']),env['BACKUP_PATH'],env['BACKUP_PASSWORD']))
    print(path)


if __name__=='__main__': main()
