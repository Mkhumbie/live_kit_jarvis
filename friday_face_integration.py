"""
Friday Face Integration Module

Enhanced facial recognition engine for LiveKit Jarvis Agent with:
- PyTorch FaceNet-based face detection and recognition
- LiveKit video frame processing integration
- SQLite database for face embeddings storage
- Async processing with threadpool for non-blocking operations
- Memory integration for conversation context
- Comprehensive error handling and logging

Author: GitHub Copilot
Integration Date: November 2025
"""

import asyncio
import uuid
import json
import os
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any, Callable
import numpy as np
from PIL import Image
import logging
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from pathlib import Path

# Import torch and related libraries with error handling
try:
    import torch
    import cv2
    from facenet_pytorch import MTCNN, InceptionResnetV1
    TORCH_AVAILABLE = True
except ImportError as e:
    TORCH_AVAILABLE = False
    torch = None
    cv2 = None
    MTCNN = None
    InceptionResnetV1 = None
    print(f"Warning: PyTorch facial recognition dependencies not available: {e}")

# Configure logging for facial recognition
logger = logging.getLogger("friday_face")
logger.setLevel(logging.DEBUG)

# Add handler if none exists
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# -----------------------
# Configuration with Environment Variables
# -----------------------
FRAME_INTERVAL = int(os.getenv("FACE_FRAME_INTERVAL", "5"))              # process every Nth frame
MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.82"))       # cosine similarity threshold
DB_PATH = os.getenv("FACE_DB_PATH", "faces.db")                          # database file path
def torch_available() -> bool:
    """Check if torch is available and functional"""
    return TORCH_AVAILABLE and torch is not None

DEVICE = torch.device("cuda" if torch_available() and torch.cuda.is_available() else "cpu") if TORCH_AVAILABLE else "cpu"
MAX_WORKERS = int(os.getenv("FACE_MAX_WORKERS", "2"))                    # threadpool size for model inference
ENABLE_AUTO_ENROLL = os.getenv("FACE_AUTO_ENROLL", "true").lower() == "true"  # auto-enroll unknown faces
DEBUG_SAVE_FRAMES = os.getenv("FACE_DEBUG_SAVE_FRAMES", "false").lower() == "true"  # save debug frames

# -----------------------
# Utility Functions
# -----------------------
def emb_to_blob(emb: np.ndarray) -> bytes:
    """Convert numpy embedding to binary blob for database storage"""
    return emb.astype(np.float32).tobytes()

