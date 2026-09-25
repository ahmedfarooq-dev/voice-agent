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


WORD_BUDGET = 3000
SUSPICIOUS = [
    r"\bTBD\b", r"\bTODO\b", r"\bxxx+\b", r"placeholder", r"lorem ipsum", r"\bN/A\b",
    r"\?\?+", r"https?://", r"click here", r"\[|\]", r"ask (razi|the owner|the client)",
]


def check(folder: Path = KNOWLEDGE_DIR) -> bool:
    """Pre-flight check for a client's document. Returns True when safe to go live."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from booking import parse_bookable_hours

    problems: list[tuple[str, str]] = []  # (level, message)

    def fail(msg: str) -> None:
        problems.append(("FAIL", msg))

    def warn(msg: str) -> None:
        problems.append(("WARN", msg))

    ignored = [
        p.name for p in folder.glob("*")
        if p.is_file() and p.suffix.lower() not in (".docx", ".md", ".txt")
        and not p.name.startswith("~$")
    ]
    for name in ignored:
        fail(f"{name}: unsupported file type, the bot ignores it. Convert to the .docx template.")

    doc = load_intake(folder)
    if not doc.sources:
        fail("No document found. Put the filled Client-Intake-Template.docx in server/knowledge/.")
        return _report(problems)

    # Basics the engine relies on
    if not doc.get("company name"):
        fail("Basics: 'Company name' is empty. The agent would call itself 'our company'.")
    for key in ("agent's name", "greeting", "business hours, in words", "appointment name"):
        if not doc.get(key):
            warn(f"Basics: '{key}' is empty; a generic default will be used.")
    tz = doc.get("timezone")
    if not tz:
        warn("Basics: 'Timezone' is empty; the .env default applies.")
    else:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            fail(f"Basics: Timezone {tz!r} is not valid. Use a name like America/New_York.")
    hours = doc.get("bookable hours")
    if hours:
        try:
            parse_bookable_hours(hours)
        except ValueError as e:
            fail(f"Basics: {e}")
    else:
        warn("Basics: 'Bookable hours' is empty; the .env default applies.")
    minutes = doc.get("appointment length in minutes")
    if minutes and not re.search(r"\d", minutes):
        fail(f"Basics: 'Appointment length in minutes' must be a number, got {minutes!r}.")

    # Content
    text = doc.knowledge + "\n" + doc.behavior
    words = len(text.split())
    if words > WORD_BUDGET:
        warn(f"Document is {words} words; over {WORD_BUDGET} replies get slower and hit free-tier limits.")
    for heading in ("About the company", "Services", "Frequently asked questions"):
        body = re.search(rf"^# {re.escape(heading)}\n(.*?)(?=^# |\Z)", doc.knowledge, re.S | re.M)
        if not body or not body.group(1).strip():
            warn(f"Section '{heading}' is empty.")
    if not doc.behavior.replace("#", "").strip() or len(doc.behavior.split()) < 12:
        warn("'How the agent should behave' has no instructions; the engine defaults apply.")

    for pattern in SUSPICIOUS:
        for m in re.finditer(pattern, text, re.I):
            line = text[text.rfind("\n", 0, m.start()) + 1 : text.find("\n", m.end())].strip()
            warn(f"Suspicious text would be read aloud: \"{line[:90]}\"")
            break  # one example per pattern is enough

    return _report(problems, doc)


def _report(problems: list[tuple[str, str]], doc: Intake | None = None) -> bool:
    if doc:
        print(f"Checked: {', '.join(doc.sources)}")
        agent = doc.get("agent's name") or "(default)"
        words = len((doc.knowledge + doc.behavior).split())
        print(f"Company: {doc.get('company name') or '(missing)'} | Agent: {agent} | {words} words")
    for level, msg in problems:
        print(f"  {level}  {msg}")
    fails = sum(1 for level, _ in problems if level == "FAIL")
    warns = len(problems) - fails
    if fails:
        print(f"RESULT: NOT READY. {fails} problem(s) must be fixed, {warns} warning(s).")
    elif warns:
        print(f"RESULT: OK with {warns} warning(s). Review them, then go live.")
    else:
        print("RESULT: OK. Ready to go live.")
    return fails == 0


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--check") + 1]) if len(sys.argv) > sys.argv.index("--check") + 1 else KNOWLEDGE_DIR
        sys.exit(0 if check(folder) else 1)
    i = load_intake()
    print("SOURCES:", i.sources)
    print("SETTINGS:", i.settings)
    print("\n=== BEHAVIOR ===\n" + i.behavior)
    print("\n=== KNOWLEDGE ===\n" + i.knowledge)
