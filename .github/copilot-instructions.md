# Copilot Instructions for LiveKit Jarvis Agent

## Project Overview
LiveKit Jarvis is a voice-based AI agent application using the LiveKit real-time communication framework with OpenAI integration. The project uses `livekit-agents` SDK to build conversational agents that can participate in real-time voice calls.

## Architecture & Components

### Core Structure
- **agent.py** - Main agent orchestration (to be implemented): Initializes the LiveKit agent, sets up hooks for call events, and manages the conversation lifecycle
- **tools.py** - Agent tools/actions (to be implemented): Defines custom tools the agent can invoke during conversations (e.g., search, data lookup, external API calls)
- **prompts.py** - System prompts and instructions (to be implemented): Contains the agent's persona, behavior guidelines, and conversation templates
- **requirements.txt** - Python dependencies managing the agent stack

### Key Dependencies
- **livekit-agents** - Core framework for building voice agents on LiveKit
- **livekit-plugins-openai** - OpenAI integration (LLM and speech services)
- **livekit-plugins-silero** - Offline speech-to-text option
- **livekit-plugins-google** - Google Cloud speech/TTS integration
- **livekit-plugins-noise-cancellation** - Audio preprocessing
- **mem0ai** - Memory/context management across conversations
- **langchain_community** - LangChain utilities for agent tooling
- **duckduckgo-search** - Web search capability for agent tools

### Configuration
LiveKit credentials are stored in `.env`:
- `LIVEKIT_URL` - WebSocket connection to the LiveKit server
- `LIVEKIT_API_KEY` & `LIVEKIT_API_SECRET` - Authentication for room management
- `OPENAI_API_KEY` - For LLM and speech models
- `GOOGLE_API_*` - For Google Cloud services (optional)

## Typical Development Workflow

### Setup
```bash
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
```

### Agent Implementation Pattern
1. Define tools in `tools.py` using LangChain's `Tool` or custom callable classes
2. Write system prompts in `prompts.py` that instruct the agent how to use these tools
3. In `agent.py`, use `livekit.agents.VoiceAssistant` or similar to:
   - Connect to LiveKit room with credentials from `.env`
   - Register tools and prompt configuration
   - Attach event handlers (on_message, on_shutdown, etc.)

### Adding New Capabilities
- **Web search**: Use `duckduckgo_search` module for query results
- **Memory/Context**: Use `mem0ai` to persist user context across sessions
- **Custom tools**: Create callable functions that accept parsed arguments and return results

## Conventions & Patterns

### Tool Definition
Keep tools simple and focused - one action per tool. Tools should:
- Accept structured input (dict or dataclass)
- Return string/JSON output for LLM consumption
- Include clear error messages

### Prompts
System prompts should:
- Define agent role and constraints
- List available tools and when to use them
- Specify response format preferences
- Include examples of tool invocation

### Environment Variables
Never hardcode credentials. Always load from `.env` using `python-dotenv`:
```python
from dotenv import load_dotenv
import os
load_dotenv()
api_key = os.getenv('OPENAI_API_KEY')
```

## Debugging & Troubleshooting

### Common Issues
- **WebSocket connection failures**: Check `LIVEKIT_URL` format (should be `wss://...`)
- **Tool invocation failures**: Ensure tool output is JSON-serializable or plain text
- **Speech recognition errors**: Check audio input permissions and try `livekit-plugins-silero` for offline fallback
- **Memory issues**: `mem0ai` requires valid API credentials - check environment setup

### Logging
Use Python's `logging` module. LiveKit agents emit logs at `livekit.agents.*` namespace:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger('livekit.agents').setLevel(logging.DEBUG)
```

## Testing Strategy
(To be defined as project develops)
- Unit test tools independently with mock inputs
- Integration test agent with test LiveKit rooms
- Use recordings for regression testing speech models

## Key Files to Reference
- LiveKit Agents docs: https://docs.livekit.io/agents/
- LangChain tools integration: https://python.langchain.com/docs/modules/tools/
- OpenAI API: https://platform.openai.com/docs/api-reference
