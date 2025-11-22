"""Voice Recognition and Speaker Identification Module.

This module provides voice/speaker recognition capabilities that integrate
with the facial recognition system to provide multi-modal user identification.

Features:
- Voice fingerprinting using audio embeddings
- Speaker identification from audio streams
- Voice profile management (add, remove, update)
- Integration with facial recognition for cross-validation
- LiveKit audio stream processing
"""

import logging
import os
import json
import sqlite3
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

# Check for voice recognition dependencies
VOICE_RECOGNITION_AVAILABLE = False
try:
    import torch
    import torchaudio
    from speechbrain.pretrained import EncoderClassifier
    VOICE_RECOGNITION_AVAILABLE = True
    logger.info("Voice recognition dependencies available")
except (ImportError, AttributeError) as e:
    logger.warning(f"Voice recognition dependencies not available: {e}")
    logger.warning("Install with: pip install speechbrain torchaudio")
    # Make stub classes available
    EncoderClassifier = None


class VoiceDatabase:
    """SQLite database for storing voice profiles and embeddings."""
    
    def __init__(self, db_path: str = "voices.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize the voice database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                embedding BLOB NOT NULL,
                metadata TEXT,
                confidence REAL DEFAULT 0.0,
                source TEXT DEFAULT 'manual',
                created_at TEXT,
                updated_at TEXT,
                recognition_count INTEGER DEFAULT 0,
                last_recognized TEXT
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info(f"Voice database initialized at {self.db_path}")
    
    def add_voice(self, name: str, embedding: np.ndarray, 
                  metadata: Dict = None, source: str = "manual") -> str:
        """Add a voice profile to the database."""
        voice_id = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        embedding_bytes = embedding.tobytes()
        metadata_json = json.dumps(metadata or {})
        timestamp = datetime.utcnow().isoformat()
        
        cursor.execute("""
            INSERT INTO voices 
            (id, name, embedding, metadata, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (voice_id, name, embedding_bytes, metadata_json, source, timestamp, timestamp))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Added voice profile: {name} (ID: {voice_id})")
        return voice_id
    
    def get_voice(self, identifier: str) -> Optional[Tuple]:
        """Get a voice profile by ID or name."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Try by ID first
        cursor.execute("SELECT * FROM voices WHERE id = ?", (identifier,))
        result = cursor.fetchone()
        
        # If not found, try by name
        if not result:
            cursor.execute("SELECT * FROM voices WHERE name = ?", (identifier,))
            result = cursor.fetchone()
        
        conn.close()
        return result
    
    def get_all(self) -> List[Tuple]:
        """Get all voice profiles."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM voices ORDER BY recognition_count DESC")
        results = cursor.fetchall()
        
        conn.close()
        return results
    
    def update_recognition(self, voice_id: str):
        """Update recognition count and last recognized timestamp."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.utcnow().isoformat()
        cursor.execute("""
            UPDATE voices 
            SET recognition_count = recognition_count + 1,
                last_recognized = ?,
                updated_at = ?
            WHERE id = ?
        """, (timestamp, timestamp, voice_id))
        
        conn.commit()
        conn.close()
    
    def remove_voice(self, identifier: str) -> bool:
        """Remove a voice profile."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Try by ID first
        cursor.execute("DELETE FROM voices WHERE id = ?", (identifier,))
        deleted = cursor.rowcount > 0
        
        # If not found, try by name
        if not deleted:
            cursor.execute("DELETE FROM voices WHERE name = ?", (identifier,))
            deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted
    
    def update_metadata(self, identifier: str, metadata: Dict) -> bool:
        """Update metadata for a voice profile."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        metadata_json = json.dumps(metadata)
        timestamp = datetime.utcnow().isoformat()
        
        cursor.execute("""
            UPDATE voices 
            SET metadata = ?, updated_at = ?
            WHERE id = ? OR name = ?
        """, (metadata_json, timestamp, identifier, identifier))
        
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated


class VoiceRecognizer:
    """Voice/speaker recognition using SpeechBrain embeddings."""
    
    def __init__(self, model_name: str = "speechbrain/spkrec-ecapa-voxceleb"):
        if not VOICE_RECOGNITION_AVAILABLE:
            raise ImportError("Voice recognition dependencies not available")
        
        if EncoderClassifier is None:
            raise ImportError("SpeechBrain EncoderClassifier not available")
        
        logger.info(f"Loading voice recognition model: {model_name}")
        try:
            self.model = EncoderClassifier.from_hparams(
                source=model_name,
                savedir=f"pretrained_models/{model_name.split('/')[-1]}"
            )
            logger.info("Voice recognition model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load voice recognition model: {e}")
            raise
    
    def extract_embedding(self, audio_tensor: torch.Tensor, 
                         sample_rate: int = 16000) -> np.ndarray:
        """Extract voice embedding from audio tensor."""
        try:
            # Ensure correct shape (batch, samples)
            if audio_tensor.dim() == 1:
                audio_tensor = audio_tensor.unsqueeze(0)
            
            # Resample if needed
            if sample_rate != 16000:
                resampler = torchaudio.transforms.Resample(sample_rate, 16000)
                audio_tensor = resampler(audio_tensor)
            
            # Extract embedding
            with torch.no_grad():
                embedding = self.model.encode_batch(audio_tensor)
                embedding_np = embedding.squeeze().cpu().numpy()
            
            return embedding_np
            
        except Exception as e:
            logger.error(f"Error extracting voice embedding: {e}")
            raise
    
    def compute_similarity(self, embedding1: np.ndarray, 
                          embedding2: np.ndarray) -> float:
        """Compute cosine similarity between two voice embeddings."""
        # Normalize embeddings
        emb1_norm = embedding1 / (np.linalg.norm(embedding1) + 1e-8)
        emb2_norm = embedding2 / (np.linalg.norm(embedding2) + 1e-8)
        
        # Cosine similarity
        similarity = np.dot(emb1_norm, emb2_norm)
        
        # Convert to 0-1 range
        similarity = (similarity + 1) / 2
        
        return float(similarity)


class VoiceIdentificationEngine:
    """Main engine for voice identification with database integration."""
    
    def __init__(self, db: VoiceDatabase = None, 
                 recognizer: VoiceRecognizer = None,
                 match_threshold: float = 0.75):
        self.db = db or VoiceDatabase()
        self.recognizer = recognizer or (VoiceRecognizer() if VOICE_RECOGNITION_AVAILABLE else None)
        self.match_threshold = match_threshold
        
        # Track recent identifications to avoid spam
        self.recent_identifications = {}
        self.identification_cooldown = 5.0  # seconds
    
    async def identify_speaker(self, audio_tensor: torch.Tensor, 
                              sample_rate: int = 16000) -> Dict[str, Any]:
        """Identify speaker from audio tensor."""
        if not self.recognizer:
            return {
                "identified": False,
                "error": "Voice recognition not available"
            }
        
        try:
            # Extract embedding from audio
            embedding = await asyncio.to_thread(
                self.recognizer.extract_embedding,
                audio_tensor,
                sample_rate
            )
            
            # Find best match in database
            best_match = self._find_best_match(embedding)
            
            if best_match and best_match["score"] >= self.match_threshold:
                # Check cooldown
                voice_id = best_match["id"]
                current_time = datetime.now().timestamp()
                last_time = self.recent_identifications.get(voice_id, 0)
                
                if current_time - last_time > self.identification_cooldown:
                    self.recent_identifications[voice_id] = current_time
                    self.db.update_recognition(voice_id)
                    
                    return {
                        "identified": True,
                        "id": best_match["id"],
                        "name": best_match["name"],
                        "confidence": best_match["score"],
                        "metadata": best_match.get("metadata", {}),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                else:
                    # Recently identified, skip to avoid spam
                    return {
                        "identified": True,
                        "id": voice_id,
                        "name": best_match["name"],
                        "confidence": best_match["score"],
                        "recently_identified": True
                    }
            else:
                return {
                    "identified": False,
                    "reason": "No matching voice profile found",
                    "best_score": best_match["score"] if best_match else 0.0
                }
                
        except Exception as e:
            logger.error(f"Error identifying speaker: {e}")
            return {
                "identified": False,
                "error": str(e)
            }
    
    def _find_best_match(self, embedding: np.ndarray) -> Optional[Dict[str, Any]]:
        """Find best matching voice in database."""
        all_voices = self.db.get_all()
        
        if not all_voices:
            return None
        
        best_match = None
        best_score = 0.0
        
        for voice_data in all_voices:
            voice_id, name, embedding_bytes, metadata_json, _, source, _, _, _, _ = voice_data
            
            # Reconstruct embedding
            stored_embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
            
            # Compute similarity
            score = self.recognizer.compute_similarity(embedding, stored_embedding)
            
            if score > best_score:
                best_score = score
                best_match = {
                    "id": voice_id,
                    "name": name,
                    "score": score,
                    "metadata": json.loads(metadata_json) if metadata_json else {}
                }
        
        return best_match
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get voice recognition system statistics."""
        voices = self.db.get_all()
        
        total_recognitions = sum(v[8] for v in voices) if voices else 0
        
        return {
            "total_voices": len(voices),
            "total_recognitions": total_recognitions,
            "match_threshold": self.match_threshold,
            "voices": [
                {
                    "name": v[1],
                    "recognition_count": v[8],
                    "last_recognized": v[9]
                }
                for v in voices
            ]
        }


# Global voice engine instance
_voice_engine: Optional[VoiceIdentificationEngine] = None


async def get_voice_engine() -> VoiceIdentificationEngine:
    """Get or create the global voice recognition engine."""
    global _voice_engine
    
    if _voice_engine is None:
        if not VOICE_RECOGNITION_AVAILABLE:
            logger.warning("Voice recognition not available")
            return None
        
        logger.info("Initializing voice recognition engine...")
        db = VoiceDatabase()
        recognizer = VoiceRecognizer()
        _voice_engine = VoiceIdentificationEngine(db, recognizer)
        logger.info("Voice recognition engine initialized")
    
    return _voice_engine


def torch_available() -> bool:
    """Check if PyTorch is available for voice recognition."""
    return VOICE_RECOGNITION_AVAILABLE
