import logging
import os
from datetime import datetime, timedelta
import asyncio
import inspect
from dotenv import load_dotenv

# Try to import both sync and async memory clients from mem0
MemoryClient = None
AsyncMemoryClient = None
try:
    from mem0 import MemoryClient  # type: ignore
except Exception:
    pass

try:
    from mem0 import AsyncMemoryClient  # type: ignore
except Exception:
    pass

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, RoomInputOptions, ChatContext
from livekit.rtc import VideoStream, TrackKind
# LiveKit plugins (import as needed)
try:
    from livekit.plugins import noise_cancellation
except ImportError:
    noise_cancellation = None
try:
    from livekit.plugins import google
except ImportError:
    google = None
    
try:
    from livekit.plugins import silero
except ImportError:
    silero = None
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from tools import (
    read_emails, read_email_content, search_emails,
    view_calendar, add_calendar_event, edit_calendar_event, delete_calendar_event,
    view_contacts, add_contact, edit_contact, delete_contact,
    test_simple_tool,
    # Facial recognition tools
    list_known_faces, add_known_face, remove_known_face, rename_known_face,
    face_recognition_status, configure_face_recognition, test_face_capture,
    # Database manipulation tools
    update_face_attributes, get_face_details, search_faces_by_attribute, query_database,
    # Voice and multi-modal recognition tools
    voice_recognition_status, list_voice_profiles, multimodal_status, link_user_profiles
)

load_dotenv()

# Configure logging for the agent with clear timestamp and level info
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("LiveKit Jarvis Agent module loaded")


class Assistant(Agent):
    def __init__(self, memory_client=None) -> None:
        # Store memory client for later use in tool functions and session
        self.memory_client = memory_client
        self.face_engine = None  # Will be initialized when needed
        self.voice_engine = None  # Will be initialized when needed
        self.multimodal_engine = None  # Will be initialized when needed
        logger.info("Initializing Assistant with tools: emails, calendar, contacts, facial recognition, voice recognition, multi-modal identification, database manipulation, test tools")
        
        # Enhanced debugging: back to basic Agent with tool result monitoring
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.0,  # Completely deterministic for debugging
            ),
            tools=[
                # Email Tools
                read_emails,
                read_email_content,
                search_emails,
                
                # Calendar Tools
                view_calendar,
                add_calendar_event,
                edit_calendar_event,
                delete_calendar_event,
                
                # Contact Tools
                view_contacts,
                add_contact,
                edit_contact,
                delete_contact,
                
                # Facial Recognition Tools
                list_known_faces,
                add_known_face,
                remove_known_face,
                rename_known_face,
                face_recognition_status,
                configure_face_recognition,
                test_face_capture,
                
                # Database Manipulation Tools
                update_face_attributes,
                get_face_details,
                search_faces_by_attribute,
                query_database,
                
                # Voice and Multi-Modal Recognition Tools
                voice_recognition_status,
                list_voice_profiles,
                multimodal_status,
                link_user_profiles,
                
                # Utility
                test_simple_tool,
            ]
        )
        logger.info("Assistant initialized successfully")


def get_memory_client():
    """Initialize mem0 memory client with credentials from .env"""
    try:
        api_key = os.getenv("MEM0_API_KEY")
        # Prefer synchronous MemoryClient to avoid complex async/await handling
        if MemoryClient is not None:
            try:
                memory_client = MemoryClient()
                logger.info("mem0 Memory Client initialized using default constructor")
                return memory_client
            except Exception:
                logger.debug("MemoryClient() failed, will try constructor with api_key")
            try:
                # Try passing api_key explicitly
                if api_key:
                    memory_client = MemoryClient(api_key=api_key)
                    logger.info("mem0 Memory Client initialized with api_key")
                    return memory_client
            except Exception:
                logger.exception("mem0.MemoryClient initialization failed with api_key")

        # Try mem0ai package if available as a fallback
        try:
            import mem0ai
            if hasattr(mem0ai, "MemoryClient"):
                try:
                    mc = mem0ai.MemoryClient()
                    logger.info("mem0ai MemoryClient initialized via default constructor")
                    return mc
                except Exception:
                    if api_key:
                        mc = mem0ai.MemoryClient(api_key=api_key)
                        logger.info("mem0ai MemoryClient initialized with api_key")
                        return mc
            elif hasattr(mem0ai, "Client"):
                try:
                    mc = mem0ai.Client()
                    logger.info("mem0ai Client initialized via default constructor")
                    return mc
                except Exception:
                    if api_key:
                        mc = mem0ai.Client(api_key=api_key)
                        logger.info("mem0ai Client initialized with api_key")
                        return mc
            else:
                logger.debug("mem0ai package imported but no known client class found")
        except Exception:
            logger.debug("mem0ai import not available")

        # Try AsyncMemoryClient as fallback (async variant)
        if AsyncMemoryClient is not None:
            try:
                mc = AsyncMemoryClient()
                logger.info("Async mem0 Memory Client initialized using default constructor")
                return mc
            except Exception:
                logger.debug("AsyncMemoryClient() failed, will try constructor with api_key")
            try:
                if api_key:
                    mc = AsyncMemoryClient(api_key=api_key)
                    logger.info("Async mem0 Memory Client initialized with api_key")
                    return mc
            except Exception:
                logger.exception("AsyncMemoryClient initialization failed with api_key")

        logger.error("No supported mem0 client available; memory disabled.")
        return None
    except Exception as e:
        logger.exception("Failed to initialize mem0 Memory Client: %s", e)
        return None


