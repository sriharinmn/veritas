"""Generate a small filing with known facts, for testing the whole pipeline.

    python -m scripts.make_test_pdf            # writes fileupload.pdf

The starter corpus is real and therefore has no answer key: when extraction gets
something wrong there, telling that apart from the document being unusual takes
reading the page. This one is built to have an answer key.

It is deliberately a *plausible* filing rather than a list of test strings —
narrative on one page and a table on another, the way a real annual report puts
the same number in two places. That is the point: the interesting property to
test is that "₹7,225 crore" in a sentence and "72,251" in a table under a
"(₹ in millions)" caption are recognised as one fact, which is the assignment's
first required case and the thing a pipeline built on string or vector
similarity gets wrong.

What it should produce, and why each is here:

  ₹7,225 crore (p2)  ==  72,251 (p3, millions)   one fact, two dialects
  (1,820) in the table                           a parenthesised negative
  8.20% EBITDA margin (p2)  vs  8.2% (p3)        same rate, two spellings
  "formerly known as TestCo Private Limited"     a non-numeric fact
  published 15 May 2024                          makes FY24 a possible period
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

OUT = Path("fileupload.pdf")

PAGES: list[list[tuple[float, float, str, int, int]]] = [
    # (x, y, text, size, weight) — weight 1 renders bold
    [
        (72, 120, "TESTCO LIMITED", 20, 1),
        (72, 150, "Annual Report for the year ended March 31, 2024", 12, 0),
        (72, 175, "Corporate Identity Number: U63090DL2011PLC900001", 10, 0),
        (72, 195, "Dated: May 15, 2024", 10, 0),
        (72, 240, "Registered Office: 12 Example Road, New Delhi 110001, India", 10, 0),
        (72, 300, "This report is prepared in accordance with the Indian Accounting", 10, 0),
        (72, 315, "Standards (Ind AS) notified under Section 133 of the Companies Act, 2013.", 10, 0),
    ],
    [
        (72, 90, "Management Discussion and Analysis", 15, 1),
        (72, 130, "Revenue from operations for the year ended March 31, 2024 was", 11, 0),
        (72, 148, "Rs. 7,225 crore, an increase of 12.4% over the prior year. Growth was", 11, 0),
        (72, 166, "driven by higher volumes in the express parcel business.", 11, 0),
        (72, 205, "EBITDA margin stood at 8.20% in FY24, compared with 6.10% in FY23.", 11, 0),
        (72, 243, "The Company operated 132 processing centres as at March 31, 2024", 11, 0),
        (72, 261, "and employed 58,400 people.", 11, 0),
        (72, 300, "Cross-border services contributed 4.35% of consolidated revenue.", 11, 0),
    ],
    [
        (72, 90, "Statement of Profit and Loss", 15, 1),
        (72, 118, "(Rs. in millions)", 10, 0),
        (72, 150, "Particulars", 10, 1),
        (300, 150, "March 31, 2024", 10, 1),
        (420, 150, "March 31, 2023", 10, 1),
        (72, 178, "Revenue from operations", 10, 0),
        (300, 178, "72,251", 10, 0),
        (420, 178, "64,280", 10, 0),
        (72, 200, "Other income", 10, 0),
        (300, 200, "4,527", 10, 0),
        (420, 200, "3,049", 10, 0),
        (72, 222, "Total income", 10, 0),
        (300, 222, "76,778", 10, 0),
        (420, 222, "67,329", 10, 0),
        (72, 250, "Employee benefits expense", 10, 0),
        (300, 250, "14,368", 10, 0),
        (420, 250, "14,000", 10, 0),
        (72, 272, "Depreciation and amortisation expense", 10, 0),
        (300, 272, "7,216", 10, 0),
        (420, 272, "8,311", 10, 0),
        (72, 294, "Profit/(loss) after tax", 10, 0),
        (300, 294, "(1,820)", 10, 0),
        (420, 294, "(2,410)", 10, 0),
        (72, 330, "EBITDA margin (%)", 10, 0),
        (300, 330, "8.2", 10, 0),
        (420, 330, "6.1", 10, 0),
    ],
    [
        (72, 90, "Notes to the Financial Statements", 15, 1),
        (72, 130, "TestCo Limited (formerly known as TestCo Private Limited) was", 11, 0),
        (72, 148, "incorporated on June 12, 2011 under the Companies Act, 1956.", 11, 0),
        (72, 186, "The statutory auditors of the Company are Example & Co,", 11, 0),
        (72, 204, "Chartered Accountants, appointed at the Annual General Meeting.", 11, 0),
        (72, 242, "The Scheme of Arrangement was approved by the NCLT vide order", 11, 0),
        (72, 260, "dated November 27, 2019.", 11, 0),
        (72, 298, "The Company holds a 34.55% interest in Example Logistics Private", 11, 0),
        (72, 316, "Limited, an associate company incorporated in India.", 11, 0),
    ],
]


def main() -> int:
    doc = pymupdf.open()
    for page_content in PAGES:
        page = doc.new_page(width=595, height=842)  # A4
        for x, y, text, size, bold in page_content:
            page.insert_text(
                (x, y),
                text,
                fontsize=size,
                fontname="helv" if not bold else "hebo",
                color=(0.05, 0.08, 0.12),
            )
    doc.set_metadata({"title": "TestCo Limited Annual Report FY24", "author": "TestCo Limited"})
    doc.save(OUT)
    doc.close()

    size = OUT.stat().st_size
    print(f"wrote {OUT} — {len(PAGES)} pages, {size:,} bytes")
    print("\nthe answer key:")
    print("  Rs. 7,225 crore (p2)  ==  72,251 millions (p3)   -> one fact, two dialects")
    print("  8.20% (p2)            ==  8.2 (p3)               -> one rate, two spellings")
    print("  (1,820) on p3                                    -> must normalise negative")
    print("  'formerly known as TestCo Private Limited' (p4)  -> a non-numeric fact")
    print("  published 2024-05-15                             -> FY24 is a possible period")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
