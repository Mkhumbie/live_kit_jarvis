"""
LiveKit Video Frame Integration for Facial Recognition

This module demonstrates how to integrate facial recognition with LiveKit's video streams.
It provides examples for setting up video processing pipelines and handling face recognition
events during video calls.

Usage:
1. Initialize the video processor in your LiveKit agent
2. Subscribe to video frames from participants
3. Process frames for facial recognition
4. Handle recognition events (e.g., store in memory, announce to user)
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable
import numpy as np

# LiveKit imports
from livekit import rtc
from livekit.agents import Agent, WorkerContext

# Import our facial recognition processor
from facial_recognition import LiveKitFaceRecognitionProcessor

logger = logging.getLogger(__name__)


class FacialRecognitionAgent(Agent):
    """
    Enhanced LiveKit Agent with facial recognition capabilities.
    
    This agent processes video frames from participants to recognize faces
    and can store/recall information about people in the conversation.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.face_processor: Optional[LiveKitFaceRecognitionProcessor] = None
        self.video_track: Optional[rtc.RemoteVideoTrack] = None
        self.recognition_handlers: Dict[str, Callable] = {}
        
    async def on_connect(self, room: rtc.Room):
        """Called when the agent connects to a LiveKit room."""
        logger.info("Agent connected to room: %s", room.name)
        
        # Initialize facial recognition processor
        try:
            self.face_processor = LiveKitFaceRecognitionProcessor(
                on_face_recognized=self._handle_face_recognition
            )
            logger.info("Facial recognition processor initialized")
        except ImportError:
            logger.warning("Facial recognition not available - missing dependencies")
        
        # Subscribe to existing participants' video tracks
        for participant in room.remote_participants.values():
            await self._subscribe_to_participant_video(participant)
    
    async def on_participant_connected(self, room: rtc.Room, participant: rtc.RemoteParticipant):
        """Called when a new participant joins the room."""
        logger.info("Participant connected: %s", participant.identity)
        await self._subscribe_to_participant_video(participant)
    
    async def on_track_published(self, room: rtc.Room, track: rtc.RemoteTrack, participant: rtc.RemoteParticipant):
        """Called when a participant publishes a new track."""
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            logger.info("Video track published by %s", participant.identity)
            await self._handle_video_track(track, participant)
    
    async def _subscribe_to_participant_video(self, participant: rtc.RemoteParticipant):
        """Subscribe to a participant's video tracks."""
        for track_pub in participant.track_publications.values():
            if (track_pub.track and 
                track_pub.track.kind == rtc.TrackKind.KIND_VIDEO and
                isinstance(track_pub.track, rtc.RemoteVideoTrack)):
                await self._handle_video_track(track_pub.track, participant)
    
    async def _handle_video_track(self, track: rtc.RemoteVideoTrack, participant: rtc.RemoteParticipant):
        """Handle a new video track from a participant."""
        if not self.face_processor:
            return
        
        logger.info("Setting up video processing for %s", participant.identity)
        
        # Create a video stream processor
        video_stream = rtc.VideoStream(track)
        
        # Process frames in the background
        asyncio.create_task(self._process_video_frames(video_stream, participant))
    
    async def _process_video_frames(self, video_stream: rtc.VideoStream, participant: rtc.RemoteParticipant):
        """Process video frames for facial recognition."""
        if not self.face_processor:
            return
        
        logger.info("Starting video frame processing for %s", participant.identity)
        
        try:
            async for frame in video_stream:
                # Process frame for facial recognition
                await self.face_processor.process_video_frame(frame)
                
                # Add small delay to prevent overwhelming the processor
                await asyncio.sleep(0.033)  # ~30 FPS processing rate
                
        except asyncio.CancelledError:
            logger.info("Video processing cancelled for %s", participant.identity)
        except Exception as e:
            logger.error("Error processing video frames for %s: %s", participant.identity, e)
    
    async def _handle_face_recognition(self, face_info: Dict[str, Any]):
        """Handle a face recognition event."""
        person_id = face_info.get('person_id')
        confidence = face_info.get('confidence', 0.0)
        
        logger.info("Face recognized: %s (confidence: %.2f)", person_id, confidence)
        
        # Store in agent memory if available
        if hasattr(self, 'memory_client') and self.memory_client:
            try:
                memory_text = f"Recognized {person_id} via video call with {confidence:.1%} confidence"
                
                # Store in memory (implementation depends on your memory client)
                if hasattr(self.memory_client, 'add'):
                    self.memory_client.add(memory_text, user_id=person_id)
                elif hasattr(self.memory_client, 'store'):
                    self.memory_client.store(memory_text, user_id=person_id)
                    
                logger.info("Stored face recognition in memory: %s", memory_text)
            except Exception as e:
                logger.error("Failed to store face recognition in memory: %s", e)
        
        # Call any registered handlers
        for handler_name, handler in self.recognition_handlers.items():
            try:
                await handler(face_info)
            except Exception as e:
                logger.error("Error in recognition handler %s: %s", handler_name, e)
        
        # You can extend this to:
        # - Announce the recognition via TTS
        # - Update the UI with person information
        # - Trigger specific actions based on who was recognized
        # - Log recognition events for analytics
    
    def register_recognition_handler(self, name: str, handler: Callable):
        """Register a custom handler for face recognition events."""
        self.recognition_handlers[name] = handler
        logger.info("Registered face recognition handler: %s", name)
    
    def unregister_recognition_handler(self, name: str):
        """Unregister a face recognition handler."""
        if name in self.recognition_handlers:
            del self.recognition_handlers[name]
            logger.info("Unregistered face recognition handler: %s", name)


