"""Checks on the instruction files of the repository."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_claude_md_and_agents_md_hold_the_same_text():
    claude, agents = (REPO_ROOT / name for name in ("CLAUDE.md", "AGENTS.md"))

    assert claude.read_bytes() == agents.read_bytes(), (
        "CLAUDE.md and AGENTS.md differ: apply the edit to both files"
    )


# ── Architecture records (CLAUDE.md §8) ───────────────────────────────────

ADR_DIR = REPO_ROOT / "docs" / "adr"
ADR_NAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ADR_FIELDS = ("adr", "title", "date", "status", "agent", "modules")
ADR_STATUSES = ("accepted", "superseded")
ADR_SECTIONS = ["Context", "High-level diagram", "Consequences"]
# Sections a record may add after the required ones.
ADR_OPTIONAL_SECTIONS = ["ROI"]


def _records() -> dict[str, Path]:
    """The architecture records by number; any other name in the folder is a mistake."""
    others = {"README.md", "template.md"}
    records = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if path.name in others:
            continue
        match = ADR_NAME.match(path.name)
        assert match, f"{path.name}: a record is named NNNN-<kebab-case-name>.md"
        records[match.group(1)] = path
    return records


def _front_matter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    assert lines[0] == "---", f"{path.name}: no front matter"
    fields = {}
    for line in lines[1:lines.index("---", 1)]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_architecture_records_are_numbered_in_sequence():
    numbers = list(_records())

    assert numbers == [f"{n:04d}" for n in range(1, len(numbers) + 1)]


def test_architecture_records_have_their_front_matter_and_sections():
    for number, path in _records().items():
        fields = _front_matter(path)
        text = path.read_text()

        assert all(fields.get(name) for name in ADR_FIELDS), f"{path.name}: missing a field"
        assert fields["adr"] == number, f"{path.name}: `adr` differs from the file name"
        assert fields["status"] in ADR_STATUSES, f"{path.name}: unknown status"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["date"]), f"{path.name}: bad date"
        sections = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
        required, extra = sections[:len(ADR_SECTIONS)], sections[len(ADR_SECTIONS):]
        assert required == ADR_SECTIONS, f"{path.name}: sections must start with {ADR_SECTIONS}"
        assert set(extra) <= set(ADR_OPTIONAL_SECTIONS), f"{path.name}: unexpected sections {extra}"
        diagram = text.split("## High-level diagram")[1].split("## Consequences")[0]
        assert "```mermaid" in diagram, f"{path.name}: no Mermaid diagram"


def test_a_superseded_record_names_the_record_that_replaces_it():
    records = _records()
    for path in records.values():
        fields = _front_matter(path)
        if fields["status"] == "superseded":
            assert fields.get("superseded_by") in records, (
                f"{path.name}: `superseded_by` must name an existing record"
            )
        else:
            assert "superseded_by" not in fields, f"{path.name}: not superseded"


def test_exactly_one_record_is_the_current_architecture():
    current = [
        path.name for path in _records().values()
        if _front_matter(path)["status"] == "accepted"
    ]

    assert len(current) == 1, f"one record must be the current architecture, found {current}"


def test_the_index_lists_every_architecture_record_with_its_status():
    index = (ADR_DIR / "README.md").read_text()
    for number, path in _records().items():
        row = next((line for line in index.splitlines() if f"]({path.name})" in line), None)

        assert row, f"{path.name} is not in docs/adr/README.md"
        assert f"| {_front_matter(path)['status']} |" in row, f"{number}: status differs in the index"


def test_the_architecture_backlog_links_to_existing_architecture_records():
    architecture = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text()
    linked = re.findall(r"\]\(adr/([^)]+)\)", architecture)

    assert linked, "docs/ARCHITECTURE.md links to no architecture record"
    for name in linked:
        assert (ADR_DIR / name).is_file(), f"docs/ARCHITECTURE.md links to a missing {name}"
