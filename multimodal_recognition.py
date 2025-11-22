"""Multi-Modal User Identification System.

Integrates facial recognition and voice recognition for robust user identification.
Provides cross-validation between modalities and unified user profiles.
"""

import logging
import json
import sqlite3
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)


class MultiModalDatabase:
    """Unified database for multi-modal user profiles."""
    
    def __init__(self, db_path: str = "users_multimodal.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize the multi-modal database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # User profiles with links to face and voice IDs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                face_id TEXT,
                voice_id TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT,
                face_recognitions INTEGER DEFAULT 0,
                voice_recognitions INTEGER DEFAULT 0,
                total_recognitions INTEGER DEFAULT 0,
                last_seen TEXT
            )
        """)
        
        # Recognition events log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recognition_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                modality TEXT,
                confidence REAL,
                timestamp TEXT,
                session_id TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info(f"Multi-modal database initialized at {self.db_path}")
    
    def add_user(self, name: str, face_id: str = None, voice_id: str = None,
                 metadata: Dict = None) -> str:
        """Add a new user profile."""
        user_id = f"user_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.utcnow().isoformat()
        metadata_json = json.dumps(metadata or {})
        
        cursor.execute("""
            INSERT INTO users 
            (id, name, face_id, voice_id, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, face_id, voice_id, metadata_json, timestamp, timestamp))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Added user: {name} (ID: {user_id})")
        return user_id
    
    def link_face(self, user_identifier: str, face_id: str) -> bool:
        """Link a face ID to a user."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.utcnow().isoformat()
        cursor.execute("""
            UPDATE users 
            SET face_id = ?, updated_at = ?
            WHERE id = ? OR name = ?
        """, (face_id, timestamp, user_identifier, user_identifier))
        
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if updated:
            logger.info(f"Linked face {face_id} to user {user_identifier}")
        
        return updated
    
    def link_voice(self, user_identifier: str, voice_id: str) -> bool:
        """Link a voice ID to a user."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.utcnow().isoformat()
        cursor.execute("""
            UPDATE users 
            SET voice_id = ?, updated_at = ?
            WHERE id = ? OR name = ?
        """, (voice_id, timestamp, user_identifier, user_identifier))
        
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if updated:
            logger.info(f"Linked voice {voice_id} to user {user_identifier}")
        
        return updated
    
    def get_user_by_face(self, face_id: str) -> Optional[Tuple]:
        """Get user by face ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE face_id = ?", (face_id,))
        result = cursor.fetchone()
        
        conn.close()
        return result
    
    def get_user_by_voice(self, voice_id: str) -> Optional[Tuple]:
        """Get user by voice ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE voice_id = ?", (voice_id,))
        result = cursor.fetchone()
        
        conn.close()
        return result
    
    def get_user(self, identifier: str) -> Optional[Tuple]:
        """Get user by ID or name."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE id = ? OR name = ?", 
                      (identifier, identifier))
        result = cursor.fetchone()
        
        conn.close()
        return result
    
    def log_recognition(self, user_id: str, modality: str, 
                       confidence: float, session_id: str = None):
        """Log a recognition event."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.utcnow().isoformat()
        
        # Log event
        cursor.execute("""
            INSERT INTO recognition_events 
            (user_id, modality, confidence, timestamp, session_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, modality, confidence, timestamp, session_id))
        
        # Update user statistics
        if modality == "face":
            cursor.execute("""
                UPDATE users 
                SET face_recognitions = face_recognitions + 1,
                    total_recognitions = total_recognitions + 1,
                    last_seen = ?,
                    updated_at = ?
                WHERE id = ?
            """, (timestamp, timestamp, user_id))
        elif modality == "voice":
            cursor.execute("""
                UPDATE users 
                SET voice_recognitions = voice_recognitions + 1,
                    total_recognitions = total_recognitions + 1,
                    last_seen = ?,
                    updated_at = ?
                WHERE id = ?
            """, (timestamp, timestamp, user_id))
        
        conn.commit()
        conn.close()
    
    def get_all_users(self) -> List[Tuple]:
        """Get all users."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users ORDER BY total_recognitions DESC")
        results = cursor.fetchall()
        
        conn.close()
        return results


