"""Recovering column headers and row labels from half-detected tables.

PyMuPDF often detects a financial table's header as a grid and then flattens its
body into plain text. The period and the reporting basis are then two blocks away
from the values they describe, and four figures from four different columns reach
the comparator looking identical in scope — which is a contradiction by every
rule, and a wrong one.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from core.parse.columns import (
    basis_from_label,
    header_layout,
    infer_column_headers,
    numeric_runs,
)
from core.parse.pdf import Block, Page, parse_pdf

SEED = Path(__file__).resolve().parents[1] / "seed"
REPORT = SEED / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"

HEADER = (
    "| Particulars | Standalone – FY ended |  | Consolidated – FY ended |  |\n"
    "| --- | --- | --- | --- | --- |\n"
    "|  | March 31, 2024 | March 31, 2023 | March 31, 2024 | March 31, 2023 |"
)
BODY = "Revenue from Operations\n74,540.82\n66,586.61\n81,415.38\n72,253.01\nOther Income\n4,753.49\n3,311.74\n4,526.96\n3,049.48"


def _page() -> Page:
    text = HEADER + "\n\n" + BODY
    header_block = Block(
        id=uuid4(), page=1, kind="table", text=HEADER,
        char_start=0, char_end=len(HEADER), rects=[], header_path="Particulars",
    )
    body_start = len(HEADER) + 2
    body_block = Block(
        id=uuid4(), page=1, kind="paragraph", text=BODY,
        char_start=body_start, char_end=body_start + len(BODY), rects=[],
    )
    return Page(number=1, text=text, blocks=[header_block, body_block], width=595.0, height=842.0)


def test_a_spanning_header_is_forward_filled_across_its_columns():
    """"Standalone – FY ended" is written once above two columns.

    Without forward-filling the second column loses its basis, and a standalone
    figure becomes indistinguishable from a consolidated one.
    """
    layout = header_layout(_page())
    assert layout is not None
    assert layout.width == 4
    assert layout.labels == [
        "Standalone – FY ended March 31, 2024",
        "Standalone – FY ended March 31, 2023",
        "Consolidated – FY ended March 31, 2024",
        "Consolidated – FY ended March 31, 2023",
    ]


def test_every_recovered_column_resolves_to_a_real_period():
    layout = header_layout(_page())
    assert layout.periods_resolve()


def test_flattened_rows_are_found_with_their_labels():
    runs = numeric_runs(_page())
    assert len(runs) == 2
    assert all(len(spans) == 4 for _label, spans in runs)


def test_values_are_tagged_with_both_column_and_row():
    page = _page()
    cmap = infer_column_headers(page)
    assert len(cmap) == 8

    first = cmap.lookup(page.text.index("74,540.82"))
    assert first is not None
    assert first.row_label == "Revenue from Operations"
    assert first.column == "Standalone – FY ended March 31, 2024"

    third = cmap.lookup(page.text.index("81,415.38"))
    assert third.column == "Consolidated – FY ended March 31, 2024"
    assert third.row_label == "Revenue from Operations"

    other = cmap.lookup(page.text.index("4,753.49"))
    assert other.row_label == "Other Income"


def test_a_run_that_does_not_match_the_header_width_is_left_alone():
    """Guessing on a partial match would attach a period to a figure that does
    not have one, which silently makes two incomparable claims look comparable —
    worse than leaving it unlabelled, because the system then invents a
    contradiction of its own."""
    text = HEADER + "\n\nSome Metric\n1.00\n2.00"
    page = _page()
    page.text = text
    page.blocks[1].text = "Some Metric\n1.00\n2.00"
    page.blocks[1].char_start = len(HEADER) + 2
    page.blocks[1].char_end = len(text)
    assert len(infer_column_headers(page)) == 0


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Standalone – FY ended March 31, 2024", "standalone"),
        ("Consolidated – FY ended March 31, 2023", "consolidated"),
        ("Stand-alone results", "standalone"),
        ("March 31, 2024", None),
        (None, None),
    ],
)
def test_basis_is_read_from_the_column_header(label, expected):
    assert basis_from_label(label) == expected


@pytest.mark.skipif(not REPORT.exists(), reason="starter corpus not present")
def test_it_works_on_the_real_annual_report():
    doc = parse_pdf(REPORT)
    page = next(p for p in doc.pages if p.number == 22)
    cmap = infer_column_headers(page)

    assert len(cmap) >= 40
    cell = cmap.lookup(page.text.index("74,540.82"))
    assert cell is not None
    assert "Revenue from Operations" in cell.row_label
    assert basis_from_label(cell.column) == "standalone"
    assert "2024" in cell.column
