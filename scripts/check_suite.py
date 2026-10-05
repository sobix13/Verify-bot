#!/usr/bin/env python3
"""Read-only shared registry inspection or isolated four-bot compatibility tests."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ripcars_coordination as protocol


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',default='/var/lib/ripcars-bots/coordination.sqlite3')
    parser.add_argument('--guild',type=int)
    parser.add_argument('--integration',action='store_true',help='Use temporary databases to exercise actual Gate/Crew/Raffle/Verifier stores.')
    parser.add_argument('--gate',type=Path)
    parser.add_argument('--crew',type=Path)
    parser.add_argument('--raffle',type=Path)
    parser.add_argument('--gate-python',type=Path)
    parser.add_argument('--crew-python',type=Path)
    parser.add_argument('--raffle-python',type=Path)
    args=parser.parse_args()
    if args.integration:
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
        for name in ('gate','crew','raffle'):
            folder=getattr(args,name)
            if folder is None or not (folder/'VERSION').is_file():parser.error('Provide an existing --'+name+' source directory.')
            env['RIPCARS_'+name.upper()+'_ROOT']=str(folder.resolve())
            python=getattr(args,name+'_python')
            if python:
                if not python.is_file():parser.error('Invalid --'+name+'-python path.')
                env['RIPCARS_'+name.upper()+'_PYTHON']=str(python.absolute())
        return subprocess.run([sys.executable,'-m','unittest','discover','-s','suite_tests','-t','.', '-v'],cwd=ROOT,env=env,check=False).returncode
    try:report=protocol.inspect(args.registry,args.guild)
    except (OSError,ValueError,protocol.sqlite3.DatabaseError) as exc:
        report={'ok':False,'issues':['Registry inspection failed: '+type(exc).__name__]}
    print(json.dumps(report,indent=2))
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
