#!/usr/bin/env python3
"""Restore an authenticated backup to a NEW database path, paused for review."""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ripcars_verifier.backups import restore_backup


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('backup'); p.add_argument('--output',required=True); args=p.parse_args()
    password=getpass.getpass('Backup encryption password: ')
    try: result=restore_backup(args.backup,args.output,password)
    except (ValueError,OSError) as exc: print(str(exc),file=sys.stderr); return 1
    print('Restored to '+str(result)+'. All server configurations are paused. Stop the service before switching DATABASE_PATH; do not overwrite a running database.')
    return 0


if __name__=='__main__': raise SystemExit(main())
