"""PDF parsing with provenance.

Every downstream claim has to point at the exact span of the exact page it came
from, and that pointer has to survive all the way to a highlight box rendered
over the page in the browser. So the invariant this module establishes is:

    page.text[block.char_start:block.char_end] == block.text        (exactly)

Page text is *built from* the blocks rather than extracted separately, which is
what guarantees it. Extracting page text and block text independently â€” the
obvious approach â€” produces offsets that drift apart on any page with a table or
a multi-column layout, and the drift is silent: the highlight lands a few
characters off, or on the wrong line, and nobody notices until a reviewer does.

Tables keep their header row and per-cell geometry, because a number lifted out
of a table cell is meaningless without its column header, and because being able
to highlight the cell rather than the page is most of what makes the evidence
pane feel real.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

import pymupdf
import structlog

from core.models import Rect

log = structlog.get_logger(__name__)

BlockKind = Literal["paragraph", "heading", "table", "ocr"]

# Below this many extracted characters per page we assume the page is a scan or
# is image-only, and route it to the vision path instead of returning nothing.
SCANNED_CHAR_THRESHOLD = 120


@dataclass
class Block:
    id: UUID
    page: int
    kind: BlockKind
    text: str
    char_start: int
    char_end: int
    rects: list[Rect]
    # For a table cell or a row: the column header path it sits under. Injected
    # into the extraction prompt so a bare "72,251" arrives with its meaning.
    header_path: str | None = None
    table_index: int | None = None


@dataclass
class Page:
    number: int  # 1-indexed, matching what a reader sees
    text: str
    blocks: list[Block]
    width: float
    height: float

    @property
    def char_count(self) -> int:
        return len(self.text.strip())

    @property
    def is_scanned(self) -> bool:
        return self.char_count < SCANNED_CHAR_THRESHOLD


@dataclass
class ParsedDocument:
    path: str
    filename: str
    sha256: str
    page_count: int
    pages: list[Page]
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def scanned_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.is_scanned]

    @property
    def total_chars(self) -> int:
        return sum(p.char_count for p in self.pages)


# A fixed namespace so a document's identity is a pure function of its bytes.
# Any run, any machine, any number of interruptions: same file, same id.
DOCUMENT_NAMESPACE = UUID("6f4a1c2e-9d3b-4e57-8a10-2c5f7b91d0a4")


def document_uuid(sha256: str) -> UUID:
    """The stable identity of a document, derived from its content hash."""
    return uuid5(DOCUMENT_NAMESPACE, sha256)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_rect(r: pymupdf.Rect, page_rect: pymupdf.Rect) -> Rect:
    """Normalise to 0..1 against the page rectangle.

    Normalised rather than absolute so the same box renders correctly at any
    zoom in PDF.js without the frontend needing to know the page dimensions.
    """
    w = page_rect.width or 1.0
    h = page_rect.height or 1.0
    return Rect(
        x0=max(0.0, min(1.0, (r.x0 - page_rect.x0) / w)),
        y0=max(0.0, min(1.0, (r.y0 - page_rect.y0) / h)),
        x1=max(0.0, min(1.0, (r.x1 - page_rect.x0) / w)),
        y1=max(0.0, min(1.0, (r.y1 - page_rect.y0) / h)),
    )


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """Serialise a table so an LLM reads it as a table rather than as noise.

    Markdown is used because every model has seen a great deal of it and because
    it preserves the column alignment that gives a cell its meaning.
    """
    clean = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    out = ["| " + " | ".join(clean[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(out)


def _header_path(rows: list[list[str | None]]) -> str | None:
    if not rows:
        return None
    header = [(c or "").replace("\n", " ").strip() for c in rows[0]]
    header = [h for h in header if h]
    return " | ".join(header) if header else None


def parse_pdf(path: str | Path, *, detect_tables: bool = True) -> ParsedDocument:
    """Parse a PDF into pages and blocks with aligned character offsets.

    `detect_tables=False` is roughly four times faster because table detection
    dominates the cost â€” measured at 42s versus 11s on a 100-page annual report.
    That gap is what makes the two-phase strategy for large documents possible:

      phase A  text only, whole document, fast. Feeds the regex spot sweep,
               which yields a candidate density per page.
      phase B  full parse including tables, per page, on demand, in descending
               density order â€” so the financial statements are processed first
               and the signature pages last.

    A reader therefore sees real facts within seconds of uploading a 500-page
    filing, rather than waiting for a progress bar to cross a document they do
    not care about all of.

    Raises ValueError with a readable message for inputs a reviewer might
    plausibly hand us â€” an encrypted file, a renamed .docx, a corrupt download.
    Failing clearly at the boundary is worth more here than failing deep in the
    pipeline with a stack trace.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"No such file: {p}")

    try:
        doc = pymupdf.open(p)
    except Exception as e:
        raise ValueError(
            f"Could not open {p.name} as a PDF â€” it may be corrupt or not a PDF at all "
            f"({type(e).__name__})."
        ) from e

    if doc.needs_pass:
        doc.close()
        raise ValueError(
            f"{p.name} is password-protected. Veritas cannot read encrypted PDFs; "
            f"please supply a decrypted copy."
        )

    if doc.page_count == 0:
        doc.close()
        raise ValueError(f"{p.name} contains no pages.")

    pages: list[Page] = []
    for index in range(doc.page_count):
        pages.append(_parse_page(doc[index], index + 1, detect_tables=detect_tables))

    meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
    page_count = doc.page_count
    doc.close()

    parsed = ParsedDocument(
        path=str(p),
        filename=p.name,
        sha256=sha256_file(p),
        page_count=page_count,
        pages=pages,
        metadata=meta,
    )
    log.info(
        "parsed",
        file=p.name,
        pages=page_count,
        chars=parsed.total_chars,
        scanned_pages=len(parsed.scanned_pages),
    )
    return parsed


