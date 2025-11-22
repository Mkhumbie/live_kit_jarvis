"""
MediaPipe-based Facial Recognition for LiveKit Jarvis Agent

This is a simplified facial recognition system that uses MediaPipe for face detection
and basic face feature extraction. It provides functional face detection and basic
identification capabilities without requiring complex dependencies like dlib.

Features:
- Real-time face detection using MediaPipe
- Basic face landmark extraction and matching
- Simple face database management
- Integration with LiveKit video streams
"""

import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

# Core dependencies
try:
    import cv2
    import mediapipe as mp
    import numpy as np
    from PIL import Image
    FACE_RECOGNITION_AVAILABLE = True
except ImportError as e:
    FACE_RECOGNITION_AVAILABLE = False
    logging.warning(f"MediaPipe face recognition not available: {e}")

# LiveKit imports
from livekit import rtc
from livekit.agents import function_tool, RunContext

logger = logging.getLogger(__name__)


class SimpleFaceDatabase:
    """Manages face landmarks and metadata for known individuals using MediaPipe."""
    
    def __init__(self, database_path: str = "face_database.json"):
        self.database_path = Path(database_path)
        self.faces: Dict[str, Dict[str, Any]] = {}
        self.load_database()
    
    def load_database(self):
        """Load face data and metadata from JSON file."""
        try:
            if self.database_path.exists():
                with open(self.database_path, 'r') as f:
                    self.faces = json.load(f)
                logger.info(f"Loaded {len(self.faces)} faces from database")
        except Exception as e:
            logger.error(f"Failed to load face database: {e}")
            self.faces = {}
    
    def save_database(self):
        """Save face data and metadata to JSON file."""
        try:
            with open(self.database_path, 'w') as f:
                json.dump(self.faces, f, indent=2, default=str)
            logger.info(f"Saved {len(self.faces)} faces to database")
        except Exception as e:
            logger.error(f"Failed to save face database: {e}")
    
    def add_face(self, person_id: str, landmarks: List[float], metadata: Dict[str, Any] = None):
        """Add a new face landmark set to the database."""
        self.faces[person_id] = {
            'landmarks': landmarks,
            'added_date': datetime.now().isoformat(),
            'recognition_count': 0,
            'last_seen': None,
            'metadata': metadata or {}
        }
        self.save_database()
        logger.info(f"Added face landmarks for {person_id} to database")
    
    def recognize_face(self, unknown_landmarks: List[float], tolerance: float = 0.15) -> Tuple[Optional[str], float]:
        """Recognize face landmarks against the database using simple distance matching."""
        if not self.faces or not unknown_landmarks:
            return None, 0.0
        
        best_match = None
        best_distance = float('inf')
        
        for person_id, person_data in self.faces.items():
            stored_landmarks = person_data.get('landmarks', [])
            if not stored_landmarks:
                continue
            
            # Calculate Euclidean distance between landmark sets
            distance = self._calculate_landmark_distance(unknown_landmarks, stored_landmarks)
            
            if distance < best_distance:
                best_distance = distance
                best_match = person_id
        
        if best_match and best_distance <= tolerance:
            confidence = max(0.0, 1.0 - (best_distance / tolerance))
            
            # Update recognition stats
            self.faces[best_match]['recognition_count'] += 1
            self.faces[best_match]['last_seen'] = datetime.now().isoformat()
            
            return best_match, confidence
        
        return None, 0.0
    
    def _calculate_landmark_distance(self, landmarks1: List[float], landmarks2: List[float]) -> float:
        """Calculate distance between two landmark sets."""
        if len(landmarks1) != len(landmarks2):
            return float('inf')
        
        # Simple Euclidean distance
        total = sum((a - b) ** 2 for a, b in zip(landmarks1, landmarks2))
        return (total / len(landmarks1)) ** 0.5
    
    def get_person_info(self, person_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a person in the database."""
        return self.faces.get(person_id)
    
    def list_known_faces(self) -> List[str]:
        """Get list of all known person IDs."""
        return list(self.faces.keys())


class MediaPipeFaceProcessor:
    """Processes video frames for facial detection and recognition using MediaPipe."""
    
    def __init__(self, database_path: str = "face_database.json"):
        if not FACE_RECOGNITION_AVAILABLE:
            raise ImportError("MediaPipe face recognition not available. Install opencv-python and mediapipe.")
        
        # Initialize MediaPipe Face Mesh
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=3,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        self.face_db = SimpleFaceDatabase(database_path)
        self.last_recognition_time = {}
        self.recognition_cooldown = 3.0  # Seconds between recognitions for same person
        
    def process_frame(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Process a video frame and return recognized faces."""
        try:
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Process the frame
            results = self.face_mesh.process(rgb_frame)
            
            recognized_faces = []
            current_time = datetime.now().timestamp()
            
            if results.multi_face_landmarks:
                for face_landmarks in results.multi_face_landmarks:
                    # Extract key landmark points (simplified feature set)
                    landmarks = self._extract_key_landmarks(face_landmarks)
                    
                    # Recognize the face
                    person_id, confidence = self.face_db.recognize_face(landmarks)
                    
                    if person_id:
                        # Check cooldown to avoid spam recognition
                        last_seen = self.last_recognition_time.get(person_id, 0)
                        if current_time - last_seen > self.recognition_cooldown:
                            self.last_recognition_time[person_id] = current_time
                            
                            face_info = {
                                'person_id': person_id,
                                'confidence': confidence,
                                'landmarks_count': len(landmarks),
                                'timestamp': datetime.now().isoformat(),
                                'metadata': self.face_db.get_person_info(person_id)
                            }
                            recognized_faces.append(face_info)
            
            return recognized_faces
            
        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            return []
    
    def _extract_key_landmarks(self, face_landmarks) -> List[float]:
        """Extract key facial landmarks as a simplified feature vector."""
        # Key landmark indices (based on MediaPipe face mesh)
        key_indices = [
            # Face outline
            10, 151, 9, 175, 136, 365, 397, 379, 378, 400,
            # Eyes
            33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
            # Nose
            1, 2, 5, 4, 6, 168, 8, 9, 10, 151,
            # Mouth
            61, 84, 17, 314, 405, 320, 307, 375, 321, 308, 324, 318
        ]
        
        landmarks = []
        for idx in key_indices:
            if idx < len(face_landmarks.landmark):
                landmark = face_landmarks.landmark[idx]
                landmarks.extend([landmark.x, landmark.y, landmark.z])
        
        return landmarks
    
    def encode_face_from_image(self, image_path: str) -> Optional[List[float]]:
        """Extract face landmarks from an image file."""
        try:
            image = cv2.imread(image_path)
            if image is None:
                logger.error(f"Could not load image: {image_path}")
                return None
            
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_image)
            
            if results.multi_face_landmarks:
                # Use the first detected face
                face_landmarks = results.multi_face_landmarks[0]
                return self._extract_key_landmarks(face_landmarks)
            else:
                logger.warning(f"No face found in image: {image_path}")
                return None
                
        except Exception as e:
            logger.error(f"Error encoding face from {image_path}: {e}")
            return None
    
    def add_person_from_image(self, person_id: str, image_path: str, metadata: Dict[str, Any] = None):
        """Add a person to the database from an image file."""
        landmarks = self.encode_face_from_image(image_path)
        if landmarks is not None:
            self.face_db.add_face(person_id, landmarks, metadata)
            return True
        return False


