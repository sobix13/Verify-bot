import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import aiohttp

from ripcars_verifier.bot import Verifier


class RuntimeTests(IsolatedAsyncioTestCase):
    async def test_actual_web_listener_and_worker_start_without_live_discord(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); env={'PUBLIC_URL':'https://verify.example.com','WEB_HOST':'127.0.0.1','WEB_PORT':'0','DATABASE_PATH':str(root/'db.sqlite3'),'COORDINATION_PATH':str(root/'shared.sqlite3'),'BACKUP_PATH':str(root/'backups')}
            async with Verifier(env) as bot:
                bot._connection.user=SimpleNamespace(id=900); bot.tree.sync=AsyncMock(return_value=[])
                await bot.setup_hook(); bot._ready.set(); await bot.on_ready(); await asyncio.sleep(0.05)
                address=bot.runner.addresses[0]
                async with aiohttp.ClientSession() as client:
                    response=await client.get(f'http://127.0.0.1:{address[1]}/healthz'); data=await response.json()
                    self.assertEqual(response.status,200); self.assertTrue(data['worker']); self.assertTrue(data['discord'])
                    page=await client.get(f'http://127.0.0.1:{address[1]}/link'); self.assertEqual(page.status,200)
                self.assertEqual(len(bot.tree.get_command('verifier').commands),14); self.assertEqual(len(bot.tasks),2)
                await bot.on_disconnect(); self.assertFalse((await bot.web_health())['ok'])
                await bot.on_resumed(); self.assertTrue((await bot.web_health())['ok'])
            self.assertTrue(all(task.done() for task in bot.tasks))

    async def test_actual_worker_creates_daily_encrypted_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); env={'PUBLIC_URL':'https://verify.example.com','WEB_HOST':'127.0.0.1','WEB_PORT':'0','DATABASE_PATH':str(root/'db.sqlite3'),'COORDINATION_PATH':str(root/'shared.sqlite3'),'BACKUP_PATH':str(root/'backups'),'BACKUP_PASSWORD':'testing-password-with-at-least-20-chars'}
            async with Verifier(env) as bot:
                bot._connection.user=SimpleNamespace(id=900); bot.tree.sync=AsyncMock(return_value=[])
                await bot.setup_hook(); bot._ready.set(); await bot.on_ready()
                for _ in range(30):
                    if list((root/'backups').glob('*.rcvbackup')): break
                    await asyncio.sleep(0.02)
                self.assertEqual(len(list((root/'backups').glob('*.rcvbackup'))),1)
