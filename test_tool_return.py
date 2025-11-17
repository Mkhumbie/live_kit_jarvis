"""
Test to verify if LiveKit agents properly handle tool results
"""
import logging
from livekit.agents import function_tool, RunContext

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@function_tool()
async def test_tool(context: RunContext) -> str:
    """Simple test tool that returns predictable output."""
    result = "TEST_OUTPUT_START: This is a test result from test_tool. TEST_OUTPUT_END"
    print(f"==== [TEST TOOL] Returning: {result} ====")
    logger.info("test_tool returning: %s", result)
    return result

if __name__ == "__main__":
    # Test the function directly
    import asyncio
    
    async def test_direct_call():
        result = await test_tool(context=None)
        print(f"Direct call result: {result}")
    
    asyncio.run(test_direct_call())