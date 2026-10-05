from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

from .config import canonical, defaults, validate


class Conflict(ValueError):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS settings(guild INTEGER PRIMARY KEY,revision INTEGER NOT NULL,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER NOT NULL,actor INTEGER NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS links(guild INTEGER NOT NULL,user INTEGER NOT NULL,kind TEXT NOT NULL,identity TEXT NOT NULL,active INTEGER NOT NULL,created REAL NOT NULL,PRIMARY KEY(guild,kind,identity));
CREATE INDEX IF NOT EXISTS links_user ON links(guild,user,active);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,guild INTEGER NOT NULL,user INTEGER NOT NULL,purpose TEXT NOT NULL,expires REAL NOT NULL,message TEXT,wallet TEXT,used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger(guild INTEGER NOT NULL,event_id TEXT NOT NULL,user INTEGER NOT NULL,wallet TEXT NOT NULL,usd_micros INTEGER NOT NULL,source TEXT NOT NULL,proof TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(guild,event_id));
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(guild,user);
CREATE TABLE IF NOT EXISTS observations(guild INTEGER NOT NULL,wallet TEXT NOT NULL,signature TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(guild,wallet,signature));
CREATE TABLE IF NOT EXISTS checkpoints(guild INTEGER NOT NULL,wallet TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(guild,wallet));
CREATE TABLE IF NOT EXISTS snapshots(guild INTEGER NOT NULL,user INTEGER NOT NULL,value TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS jobs(guild INTEGER NOT NULL,user INTEGER NOT NULL,next_run REAL NOT NULL,failures INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS role_grants(guild INTEGER NOT NULL,user INTEGER NOT NULL,role INTEGER NOT NULL,state TEXT NOT NULL,PRIMARY KEY(guild,user,role));
CREATE TABLE IF NOT EXISTS deliveries(guild INTEGER NOT NULL,event_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,message INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,next_run REAL NOT NULL,created REAL NOT NULL,PRIMARY KEY(guild,event_id));
CREATE TABLE IF NOT EXISTS webhook_ids(scope TEXT NOT NULL,event_id TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(scope,event_id));
'''


class Store:
    def __init__(self,path):
        self.path=Path(path); self.lock=asyncio.Lock()

    def connect(self):
        c=sqlite3.connect(self.path,timeout=15); c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=15000'); c.execute('PRAGMA foreign_keys=ON')
        return c

    async def run(self,fn):
        async with self.lock:
            def execute():
                c=self.connect()
                try:
                    c.execute('BEGIN IMMEDIATE'); result=fn(c); c.commit(); return result
                except BaseException:
                    c.rollback(); raise
                finally: c.close()
            task=asyncio.create_task(asyncio.to_thread(execute))
            try:return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:await task
                finally:raise

    async def open(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        def init():
            with self.connect() as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                    old=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    if not old or old[0]!='1': raise Conflict('Unsupported database schema. Install a compatible release; no migration was applied.')
                c.execute('PRAGMA journal_mode=WAL'); c.executescript(SCHEMA)
                version=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                if version!='1': raise Conflict('Unsupported database schema. Install a compatible release.')
        await asyncio.to_thread(init)
        os.chmod(self.path,0o600)

    async def query(self,sql,args=()):
        return await self.run(lambda c:[dict(r) for r in c.execute(sql,args).fetchall()])

    async def execute(self,sql,args=()):
        return await self.run(lambda c:c.execute(sql,args).rowcount)

    async def settings(self,guild):
        def get(c):
            row=c.execute('SELECT * FROM settings WHERE guild=?',(guild,)).fetchone()
            if not row:
                cfg=defaults(); c.execute('INSERT INTO settings VALUES(?,1,?)',(guild,canonical(cfg))); return cfg,1
            return json.loads(row['value']),row['revision']
        return await self.run(get)

    async def save_settings(self,guild,cfg,revision,actor):
        validate(cfg)
        def save(c):
            existing=c.execute("SELECT DISTINCT CASE WHEN event_id LIKE 'solana:%' THEN 'rpc' ELSE 'platform' END AS mode FROM ledger WHERE guild=?",(guild,)).fetchall()
            if cfg['deposit_mode']!='off' and any(r['mode']!=cfg['deposit_mode'] for r in existing):
                raise Conflict('The deposit ledger already uses another accounting method. Keep that method or perform an audited ledger migration; switching could count the same payment twice.')
            cur=c.execute('UPDATE settings SET value=?,revision=revision+1 WHERE guild=? AND revision=?',(canonical(cfg),guild,revision))
            if cur.rowcount!=1: raise Conflict('Settings changed. Reopen the panel and retry.')
            c.execute('INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,?,?,?)',(guild,actor,'settings',canonical(cfg),time.time()))
            c.execute('UPDATE jobs SET next_run=? WHERE guild=?',(time.time(),guild))
            return revision+1
        return await self.run(save)

    async def audit(self,guild,actor,action,detail):
        await self.execute('INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,?,?,?)',(guild,actor,action,str(detail)[:100000],time.time()))

    async def links(self,guild,user):
        return await self.query('SELECT * FROM links WHERE guild=? AND user=? AND active=1 ORDER BY kind,identity',(guild,user))

    async def credit(self,guild,user,wallet,event_id,amount,source,proof):
        if type(amount) is not int or not 0<amount<=10**18: raise ValueError('Invalid deposit amount.')
        if not isinstance(event_id,str) or not event_id.startswith(('solana:','platform:')) or len(event_id)>500: raise ValueError('Invalid canonical deposit event ID.')
        def put(c):
            mode='rpc' if event_id.startswith('solana:') else 'platform'
            other=c.execute("SELECT event_id FROM ledger WHERE guild=? AND event_id NOT LIKE ? LIMIT 1",(guild,('solana:' if mode=='rpc' else 'platform:')+'%')).fetchone()
            if other: raise Conflict('Mixed deposit accounting methods require an audited migration before importing receipts.')
            old=c.execute('SELECT * FROM ledger WHERE guild=? AND event_id=?',(guild,event_id)).fetchone()
            if old:
                if (old['user'],old['wallet'],old['usd_micros']) != (user,wallet,amount):
                    raise Conflict('Deposit identity or valuation changed. Manual ledger review is required.')
                return False
            c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?)',(guild,event_id,user,wallet,amount,source,canonical(proof),time.time()))
            return True
        return await self.run(put)

    async def total(self,guild,user):
        rows=await self.query('SELECT usd_micros FROM ledger WHERE guild=? AND user=?',(guild,user))
        return sum(r['usd_micros'] for r in rows)

    async def enqueue(self,guild,user,when=None):
        await self.execute('INSERT INTO jobs(guild,user,next_run) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET next_run=MIN(jobs.next_run,excluded.next_run)',(guild,user,time.time() if when is None else when))

    async def member_refresh(self,guild,user,now=None):
        now=time.time() if now is None else now
        def queue(c):
            row=c.execute("SELECT created FROM audit WHERE guild=? AND actor=? AND action='member_refresh' ORDER BY id DESC LIMIT 1",(guild,user)).fetchone()
            if row and now-row['created']<60: raise ValueError('Your refresh is already queued. Wait one minute before requesting another.')
            c.execute("INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,'member_refresh','Queued by member',?)",(guild,user,now))
            c.execute('INSERT INTO jobs(guild,user,next_run) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET next_run=MIN(jobs.next_run,excluded.next_run)',(guild,user,now))
        await self.run(queue)

    async def backup(self,destination,guild=None):
        target=Path(destination); target.parent.mkdir(parents=True,exist_ok=True)
        async with self.lock:
            def copy():
                src=self.connect(); dst=sqlite3.connect(target)
                try:
                    src.backup(dst)
                    if guild is not None:
                        for table in ('settings','audit','links','sessions','ledger','observations','checkpoints','snapshots','jobs','role_grants','deliveries'):
                            dst.execute(f'DELETE FROM {table} WHERE guild<>?',(guild,))
                        dst.execute('DELETE FROM sessions')
                        dst.execute('DELETE FROM webhook_ids')
                        dst.commit()
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise Conflict('Backup integrity check failed.')
                finally: dst.close(); src.close()
                os.chmod(target,0o600)
            await asyncio.to_thread(copy)
        return target