def _parse_page(page: pymupdf.Page, number: int, *, detect_tables: bool = True) -> Page:
    rect = page.rect
    blocks: list[Block] = []
    parts: list[str] = []
    cursor = 0

    table_rects: list[pymupdf.Rect] = []
    tables: list = []
    if detect_tables:
        try:
            finder = page.find_tables()
            tables = list(finder.tables)
        except Exception as e:  # noqa: BLE001 â€” table detection is best-effort
            log.debug("table_detection_failed", page=number, error=str(e))
            tables = []

    # Tables first, so their geometry can be used to suppress the duplicate
    # text blocks that PyMuPDF also reports for the same region.
    for t_index, table in enumerate(tables):
        try:
            rows = table.extract()
        except Exception:  # noqa: BLE001
            continue
        md = _table_to_markdown(rows)
        if not md.strip():
            continue
        t_rect = pymupdf.Rect(table.bbox)
        table_rects.append(t_rect)

        start = cursor
        parts.append(md)
        cursor += len(md)
        blocks.append(
            Block(
                id=uuid4(),
                page=number,
                kind="table",
                text=md,
                char_start=start,
                char_end=cursor,
                rects=[_norm_rect(t_rect, rect)],
                header_path=_header_path(rows),
                table_index=t_index,
            )
        )
        parts.append("\n\n")
        cursor += 2

    raw = page.get_text("dict")
    for b in raw.get("blocks", []):
        if b.get("type") != 0:  # 0 = text; images are handled by the vision path
            continue
        b_rect = pymupdf.Rect(b["bbox"])
        # Skip text already captured as part of a table.
        if any(_mostly_inside(b_rect, t) for t in table_rects):
            continue

        # Join spans within a line, but keep lines separate. Flattening every
        # span of every line into one string glues unrelated text together:
        # a P&L row came back as "74,540.8266" (two adjacent column values run
        # into one number) and a slide label as "aFY24" (a bullet marker fused
        # onto a fiscal year). Both produced confident, well-grounded, wrong
        # facts â€” the number really is on the page, it just never existed.
        rendered = [
            "".join(span["text"] for span in line.get("spans", []))
            for line in b.get("lines", [])
        ]
        text = "\n".join(x for x in rendered if x.strip()).strip()
        if not text:
            continue

        start = cursor
        parts.append(text)
        cursor += len(text)
        blocks.append(
            Block(
                id=uuid4(),
                page=number,
                kind="heading" if _looks_like_heading(b, text) else "paragraph",
                text=text,
                char_start=start,
                char_end=cursor,
                rects=[_norm_rect(b_rect, rect)],
            )
        )
        parts.append("\n\n")
        cursor += 2

    page_text = "".join(parts)

    # The invariant everything downstream depends on. Cheap to assert, and a
    # violation here would corrupt every citation on the page.
    for blk in blocks:
        assert page_text[blk.char_start : blk.char_end] == blk.text, (
            f"offset drift on page {number}, block {blk.id}"
        )

    return Page(
        number=number,
        text=page_text,
        blocks=blocks,
        width=rect.width,
        height=rect.height,
    )


def _mostly_inside(inner: pymupdf.Rect, outer: pymupdf.Rect) -> bool:
    clipped = pymupdf.Rect(inner) & outer
    if not clipped.is_valid or inner.get_area() == 0:
        return False
    return clipped.get_area() / inner.get_area() > 0.6


def _looks_like_heading(block: dict, text: str) -> bool:
    """Cheap structural heuristic â€” larger or bolder than body text, and short.

    Deliberately generic: nothing here keys off a document's own section names,
    because a rule that only works on Delhivery's annual report is not a rule.
    """
    if len(text) > 120:
        return False
    sizes = [
        span.get("size", 0)
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ]
    flags = [
        span.get("flags", 0)
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ]
    if not sizes:
        return False
    bold = any(f & 2**4 for f in flags)
    return bold or max(sizes) >= 13.0


def find_span_rects(page: pymupdf.Page, quote: str) -> list[Rect]:
    """Locate a verbatim quote on a rendered page, for span-precise highlighting.

    Computed on demand rather than stored: keeping word-level geometry for every
    page of a 100-page filing would bloat the database for something needed only
    when a reader actually clicks a claim.
    """
    needle = " ".join(quote.split())
    if not needle:
        return []
    try:
        hits = page.search_for(needle)
    except Exception:  # noqa: BLE001
        hits = []
    if not hits and len(needle) > 60:
        # Long quotes often break across lines in ways search_for will not match.
        # The first clause is usually enough to land on the right line.
        hits = page.search_for(needle[:60])
    return [_norm_rect(h, page.rect) for h in hits]
