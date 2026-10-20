#!/usr/bin/env python3
"""Configure only RPC credentials; verify mainnet endpoints before saving anything."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.configure_env import write_env
from scripts.preflight import read_env,validate_env
from ripcars_verifier.network import Http,Rpc


async def check_endpoints(env,http):
    if not env.get('RPC_URL'): raise ValueError('Main RPC URL is required.')
    for key in ('RPC_URL','RPC_FALLBACK_URL'):
        if not env.get(key): continue
        rpc=Rpc(http,env[key])
        if await rpc.call('getHealth',[])!='ok': raise ValueError(key+' is not healthy.')
        slot=await rpc.call('getSlot',[{'commitment':'finalized'}])
        if type(slot) is not int or slot<0: raise ValueError(key+' returned an invalid slot.')
        await rpc.call('getTransaction',['1'*64,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':0}])
        print(key+': mainnet and transaction lookup passed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',type=Path,default=Path('/etc/ripcars-verifier.env'))
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0: raise SystemExit('Run this VPS credential tool as root.')
    target=args.file
    if target.is_symlink() or not target.is_file(): raise SystemExit('Use the existing regular Verifier environment file.')
    env=read_env(target)
    if not args.check_only:
        print('Only RPC settings will change. Enter keeps an existing URL; - clears the optional backup. Pasted URLs are hidden.')
        for key in ('RPC_URL','RPC_FALLBACK_URL'):
            value=getpass.getpass(key+' ['+('set' if env.get(key) else 'not set')+']: ').strip()
            if value=='-': env[key]=''
            elif value: env[key]=value
    errors=validate_env(env)
    if errors: raise SystemExit('\n'.join(errors))
    async def check():
        http=Http(); await http.open()
        try: await check_endpoints(env,http)
        finally: await http.close()
    try: asyncio.run(check())
    except Exception: raise SystemExit('RPC check failed. Check HTTPS URLs, mainnet, credentials, request limits and transaction lookup support. No settings were saved.') from None
    if args.check_only: return 0
    fd,backup=tempfile.mkstemp(prefix=target.name+'.before-rpc-',dir=target.parent)
    with os.fdopen(fd,'wb') as file: file.write(target.read_bytes())
    write_env(target,env)
    print('RPC saved with mode 600. Other credentials were retained. Previous configuration: '+backup)
    print('Run: systemctl restart ripcars-verifier.service')
    print('Then open /verifier panel > Token holder setup. Enter the official mint and run Check RPC connection.')
    return 0


if __name__=='__main__': raise SystemExit(main())
