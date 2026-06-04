"""Fix malformed 'note = {DOI unverified: ...},' lines in seismic.bib.

fix_bad_dois.py included truncated bib-title snippets such as '{G' in the
note value, leaving unbalanced braces.  BibTeX parsers count depth, so an
unmatched { causes the entire remainder of the file to be swallowed.

Fix: for every note line added by fix_bad_dois.py, replace any { or }
INSIDE the note value (between the outer braces) with ( and ).

Usage:
    python scripts/fix_note_braces.py [--dry-run]
"""
import re
import sys
from pathlib import Path

BIB = Path("bib/seismic.bib")

# Greedy: captures everything between the outer { } of the note field value
# The .+ is greedy so \} matches the LAST } on the line → correct
NOTE_RE = re.compile(
    r'^(\s*note\s*=\s*)\{(DOI unverified:.+)\}(,?\s*)$'
)


def fix_line(line: str) -> tuple[str, bool]:
    """Return (fixed_line, changed)."""
    m = NOTE_RE.match(line.rstrip('\n'))
    if not m:
        return line, False

    prefix  = m.group(1)   # '  note       = '
    content = m.group(2)   # 'DOI unverified: REASON'
    suffix  = m.group(3)   # ','

    # Replace stray braces inside the reason text
    clean = content.replace('{', '(').replace('}', ')')
    if clean == content:
        return line, False   # nothing to fix

    return f"{prefix}{{{clean}}}{suffix}\n", True


def main(dry_run: bool = False) -> None:
    text = BIB.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    out = []
    fixed = 0
    for line in lines:
        new_line, changed = fix_line(line)
        out.append(new_line)
        if changed:
            fixed += 1
            if dry_run:
                print(f"BEFORE: {line.rstrip()}")
                print(f"AFTER:  {new_line.rstrip()}")
                print()

    print(f"Fixed {fixed} malformed note line(s).")
    if dry_run:
        print("Dry run — not written.")
        return

    BIB.write_text(''.join(out), encoding='utf-8')
    print(f"Written to {BIB}")


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    main(dry_run=dry)
