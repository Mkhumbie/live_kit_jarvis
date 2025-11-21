# Pre-Facial Recognition State Snapshot

**Created:** November 21, 2025  
**Purpose:** Clean snapshot of LiveKit Jarvis agent before facial recognition integration

## What's Included

This directory contains the core files in their **pre-facial recognition state**:

- `agent.py` - Main LiveKit agent with Google LLM integration
- `tools.py` - Complete email/calendar/contact management tools  
- `prompts.py` - Agent persona and instructions
- `requirements.txt` - Core dependencies (no facial recognition packages)
- `.env` - Environment configuration template

## Current Features (Working)

✅ **Email Management:**
- Read emails (`read_emails`, `read_email_content`)
- Search emails (`search_emails`)
- Intelligent email parsing (no raw content/signatures)

✅ **Calendar Management:**
- View calendar (`view_calendar`)
- Add/edit/delete events (`add_calendar_event`, `edit_calendar_event`, `delete_calendar_event`)
- Intelligent caching system with 5-minute TTL
- Background cache refresh to avoid LiveKit timeout

✅ **Contact Management:**
- View/add/edit/delete contacts
- Full Microsoft Graph integration

✅ **Core Features:**
- mem0 conversation memory
- Google Gemini LLM with realtime voice
- LiveKit video support (basic, no processing)
- Noise cancellation
- Anti-hallucination measures with TOOL_OUTPUT markers

## What's NOT Included

❌ **No Facial Recognition:**
- No MediaPipe integration
- No OpenCV dependencies
- No video frame processing
- No face detection/recognition
- No camera capture beyond basic LiveKit video

## To Restore This State

If you ever need to restore to this clean pre-facial recognition state:

1. Copy these files back to the main directory
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python agent.py dev`

## Dependencies

The `requirements.txt` contains only core packages:
- livekit-agents
- livekit-plugins-google
- livekit-plugins-noise-cancellation
- livekit-plugins-silero
- mem0ai
- msal (for Microsoft Graph)
- Standard Python packages

**No facial recognition dependencies** like mediapipe, opencv-python, etc.