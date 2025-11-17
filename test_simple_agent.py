"""
Test the read_emails with actual LiveKit agent to see where the disconnect happens
"""
import asyncio
import logging
from livekit.agents import Agent, function_tool, RunContext
from livekit.plugins import google

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Import our working tool
from tools import read_emails

class SimpleEmailAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=(
                "You are a simple email agent. When asked to check emails, use the read_emails tool "
                "and present the EXACT result it returns. Do not make up any content. "
                "The tool will return text starting with 'TOOL_OUTPUT_START:' and ending with 'TOOL_OUTPUT_END'. "
                "Present everything between those markers."
            ),
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.0,
            ),
            tools=[read_emails]
        )

async def test_agent_tool_handling():
    """Test if the agent can properly handle our tool results"""
    print("Testing SimpleEmailAgent...")
    
    try:
        agent = SimpleEmailAgent()
        print("Agent created successfully")
        
        # We can't easily test the full flow without a LiveKit session,
        # but we can verify the agent was created with the tool
        print(f"Agent tools: {[tool.__name__ for tool in agent._tools]}")
        return True
        
    except Exception as e:
        print(f"Error creating agent: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_agent_tool_handling())