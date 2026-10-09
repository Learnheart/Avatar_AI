"""
AvatarState machine rules (design.md §2).

  idle → listening → thinking → speaking → idle

Barge-in goes thinking / speaking → listening. Every state may fall back to idle
(empty transcript, turn finished, error, timeout, disconnect).
"""
from backend.models import AvatarState

_ALLOWED: dict[AvatarState, frozenset[AvatarState]] = {
    AvatarState.IDLE: frozenset({AvatarState.LISTENING}),
    AvatarState.LISTENING: frozenset({AvatarState.THINKING, AvatarState.IDLE}),
    AvatarState.THINKING: frozenset(
        {AvatarState.SPEAKING, AvatarState.LISTENING, AvatarState.IDLE}
    ),
    AvatarState.SPEAKING: frozenset({AvatarState.LISTENING, AvatarState.IDLE}),
}


def can_transition(old: AvatarState, new: AvatarState) -> bool:
    return new in _ALLOWED[old]
