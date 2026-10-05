from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC=b'RIPCARS-VERIFIER-BACKUP-1\n'


def encrypt(payload,password):
    if not isinstance(password,str) or len(password)<20: raise ValueError('Set BACKUP_PASSWORD to at least 20 characters on the VPS before exporting a backup.')
    salt=secrets.token_bytes(16); nonce=secrets.token_bytes(12)
    key=Scrypt(salt=salt,length=32,n=2**14,r=8,p=1).derive(password.encode())
    return MAGIC+salt+nonce+AESGCM(key).encrypt(nonce,payload,MAGIC)


def decrypt(data,password):
    if not data.startswith(MAGIC) or len(data)<len(MAGIC)+44 or len(data)>256*1024*1024: raise ValueError('Invalid verifier backup format or size.')
    offset=len(MAGIC); salt=data[offset:offset+16]; nonce=data[offset+16:offset+28]
    key=Scrypt(salt=salt,length=32,n=2**14,r=8,p=1).derive(password.encode())
    try: return AESGCM(key).decrypt(nonce,data[offset+28:],MAGIC)
    except Exception: raise ValueError('Backup authentication failed. Check its password and file integrity.') from None


async def create_backup(store,folder,password,guild=None):
    root=Path(folder); root.mkdir(parents=True,exist_ok=True)
    name='ripcars-verifier-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+secrets.token_hex(3)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        source=await store.backup(Path(temporary)/'verifier.sqlite3',guild)
        if source.stat().st_size>128*1024*1024: raise ValueError('This release supports database restores up to 128 MiB. Upgrade the reviewed backup limit before relying on a larger backup.')
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w:gz') as archive:
            archive.add(source,arcname='verifier.sqlite3')
            value=json.dumps({'schema_version':1,'bot':'ripcars-verifier','created_at':time.time(),'secrets_included':False,'guild_id':guild}).encode()
            info=tarfile.TarInfo('manifest.json'); info.size=len(value); archive.addfile(info,io.BytesIO(value))
        encrypted=encrypt(raw.getvalue(),password)
    path=root/(name+'.rcvbackup'); path.write_bytes(encrypted); os.chmod(path,0o600)
    return path


def restore_backup(file,target,password):
    target=Path(target)
    if target.exists(): raise ValueError('Restore into a new path. Never overwrite a running database.')
    raw=decrypt(Path(file).read_bytes(),password)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        entries=archive.getmembers()
        if len(entries)!=2 or {e.name for e in entries}!={'verifier.sqlite3','manifest.json'} or any(not e.isfile() or e.size>128*1024*1024 for e in entries) or archive.getmember('manifest.json').size>4096: raise ValueError('Backup members are invalid.')
        manifest=json.load(archive.extractfile('manifest.json'))
        if manifest.get('schema_version')!=1 or manifest.get('bot')!='ripcars-verifier': raise ValueError('Backup version is unsupported.')
        database=archive.extractfile('verifier.sqlite3').read()
    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(database); os.chmod(target,0o600)
    try:
        with sqlite3.connect(target) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Restored database failed its integrity check.')
            if c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]!='1': raise ValueError('Restored schema is unsupported.')
            for guild,value in c.execute('SELECT guild,value FROM settings').fetchall():
                cfg=json.loads(value); cfg['enabled']=False
                c.execute('UPDATE settings SET value=?,revision=revision+1 WHERE guild=?',(json.dumps(cfg),guild))
            c.execute('DELETE FROM sessions')
            c.execute("UPDATE deliveries SET state='review' WHERE state IN ('pending','sending','review')")
            c.execute('UPDATE jobs SET next_run=0')
            c.commit()
    except BaseException:
        target.unlink(missing_ok=True); raise
    return target