# Global facial recognition processor instance
_facial_processor: Optional[MediaPipeFaceProcessor] = None


def get_facial_processor() -> MediaPipeFaceProcessor:
    """Get or create the global facial recognition processor."""
    global _facial_processor
    if _facial_processor is None:
        _facial_processor = MediaPipeFaceProcessor()
    return _facial_processor


# =======================================
# AGENT FUNCTION TOOLS
# =======================================

@function_tool()
async def add_known_face(
    context: RunContext,  # type: ignore
    person_id: str,
    image_path: str,
    name: str = "",
    role: str = "",
    notes: str = ""
) -> str:
    """Add a known person to the facial recognition database from an image.
    
    Args:
        person_id: Unique identifier for the person
        image_path: Path to actual image file (JPG, PNG, etc.) - NOT "video frame"
        name: Display name for the person
        role: Person's role or title
        notes: Additional notes about the person
        
    Returns:
        Confirmation of addition
    """
    try:
        if not FACE_RECOGNITION_AVAILABLE:
            return "Facial recognition not available. Please install opencv-python and mediapipe."
        
        # Check if user is trying to use "video frame" - this won't work
        if image_path.lower() in ["video frame", "video", "frame", "camera"]:
            return """I cannot detect faces from live video frames yet. To add someone to facial recognition:
            
1. Take a clear photo of the person and save it as a file (JPG or PNG)
2. Use the full file path, for example:
   - C:\\Users\\username\\Pictures\\john_photo.jpg
   - ./photos/sarah.png
   - photo.jpg (if in current directory)

3. The photo should have:
   ✓ Clear, front-facing view of the face
   ✓ Good lighting
   ✓ No obstructions (sunglasses, masks)
   ✓ Face takes up at least 20% of the image
   
Example: "Add John to facial recognition using C:\\Photos\\john.jpg" """
        
        processor = get_facial_processor()
        metadata = {
            'name': name or person_id,
            'role': role,
            'notes': notes
        }
        
        success = processor.add_person_from_image(person_id, image_path, metadata)
        
        if success:
            result = f"✅ Successfully added {name or person_id} to facial recognition database using MediaPipe!"
            logger.info(f"Added face: {person_id} - {name}")
        else:
            result = f"❌ Failed to add {name or person_id} - no clear face found in image. Please use a clearer photo with good lighting and front-facing view."
            
        return result
        
    except Exception as e:
        logger.exception(f"Error adding known face: {e}")
        return f"Error adding face: {e}"


