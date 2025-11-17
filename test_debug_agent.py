"""
Minimal agent test to isolate tool result handling issue
"""
import logging
import asyncio
from livekit.agents import function_tool, RunContext, Agent
from livekit.plugins import google

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@function_tool()
async def debug_tool(context: RunContext) -> str:
    """Simple debug tool that returns predictable output"""
    result = "DEBUG_TOOL_SUCCESS: This is the actual result from debug_tool."
    print(f"==== DEBUG_TOOL RETURNING: {result} ====")
    logger.critical("DEBUG_TOOL RESULT: %s", result)
    
    # Write to file for verification
    try:
        from datetime import datetime
        with open("debug_tool_executed.txt", "w") as f:
            f.write(f"Tool executed at: {datetime.now()}\n")
            f.write(f"Result: {result}\n")
        print("==== DEBUG: Tool result written to debug_tool_executed.txt ====")
    except Exception as e:
        print(f"Debug file error: {e}")
    
    return result

class DebugAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=(
                "You are a debug agent. When the user says 'test tool', call the debug_tool function and "
                "present its exact result. Do not make up any responses. Only use what the tool returns. "
                "If the tool returns 'DEBUG_TOOL_SUCCESS: This is the actual result from debug_tool.', "
                "then say exactly that. Do not say anything else."
            ),
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.0,  # Completely deterministic
            ),
            tools=[debug_tool]
        )
        logger.info("DebugAgent initialized with debug_tool")

if __name__ == "__main__":
    print("Debug agent created")
    agent = DebugAgent()
    print("Debug agent initialization complete")