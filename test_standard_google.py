"""
Test agent using standard Google LLM instead of Realtime model
"""
import logging
import os
from dotenv import load_dotenv

from livekit.agents import Agent
from livekit.plugins import google
from tools import read_emails, test_simple_tool

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class StandardGoogleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful assistant. When asked to test tools or read emails, call the appropriate tool and present its exact result. Do not make up any responses.",
            llm=google.LLM(
                model="gemini-1.5-flash",  # Use standard model instead of realtime
                temperature=0.1,
            ),
            tts=google.TTS(),  # Use default TTS settings
            tools=[read_emails, test_simple_tool]
        )
        print("Standard Google Agent initialized with read_emails and test_simple_tool")

if __name__ == "__main__":
    print("Standard Google test agent created successfully")
    agent = StandardGoogleAgent()