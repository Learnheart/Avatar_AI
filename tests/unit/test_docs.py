"""Checks on the instruction files of the repository."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_claude_md_and_agents_md_hold_the_same_text():
    claude, agents = (REPO_ROOT / name for name in ("CLAUDE.md", "AGENTS.md"))

    assert claude.read_bytes() == agents.read_bytes(), (
        "CLAUDE.md and AGENTS.md differ: apply the edit to both files"
    )