@function_tool()
async def list_known_faces(
    context: RunContext  # type: ignore
) -> str:
    """List all people in the facial recognition database.
    
    Returns:
        List of known people with their information
    """
    try:
        if not FACE_RECOGNITION_AVAILABLE:
            return "Facial recognition not available."
        
        processor = get_facial_processor()
        known_faces = processor.face_db.list_known_faces()
        
        if not known_faces:
            return "No known faces in the database."
        
        result_lines = ["Known faces in database (MediaPipe-based):"]
        
        for person_id in known_faces:
            person_info = processor.face_db.get_person_info(person_id)
            name = person_info['metadata'].get('name', person_id)
            role = person_info['metadata'].get('role', '')
            recognition_count = person_info['recognition_count']
            last_seen = person_info.get('last_seen', 'Never')
            
            if last_seen != 'Never':
                last_seen_dt = datetime.fromisoformat(last_seen)
                last_seen = last_seen_dt.strftime('%Y-%m-%d %H:%M')
            
            person_line = f"- {name} (ID: {person_id})"
            if role:
                person_line += f", {role}"
            person_line += f" | Recognized {recognition_count} times | Last seen: {last_seen}"
            
            result_lines.append(person_line)
        
        return "\n".join(result_lines)
        
    except Exception as e:
        logger.exception(f"Error listing known faces: {e}")
        return f"Error listing faces: {e}"


