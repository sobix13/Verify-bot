"""Real bot stores in four separate processes, sharing temporary SQLite only."""
import itertools
import json
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
KINDS=('gate','crew','raffle','verifier')


class Peer:
    def __init__(self,kind,temporary):
        source=ROOT if kind=='verifier' else Path(os.environ['RIPCARS_'+kind.upper()+'_ROOT'])
        python=os.environ.get('RIPCARS_'+kind.upper()+'_PYTHON',sys.executable)
        self.process=subprocess.Popen([python,str(ROOT/'scripts/suite_peer.py'),kind,str(source),str(temporary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        ready=self.read()
        if not ready.get('ready'):raise AssertionError(str(ready))

    def read(self):
        if not select.select([self.process.stdout],[],[],20)[0]:
            self.process.kill();self.process.wait()
            raise AssertionError('Temporary compatibility peer timed out.')
        line=self.process.stdout.readline()
        if not line:
            self.process.wait(timeout=5)
            raise AssertionError(self.process.stderr.read())
        return json.loads(line)

    def command(self,action,guild=101):
        self.process.stdin.write(json.dumps({'action':action,'guild':guild})+'\n');self.process.stdin.flush()
        return self.read()

    def close(self):
        if self.process.poll() is None:
            self.command('stop');self.process.wait(timeout=5)
        self.process.stdin.close();self.process.stdout.close();self.process.stderr.close()


class FourBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ripcars-suite-')
        self.directory=Path(self.tmp.name)
        (self.directory/'RIPCARS_SUITE_TEMPORARY').write_text('isolated compatibility fixture\n')
        self.peers=[]

    def tearDown(self):
        for peer in reversed(self.peers):peer.close()
        self.tmp.cleanup()

    def peer(self,kind,directory=None):
        peer=Peer(kind,directory or self.directory);self.peers.append(peer);return peer

    def test_all_four_can_initialize_a_fresh_shared_database(self):
        for kind in KINDS:
            peer=self.peer(kind)
            self.assertEqual(peer.command('resources')['resources'],{})
            self.assertIn('suite_meta',peer.command('schema')['tables'])

    def test_each_first_controller_preserves_later_readers(self):
        for kind in KINDS:
            with self.subTest(first=kind):
                directory=self.directory/kind;directory.mkdir()
                (directory/'RIPCARS_SUITE_TEMPORARY').write_text('isolated compatibility fixture\n')
                for controller in (kind,*(other for other in KINDS if other!=kind)):
                    peer=self.peer(controller,directory)
                    self.assertEqual(peer.command('resources')['resources'],{})

    def test_all_four_guild_leases_are_independent(self):
        for guild,kind in enumerate(KINDS,200):
            self.assertTrue(self.peer(kind).command('acquire',guild)['acquired'])

    def test_foreign_and_manual_resource_bindings_survive_all_readers(self):
        self.peer('verifier')
        rows=[(101,'role:rippers',201,'role','ripcars-gate','{}','{}','active'),
              (101,'channel:ticket',301,'text','bot:701','{}','{}','external'),
              (101,'verifier:role:703:tier_1k',401,'role','bot:703','{}','{}','manual'),
              (101,'raffle:message:702:1',501,'message','bot:702','{}','{}','active')]
        with sqlite3.connect(self.directory/'coordination.sqlite3') as c:c.executemany('INSERT INTO resources VALUES(?,?,?,?,?,?,?,?)',rows)
        for kind in KINDS:
            resources=self.peer(kind).command('resources')['resources']
            for guild,key,oid,resource_kind,owner,baseline,desired,state in rows:
                self.assertEqual((resources[key]['object_id'],resources[key]['owner'],resources[key]['state']),(oid,owner,state))
        with sqlite3.connect(self.directory/'coordination.sqlite3') as c:self.assertEqual(c.execute('SELECT * FROM resources ORDER BY key').fetchall(),sorted(rows,key=lambda row:row[1]))

    def test_private_financial_support_and_raffle_tables_do_not_enter_shared_registry(self):
        for kind in KINDS:
            tables=set(self.peer(kind).command('schema')['tables'])
            self.assertFalse(tables & {'ledger','links','deliveries','tickets','cases','raffles','entries','crew_settings'})

    def test_legacy_gate_lease_blocks_all_new_controllers(self):
        self.peer('verifier')
        with sqlite3.connect(self.directory/'coordination.sqlite3') as c:c.execute("INSERT INTO leases VALUES(101,'server-setup','old-gate',99999999999)")
        for kind in KINDS:self.assertTrue(self.peer(kind).command('acquire')['blocked'])

    def test_legacy_raffle_lock_blocks_all_new_controllers(self):
        self.peer('gate')
        with sqlite3.connect(self.directory/'coordination.sqlite3') as c:c.execute("INSERT INTO locks VALUES(101,'server-setup','old-raffle',99999999999)")
        for kind in KINDS:self.assertTrue(self.peer(kind).command('acquire')['blocked'])

    def test_protocol_file_is_identical_in_every_repository(self):
        expected=(ROOT/'ripcars_coordination.py').read_bytes()
        for kind in KINDS[:-1]:self.assertEqual((Path(os.environ['RIPCARS_'+kind.upper()+'_ROOT'])/'ripcars_coordination.py').read_bytes(),expected)

    def test_raffle_consumes_a_dedicated_verifier_role_without_assigning_it(self):
        verifier=self.peer('verifier');raffle=self.peer('raffle')
        self.assertTrue(verifier.command('bind_role')['bound'])
        before=raffle.command('resources')['resources']
        self.assertTrue(raffle.command('eligible')['eligible'])
        self.assertFalse(raffle.command('ineligible')['eligible'])
        self.assertEqual(raffle.command('resources')['resources'],before)


def contention(holder_kind,contender_kind):
    def test(self):
        holder=self.peer(holder_kind);contender=self.peer(contender_kind)
        self.assertTrue(holder.command('acquire')['acquired'])
        self.assertTrue(contender.command('acquire')['blocked'])
        self.assertTrue(holder.command('release')['released'])
        self.assertTrue(contender.command('acquire')['acquired'])
        self.assertTrue(contender.command('release')['released'])
    return test


for holder_kind,contender_kind in itertools.permutations(KINDS,2):
    setattr(FourBotTests,'test_'+holder_kind+'_blocks_'+contender_kind,contention(holder_kind,contender_kind))


if __name__=='__main__':unittest.main()