async def get_conversation_memory(memory_client, user_name="Mkhumbie"):
    """Retrieve previous conversation memories from mem0 and format them

    Supports both sync and async mem0 clients. Returns a formatted string suitable for injecting into
    session instructions.
    """
    if not memory_client:
        logger.debug("No memory client available, skipping memory retrieval")
        return ""

    try:
        memories = None
        
        # Check if this is an async client
        is_async = hasattr(memory_client, '__class__') and 'Async' in memory_client.__class__.__name__
        
        # Try get_all(filters=...) first as in your tested script
        if hasattr(memory_client, "get_all"):
            try:
                result = memory_client.get_all(filters={"user_id": user_name})
                if is_async and inspect.iscoroutine(result):
                    memories = await result
                else:
                    memories = result
            except TypeError:
                try:
                    result = memory_client.get_all(user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        memories = await result
                    else:
                        memories = result
                except Exception:
                    memories = None
            except Exception:
                memories = None

        # If no memories yet, try search with filters
        if memories is None and hasattr(memory_client, "search"):
            try:
                result = memory_client.search(query=f"What are {user_name}'s preferences?", filters={"user_id": user_name})
                if is_async and inspect.iscoroutine(result):
                    memories = await result
                else:
                    memories = result
            except TypeError:
                try:
                    result = memory_client.search(query=user_name, user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        memories = await result
                    else:
                        memories = result
                except Exception:
                    memories = None
            except Exception:
                memories = None

        # Fallback to REST search
        if memories is None:
            try:
                memories = mem0_http_search(user_name)
            except Exception:
                memories = []

        # Normalize memories to a list of strings if it's a dict or other structure
        if memories is None:
            memories = []
        elif isinstance(memories, dict):
            memories = memories.get("results") or memories.get("items") or memories.get("memories") or []

        if memories:
            logger.info("Retrieved %d previous memories for user %s", len(memories), user_name)
            # Format memories as readable text
            memory_items = []
            for m in memories:
                if isinstance(m, str):
                    memory_items.append(f"- {m}")
                elif isinstance(m, dict):
                    text = m.get("memory") or m.get("content") or m.get("text") or str(m)
                    memory_items.append(f"- {text}")
            memory_text = "\n".join(memory_items)
            return f"\n\nPrevious conversation context:\n{memory_text}"
        else:
            logger.info("No previous memories found for user %s", user_name)
            return ""
    except Exception as e:
        logger.exception("Error retrieving memories: %s", e)
        return ""


def mem0_http_create(content: str, user_id: str) -> bool:
    """Fallback: create a memory directly via mem0 REST API using requests.

    This is a best-effort fallback for when installed SDKs don't match expected
    method signatures. It logs HTTP responses for debugging.
    """
    try:
        api_key = os.getenv("MEM0_API_KEY")
        if not api_key:
            logger.warning("mem0_http_create: MEM0_API_KEY missing, cannot create memory")
            return False
        url = "https://api.mem0.ai/v2/memories/"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "text": content,
            "metadata": {"user_id": user_id},
        }
        import requests

        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            logger.info("mem0_http_create: memory created via REST API for user %s", user_id)
            return True
        else:
            logger.error("mem0_http_create: failed (%d): %s", r.status_code, r.text)
            return False
    except Exception as e:
        logger.exception("mem0_http_create: exception creating memory: %s", e)
        return False


def mem0_http_search(user_name: str):
    """Fallback: search memories via mem0 REST API. Returns list of memory texts or []."""
    try:
        api_key = os.getenv("MEM0_API_KEY")
        if not api_key:
            logger.warning("mem0_http_search: MEM0_API_KEY missing, cannot search memories")
            return []
        url = "https://api.mem0.ai/v2/memories/search/"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"query": f"{user_name}", "limit": 50}
        import requests

        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Try to extract text fields from common shapes
            items = []
            if isinstance(data, dict):
                results = data.get("results") or data.get("items") or data.get("memories") or []
            else:
                results = data
            for it in results:
                if isinstance(it, str):
                    items.append(it)
                elif isinstance(it, dict):
                    text = it.get("text") or it.get("content") or it.get("memory") or str(it)
                    items.append(text)
            logger.info("mem0_http_search: found %d items for user %s", len(items), user_name)
            return items
        else:
            logger.error("mem0_http_search: failed (%d): %s", r.status_code, r.text)
            return []
    except Exception as e:
        logger.exception("mem0_http_search: exception searching memories: %s", e)
        return []


