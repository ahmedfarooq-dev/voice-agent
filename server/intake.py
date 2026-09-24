"""Reads the client's filled-in intake document(s) from the knowledge folder.

Accepts .docx (the client template), plus .md/.txt for hand-written notes.

Conventions the template relies on:
- Word headings become section headings.
- A paragraph or table cell wrapped in [square brackets] is guidance for the
  client and is dropped, so leftover instructions never reach the agent.
- Two-column tables become "Field: Answer" lines. Rows in the "Basics" section
  are also captured as settings (company name, agent name, timezone, ...).
- Everything under the heading "How the agent should behave" is treated as
  instructions; everything else is knowledge the agent may state.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
BEHAVIOR_HEADING = "how the agent should behave"
BASICS_HEADING = "basics"


@dataclass
class Intake:
    settings: dict[str, str] = field(default_factory=dict)
    behavior: str = ""
    knowledge: str = ""
    sources: list[str] = field(default_factory=list)

    def get(self, key: str, default: str = "") -> str:
        return self.settings.get(key.lower().strip(), default)


def _is_guidance(text: str) -> bool:
    t = text.strip()
    return len(t) >= 2 and t.startswith("[") and t.endswith("]")


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _parse_docx(path: Path, intake: Intake) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    current_h1 = ""
    out: list[str] = []
    behavior: list[str] = []

    def emit(line: str) -> None:
        (behavior if current_h1 == BEHAVIOR_HEADING else out).append(line)

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = _clean(para.text)
            if not text or _is_guidance(text):
                continue
            style = (para.style.name or "") if para.style is not None else ""
            if style.startswith("Heading"):
                level = int(style[-1]) if style[-1].isdigit() else 1
                if level == 1:
                    current_h1 = text.lower()
                emit(f"\n{'#' * level} {text}")
            elif style == "Title":
                continue
            else:
                emit(text)

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows = [[_clean(c.text) for c in r.cells] for r in table.rows]
            if not rows:
                continue
            header = [h.lower() for h in rows[0]]
            two_col = all(len(r) >= 2 for r in rows)
            is_header = header[:1] in (["field"], ["question"], ["objection"])
            body = rows[1:] if is_header else rows
            for r in body:
                key, val = r[0], (r[1] if two_col else "")
                if not key or not val or _is_guidance(val) or _is_guidance(key):
                    continue
                if header[:1] == ["question"]:
                    emit(f"Q: {key}\nA: {val}")
                elif header[:1] == ["objection"]:
                    emit(f'Objection: "{key}"\nAnswer: {val}')
                else:
                    emit(f"{key}: {val}")
                    if current_h1 == BASICS_HEADING:
                        intake.settings[key.lower()] = val

    intake.knowledge += "\n".join(out) + "\n"
    intake.behavior += "\n".join(behavior) + "\n"


def _parse_text(path: Path, intake: Intake) -> None:
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if not _is_guidance(ln)
    ]
    intake.knowledge += "\n".join(lines).strip() + "\n"


def load_intake(folder: Path = KNOWLEDGE_DIR) -> Intake:
    intake = Intake()
    for path in sorted(folder.glob("*")):
        if path.name.startswith("~$"):  # Word lock files
            continue
        try:
            if path.suffix.lower() == ".docx":
                _parse_docx(path, intake)
            elif path.suffix.lower() in (".md", ".txt"):
                _parse_text(path, intake)
            else:
                continue
            intake.sources.append(path.name)
        except Exception as e:
            logger.error(f"Could not read {path.name}: {e}")
    intake.knowledge = intake.knowledge.strip()
    intake.behavior = intake.behavior.strip()
    if not intake.sources:
        logger.warning(f"No knowledge files found in {folder}")
    return intake


if __name__ == "__main__":
    i = load_intake()
    print("SOURCES:", i.sources)
    print("SETTINGS:", i.settings)
    print("\n=== BEHAVIOR ===\n" + i.behavior)
    print("\n=== KNOWLEDGE ===\n" + i.knowledge)
