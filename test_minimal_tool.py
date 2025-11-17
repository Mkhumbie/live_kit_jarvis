"""
Minimal test to isolate the tool execution issue with LiveKit agents
"""
import asyncio
import logging
from livekit.agents import function_tool, RunContext, Agent
from livekit.plugins import google

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@function_tool()
async def test_simple_return(context: RunContext) -> str:
    """Test tool that returns a simple, predictable string."""
    result = "SIMPLE_TEST_RESULT: This is the actual tool result."
    print(f"==== [SIMPLE TEST TOOL] Returning: {result} ====")
    logger.info("test_simple_return: %s", result)
    return result

class TestAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a test agent. When a user asks you to test a tool, call the test_simple_return tool and present its exact result. Do not make up any responses.",
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.1,  # Lower temperature for more deterministic behavior
            ),
            tools=[test_simple_return]
        )
        print("TestAgent initialized with test_simple_return tool")

async def test_tool_directly():
    """Test the tool function directly without agent framework."""
    print("=== Direct Tool Test ===")
    try:
        result = await test_simple_return(context=None)
        print(f"Direct test result: {result}")
        return result == "SIMPLE_TEST_RESULT: This is the actual tool result."
    except Exception as e:
        print(f"Direct test failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_tool_directly())