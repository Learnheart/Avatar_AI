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
    # Only for an STT done by a chat model (STT_BACKEND=databricks): how much it may
    # think before transcribing, and the cap on the transcript length.
    STT_REASONING_EFFORT: str = env_str("STT_REASONING_EFFORT", "minimal")
    STT_MAX_TOKENS: int = env_int("STT_MAX_TOKENS", 512)
    # An utterance whose every segment has a no-speech probability above this is not
    # speech: its transcript is dropped. 1.0 = nothing is refused.
    STT_NO_SPEECH_MAX_PROB: float = env_float("STT_NO_SPEECH_MAX_PROB", 1.0)

    # ── Answering (LLM) ─────────────────────────────────────────────────
    LLM_REASONING_EFFORT: str = env_str("LLM_REASONING_EFFORT", "low")
    LLM_MAX_TOKENS: int = env_int("LLM_MAX_TOKENS", 512)
    # Max wait for each streamed delta, first token included.
    LLM_TIMEOUT_SEC: float = env_float("LLM_TIMEOUT_SEC", 8.0)
    # A whole answer asked in the background (`LLM.complete`: reading an import or a
    # closed conversation), not spoken: it may take longer and be longer than the answer
    # of a turn.
    LLM_COMPLETE_TIMEOUT_SEC: float = env_float("LLM_COMPLETE_TIMEOUT_SEC", 30.0)
    LLM_COMPLETE_MAX_TOKENS: int = env_int("LLM_COMPLETE_MAX_TOKENS", 2048)

    # ── Conversation (short-term memory) ────────────────────────────────
    # A conversation closes after this long without a face (while the camera is
    # connected), or this long after its last turn ended.
    CONVERSATION_ABSENCE_SEC: float = env_float("CONVERSATION_ABSENCE_SEC", 30.0)
    CONVERSATION_IDLE_SEC: float = env_float("CONVERSATION_IDLE_SEC", 120.0)
    # The earlier turns of the conversation given to the LLM cost at most this many
    # tokens; past it the oldest turns are dropped, whole.
    MEMORY_HISTORY_MAX_TOKENS: int = env_int("MEMORY_HISTORY_MAX_TOKENS", 2000)
    # Tokens are estimated, not counted (each backend has its own tokenizer): this many
    # characters make one token. Measured with spoken Vietnamese on the Databricks
    # model: 3.3. Lower = more tokens counted, so less history kept.
    MEMORY_HISTORY_CHARS_PER_TOKEN: float = env_float("MEMORY_HISTORY_CHARS_PER_TOKEN", 3.0)

    # ── Memory (what is kept about a logged-in user) ────────────────────
    # Memory items given to the LLM as the profile of the person; past it the least
    # recently updated preferences and personality traits are left out.
    MEMORY_PROMPT_MAX_ITEMS: int = env_int("MEMORY_PROMPT_MAX_ITEMS", 20)
    # Max wait for the memory: for the profile in a turn (past it the question is
    # answered without it), and at shutdown for the turns still being written.
    MEMORY_TIMEOUT_SEC: float = env_float("MEMORY_TIMEOUT_SEC", 1.0)
    # Characters of one memory item: a short sentence, since every active item is in
    # every prompt. A longer text is refused.
    MEMORY_ITEM_MAX_CHARS: int = env_int("MEMORY_ITEM_MAX_CHARS", 200)
    # An import (a file or a pasted text the person describes themselves with): the
    # size of the file, and the characters of its text given to the LLM in one request.
    # Past either it is refused.
    MEMORY_IMPORT_MAX_FILE_MB: float = env_float("MEMORY_IMPORT_MAX_FILE_MB", 5.0)
    MEMORY_IMPORT_MAX_CHARS: int = env_int("MEMORY_IMPORT_MAX_CHARS", 20000)
    # Gathering from conversations. A closed conversation is read once by the LLM into
    # proposed (pending) items. A proposed preference or personality trait becomes
    # active by itself once this many conversations stated it; a name or a way of
    # addressing never does.
    MEMORY_PROMOTE_SEEN_COUNT: int = env_int("MEMORY_PROMOTE_SEEN_COUNT", 2)
    # An utterance the STT was less sure of than this is left out of what is read (0 =
    # nothing is left out). Measured: sentences about oneself 0.93-0.97, one-word
    # answers 0.45-0.70.
    MEMORY_MIN_STT_CONFIDENCE: float = env_float("MEMORY_MIN_STT_CONFIDENCE", 0.6)
    # The stored turns of a conversation are deleted once it is read, and after this
    # many days whatever happened.
    MEMORY_TRANSCRIPT_TTL_DAYS: float = env_float("MEMORY_TRANSCRIPT_TTL_DAYS", 7.0)
    # Characters of a conversation read in one request; a longer one keeps its most
    # recent turns.
    MEMORY_CONSOLIDATE_MAX_CHARS: int = env_int("MEMORY_CONSOLIDATE_MAX_CHARS", 20000)
    # Rejected items shown to the LLM that reads a conversation, the most recently
    # rejected first (every active and pending item is shown). One that is not shown is
    # still never proposed again.
    MEMORY_EXTRACT_MAX_REJECTED: int = env_int("MEMORY_EXTRACT_MAX_REJECTED", 50)

    # ── Persona (the personality a logged-in user gives the avatar) ─────
    # Characters of one field of the persona: every field that is set is in every
    # prompt of that user. A longer text is refused.
    PERSONA_FIELD_MAX_CHARS: int = env_int("PERSONA_FIELD_MAX_CHARS", 300)
    # Characters of a pasted description of the avatar read by the LLM into the
    # fields; a longer one is refused.
    PERSONA_IMPORT_MAX_CHARS: int = env_int("PERSONA_IMPORT_MAX_CHARS", 8000)
    # Max wait for the persona in a turn; past it the question is answered with the
    # character of the system prompt.
    PERSONA_TIMEOUT_SEC: float = env_float("PERSONA_TIMEOUT_SEC", 1.0)

    # ── Knowledge (uploaded documents) ──────────────────────────────────
    # Passages given to the LLM with the question.
    KNOWLEDGE_TOP_K: int = env_int("KNOWLEDGE_TOP_K", 4)
    # Cosine similarity below which a passage is not used (0 = keep the top K).
    KNOWLEDGE_MIN_SCORE: float = env_float("KNOWLEDGE_MIN_SCORE", 0.0)
    # Max wait for the lookup in a turn; past it the question is answered without it.
    KNOWLEDGE_TIMEOUT_SEC: float = env_float("KNOWLEDGE_TIMEOUT_SEC", 2.0)
    # Previous utterances of the person searched together with the current one, so a
    # follow-up finds the passages of its subject (0 = the current utterance alone).
    KNOWLEDGE_QUERY_HISTORY_TURNS: int = env_int("KNOWLEDGE_QUERY_HISTORY_TURNS", 1)
    # Size of a chunk of a document, and how much of it is repeated in the next one.
    KNOWLEDGE_CHUNK_CHARS: int = env_int("KNOWLEDGE_CHUNK_CHARS", 800)
    KNOWLEDGE_CHUNK_OVERLAP_CHARS: int = env_int("KNOWLEDGE_CHUNK_OVERLAP_CHARS", 120)
    KNOWLEDGE_MAX_FILE_MB: float = env_float("KNOWLEDGE_MAX_FILE_MB", 10.0)
    # Texts per embedding request, and the timeout of one request.
    EMBEDDING_BATCH_SIZE: int = env_int("EMBEDDING_BATCH_SIZE", 32)
    EMBEDDING_TIMEOUT_SEC: float = env_float("EMBEDDING_TIMEOUT_SEC", 20.0)
    # Vector size of the `local_hash` embedding.
    EMBEDDING_HASH_DIM: int = env_int("EMBEDDING_HASH_DIM", 1024)

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

    # ── Observability ───────────────────────────────────────────────────
    # Characters of a text value (transcript, prompt, answer) shown in a `Span` log
    # line; the span itself keeps the full value.
    TRACE_LOG_VALUE_MAX_CHARS: int = env_int("TRACE_LOG_VALUE_MAX_CHARS", 200)
    # Seconds between two pushes of the metrics to the OTLP backend.
    METRIC_EXPORT_INTERVAL_SEC: int = env_int("METRIC_EXPORT_INTERVAL_SEC", 15)

    # Capture (CAPTURE_ENABLED): camera images kept per second (0 = every tracked frame),
    # their JPEG quality, the size at which the oldest hours are deleted, and how many
    # items may wait for the writer before new ones are dropped.
    CAPTURE_FRAME_FPS: float = env_float("CAPTURE_FRAME_FPS", 0.0)
    CAPTURE_JPEG_QUALITY: int = env_int("CAPTURE_JPEG_QUALITY", 80)
    CAPTURE_MAX_GB: float = env_float("CAPTURE_MAX_GB", 20.0)
    CAPTURE_QUEUE_SIZE: int = env_int("CAPTURE_QUEUE_SIZE", 64)

    # ── Failure handling ────────────────────────────────────────────────
    # SDK-level retries per Groq request. 0: a failed call goes straight to the engine's
    # own fallback instead of spending the latency budget on retries.
    GROQ_MAX_RETRIES: int = env_int("GROQ_MAX_RETRIES", 0)
    # With a backup backend (STT_BACKEND_BACKUP / LLM_BACKEND_BACKUP): how long the
    # primary may take to answer (STT) or to send its first delta (LLM) before the backup
    # is used, and how long the primary is then skipped before it is tried again.
    FALLBACK_PRIMARY_TIMEOUT_SEC: float = env_float("FALLBACK_PRIMARY_TIMEOUT_SEC", 3.0)
    FALLBACK_COOLDOWN_SEC: float = env_float("FALLBACK_COOLDOWN_SEC", 300.0)
    # A Databricks CLI profile token is renewed this many seconds before it expires.
    DATABRICKS_TOKEN_REFRESH_MARGIN_SEC: float = env_float(
        "DATABRICKS_TOKEN_REFRESH_MARGIN_SEC", 120.0
    )
    # Seconds between attempts to (re)open the camera.
    CAMERA_RETRY_SEC: float = env_float("CAMERA_RETRY_SEC", 2.0)
    # Characters of an underlying error's text kept in domain error messages and logs.
    ERROR_SUMMARY_MAX_CHARS: int = env_int("ERROR_SUMMARY_MAX_CHARS", 200)
