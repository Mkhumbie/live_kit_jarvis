# Voice Recognition & Multi-Modal Identification System

## Overview
This system provides comprehensive user identification through multiple modalities:
- **Facial Recognition**: Visual identification using FaceNet embeddings
- **Voice Recognition**: Speaker identification using SpeechBrain ECAPA-TDNN
- **Multi-Modal**: Combined face + voice for cross-validated identification

## Architecture

### Voice Recognition (`voice_recognition.py`)
- **VoiceDatabase**: SQLite storage for voice embeddings and profiles
- **VoiceRecognizer**: SpeechBrain model for voice embedding extraction
- **VoiceIdentificationEngine**: Speaker identification and matching

### Multi-Modal Integration (`multimodal_recognition.py`)
- **MultiModalDatabase**: Unified user profiles linking face and voice IDs
- **MultiModalIdentificationEngine**: Cross-validation between modalities
- Recognition event logging for analytics

## Installation

```bash
# Install voice recognition dependencies
pip install speechbrain torchaudio

# All dependencies
pip install -r requirements.txt
```

## Features

### 1. Voice Recognition
- **Speaker Identification**: Identify users from audio streams
- **Voice Embeddings**: 192-dimensional ECAPA-TDNN embeddings
- **Similarity Matching**: Cosine similarity with configurable threshold
- **Profile Management**: Add, remove, and update voice profiles

### 2. Multi-Modal Identification
- **Cross-Validation**: Verify identity using both face and voice
- **Conflict Detection**: Alert when modalities disagree
- **Unified Profiles**: Link face and voice to single user identity
- **Recognition Analytics**: Track which modality identified the user

### 3. LiveKit Integration
- **Audio Stream Processing**: Real-time voice recognition from LiveKit rooms
- **Video + Audio**: Simultaneous face and voice identification
- **Session Context**: Track user identity throughout conversation

## Agent Tools

### Voice Recognition Tools
```python
# Check voice recognition status
voice_recognition_status() -> str

# List all voice profiles
list_voice_profiles(search_name: Optional[str]) -> str

# Get multi-modal system status
multimodal_status() -> str

# Link face and voice profiles
link_user_profiles(name: str, face_name: Optional[str], voice_name: Optional[str]) -> str
```

### Usage Examples

**Voice Commands:**
- "Is voice recognition working?" → voice_recognition_status()
- "List voice profiles" → list_voice_profiles()
- "Show multi-modal status" → multimodal_status()
- "Link my face and voice profiles" → link_user_profiles("Mkhumbie", "Mkhumbie", "Mkhumbie")

## Database Schema

### Voice Database (`voices.db`)
```sql
CREATE TABLE voices (
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
);
```

### Multi-Modal Database (`users_multimodal.db`)
```sql
CREATE TABLE users (
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
);

CREATE TABLE recognition_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    modality TEXT,
    confidence REAL,
    timestamp TEXT,
    session_id TEXT
);
```

## Recognition Flow

### 1. Face-Only Recognition
```
Video Frame → Face Detection → Embedding Extraction → Database Match → User Identified
```

### 2. Voice-Only Recognition
```
Audio Stream → Voice Embedding → Database Match → User Identified
```

### 3. Multi-Modal Recognition
```
Video + Audio → [Face Recognition] + [Voice Recognition] → Cross-Validation → User Identified
                                                          ↓
                                            If both match: High confidence
                                            If conflict: Alert
                                            If only one: Single-modality confidence
```

## Configuration

### Match Thresholds
- **Face Recognition**: 0.82 (82% similarity required)
- **Voice Recognition**: 0.75 (75% similarity required)
- **Multi-Modal**: 0.70 (lower threshold when both agree)

### Cooldown Periods
- **Face Recognition**: 3 seconds between identifications
- **Voice Recognition**: 5 seconds between identifications

## Agent Integration

The agent automatically:
1. Initializes all recognition engines on startup
2. Processes video frames for facial recognition
3. Processes audio streams for voice recognition
4. Cross-validates identifications when both available
5. Greets users personally when recognized
6. Maintains conversation context based on user identity

## Greeting Protocol

**Face Recognition:**
- "Ah, there you are, {{Name}}! Good to see your face again."

**Voice Recognition:**
- "I recognize your voice, {{Name}}! Good to hear you."

**Multi-Modal (Both Match):**
- "Face and voice confirmed - definitely {{Name}}. Welcome!"

**Conflict Detected:**
- "Interesting... your face says {{Name1}} but your voice says {{Name2}}. Which is correct?"

## Performance Considerations

### Optimization Tips
1. **Frame Processing**: Process every 30th frame (not every frame)
2. **Cooldown Periods**: Prevent spam recognitions
3. **Parallel Processing**: Run face and voice in parallel
4. **Async Operations**: All recognition is async for non-blocking

### Resource Usage
- **Face Recognition**: GPU-accelerated (CUDA if available)
- **Voice Recognition**: CPU or GPU (auto-detected)
- **Memory**: ~500MB for models
- **CPU**: ~5-10% per stream

## Troubleshooting

### Voice Recognition Not Available
```bash
# Install dependencies
pip install speechbrain torchaudio

# Verify installation
python -c "from speechbrain.pretrained import EncoderClassifier; print('OK')"
```

### Models Not Downloading
```bash
# Manually download models
mkdir -p pretrained_models
cd pretrained_models
git clone https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
```

### Poor Recognition Accuracy
1. Check audio quality (sample rate, noise)
2. Adjust match thresholds
3. Add more training samples per user
4. Verify lighting for face recognition

## Future Enhancements

### Planned Features
- [ ] Voice profile enrollment from audio samples
- [ ] Real-time voice activity detection
- [ ] Emotion detection from voice
- [ ] Age/gender estimation from voice
- [ ] Multi-speaker diarization
- [ ] Voice cloning detection

### Integration Opportunities
- [ ] Smart home control via voice commands
- [ ] Personalized TTS voice per user
- [ ] Voice-based authentication
- [ ] Meeting transcription with speaker identification
- [ ] Voice-controlled agent commands

## Security Considerations

### Privacy
- Voice embeddings are stored locally (not audio)
- Embeddings are not reversible to original audio
- User consent required before enrollment
- Option to delete profiles at any time

### Authentication
- Not suitable as sole authentication method
- Use as part of multi-factor authentication
- Combine with traditional credentials
- Monitor for spoofing attempts

## License & Credits

### Models Used
- **FaceNet** (Facial Recognition): BSD License
- **SpeechBrain ECAPA-TDNN** (Voice Recognition): Apache 2.0 License

### Dependencies
- PyTorch
- SpeechBrain
- TorchAudio
- LiveKit SDK