def blob_to_emb(blob: bytes) -> np.ndarray:
    """Convert binary blob back to numpy embedding"""
    return np.frombuffer(blob, dtype=np.float32)

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Calculate cosine similarity between two embeddings"""
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def normalize_frame(frame: Any) -> Optional[np.ndarray]:
    """
    Normalize different frame formats to RGB numpy array.
    Supports: numpy arrays, PIL Images, OpenCV frames, etc.
    """
    try:
        if frame is None:
            return None
            
        # Handle PIL Image
        if hasattr(frame, 'convert'):
            return np.array(frame.convert('RGB'))
        
        # Handle numpy array
        if isinstance(frame, np.ndarray):
            # Convert BGR to RGB if needed (OpenCV default)
            if frame.ndim == 3 and frame.shape[2] == 3:
                # Assume BGR and convert to RGB
                return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if cv2 else frame[:,:,::-1]
            return frame
        
        # Handle bytes or other formats
        if isinstance(frame, bytes):
            # Try to decode as image
            import io
            img = Image.open(io.BytesIO(frame))
            return np.array(img.convert('RGB'))
            
        logger.warning(f"Unsupported frame type: {type(frame)}")
        return None
        
    except Exception as e:
        logger.error(f"Error normalizing frame: {e}")
        return None

# -----------------------
# Enhanced SQLite Database with Thread Safety
# -----------------------
class FaceDB:
    """Thread-safe SQLite database for face embeddings storage"""
    
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._local = threading.local()
        self.db_lock = threading.Lock()
        self._ensure_directory()
        self._create_table()
        logger.info(f"FaceDB initialized at: {path}")

    def _ensure_directory(self):
        """Ensure the database directory exists"""
        db_dir = Path(self.path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    @property
    def conn(self):
        """Thread-local database connection"""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.path, check_same_thread=False)
        return self._local.conn

    def _create_table(self):
        """Create the faces table with enhanced schema"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS faces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    metadata TEXT,
                    confidence REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'manual'
                )
            """)
            
            # Add indexes for better performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_faces_created_at ON faces(created_at)")
            
            self.conn.commit()

    def add_face(self, name: str, embedding: np.ndarray, metadata: Dict = None, confidence: float = 1.0, source: str = 'manual') -> str:
        """Add a new face to the database"""
        with self.db_lock:
            id_ = str(uuid.uuid4())
            blob = emb_to_blob(embedding)
            timestamp = datetime.utcnow().isoformat()
            
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO faces (id, name, embedding, created_at, updated_at, metadata, confidence, source) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (id_, name, blob, timestamp, timestamp, json.dumps(metadata or {}), confidence, source))
            
            self.conn.commit()
            logger.info(f"Added face: {name} (ID: {id_}, source: {source})")
            return id_

    def get_all(self) -> List[Tuple[str, str, np.ndarray, Dict, float, str]]:
        """Get all faces from database"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("SELECT id, name, embedding, metadata, confidence, source FROM faces ORDER BY created_at DESC")
            rows = cur.fetchall()
            
            result = []
            for r in rows:
                try:
                    emb = blob_to_emb(r[2])
                    meta = json.loads(r[3] or "{}")
                    confidence = r[4] if r[4] is not None else 1.0
                    source = r[5] or 'manual'
                    result.append((r[0], r[1], emb, meta, confidence, source))
                except Exception as e:
                    logger.error(f"Error loading face record {r[0]}: {e}")
                    continue
            
            return result

    def update_name(self, id_: str, new_name: str) -> bool:
        """Update the name of a face record"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("UPDATE faces SET name = ?, updated_at = ? WHERE id = ?", 
                       (new_name, datetime.utcnow().isoformat(), id_))
            
            success = cur.rowcount > 0
            self.conn.commit()
            
            if success:
                logger.info(f"Updated face name: {id_} -> {new_name}")
            else:
                logger.warning(f"Face ID not found for update: {id_}")
                
            return success

    def delete_face(self, id_: str) -> bool:
        """Delete a face record"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM faces WHERE id = ?", (id_,))
            
            success = cur.rowcount > 0
            self.conn.commit()
            
            if success:
                logger.info(f"Deleted face: {id_}")
            else:
                logger.warning(f"Face ID not found for deletion: {id_}")
                
            return success

    def get_face_count(self) -> int:
        """Get total number of faces in database"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM faces")
            return cur.fetchone()[0]

    def search_by_name(self, name_pattern: str) -> List[Tuple[str, str, np.ndarray, Dict, float, str]]:
        """Search faces by name pattern"""
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT id, name, embedding, metadata, confidence, source 
                FROM faces 
                WHERE name LIKE ? 
                ORDER BY created_at DESC
            """, (f"%{name_pattern}%",))
            
            rows = cur.fetchall()
            result = []
            for r in rows:
                try:
                    emb = blob_to_emb(r[2])
                    meta = json.loads(r[3] or "{}")
                    confidence = r[4] if r[4] is not None else 1.0
                    source = r[5] or 'manual'
                    result.append((r[0], r[1], emb, meta, confidence, source))
                except Exception as e:
                    logger.error(f"Error loading face record {r[0]}: {e}")
                    continue
            
            return result

