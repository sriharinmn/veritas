"""Recovering column headers for tables the detector only half-found.

PyMuPDF frequently detects a financial table's *header* and then flattens its
*body* into ordinary text — the header comes back as a proper grid while the
rows arrive as a label line followed by one number per line:

    | Particulars | Standalone – FY ended |    | Consolidated – FY ended |    |
    |             | March 31, 2024 | March 31, 2023 | March 31, 2024 | March 31, 2023 |

    Revenue from Operations
    74,540.82
    66,586.61
    81,415.38
    72,253.01

Every one of those four numbers then reaches the extractor with no period and no
basis attached, and four claims are produced that look identical in scope while
holding different values. The comparator, correctly, calls that a contradiction.
It is the single largest source of false contradictions in this corpus, and it
is not a model problem — the information is right there on the page, two blocks
away.

The recovery is positional and entirely generic: a label followed by *k*
consecutive numeric lines, where a header on the same page defines *k* value
columns, means the i-th number belongs to the i-th column. Nothing here knows
anything about Delhivery, revenue, or annual reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from core.models import PeriodKind
from core.normalize.periods import parse_period
from core.parse.pdf import Page

log = structlog.get_logger(__name__)

# A line that is nothing but a number, including the financial conventions:
# parenthesised negatives, nil dashes, and thousands separators.
_NUMERIC_LINE = re.compile(r"^\s*[(\-–—]?\s*(?:[\d,]+(?:\.\d+)?|[-–—])\s*\)?\s*$")

# A run shorter than this is more likely to be prose than a flattened table row.
MIN_RUN = 2


@dataclass
class ColumnLayout:
    """The value columns of a table, in left-to-right order."""

    labels: list[str]
    source_quote: str

    @property
    def width(self) -> int:
        return len(self.labels)

    def periods_resolve(self) -> bool:
        return all(
            parse_period(x).kind is not PeriodKind.UNKNOWN for x in self.labels
        )


def _split_row(row: str) -> list[str]:
    cells = row.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _forward_fill(cells: list[str]) -> list[str]:
    """Spread a spanning header across the columns it covers.

    "Standalone – FY ended" sits above two columns and is written once, with the
    second cell empty. Without forward-filling, the second column loses its basis
    and a standalone figure becomes indistinguishable from a consolidated one.
    """
    out: list[str] = []
    last = ""
    for c in cells:
        if c:
            last = c
        out.append(last)
    return out


def header_layout(page: Page) -> ColumnLayout | None:
    """Build a column layout from any markdown header table on the page.

    Multi-row headers are combined, so a basis row above a period row produces
    "Standalone – FY ended March 31, 2024" rather than either half alone.
    """
    for block in page.blocks:
        if block.kind != "table":
            continue
        rows = [r for r in block.text.split("\n") if r.strip().startswith("|")]
        rows = [r for r in rows if not set(r.replace("|", "").strip()) <= {"-", " "}]
        if len(rows) < 2:
            continue

        grids = [_split_row(r) for r in rows]
        width = max(len(g) for g in grids)
        grids = [g + [""] * (width - len(g)) for g in grids]

        # The first column of a financial table is the row label, not a value.
        combined: list[str] = []
        for col in range(1, width):
            parts = []
            for grid in grids:
                filled = _forward_fill(grid)
                if filled[col] and filled[col] not in parts:
                    parts.append(filled[col])
            combined.append(" ".join(parts).strip())

        labels = [c for c in combined if c]
        if len(labels) < 2:
            continue
        layout = ColumnLayout(labels=labels, source_quote=block.text[:300])
        if layout.periods_resolve():
            return layout

    return None


def numeric_runs(page: Page) -> list[tuple[int, list[tuple[int, int]]]]:
    """Find flattened table rows: a label line followed by numeric lines.

    Returns, per run, the character offset of the label line and the spans of the
    numeric lines that follow it — so a caller can map a value's position back to
    a column without re-parsing anything.
    """
    runs: list[tuple[int, list[tuple[int, int]]]] = []

    for block in page.blocks:
        if block.kind == "table":
            continue

        offset = block.char_start
        label_at: int | None = None
        current: list[tuple[int, int]] = []

        for line in block.text.split("\n"):
            start = offset
            end = offset + len(line)
            offset = end + 1  # the newline

            if _NUMERIC_LINE.match(line):
                if label_at is not None:
                    current.append((start, end))
                continue

            if len(current) >= MIN_RUN and label_at is not None:
                runs.append((label_at, current))
            current = []
            label_at = start if line.strip() else None

        if len(current) >= MIN_RUN and label_at is not None:
            runs.append((label_at, current))

    return runs


@dataclass
class Cell:
    """One recovered table cell: where it is, its column, and its row label."""

    start: int
    end: int
    column: str
    row_label: str


@dataclass
class ColumnMap:
    """Character ranges on a page, each tagged with its column and row."""

    cells: list[Cell]
    layout: ColumnLayout | None = None

    def lookup(self, offset: int) -> Cell | None:
        for cell in self.cells:
            if cell.start <= offset < cell.end:
                return cell
        return None

    def __len__(self) -> int:
        return len(self.cells)


def infer_column_headers(page: Page) -> ColumnMap:
    """Tag each flattened table value with the column header it belongs under.

    Only applied when a run's length exactly matches the header width. A partial
    match means something about the layout is not understood, and guessing there
    would attach a period to a figure that does not have one — which is worse
    than leaving it unlabelled, because a wrong period silently makes two
    incomparable claims look comparable, and the system then reports a
    contradiction it invented itself.
    """
    layout = header_layout(page)
    if layout is None:
        return ColumnMap(cells=[])

    cells: list[Cell] = []
    matched = 0
    for label_at, runs in numeric_runs(page):
        if len(runs) != layout.width:
            continue
        matched += 1
        # The label line of the run is the row header — "Revenue from
        # Operations" — which is what the value actually measures. Recovering it
        # matters as much as the column: once the body is flattened, the label
        # sits on its own line and every heuristic that looks backwards from the
        # number finds nothing but a line break.
        row_end = page.text.find("\n", label_at)
        row_label = page.text[label_at : row_end if row_end != -1 else len(page.text)].strip()
        for (start, end), label in zip(runs, layout.labels, strict=True):
            cells.append(Cell(start=start, end=end, column=label, row_label=row_label))

    if cells:
        log.info(
            "columns.inferred",
            page=page.number,
            columns=layout.width,
            rows=matched,
            values=len(cells),
        )
    return ColumnMap(cells=cells, layout=layout)


_BASIS_RE = re.compile(r"\b(consolidated|standalone|stand-alone)\b", re.IGNORECASE)


def basis_from_label(text: str | None) -> str | None:
    """Read the reporting basis out of a column header.

    Indian annual reports routinely present standalone and consolidated figures
    side by side in one table, and the basis appears only in the spanning header
    above the period row. Without it, a standalone figure and a consolidated one
    for the same metric and the same year differ in value while looking identical
    in scope — which is a contradiction by every rule in the comparator, and a
    wrong one.
    """
    m = _BASIS_RE.search(text or "")
    if not m:
        return None
    return "standalone" if m.group(1).lower().startswith("stand") else "consolidated"
