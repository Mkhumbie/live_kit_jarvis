import asyncio
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from tools import read_emails

async def main():
    # Call the tool function directly to simulate the agent invoking it
    result = await read_emails(None, limit=5)
    print("\n=== TOOL RETURNED ===")
    print(result)

if __name__ == '__main__':
    asyncio.run(main())