class MultiModalIdentificationEngine:
    """
    Multi-modal identification engine combining face and voice recognition.
    Provides unified user identification with cross-validation.
    """
    
    def __init__(self, face_engine=None, voice_engine=None):
        self.face_engine = face_engine
        self.voice_engine = voice_engine
        self.multimodal_db = MultiModalDatabase()
        
        # Confidence thresholds for each modality
        self.face_threshold = 0.82
        self.voice_threshold = 0.75
        self.multimodal_threshold = 0.70  # Lower threshold when both agree
        
        logger.info("Multi-modal identification engine initialized")
    
    async def identify_from_face(self, frame, user_context: Dict = None) -> Dict[str, Any]:
        """Identify user from face detection."""
        if not self.face_engine:
            return {"identified": False, "modality": "face", "error": "Face engine not available"}
        
        try:
            # Process frame with face engine
            results = await self.face_engine.process_frame_async(frame, user_context)
            
            if not results:
                return {"identified": False, "modality": "face"}
            
            # Get best face match
            best_result = max(results, key=lambda x: x.get("score", 0))
            
            if best_result.get("identified"):
                face_id = best_result["id"]
                
                # Look up user in multimodal database
                user_data = self.multimodal_db.get_user_by_face(face_id)
                
                if user_data:
                    user_id, name = user_data[0], user_data[1]
                    confidence = best_result["score"]
                    
                    # Log recognition
                    session_id = user_context.get("session_id") if user_context else None
                    self.multimodal_db.log_recognition(user_id, "face", confidence, session_id)
                    
                    return {
                        "identified": True,
                        "modality": "face",
                        "user_id": user_id,
                        "name": name,
                        "confidence": confidence,
                        "face_result": best_result
                    }
            
            return {"identified": False, "modality": "face"}
            
        except Exception as e:
            logger.error(f"Error identifying from face: {e}")
            return {"identified": False, "modality": "face", "error": str(e)}
    
    async def identify_from_voice(self, audio_tensor, sample_rate: int = 16000,
                                  user_context: Dict = None) -> Dict[str, Any]:
        """Identify user from voice."""
        if not self.voice_engine:
            return {"identified": False, "modality": "voice", "error": "Voice engine not available"}
        
        try:
            # Process audio with voice engine
            result = await self.voice_engine.identify_speaker(audio_tensor, sample_rate)
            
            if result.get("identified"):
                voice_id = result["id"]
                
                # Look up user in multimodal database
                user_data = self.multimodal_db.get_user_by_voice(voice_id)
                
                if user_data:
                    user_id, name = user_data[0], user_data[1]
                    confidence = result["confidence"]
                    
                    # Log recognition
                    session_id = user_context.get("session_id") if user_context else None
                    self.multimodal_db.log_recognition(user_id, "voice", confidence, session_id)
                    
                    return {
                        "identified": True,
                        "modality": "voice",
                        "user_id": user_id,
                        "name": name,
                        "confidence": confidence,
                        "voice_result": result
                    }
            
            return {"identified": False, "modality": "voice"}
            
        except Exception as e:
            logger.error(f"Error identifying from voice: {e}")
            return {"identified": False, "modality": "voice", "error": str(e)}
    
    async def identify_multimodal(self, frame=None, audio_tensor=None, 
                                  sample_rate: int = 16000,
                                  user_context: Dict = None) -> Dict[str, Any]:
        """
        Identify user using both face and voice with cross-validation.
        Provides highest confidence when both modalities agree.
        """
        results = {
            "face": None,
            "voice": None,
            "identified": False,
            "modality": "multimodal"
        }
        
        # Process both modalities in parallel
        tasks = []
        if frame is not None and self.face_engine:
            tasks.append(self.identify_from_face(frame, user_context))
        if audio_tensor is not None and self.voice_engine:
            tasks.append(self.identify_from_voice(audio_tensor, sample_rate, user_context))
        
        if not tasks:
            return results
        
        identifications = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Parse results
        for identification in identifications:
            if isinstance(identification, Exception):
                logger.error(f"Identification error: {identification}")
                continue
            
            modality = identification.get("modality")
            if modality == "face":
                results["face"] = identification
            elif modality == "voice":
                results["voice"] = identification
        
        # Cross-validate
        face_id = results["face"].get("identified") if results["face"] else False
        voice_id = results["voice"].get("identified") if results["voice"] else False
        
        if face_id and voice_id:
            # Both modalities identified someone
            face_name = results["face"]["name"]
            voice_name = results["voice"]["name"]
            
            if face_name == voice_name:
                # Perfect match - both agree
                avg_confidence = (
                    results["face"]["confidence"] + 
                    results["voice"]["confidence"]
                ) / 2
                
                results["identified"] = True
                results["user_id"] = results["face"]["user_id"]
                results["name"] = face_name
                results["confidence"] = avg_confidence
                results["validation"] = "cross-validated"
                
                logger.info(f"Multi-modal identification: {face_name} "
                          f"(face: {results['face']['confidence']:.2%}, "
                          f"voice: {results['voice']['confidence']:.2%})")
            else:
                # Conflict - different people identified
                results["conflict"] = True
                results["face_name"] = face_name
                results["voice_name"] = voice_name
                logger.warning(f"Multi-modal conflict: face={face_name}, voice={voice_name}")
        
        elif face_id:
            # Only face identified
            results["identified"] = True
            results["user_id"] = results["face"]["user_id"]
            results["name"] = results["face"]["name"]
            results["confidence"] = results["face"]["confidence"]
            results["validation"] = "face-only"
        
        elif voice_id:
            # Only voice identified
            results["identified"] = True
            results["user_id"] = results["voice"]["user_id"]
            results["name"] = results["voice"]["name"]
            results["confidence"] = results["voice"]["confidence"]
            results["validation"] = "voice-only"
        
        return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get system statistics."""
        users = self.multimodal_db.get_all_users()
        
        return {
            "total_users": len(users),
            "face_engine_available": self.face_engine is not None,
            "voice_engine_available": self.voice_engine is not None,
            "users": [
                {
                    "name": u[1],
                    "has_face": u[2] is not None,
                    "has_voice": u[3] is not None,
                    "face_recognitions": u[7],
                    "voice_recognitions": u[8],
                    "total_recognitions": u[9],
                    "last_seen": u[10]
                }
                for u in users
            ]
        }


# Global multimodal engine instance
_multimodal_engine: Optional[MultiModalIdentificationEngine] = None


async def get_multimodal_engine(face_engine=None, voice_engine=None) -> MultiModalIdentificationEngine:
    """Get or create the global multi-modal identification engine."""
    global _multimodal_engine
    
    if _multimodal_engine is None:
        logger.info("Initializing multi-modal identification engine...")
        _multimodal_engine = MultiModalIdentificationEngine(face_engine, voice_engine)
        logger.info("Multi-modal identification engine initialized")
    
    # Update engines if provided
    if face_engine:
        _multimodal_engine.face_engine = face_engine
    if voice_engine:
        _multimodal_engine.voice_engine = voice_engine
    
    return _multimodal_engine
