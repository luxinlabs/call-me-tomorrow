"""Shared in-process store for live transcript turns.

Isolated here so bot.py (run as __main__) and transcript.py both import
from the same module and share the same dict — avoiding the double-import
bug where `from bot import _push_live_turn` would re-instantiate the module.
"""

_live_transcripts: dict[str, list[dict]] = {}


def push_live_turn(session_key: str, role: str, speaker: str, text: str) -> None:
    if session_key not in _live_transcripts:
        _live_transcripts[session_key] = []
    _live_transcripts[session_key].append({"role": role, "speaker": speaker, "text": text})
