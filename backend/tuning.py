"""Behaviour parameters: how the avatar listens, answers and moves. Edit here to tune it.

Secrets and per-machine values are in `backend/config.py`; texts are in
`backend/prompts.py`. Each parameter here can be overridden by an environment variable
of the same name.
"""
from dataclasses import dataclass

from backend.utils.env import env_float, env_int, env_str


@dataclass
class Tuning:
    # ── Listening (VAD) ─────────────────────────────────────────────────
    VAD_THRESHOLD: float = env_float("VAD_THRESHOLD", 0.5)
    # End-of-speech confirmation window.
    VAD_MIN_SILENCE_MS: int = env_int("VAD_MIN_SILENCE_MS", 500)
    VAD_SPEECH_PAD_MS: int = env_int("VAD_SPEECH_PAD_MS", 100)
    # Audio kept before speech_start, so the first syllable is not clipped.
    VAD_PREROLL_MS: int = env_int("VAD_PREROLL_MS", 300)

    # ── Understanding (STT) ─────────────────────────────────────────────
    STT_LANGUAGE: str = env_str("STT_LANGUAGE", "vi")
    STT_TIMEOUT_SEC: float = env_float("STT_TIMEOUT_SEC", 8.0)
    STT_MAX_UTTERANCE_SEC: float = env_float("STT_MAX_UTTERANCE_SEC", 30.0)

    # ── Answering (LLM) ─────────────────────────────────────────────────
    LLM_REASONING_EFFORT: str = env_str("LLM_REASONING_EFFORT", "low")
    LLM_MAX_TOKENS: int = env_int("LLM_MAX_TOKENS", 512)
    # Max wait for each streamed delta, first token included.
    LLM_TIMEOUT_SEC: float = env_float("LLM_TIMEOUT_SEC", 8.0)

    # ── Speaking (TTS) ──────────────────────────────────────────────────
    ZEROTTS_VOICE: str = env_str("ZEROTTS_VOICE", "maichi")
    # Size of each audio message.
    TTS_CHUNK_MS: int = env_int("TTS_CHUNK_MS", 240)
    # Per sentence.
    TTS_TIMEOUT_SEC: float = env_float("TTS_TIMEOUT_SEC", 10.0)

    # ── Head tracking ───────────────────────────────────────────────────
    TRACKING_FPS: int = env_int("TRACKING_FPS", 30)
    # Faces detected per frame; the largest one is tracked.
    TRACKING_MAX_FACES: int = env_int("TRACKING_MAX_FACES", 3)
    TRACKING_MIN_CONFIDENCE: float = env_float("TRACKING_MIN_CONFIDENCE", 0.5)
    # Head rotation clamps (radians).
    HEAD_PITCH_MAX: float = env_float("HEAD_PITCH_MAX", 0.52)
    HEAD_YAW_MAX: float = env_float("HEAD_YAW_MAX", 0.78)
    HEAD_ROLL_MAX: float = env_float("HEAD_ROLL_MAX", 0.35)
    # No face for this long → the head returns to neutral.
    NO_FACE_TIMEOUT_SEC: float = env_float("NO_FACE_TIMEOUT_SEC", 3.0)

    # ── Turn ────────────────────────────────────────────────────────────
    TURN_TIMEOUT_SEC: float = env_float("TURN_TIMEOUT_SEC", 30.0)
    # Grace after the estimated end of playback.
    PLAYBACK_END_TIMEOUT_SEC: float = env_float("PLAYBACK_END_TIMEOUT_SEC", 2.0)

    # ── Failure handling ────────────────────────────────────────────────
    # SDK-level retries per Groq request. 0: a failed call goes straight to the engine's
    # own fallback instead of spending the latency budget on retries.
    GROQ_MAX_RETRIES: int = env_int("GROQ_MAX_RETRIES", 0)
    # Seconds between attempts to (re)open the camera.
    CAMERA_RETRY_SEC: float = env_float("CAMERA_RETRY_SEC", 2.0)
    # Characters of an underlying error's text kept in domain error messages and logs.
    ERROR_SUMMARY_MAX_CHARS: int = env_int("ERROR_SUMMARY_MAX_CHARS", 200)