# -----------------------
# Enhanced Face Recognition Engine
# -----------------------
class FaceRecognizer:
    """Enhanced face recognition using FaceNet with error handling"""
    
    def __init__(self, device=None):
        if not torch_available():
            raise RuntimeError("PyTorch and facial recognition dependencies are not available. Install: torch facenet-pytorch opencv-python")
        
        self.device = device or DEVICE
        self.models_initialized = False
        self._init_models()

    def _init_models(self):
        """Initialize MTCNN and FaceNet models with error handling"""
        try:
            logger.info(f"Initializing face recognition models on device: {self.device}")
            
            # Initialize MTCNN for face detection
            self.mtcnn = MTCNN(
                keep_all=True, 
                device=self.device,
                min_face_size=40,  # minimum face size in pixels
                thresholds=[0.6, 0.7, 0.7],  # detection thresholds
                post_process=False
            )
            
            # Initialize InceptionResnetV1 for face recognition
            self.model = InceptionResnetV1(pretrained='vggface2').eval().to(self.device)
            
            self.models_initialized = True
            logger.info("Face recognition models initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize face recognition models: {e}")
            self.models_initialized = False
            raise

    def detect_and_embed(self, frame_rgb: np.ndarray) -> List[Dict[str, Any]]:
        """
        Detect faces and extract embeddings from RGB frame.
        Returns list of dicts with bbox and embedding info.
        """
        if not self.models_initialized:
            logger.error("Face recognition models not initialized")
            return []

        try:
            # Convert numpy array to PIL Image
            if frame_rgb.dtype != np.uint8:
                frame_rgb = (frame_rgb * 255).astype(np.uint8)
            
            img = Image.fromarray(frame_rgb)
            
            # Detect faces
            boxes, probs = self.mtcnn.detect(img)
            results = []
            
            if boxes is None:
                logger.debug("No faces detected in frame")
                return results

            logger.debug(f"Detected {len(boxes)} faces")
            
            # Process each detected face
            faces = []
            boxes_int = []
            valid_indices = []
            
            for i, (box, prob) in enumerate(zip(boxes, probs)):
                if prob < 0.9:  # Skip low-confidence detections
                    logger.debug(f"Skipping low-confidence detection: {prob:.3f}")
                    continue
                
                # Convert box coordinates to integers and ensure they're valid
                x1, y1, x2, y2 = [int(max(0, v)) for v in box]
                
                # Ensure box is within image bounds
                h, w = img.size[1], img.size[0]  # PIL Image.size is (width, height)
                x1, x2 = max(0, x1), min(w, x2)
                y1, y2 = max(0, y1), min(h, y2)
                
                # Skip if box is too small
                if (x2 - x1) < 30 or (y2 - y1) < 30:
                    logger.debug(f"Skipping small face detection: {x2-x1}x{y2-y1}")
                    continue
                
                # Crop and resize face to 160x160 for FaceNet
                try:
                    crop = img.crop((x1, y1, x2, y2)).convert('RGB').resize((160, 160), Image.LANCZOS)
                    face_array = np.asarray(crop).astype(np.float32) / 255.0
                    faces.append(face_array)
                    boxes_int.append((x1, y1, x2, y2))
                    valid_indices.append(i)
                except Exception as e:
                    logger.warning(f"Error processing face crop: {e}")
                    continue

            if not faces:
                logger.debug("No valid faces after processing")
                return results

            # Generate embeddings
            try:
                tensors = torch.tensor(np.stack(faces)).permute(0, 3, 1, 2).to(self.device)
                
                with torch.no_grad():
                    embeddings = self.model(tensors).cpu().numpy().astype(np.float32)
                
                # Create results
                for i, (box, emb, orig_idx) in enumerate(zip(boxes_int, embeddings, valid_indices)):
                    results.append({
                        "bbox": box,
                        "embedding": emb,
                        "confidence": float(probs[orig_idx]),
                        "face_size": (box[2] - box[0], box[3] - box[1])
                    })
                
                logger.debug(f"Successfully generated {len(results)} face embeddings")
                
            except Exception as e:
                logger.error(f"Error generating embeddings: {e}")
                return []

            return results

        except Exception as e:
            logger.error(f"Error in detect_and_embed: {e}")
            return []

