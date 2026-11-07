"""Voice subsystem for MAYA AI (Phase 12).

Provides audio conversion, VAD, real-time Gemini Live session management,
barge-in interruption handling, session resumption, and task decoupling bridge.
"""

from app.voice.audio_converter import (
    AudioConverter,
    chunk_pcm16,
    pcm16_to_samples,
    resample_linear,
    resample_pcm,
    samples_to_pcm16,
)
from app.voice.event_bridge import (
    SpokenTaskAnnouncement,
    VoiceEventBridge,
    format_spoken_task_update,
)
from app.voice.live_session import (
    LiveVoiceSession,
    OutgoingMessage,
    OutgoingMessageType,
)
from app.voice.session_manager import VoiceSessionManager
from app.voice.tools import (
    MCPToolRegistrationBlockedError,
    VoiceApprovalProhibitedError,
    VoiceExecutionContext,
    VoiceToolRegistry,
)
from app.voice.vad import VADState, VoiceActivityDetector

__all__ = [
    "AudioConverter",
    "LiveVoiceSession",
    "MCPToolRegistrationBlockedError",
    "OutgoingMessage",
    "OutgoingMessageType",
    "SpokenTaskAnnouncement",
    "VADState",
    "VoiceActivityDetector",
    "VoiceApprovalProhibitedError",
    "VoiceEventBridge",
    "VoiceExecutionContext",
    "VoiceSessionManager",
    "VoiceToolRegistry",
    "chunk_pcm16",
    "format_spoken_task_update",
    "pcm16_to_samples",
    "resample_linear",
    "resample_pcm",
    "samples_to_pcm16",
]