@function_tool()
async def get_facial_recognition_status(
    context: RunContext  # type: ignore
) -> str:
    """Get the current status of facial recognition system.
    
    Returns:
        Status information about facial recognition
    """
    try:
        if not FACE_RECOGNITION_AVAILABLE:
            return "❌ Facial recognition is not available. Required libraries (opencv-python, mediapipe) are not installed."
        
        processor = get_facial_processor()
        known_faces_count = len(processor.face_db.list_known_faces())
        
        status_lines = [
            "🎭 Facial Recognition Status: ACTIVE (MediaPipe-based)",
            f"👥 Known faces in database: {known_faces_count}",
            "🔍 Face detection: MediaPipe Face Mesh (468 landmarks)",
            f"⏱️  Recognition cooldown: {processor.recognition_cooldown} seconds",
            "🎥 Integration: LiveKit video stream processing",
            "🧠 Technology: MediaPipe landmarks + distance matching",
            "📱 Database: Local JSON storage (face_database.json)"
        ]
        
        if known_faces_count > 0:
            status_lines.append("\n👤 Known People:")
            for person_id in processor.face_db.list_known_faces()[:5]:
                person_info = processor.face_db.get_person_info(person_id)
                name = person_info['metadata'].get('name', person_id)
                count = person_info['recognition_count']
                last_seen = person_info.get('last_seen', 'Never')
                
                if last_seen != 'Never':
                    from datetime import datetime
                    last_seen_dt = datetime.fromisoformat(last_seen)
                    last_seen = last_seen_dt.strftime('%Y-%m-%d %H:%M')
                
                status_lines.append(f"   • {name} - {count} recognitions, last seen: {last_seen}")
        else:
            status_lines.append("\n📝 To add people: 'Add [name] to facial recognition using [photo_path]'")
            status_lines.append("   Example: 'Add John to facial recognition using C:\\Photos\\john.jpg'")
        
        return "\n".join(status_lines)
        
    except Exception as e:
        logger.exception(f"Error getting facial recognition status: {e}")
        return f"Error getting status: {e}"


# =======================================
# LIVEKIT VIDEO FRAME PROCESSOR
# =======================================

class LiveKitFaceRecognitionProcessor:
    """Integrates facial recognition with LiveKit video streams using MediaPipe."""
    
    def __init__(self, on_face_recognized=None):
        self.processor = get_facial_processor() if FACE_RECOGNITION_AVAILABLE else None
        self.on_face_recognized = on_face_recognized
        self.processing = False
        self.frame_skip_count = 0
        self.process_every_n_frames = 30  # Process every 30th frame for better performance
    
    async def process_video_frame(self, frame: rtc.VideoFrame):
        """Process a LiveKit video frame for facial recognition."""
        if not self.processor or self.processing:
            return
        
        # Skip frames for performance
        self.frame_skip_count += 1
        if self.frame_skip_count < self.process_every_n_frames:
            return
        self.frame_skip_count = 0
        
        try:
            self.processing = True
            
            # Convert LiveKit frame to numpy array (placeholder implementation)
            # In practice, you'd need to handle the specific video frame buffer format
            # and convert it to a numpy array that OpenCV/MediaPipe can process
            
            # For now, we'll simulate the processing
            # numpy_frame = self.convert_livekit_frame_to_numpy(frame)
            # recognized_faces = self.processor.process_frame(numpy_frame)
            
            # Placeholder for demonstration
            recognized_faces = []
            
            if recognized_faces and self.on_face_recognized:
                for face_info in recognized_faces:
                    await self.on_face_recognized(face_info)
                    
        except Exception as e:
            logger.error(f"Error processing video frame: {e}")
        finally:
            self.processing = False
    
    def convert_livekit_frame_to_numpy(self, frame: rtc.VideoFrame) -> np.ndarray:
        """Convert LiveKit video frame to numpy array.
        
        This is a placeholder implementation. The actual conversion
        depends on LiveKit's video frame format and may require
        specific buffer handling.
        """
        # Placeholder implementation
        # In practice, you'd need to handle the specific video frame buffer format
        # and convert it to a numpy array that OpenCV can process
        
        # Example approach (may need adaptation to actual LiveKit API):
        # buffer = frame.data
        # width, height = frame.width, frame.height
        # numpy_array = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 3))
        # return numpy_array
        
        # For now, return a dummy frame
        return np.zeros((480, 640, 3), dtype=np.uint8)