# -----------------------
# Main Orchestrator with LiveKit Integration
# -----------------------
class FridayFaceEngine:
    """
    Main facial recognition engine with async processing and LiveKit integration
    """
    
    def __init__(self, db: FaceDB = None, recognizer: FaceRecognizer = None, 
                 notification_callback: Optional[Callable] = None):
        self.db = db or FaceDB()
        self.recognizer = recognizer
        self.notification_callback = notification_callback
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.frame_counter = 0
        self.last_identifications = {}  # Cache recent identifications
        self.processing_active = True
        
        # Initialize recognizer if not provided and torch is available
        if self.recognizer is None and torch_available():
            try:
                self.recognizer = FaceRecognizer()
                logger.info("FaceRecognizer initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize FaceRecognizer: {e}")
                self.recognizer = None
        
        logger.info(f"FridayFaceEngine initialized (recognizer={'available' if self.recognizer else 'unavailable'})")

    async def process_frame_async(self, frame: Any, user_context: Dict = None) -> List[Dict[str, Any]]:
        """
        Process a video frame for face recognition.
        Returns list of identification results with context.
        """
        if not self.processing_active or not self.recognizer:
            return []

        self.frame_counter += 1
        
        # Process only every Nth frame to manage performance
        if (self.frame_counter % FRAME_INTERVAL) != 0:
            return []

        try:
            # Normalize frame to RGB numpy array
            frame_rgb = normalize_frame(frame)
            if frame_rgb is None:
                logger.debug("Frame normalization failed")
                return []

            # Save debug frame if enabled
            if DEBUG_SAVE_FRAMES:
                self._save_debug_frame(frame_rgb, self.frame_counter)

            # Run face detection and recognition in threadpool
            loop = asyncio.get_event_loop()
            detections = await loop.run_in_executor(
                self.executor, 
                self.recognizer.detect_and_embed, 
                frame_rgb
            )
            
            if not detections:
                return []

            # Match against known faces
            db_entries = self.db.get_all()
            results = []
            
            for detection in detections:
                embedding = detection["embedding"]
                bbox = detection["bbox"]
                detection_confidence = detection.get("confidence", 1.0)
                
                # Find best match in database
                best_match = self._find_best_match(embedding, db_entries)
                
                if best_match and best_match["score"] >= MATCH_THRESHOLD:
                    # Recognized face
                    result = {
                        "identified": True,
                        "id": best_match["id"],
                        "name": best_match["name"],
                        "score": best_match["score"],
                        "bbox": bbox,
                        "detection_confidence": detection_confidence,
                        "face_size": detection.get("face_size"),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    
                    # Check if this is a new identification (avoid spam)
                    if self._is_new_identification(result):
                        await self._handle_identification(result, user_context)
                    
                    results.append(result)
                    
                else:
                    # Unknown face - handle based on auto-enroll setting
                    if ENABLE_AUTO_ENROLL:
                        placeholder_name = f"unknown_{str(uuid.uuid4())[:8]}"
                        new_id = self.db.add_face(
                            name=placeholder_name, 
                            embedding=embedding,
                            metadata={
                                "auto_enrolled": True,
                                "detection_confidence": float(detection_confidence),
                                "bbox": bbox,
                                "timestamp": datetime.utcnow().isoformat()
                            },
                            confidence=float(detection_confidence),
                            source="auto_enroll"
                        )
                        
                        result = {
                            "identified": False,
                            "id": new_id,
                            "name": placeholder_name,
                            "score": best_match["score"] if best_match else 0.0,
                            "bbox": bbox,
                            "detection_confidence": detection_confidence,
                            "auto_enrolled": True,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                        
                        await self._handle_unknown_face(result, user_context)
                        results.append(result)
                    else:
                        # Just report as unknown without storing
                        result = {
                            "identified": False,
                            "id": None,
                            "name": "Unknown Person",
                            "score": best_match["score"] if best_match else 0.0,
                            "bbox": bbox,
                            "detection_confidence": detection_confidence,
                            "auto_enrolled": False,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                        results.append(result)

            return results

        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            return []

    def _find_best_match(self, embedding: np.ndarray, db_entries: List) -> Optional[Dict[str, Any]]:
        """Find the best matching face in the database"""
        best_score = -1.0
        best_match = None
        
        for face_id, name, db_emb, metadata, confidence, source in db_entries:
            try:
                score = cosine_similarity(embedding, db_emb)
                if score > best_score:
                    best_score = score
                    best_match = {
                        "id": face_id,
                        "name": name,
                        "score": score,
                        "metadata": metadata,
                        "db_confidence": confidence,
                        "source": source
                    }
            except Exception as e:
                logger.warning(f"Error comparing with face {face_id}: {e}")
                continue
        
        return best_match

    def _is_new_identification(self, result: Dict[str, Any]) -> bool:
        """Check if this identification is new (to avoid notification spam)"""
        person_id = result["id"]
        current_time = datetime.utcnow()
        
        # Check if we've seen this person recently (within last 30 seconds)
        if person_id in self.last_identifications:
            last_time = self.last_identifications[person_id]
            time_diff = (current_time - last_time).total_seconds()
            if time_diff < 30:  # 30 second cooldown
                return False
        
        self.last_identifications[person_id] = current_time
        return True

    async def _handle_identification(self, result: Dict[str, Any], user_context: Dict = None):
        """Handle successful face identification with context-aware messaging"""
        name = result["name"]
        score = result["score"]
        
        # Create context-aware message
        if name.lower() == "mkhumbie":
            # For Mkhumbie, use a more personal approach
            message = f"Mkhumbie detected in video (confidence: {score:.1%})"
        else:
            # For others, use standard identification message
            message = f"{name} detected in video (confidence: {score:.1%})"
        
        logger.info(f"Identified: {message}")
        
        # Add visual context to result for notification handler
        result["detection_message"] = message
        result["is_primary_user"] = (name.lower() == "mkhumbie")
        
        if self.notification_callback:
            try:
                await self.notification_callback(message, result, user_context)
            except Exception as e:
                logger.error(f"Notification callback error: {e}")

    async def _handle_unknown_face(self, result: Dict[str, Any], user_context: Dict = None):
        """Handle unknown face detection"""
        if result.get("auto_enrolled"):
            message = f"I detected an unknown person and added them as '{result['name']}'"
            logger.info(f"Auto-enrolled: {message}")
            
            if self.notification_callback:
                try:
                    await self.notification_callback(message, result, user_context)
                except Exception as e:
                    logger.error(f"Notification callback error: {e}")

    def _save_debug_frame(self, frame_rgb: np.ndarray, frame_number: int):
        """Save debug frame for troubleshooting"""
        try:
            debug_dir = Path("debug_frames")
            debug_dir.mkdir(exist_ok=True)
            
            frame_path = debug_dir / f"frame_{frame_number:06d}.jpg"
            Image.fromarray(frame_rgb).save(frame_path, quality=85)
            
        except Exception as e:
            logger.warning(f"Failed to save debug frame: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics"""
        return {
            "total_faces": self.db.get_face_count(),
            "frames_processed": self.frame_counter,
            "models_initialized": self.recognizer is not None and self.recognizer.models_initialized,
            "processing_active": self.processing_active,
            "auto_enroll_enabled": ENABLE_AUTO_ENROLL,
            "match_threshold": MATCH_THRESHOLD,
            "frame_interval": FRAME_INTERVAL
        }

    def stop_processing(self):
        """Stop frame processing"""
        self.processing_active = False
        logger.info("Face processing stopped")

    def start_processing(self):
        """Start/resume frame processing"""
        self.processing_active = True
        logger.info("Face processing started")

    def cleanup(self):
        """Cleanup resources"""
        self.stop_processing()
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=True)
        logger.info("FridayFaceEngine cleanup completed")

# -----------------------
# Default Notification Handler
# -----------------------
async def default_notify(agent_instance, message: str, face_result: Dict = None, user_context: Dict = None):
    """
    Enhanced notification handler for face recognition events with personalized greetings.
    Provides special greeting behavior when Mkhumbie is detected.
    """
    logger.info(f"Face Recognition Event: {message}")
    
    # Check if this is Mkhumbie being detected
    personalized_message = message
    if face_result and face_result.get("identified") and face_result.get("name"):
        name = face_result["name"]
        score = face_result.get("score", 0)
        
        # Dynamic greeting based on detected person
        import random
        
        # Check if this person appears to be the primary user (most frequent in database)
        is_primary_user = name.lower() in ["mkhumbie"]  # Can be expanded based on usage patterns
        
        if is_primary_user:
            # Special greeting for primary user with British butler flair
            greeting_options = [
                f"Ah, there you are, {name}! Good to see your face again, Sir.",
                f"Well, well. If it isn't {name} himself. How may I assist you today?",
                f"I can see you clearly now, {name}. Ready to make my day interesting, are we?",
                f"Excellent! {name} is in view. I'm at your service, Sir.",
                f"Ah, {name}! There you are. I was wondering when you'd show up.",
                f"Welcome back, {name}. Ready to get started?",
                f"Good to see you, {name}. What shall we accomplish today?"
            ]
            personalized_message = random.choice(greeting_options)
        else:
            # Acknowledge other people politely but warmly
            greeting_options = [
                f"I see {name} has joined us. Welcome!",
                f"Ah, hello there {name}. Good to see you.",
                f"Welcome, {name}. How may I assist you today?",
                f"{name} is in view. Good to see you again."
            ]
            personalized_message = random.choice(greeting_options)
    
    # Try different notification methods based on agent capabilities
    if agent_instance:
        # Method 1: Try to speak the personalized message if TTS is available
        if hasattr(agent_instance, 'speak'):
            try:
                await agent_instance.speak(personalized_message)
                return
            except Exception as e:
                logger.debug(f"TTS speak failed: {e}")
        
        # Method 2: Try to send chat message if available
        if hasattr(agent_instance, 'send_chat_message'):
            try:
                await agent_instance.send_chat_message(personalized_message)
                return
            except Exception as e:
                logger.debug(f"Chat message failed: {e}")
        
        # Method 3: Try to send message to user if available
        if hasattr(agent_instance, 'send_message_to_user'):
            try:
                await agent_instance.send_message_to_user(personalized_message)
                return
            except Exception as e:
                logger.debug(f"User message failed: {e}")
    
    # Fallback: just log and print with personalized message
    print(f"🎩 Friday: {personalized_message}")

# -----------------------
# Module Initialization Check
# -----------------------
def check_dependencies() -> Dict[str, bool]:
    """Check if all required dependencies are available"""
    deps = {
        "torch": torch_available(),
        "opencv": cv2 is not None,
        "facenet_pytorch": MTCNN is not None and InceptionResnetV1 is not None,
        "PIL": True,  # Always available with Pillow
        "numpy": True,  # Always available
        "sqlite3": True  # Part of Python standard library
    }
    
    missing = [k for k, v in deps.items() if not v]
    if missing:
        logger.warning(f"Missing dependencies: {missing}")
        logger.warning("Install with: pip install torch facenet-pytorch opencv-python Pillow numpy")
    else:
        logger.info("All facial recognition dependencies are available")
    
    return deps

# Initialize dependency check
DEPENDENCIES_OK = all(check_dependencies().values())

if __name__ == "__main__":
    # Quick test of the facial recognition engine
    async def test_engine():
        if not DEPENDENCIES_OK:
            print("Dependencies not met. Cannot run test.")
            return
        
        # Initialize components
        db = FaceDB("test_faces.db")
        
        try:
            recognizer = FaceRecognizer()
            engine = FridayFaceEngine(db, recognizer)
            
            print(f"Engine statistics: {engine.get_statistics()}")
            print("Facial recognition engine test completed successfully")
            
        except Exception as e:
            print(f"Test failed: {e}")
        finally:
            if 'engine' in locals():
                engine.cleanup()
    
    asyncio.run(test_engine())