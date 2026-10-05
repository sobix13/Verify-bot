#!/usr/bin/env python3
"""Build a new isolated release and run its actual tests before activation."""
import argparse
import os
import re
import shutil
import subprocess
import time
import venv
from pathlib import Path


def stage(source,destination,run_checks=True):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    version=(source/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version): raise ValueError('Invalid release version.')
    if destination.exists(): raise ValueError('A release directory already exists. Choose a new candidate path.')
    if source==destination or source in destination.parents: raise ValueError('Stage outside the source tree.')
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.git','.env','.venv','__pycache__','*.pyc','data','backups','releases','dist'))
    venv.create(destination/'.venv',with_pip=True,symlinks=True)
    python=destination/'.venv/bin/python'
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(destination/'requirements-lock.txt')],cwd=destination,check=True)
    if run_checks:
        env={**os.environ,'PYTHON':str(python),'PYTHONDONTWRITEBYTECODE':'1'}
        subprocess.run(['bash','run_tests.sh'],cwd=destination,env=env,check=True)
    (destination/'STAGED_OK').write_text('Version '+version+'; isolated checks passed at '+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
    return destination


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--source',default=str(Path(__file__).resolve().parents[1])); p.add_argument('--destination',required=True); args=p.parse_args()
    print(stage(args.source,args.destination))


if __name__=='__main__': main()