# Example usage and integration patterns
def setup_facial_recognition_agent() -> FacialRecognitionAgent:
    """
    Example setup for a facial recognition agent.
    
    This shows how to create and configure the agent with custom recognition handlers.
    """
    
    # Create the agent (you'll need to pass your actual agent configuration)
    agent = FacialRecognitionAgent()
    
    # Example: Add a handler to announce recognitions
    async def announce_recognition(face_info: Dict[str, Any]):
        """Announce when someone is recognized."""
        person_id = face_info.get('person_id')
        confidence = face_info.get('confidence', 0.0)
        metadata = face_info.get('metadata', {})
        
        name = metadata.get('name', person_id) if metadata else person_id
        
        # This would be sent via TTS or chat in your actual implementation
        announcement = f"Hello {name}! Nice to see you again."
        logger.info("Recognition announcement: %s", announcement)
        
        # In practice, you might use the agent's TTS system:
        # await agent.say(announcement)
    
    # Register the handler
    agent.register_recognition_handler("announcer", announce_recognition)
    
    return agent


async def main():
    """
    Example main function showing how to run the facial recognition agent.
    
    Note: This is a simplified example. In practice, you'll need to integrate
    this with your LiveKit room connection and agent lifecycle management.
    """
    
    # Create the agent
    agent = setup_facial_recognition_agent()
    
    # In practice, you'd connect to a LiveKit room and run the agent
    # This is just to show the structure
    logger.info("Facial recognition agent created and configured")
    
    # The agent would be started by your LiveKit agent framework
    # e.g., agent.start() or similar depending on your setup


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run the example
    asyncio.run(main())


# Additional helper functions for integration

def add_person_from_video_call(agent: FacialRecognitionAgent, person_id: str, name: str, role: str = ""):
    """
    Helper function to add someone to facial recognition during a video call.
    
    This could be triggered by voice commands like "Remember this person as John"
    """
    
    async def capture_and_add_handler(face_info: Dict[str, Any]):
        """Capture face from current video frame and add to database."""
        try:
            # In practice, you'd capture the current frame and save it
            # Then use add_known_face to add them to the database
            
            # This is a placeholder - actual implementation would:
            # 1. Capture current video frame
            # 2. Save frame as image file
            # 3. Call add_known_face with the image path
            
            logger.info("Would add %s (%s) to facial recognition database", name, person_id)
            
        except Exception as e:
            logger.error("Failed to add person to database: %s", e)
    
    # Register a one-time handler
    agent.register_recognition_handler(f"add_person_{person_id}", capture_and_add_handler)
    
    # You might want to remove this handler after a timeout
    async def cleanup():
        await asyncio.sleep(10)  # Wait 10 seconds
        agent.unregister_recognition_handler(f"add_person_{person_id}")
    
    asyncio.create_task(cleanup())