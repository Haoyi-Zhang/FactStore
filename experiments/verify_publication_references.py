"""Verify the publication reference inventory and, when supplied, TeX/BibTeX closure."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

MINIMUM_REFERENCES = 55
AUDIT_COLUMNS = {
    "bibtex_key",
    "title",
    "publication_kind",
    "audited_authors",
    "audited_year",
    "audited_venue",
    "stable_locator",
    "metadata_authority",
    "claim_supported",
    "paper_location",
    "metadata_match",
    "passage_fit",
    "verified_on",
    "notes",
}
MANIFEST_COLUMNS = {"bibtex_key", "title", "manuscript_role", "stable_locator"}


def fail(message: str) -> None:
    raise AssertionError(message)


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def unique_map(rows: list[dict[str, str]], label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row["bibtex_key"].strip()
        if not key:
            fail(f"{label}: empty BibTeX key")
        if key in result:
            fail(f"{label}: duplicate BibTeX key {key}")
        result[key] = row
    return result


def strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        cut = len(line)
        for index, character in enumerate(line):
            if character == "%" and (index == 0 or line[index - 1] != "\\"):
                cut = index
                break
        lines.append(line[:cut])
    return "\n".join(lines)


def manuscript_tex_surface(tex_path: Path) -> str:
    """Return a deterministic, recursively expanded local TeX source surface.

    Journal manuscripts commonly split content across ``\\input`` files.  The
    reference verifier must inspect the complete source tree rather than only the
    wrapper file; otherwise an empty wrapper can falsely appear citation-free.
    Only relative local ``.tex`` files are followed, cycles are rejected, and
    missing inputs fail closed.
    """

    active: set[Path] = set()
    visited: set[Path] = set()

    def expand(path: Path) -> str:
        path = path.resolve()
        if path in active:
            fail(f"manuscript TeX input cycle at {path}")
        if path in visited:
            return ""
        if not path.is_file():
            fail(f"manuscript TeX input is missing: {path}")
        active.add(path)
        visited.add(path)
        text = strip_tex_comments(path.read_text(encoding="utf-8"))
        pieces: list[str] = []
        cursor = 0
        pattern = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
        for match in pattern.finditer(text):
            pieces.append(text[cursor:match.start()])
            name = match.group(1).strip()
            if not name:
                fail(f"empty TeX input command in {path}")
            candidate = Path(name)
            if candidate.is_absolute() or ".." in candidate.parts:
                fail(f"nonlocal TeX input is not permitted: {name}")
            if candidate.suffix == "":
                candidate = candidate.with_suffix(".tex")
            if candidate.suffix.lower() != ".tex":
                # Data files and other package inputs cannot carry citations.
                pieces.append(match.group(0))
            else:
                pieces.append(expand(path.parent / candidate))
            cursor = match.end()
        pieces.append(text[cursor:])
        active.remove(path)
        return "\n".join(pieces)

    return expand(tex_path)


def citation_groups(tex_path: Path) -> list[list[str]]:
    text = manuscript_tex_surface(tex_path)
    groups: list[list[str]] = []
    for match in re.finditer(r"\\cite\s*\{([^}]*)\}", text, flags=re.DOTALL):
        group = [key.strip() for key in match.group(1).split(",") if key.strip()]
        if not group:
            fail("manuscript contains an empty citation command")
        groups.append(group)
    return groups


def cited_keys(tex_path: Path) -> set[str]:
    return {key for group in citation_groups(tex_path) for key in group}


def split_top_level(text: str) -> list[str]:
    """Split a BibTeX entry body on commas outside braces and quoted strings."""
    parts: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"' and depth == 0:
            quoted = not quoted
            continue
        if not quoted:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth < 0:
                    fail("bibliography: unbalanced braces")
            elif character == "," and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
    if quoted or depth != 0:
        fail("bibliography: unbalanced entry value")
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def unwrap_bib_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')):
        return value[1:-1].strip()
    return value


def parse_bibtex(bib_path: Path) -> dict[str, dict[str, str]]:
    """Parse the simple, self-contained BibTeX surface shipped with the paper."""
    text = bib_path.read_text(encoding="utf-8")
    entries: dict[str, dict[str, str]] = {}
    cursor = 0
    header = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,", flags=re.MULTILINE)
    while True:
        match = header.search(text, cursor)
        if match is None:
            break
        entry_type, key = match.group(1).lower(), match.group(2)
        if key in entries:
            fail(f"bibliography: duplicate entry key {key}")
        depth = 1
        quoted = False
        escaped = False
        end = match.end()
        while end < len(text) and depth:
            character = text[end]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"' and depth == 1:
                quoted = not quoted
            elif not quoted:
                if character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
            end += 1
        if depth != 0:
            fail(f"bibliography: unterminated entry {key}")
        body = text[match.end():end - 1]
        fields: dict[str, str] = {"entry_type": entry_type}
        for part in split_top_level(body):
            if not part:
                continue
            if "=" not in part:
                fail(f"bibliography: malformed field in {key}: {part}")
            name, value = part.split("=", 1)
            name = name.strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
                fail(f"bibliography: malformed field name in {key}: {name}")
            if name in fields:
                fail(f"bibliography: duplicate field {name} in {key}")
            fields[name] = unwrap_bib_value(value)
        entries[key] = fields
        cursor = end
    if not entries:
        fail("bibliography: no entries found")
    return entries


ACCENT_MARKS = {
    "'": "\u0301", '`': "\u0300", '"': "\u0308", "^": "\u0302",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "c": "\u0327",
}
TEX_LETTERS = {
    r"\AA": "Å", r"\aa": "å", r"\AE": "Æ", r"\ae": "æ",
    r"\O": "Ø", r"\o": "ø", r"\L": "Ł", r"\l": "ł",
    r"\ss": "ß",
}


def bib_text_plain(value: str) -> str:
    """Normalize the bounded TeX surface used by the shipped bibliography."""
    def accent(match: re.Match[str]) -> str:
        mark, letter = match.group(1), match.group(2)
        combining = ACCENT_MARKS.get(mark)
        return unicodedata.normalize("NFC", letter + combining) if combining else letter

    for command, replacement in TEX_LETTERS.items():
        value = value.replace("{" + command + "}", replacement).replace(command, replacement)
    value = re.sub(r"\{?\\([`'\"\^~=\.c])\s*\{?([A-Za-z])\}?\}?", accent, value)
    value = value.replace(r"\&", "&").replace("---", "-").replace("--", "-")
    value = value.replace("{", "").replace("}", "")
    if "\\" in value:
        fail(f"bibliography: unsupported TeX command in metadata: {value}")
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def bib_title_plain(value: str) -> str:
    return bib_text_plain(value)


def bib_authors_plain(value: str) -> list[str]:
    authors = [bib_text_plain(part) for part in re.split(r"\s+and\s+", value.strip())]
    if not authors or any(not author for author in authors):
        fail("bibliography: empty author in author list")
    return authors


def bib_venue_plain(fields: dict[str, str]) -> str:
    for field in ("journal", "booktitle", "note"):
        if fields.get(field, "").strip():
            return bib_text_plain(fields[field])
    return ""


def canonical_text(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=Path("reference_audit.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("publication_reference_manifest.csv"))
    parser.add_argument("--tex", type=Path)
    parser.add_argument("--bib", type=Path)
    args = parser.parse_args()

    if (args.tex is None) != (args.bib is None):
        fail("--tex and --bib must be supplied together")

    audit_rows, audit_fields = read_csv(args.audit)
    manifest_rows, manifest_fields = read_csv(args.manifest)
    if set(audit_fields) != AUDIT_COLUMNS:
        fail("reference audit columns differ from the frozen schema")
    if set(manifest_fields) != MANIFEST_COLUMNS:
        fail("publication reference manifest columns differ from the frozen schema")

    audit = unique_map(audit_rows, "reference audit")
    manifest = unique_map(manifest_rows, "reference manifest")
    if len(audit) < MINIMUM_REFERENCES:
        fail(f"reference inventory has {len(audit)} entries; minimum is {MINIMUM_REFERENCES}")
    if set(audit) != set(manifest):
        fail("reference audit and publication manifest key sets differ")
    for key in audit:
        if audit[key]["title"].strip() != manifest[key]["title"].strip():
            fail(f"title mismatch for {key}")
        if audit[key]["stable_locator"].strip() != manifest[key]["stable_locator"].strip():
            fail(f"stable locator mismatch for {key}")
        required_text = (
            "title", "publication_kind", "audited_authors", "audited_year",
            "audited_venue", "stable_locator", "metadata_authority",
            "claim_supported", "paper_location", "verified_on", "notes",
        )
        for field in required_text:
            if not audit[key][field].strip():
                fail(f"missing {field} for {key}")
        if audit[key]["metadata_match"] != "yes":
            fail(f"metadata audit is not closed for {key}")
        if audit[key]["passage_fit"] != "yes":
            fail(f"passage audit is not closed for {key}")
        try:
            date.fromisoformat(audit[key]["verified_on"])
        except ValueError as error:
            fail(f"invalid verification date for {key}: {error}")

    status: dict[str, object] = {
        "status": "REFERENCE_INVENTORY_CONSISTENT",
        "reference_count": len(audit),
        "minimum_reference_count": MINIMUM_REFERENCES,
        "audit_manifest_key_equality": True,
    }
    if args.tex is not None and args.bib is not None:
        groups = citation_groups(args.tex)
        maximum_cluster_size = max((len(group) for group in groups), default=0)
        if maximum_cluster_size > 1:
            offenders = [group for group in groups if len(group) > 1]
            fail(f"manuscript contains multi-key citation commands: {offenders}")
        citations = {key for group in groups for key in group}
        bibliography_entries = parse_bibtex(args.bib)
        bibliography = set(bibliography_entries)
        if citations != bibliography:
            fail(
                "citation and bibliography key sets differ: "
                f"uncited={sorted(bibliography - citations)}, missing={sorted(citations - bibliography)}"
            )
        for key, fields in bibliography_entries.items():
            for field in ("author", "title", "year"):
                if not fields.get(field, "").strip():
                    fail(f"bibliography: missing {field} for {key}")
            if not re.fullmatch(r"[12][0-9]{3}", fields["year"].strip()):
                fail(f"bibliography: invalid year for {key}: {fields['year']}")
            if not any(fields.get(field, "").strip() for field in ("journal", "booktitle", "note")):
                fail(f"bibliography: missing publication venue/note for {key}")
            bib_title = bib_title_plain(fields["title"])
            if canonical_text(bib_title) != canonical_text(audit[key]["title"]):
                fail(f"bibliography title differs from audited title for {key}: {bib_title!r}")

            bib_authors = bib_authors_plain(fields["author"])
            audited_authors = [author.strip() for author in audit[key]["audited_authors"].split(";") if author.strip()]
            if [canonical_text(author) for author in bib_authors] != [canonical_text(author) for author in audited_authors]:
                fail(f"bibliography author order differs from audited metadata for {key}")
            if fields["year"].strip() != audit[key]["audited_year"].strip():
                fail(f"bibliography year differs from audited metadata for {key}")
            bib_venue = bib_venue_plain(fields)
            if canonical_text(bib_venue) != canonical_text(audit[key]["audited_venue"]):
                fail(f"bibliography venue differs from audited metadata for {key}: {bib_venue!r}")
            expected_kind = {
                "inproceedings": "peer-reviewed conference paper",
                "article": "peer-reviewed journal article",
                "misc": "scholarly preprint",
            }.get(fields["entry_type"])
            if expected_kind is None or audit[key]["publication_kind"] != expected_kind:
                fail(f"bibliography entry type differs from audited publication kind for {key}")

            locator = audit[key]["stable_locator"].strip()
            if not locator.startswith("https://"):
                fail(f"reference locator is not HTTPS for {key}")
            doi_prefix = "https://doi.org/"
            if locator.lower().startswith(doi_prefix):
                expected_doi = locator[len(doi_prefix):].lower()
                if fields.get("doi", "").strip().lower() != expected_doi:
                    fail(f"bibliography DOI differs from audited locator for {key}")
            elif fields.get("doi", "").strip():
                fail(f"reference with a BibTeX DOI must use its DOI as the audited stable locator for {key}")
            if "arxiv.org/" in locator.lower():
                locator_id = locator.rstrip("/").split("/")[-1].lower()
                searchable = " ".join(fields.get(field, "") for field in ("note", "url", "eprint")).lower()
                if locator_id not in searchable:
                    fail(f"bibliography arXiv identifier differs from audited locator for {key}")
        if citations != set(audit):
            fail(
                "publication and audit key sets differ: "
                f"publication_only={sorted(citations - set(audit))}, audit_only={sorted(set(audit) - citations)}"
            )
        status.update({
            "citation_bibliography_key_equality": True,
            "publication_audit_key_equality": True,
            "maximum_citation_cluster_size": maximum_cluster_size,
            "fully_verified_reference_count": len(audit),
            "conference_reference_count": sum(1 for fields in bibliography_entries.values() if fields["entry_type"] == "inproceedings"),
            "journal_reference_count": sum(1 for fields in bibliography_entries.values() if fields["entry_type"] == "article"),
            "preprint_reference_count": sum(1 for fields in bibliography_entries.values() if fields["entry_type"] == "misc"),
            "bibtex_metadata_surface_checked": True,
            "author_order_year_venue_checked": True,
        })

    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
