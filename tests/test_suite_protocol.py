"""Protocol regression checks, run independently in every bot repository."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import ripcars_coordination as protocol


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "shared.sqlite3"
        self.c = sqlite3.connect(self.path)
        self.c.execute("CREATE TABLE resources(guild INTEGER,key TEXT,object_id INTEGER,kind TEXT,owner TEXT,baseline TEXT,desired TEXT,state TEXT,PRIMARY KEY(guild,key))")
        protocol.initialize(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def token(self, guild=1):
        return protocol.claim(self.c, guild)

    def test_protocol_version_and_both_legacy_tables(self):
        self.assertEqual(self.c.execute("SELECT value FROM suite_meta").fetchone()[0], "2")
        for table, key in protocol.TABLES:
            self.assertEqual([r[1] for r in self.c.execute("PRAGMA table_info(" + table + ")")], ["guild", key, "token", "expires"])

    def test_claim_is_mirrored(self):
        token = self.token()
        self.assertEqual(self.c.execute("SELECT token,expires FROM leases").fetchall(), self.c.execute("SELECT token,expires FROM locks").fetchall())
        protocol.ensure(self.c, 1, "server-setup", token)

    def test_legacy_gate_lease_blocks_new_controller(self):
        self.c.execute("INSERT INTO leases VALUES(1,'server-setup','legacy',99999999999)")
        with self.assertRaises(ValueError):
            self.token()
        self.assertEqual(self.c.execute("SELECT token FROM leases").fetchone()[0], "legacy")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM locks").fetchone()[0], 0)

    def test_legacy_raffle_lock_blocks_new_controller(self):
        self.c.execute("INSERT INTO locks VALUES(1,'server-setup','legacy',99999999999)")
        with self.assertRaises(ValueError):
            self.token()
        self.assertEqual(self.c.execute("SELECT token FROM locks").fetchone()[0], "legacy")

    def test_wrong_release_preserves_both_holders(self):
        token = self.token()
        protocol.release(self.c, 1, "server-setup", "wrong")
        protocol.ensure(self.c, 1, "server-setup", token)

    def test_release_removes_both_own_rows(self):
        token = self.token()
        protocol.release(self.c, 1, "server-setup", token)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM leases").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM locks").fetchone()[0], 0)

    def test_changed_token_is_never_released(self):
        token = self.token()
        self.c.execute("UPDATE locks SET token='peer'")
        protocol.release(self.c, 1, "server-setup", token)
        self.assertEqual(self.c.execute("SELECT token FROM locks").fetchone()[0], "peer")

    def test_different_guilds_are_independent(self):
        first, second = self.token(1), self.token(2)
        protocol.ensure(self.c, 1, "server-setup", first)
        protocol.ensure(self.c, 2, "server-setup", second)

    def test_different_resource_locks_are_independent(self):
        first = self.token()
        second = protocol.claim(self.c, 1, "ticket:99")
        protocol.ensure(self.c, 1, "server-setup", first)
        protocol.ensure(self.c, 1, "ticket:99", second)

    def test_renewal_keeps_both_rows_equal(self):
        token = self.token()
        protocol.renew(self.c, 1, "server-setup", token, 300)
        self.assertEqual(self.c.execute("SELECT token,expires FROM leases").fetchall(), self.c.execute("SELECT token,expires FROM locks").fetchall())

    def test_expired_token_cannot_be_revived(self):
        token = self.token()
        self.c.execute("UPDATE leases SET expires=0")
        with self.assertRaises(ValueError):
            protocol.renew(self.c, 1, "server-setup", token)

    def test_ensure_checks_both_tables(self):
        token = self.token()
        self.c.execute("UPDATE locks SET token='peer'")
        with self.assertRaises(ValueError):
            protocol.ensure(self.c, 1, "server-setup", token)

    def test_expired_rows_can_be_replaced(self):
        first = self.token()
        self.c.execute("UPDATE leases SET expires=0")
        self.c.execute("UPDATE locks SET expires=0")
        second = self.token()
        self.assertNotEqual(first, second)
        protocol.release(self.c, 1, "server-setup", first)
        protocol.ensure(self.c, 1, "server-setup", second)

    def test_duplicate_identity_is_rejected_across_kinds(self):
        self.c.execute("INSERT INTO resources VALUES(1,'gate:role',9,'role','gate','{}','{}','active')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO resources VALUES(1,'peer:channel',9,'channel','peer','{}','{}','active')")

    def test_future_protocol_is_not_downgraded(self):
        self.c.execute("UPDATE suite_meta SET value='999'")
        with self.assertRaises(ValueError):
            protocol.initialize(self.c)
        self.assertEqual(self.c.execute("SELECT value FROM suite_meta").fetchone()[0], "999")

    def test_invalid_duration_does_not_create_a_lock(self):
        for ttl in (0, -1, 3601):
            with self.assertRaises(ValueError):
                protocol.claim(self.c, 1, ttl=ttl)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM locks").fetchone()[0], 0)

    def test_inspector_is_read_only_and_hides_tokens(self):
        token = self.token()
        self.c.commit()
        before = self.path.read_bytes()
        report = protocol.inspect(self.path)
        self.assertTrue(report["ok"])
        self.assertNotIn(token, str(report))
        self.assertEqual(before, self.path.read_bytes())

    def test_inspector_does_not_create_missing_database(self):
        path = self.path.parent / "absent.sqlite3"
        self.assertFalse(protocol.inspect(path)["ok"])
        self.assertFalse(path.exists())

    def test_inspector_reports_protected_resource(self):
        self.c.execute("INSERT INTO resources VALUES(1,'channel:general',9,'text','gate','{}','{}','manual')")
        self.c.commit()
        self.assertIn("manual", str(protocol.inspect(self.path)["issues"]))

    def test_inspector_reports_divergent_lock(self):
        self.token()
        self.c.execute("UPDATE locks SET token='peer'")
        self.c.commit()
        self.assertFalse(protocol.inspect(self.path)["ok"])

    def test_shared_file_keeps_owner_and_group(self):
        before = self.path.stat()
        protocol.shared_permissions(self.path)
        after = self.path.stat()
        self.assertEqual((before.st_uid, before.st_gid), (after.st_uid, after.st_gid))
        self.assertEqual(after.st_mode & 0o777, 0o660)


if __name__ == "__main__":
    unittest.main()
