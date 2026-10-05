import asyncio
import logging
import os

from ripcars_verifier.bot import Verifier
from ripcars_verifier.config import safe_url


async def main():
    token=os.getenv('DISCORD_TOKEN','')
    if not token or token.startswith('replace_'): raise SystemExit('Configure DISCORD_TOKEN in /etc/ripcars-verifier.env before starting.')
    if not os.getenv('PUBLIC_URL'): raise SystemExit('Configure PUBLIC_URL with the real HTTPS connection origin before starting.')
    safe_url(os.environ['PUBLIC_URL'],origin_only=True)
    async with Verifier() as bot: await bot.start(token)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('aiohttp.access').disabled=True
    asyncio.run(main())
