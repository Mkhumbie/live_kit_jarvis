"""
Test agent using OpenAI instead of Google Realtime to compare tool behavior
"""
import logging
import os
from dotenv import load_dotenv

from livekit.agents import Agent
from livekit.plugins import openai
from tools import test_simple_tool

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class OpenAITestAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a test agent. When asked to test tools, call the requested tool and present its exact result. Do not make up any responses.",
            llm=openai.LLM(
                model="gpt-4",  # Use a standard model
                temperature=0.1,
            ),
            tts=openai.TTS(
                model="tts-1",
                voice="alloy",
            ),
            tools=[test_simple_tool]
        )
        print("OpenAI TestAgent initialized with test_simple_tool")

if __name__ == "__main__":
    print("OpenAI test agent created successfully")
    agent = OpenAITestAgent()