async def ensure_user_identity_memory(memory_client, user_name="Mkhumbie"):
    """Ensure a one-time memory exists recording the user's identity.

    This records a simple memory like 'User identity: Mkhumbie' once.
    Works with both async and sync mem0 clients.
    """
    if not memory_client:
        return
    try:
        # Check if this is an async client
        is_async = hasattr(memory_client, '__class__') and 'Async' in memory_client.__class__.__name__
        
        # Check for an existing identity memory
        existing = []
        try:
            if hasattr(memory_client, "get_all"):
                # Try calling get_all with filters
                try:
                    result = memory_client.get_all(filters={"user_id": user_name})
                    if is_async and inspect.iscoroutine(result):
                        existing = await result
                    else:
                        existing = result
                except TypeError:
                    # Fallback without filters
                    result = memory_client.get_all(user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        existing = await result
                    else:
                        existing = result
            elif hasattr(memory_client, "search"):
                try:
                    result = memory_client.search(query=f"user identity: {user_name}", filters={"user_id": user_name})
                    if is_async and inspect.iscoroutine(result):
                        existing = await result
                    else:
                        existing = result
                except TypeError:
                    result = memory_client.search(query=f"user identity: {user_name}", user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        existing = await result
                    else:
                        existing = result
            else:
                existing = []
        except Exception as e:
            logger.exception("Error checking existing identity memories: %s", e)
            existing = []

        # If no existing memory contains the user identity, add one
        found = False
        if existing:
            # Normalize response to list of strings
            if isinstance(existing, dict):
                items = existing.get("results") or existing.get("items") or existing.get("memories") or []
            else:
                items = existing
            for e in items:
                if isinstance(e, str) and user_name in e:
                    found = True
                    break
                if isinstance(e, dict) and user_name in str(e):
                    found = True
                    break

        if not found:
            # mem0 expects either plain text `memory=` or `messages=` depending on SDK
            messages = [
                {"role": "user", "content": f"User identity: {user_name}"}
            ]
            plain_text = f"User identity: {user_name}"
            try:
                # Prefer add(memory=...) signature used by your tested script
                if hasattr(memory_client, "add"):
                    try:
                        result = memory_client.add(memory=plain_text, user_id=user_name)
                        if is_async and inspect.iscoroutine(result):
                            await result
                        logger.info("Stored identity memory via add(memory=...) for user %s", user_name)
                    except TypeError:
                        # Fallback to messages= signature
                        result = memory_client.add(messages=messages, user_id=user_name)
                        if is_async and inspect.iscoroutine(result):
                            await result
                        logger.info("Stored identity memory via add(messages=...) for user %s", user_name)
                elif hasattr(memory_client, "create"):
                    result = memory_client.create(messages, user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        await result
                    logger.info("Stored identity memory via create for user %s", user_name)
                elif hasattr(memory_client, "upsert"):
                    result = memory_client.upsert(messages, user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        await result
                    logger.info("Stored identity memory via upsert for user %s", user_name)
                elif hasattr(memory_client, "save"):
                    result = memory_client.save(messages, user_id=user_name)
                    if is_async and inspect.iscoroutine(result):
                        await result
                    logger.info("Stored identity memory via save for user %s", user_name)
                else:
                    # Fallback to HTTP create
                    ok = mem0_http_create(plain_text, user_id=user_name)
                    if ok:
                        logger.info("Stored identity memory via REST fallback for user %s", user_name)
                    else:
                        logger.warning("Could not persist identity memory; unsupported client API and REST fallback failed")
            except Exception as e:
                logger.exception("Error storing identity memory: %s", e)
        else:
            logger.debug("Identity memory for user %s already exists", user_name)
    except Exception as e:
        logger.exception("Error ensuring user identity memory: %s", e)


# =====================================================
# FACIAL RECOGNITION INTEGRATION
# =====================================================

async def initialize_face_engine():
    """Initialize facial recognition engine with error handling"""
    try:
        # Import facial recognition modules
        from friday_face_integration import (
            FridayFaceEngine, FaceDB, FaceRecognizer, 
            DEPENDENCIES_OK, torch_available
        )
        
        if not DEPENDENCIES_OK:
            logger.warning("Facial recognition dependencies not available - feature disabled")
            return None
        
        logger.info("Initializing facial recognition engine...")
        
        # Create database and recognizer
        db = FaceDB()
        recognizer = FaceRecognizer() if torch_available() else None
        
        # Create notification callback for face recognition events
        async def face_notification_callback(message: str, result: dict = None, context: dict = None):
            """Handle face recognition notifications by logging and potentially speaking"""
            logger.info(f"Face Recognition Event: {message}")
            
            # TODO: Integrate with agent's speech/chat capabilities
            # This could be enhanced to make the agent speak the notification:
            # if hasattr(context.get('agent'), 'speak'):
            #     await context['agent'].speak(message)
            
            print(f"🔍 Friday Face Recognition: {message}")
        
        # Initialize engine
        engine = FridayFaceEngine(db, recognizer, face_notification_callback)
        logger.info("Facial recognition engine initialized successfully")
        
        return engine
        
    except ImportError as e:
        logger.warning(f"Facial recognition not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize facial recognition engine: {e}")
        return None


async def process_video_frame(engine, frame_data, user_context=None):
    """
    Process a video frame through facial recognition engine.
    
    Args:
        engine: FridayFaceEngine instance
        frame_data: Video frame data from LiveKit
        user_context: Context information for the session
    
    Returns:
        List of face recognition results
    """
    if not engine:
        return []
    
    try:
        # Process frame through facial recognition
        results = await engine.process_frame_async(frame_data, user_context)
        
        if results:
            logger.debug(f"Processed frame: {len(results)} faces detected")
            for result in results:
                if result.get("identified"):
                    name = result.get("name", "Unknown")
                    confidence = result.get("score", 0)
                    logger.info(f"Identified: {name} (confidence: {confidence:.2%})")
        
        return results
        
    except Exception as e:
        logger.error(f"Error processing video frame: {e}")
        return []


async def entrypoint(ctx: agents.JobContext):
    """Main entry point for the agent session"""
    logger.info("Agent entrypoint started")
    
    # Get current date and time for agent awareness
    current_datetime = datetime.now()
    current_time_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M:%S")
    
    # Calculate date references for relative date handling
    current_date = current_datetime.strftime("%Y-%m-%d")
    tomorrow_date = (current_datetime + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_date = (current_datetime - timedelta(days=1)).strftime("%Y-%m-%d")
    
    logger.info("Current time: %s", current_time_str)
    
    # Initialize memory client for storing and retrieving conversation history
    memory_client = get_memory_client()
    user_name = "Mkhumbie"
    # Ensure one-time identity memory exists for the user before any conversation
    try:
        await ensure_user_identity_memory(memory_client, user_name=user_name)
    except Exception:
        logger.exception("Failed to ensure user identity memory")
    
    # Retrieve previous conversation memories to inform current session
    memory_context = await get_conversation_memory(memory_client, user_name=user_name)
    
    # Combine SESSION_INSTRUCTION with memory context and current time so agent has full history and awareness
    time_context = f"\n\n## Current Date & Time\nThe current date and time is: {current_time_str}\n"
    
    # Format session instruction with actual dates for relative date handling
    formatted_session_instruction = SESSION_INSTRUCTION.format(
        current_date=current_date,
        tomorrow_date=tomorrow_date,
        yesterday_date=yesterday_date
    )
    
    instructions_with_memory = formatted_session_instruction + time_context + memory_context
    memory_str = memory_context
    
    # Initialize facial recognition engine
    logger.info("Initializing facial recognition engine...")
    face_engine = await initialize_face_engine()
    if face_engine:
        logger.info("Facial recognition engine ready")
    else:
        logger.info("Facial recognition engine not available - continuing without it")
    
    # Initialize voice recognition engine
    logger.info("Initializing voice recognition engine...")
    voice_engine = None
    try:
        from voice_recognition import get_voice_engine, VOICE_RECOGNITION_AVAILABLE
        if VOICE_RECOGNITION_AVAILABLE:
            voice_engine = await get_voice_engine()
            logger.info("Voice recognition engine ready")
        else:
            logger.info("Voice recognition dependencies not available")
    except Exception as e:
        logger.warning(f"Voice recognition engine not available: {e}")
    
    # Initialize multi-modal identification engine
    logger.info("Initializing multi-modal identification engine...")
    multimodal_engine = None
    try:
        from multimodal_recognition import get_multimodal_engine
        multimodal_engine = await get_multimodal_engine(face_engine, voice_engine)
        logger.info("Multi-modal identification engine ready")
    except Exception as e:
        logger.warning(f"Multi-modal identification engine not available: {e}")

    logger.info("Creating AgentSession")
    session = AgentSession()
    
    # Create assistant instance with all recognition engines
    assistant = Assistant(memory_client=memory_client)
    assistant.face_engine = face_engine
    assistant.voice_engine = voice_engine
    assistant.multimodal_engine = multimodal_engine

    logger.info("Starting agent session with noise cancellation and video settings")
    
    # Enable video for dev/production mode and facial recognition
    enable_video = True  # Video enabled for camera access and facial recognition
    
    logger.info("Video enabled: %s", enable_video)
    
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_input_options=RoomInputOptions(
            # For telephony applications, use `BVCTelephony` instead for best results
            noise_cancellation=noise_cancellation.BVC(),
            video_enabled=enable_video,
        ),
    )

    # Pre-warm calendar cache for fast first access
    logger.info("Pre-warming calendar cache for optimal performance")
    try:
        from tools import warm_calendar_cache
        await warm_calendar_cache()
        logger.info("Calendar cache pre-warming completed successfully")
    except Exception as e:
        logger.warning("Calendar cache pre-warming failed: %s", e)

    logger.info("Generating initial reply to user")
    await session.generate_reply(
        instructions=instructions_with_memory,
    )

    # Set up video frame processing for facial recognition if available
    if face_engine:
        logger.info("Setting up video frame processing for facial recognition")
        
        # Set up video track subscription and processing
        @session.room.on("track_subscribed")
        async def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.TrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            if track.kind == TrackKind.VIDEO:
                logger.info(f"📹 Video track subscribed from {participant.identity}")
                
                # Create video stream and process frames
                video_stream = VideoStream(track)
                
                async def process_video_frames():
                    try:
                        async for frame in video_stream:
                            # Process frame through facial recognition
                            user_context = {
                                "participant_id": participant.identity,
                                "session_id": session.room.name
                            }
                            
                            # Process frame asynchronously
                            await process_video_frame(face_engine, frame, user_context)
                            
                    except Exception as e:
                        logger.error(f"Video frame processing error: {e}")
                
                # Start processing frames in background
                asyncio.create_task(process_video_frames())
        
        logger.info("✅ Facial recognition video processing enabled")
    else:
        logger.info("❌ Facial recognition not available - skipping video frame processing setup")

    async def shutdown_hook(chat_ctx: "ChatContext", mem0_client, user_name: str, memory_str: str):
        logger.info("Shutting down, saving chat context to memory...")
        
        # Check if this is an async client
        is_async = hasattr(mem0_client, '__class__') and 'Async' in mem0_client.__class__.__name__ if mem0_client else False
        
        messages_formatted = []
        try:
            logger.info(f"Chat context messages count: {len(chat_ctx.items)}")
            for item in chat_ctx.items:
                # Safely extract a string representation from any chat item type.
                content_str = ""
                try:
                    # Try content attribute first (Message, ChatMessage)
                    if hasattr(item, 'content'):
                        if isinstance(item.content, list):
                            content_str = ''.join(str(c) for c in item.content)
                        else:
                            content_str = str(item.content)
                    # FunctionCall-like: use name + arguments
                    elif hasattr(item, 'name') and hasattr(item, 'arguments'):
                        name = getattr(item, 'name', '')
                        args = getattr(item, 'arguments', {})
                        content_str = f"function_call: {name}({args})"
                    elif hasattr(item, 'tool_call_id'):
                        # FunctionCallResult or similar
                        content_str = f"tool_result: {getattr(item, 'tool_call_id', '')} = {getattr(item, 'result', str(item))}"
                    elif hasattr(item, 'value'):
                        content_str = str(getattr(item, 'value'))
                    else:
                        content_str = str(item)
                except Exception as e:
                    logger.debug(f"Failed to extract content from item {type(item)}: {e}")
                    content_str = str(item)

                # Skip if the content is the memory context string we injected at startup
                if memory_str and memory_str in content_str:
                    continue

                role = getattr(item, 'role', None)
                # Only persist user/assistant messages or best-effort text from function calls
                if role in ['user', 'assistant']:
                    messages_formatted.append({
                        "role": role,
                        "content": content_str.strip()
                    })
                elif content_str.strip():
                    # Include function call summaries as assistant-invoked tool evidence
                    messages_formatted.append({
                        "role": role or 'assistant',
                        "content": content_str.strip()
                    })

            logger.info(f"Formatted {len(messages_formatted)} messages to add to memory")
            if messages_formatted:
                if mem0_client is None:
                    logger.warning("No mem0 client available; skipping memory save")
                    return
                # Prepare plain text fallback
                plain = "\n".join([m.get("content") for m in messages_formatted])
                try:
                    saved_resp = None
                    if hasattr(mem0_client, "add"):
                        try:
                            # Try `memory=` signature first
                            result = mem0_client.add(memory=plain, user_id=user_name)
                            if is_async and inspect.iscoroutine(result):
                                saved_resp = await result
                            else:
                                saved_resp = result
                            logger.info("Saved chat context via add(memory=...)")
                        except TypeError:
                            # Fallback to messages= signature
                            result = mem0_client.add(messages=messages_formatted, user_id=user_name)
                            if is_async and inspect.iscoroutine(result):
                                saved_resp = await result
                            else:
                                saved_resp = result
                            logger.info("Saved chat context via add(messages=...)")
                    else:
                        ok = mem0_http_create(plain, user_id=user_name)
                        if ok:
                            logger.info("Saved chat context via REST create")
                            saved_resp = {"rest": True}
                        else:
                            logger.error("Failed to save messages via REST fallback")

                    logger.info("mem0 add/create response: %s", repr(saved_resp)[:100])  # Log first 100 chars
                except Exception as e:
                    logger.exception("Failed to persist chat context via mem0 client: %s", e)

                # Immediate verification: try to read back recent memories using get_all/search
                try:
                    if hasattr(mem0_client, "get_all"):
                        result = mem0_client.get_all(filters={"user_id": user_name})
                        if is_async and inspect.iscoroutine(result):
                            got = await result
                        else:
                            got = result
                        logger.info("Verification get_all() returned %d items", len(got) if isinstance(got, (list, dict)) else 1)
                    elif hasattr(mem0_client, "search"):
                        try:
                            result = mem0_client.search(query=user_name, filters={"user_id": user_name})
                            if is_async and inspect.iscoroutine(result):
                                got = await result
                            else:
                                got = result
                        except TypeError:
                            result = mem0_client.search(query=user_name, user_id=user_name)
                            if is_async and inspect.iscoroutine(result):
                                got = await result
                            else:
                                got = result
                        logger.info("Verification search() returned %d items", len(got) if isinstance(got, (list, dict)) else 1)
                    else:
                        logger.debug("No get_all/search available for verification; skipping")
                except Exception as e:
                    logger.exception("Verification readback failed: %s", e)
            logger.info("Chat context shutdown save completed.")
        except Exception:
            logger.exception("Failure while saving chat context on shutdown")

    # Register shutdown hook to persist chat context at the end of the session.
    # Wrap the async coroutine in a sync callback that schedules it properly.
    async def _run_shutdown():
        try:
            await shutdown_hook(session._agent.chat_ctx, memory_client, user_name, memory_str)
        except Exception:
            logger.exception("Shutdown hook task failed")

    # Register as async callback directly
    ctx.add_shutdown_callback(_run_shutdown)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))