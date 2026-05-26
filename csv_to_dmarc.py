#!/usr/bin/env python3
"""Convert the library catalog CSV to a Koha-importable MARCXML file.

The output file can use a .dmarc extension, but its content is MARCXML.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MARC_NS = "http://www.loc.gov/MARC21/slim"
ET.register_namespace("", MARC_NS)


def clean(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return ""
    return " ".join(text.split())


def safe_int(value: str | None, default: int = 0) -> int:
    text = clean(value)
    if not text:
        return default
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return default
    return int(digits)


def detect_header_row(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for idx, row in enumerate(reader):
            lowered = [cell.strip().lower() for cell in row]
            if "book id" in lowered and "title" in lowered:
                return idx
    raise ValueError("Could not locate header row containing 'Book ID' and 'Title'.")


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    header_row = detect_header_row(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()[header_row:]
    reader = csv.DictReader(lines)
    return [row for row in reader if clean(row.get("Book ID")) and clean(row.get("Title"))]


def add_datafield(record: ET.Element, tag: str, ind1: str = " ", ind2: str = " ") -> ET.Element:
    return ET.SubElement(record, f"{{{MARC_NS}}}datafield", {"tag": tag, "ind1": ind1, "ind2": ind2})


def add_subfield(parent: ET.Element, code: str, value: str) -> None:
    if value:
        sf = ET.SubElement(parent, f"{{{MARC_NS}}}subfield", {"code": code})
        sf.text = value


def make_008(log_date: str) -> str:
    parsed = clean(log_date)
    yymmdd = dt.datetime.utcnow().strftime("%y%m%d")

    if parsed:
        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                yymmdd = dt.datetime.strptime(parsed, fmt).strftime("%y%m%d")
                break
            except ValueError:
                continue

    # Minimal valid fixed-length field (40 chars).
    return f"{yymmdd}s        xx                  eng d"


def build_record(row: dict[str, str], create_items: bool, branch: str, itemtype: str) -> ET.Element:
    record = ET.Element(f"{{{MARC_NS}}}record")

    leader = ET.SubElement(record, f"{{{MARC_NS}}}leader")
    leader.text = "     nam a22     4500"

    book_id = clean(row.get("Book ID"))
    cf001 = ET.SubElement(record, f"{{{MARC_NS}}}controlfield", {"tag": "001"})
    cf001.text = book_id

    cf008 = ET.SubElement(record, f"{{{MARC_NS}}}controlfield", {"tag": "008"})
    cf008.text = make_008(row.get("Log Date", ""))

    author = clean(row.get("Author (Last Name, First Name)"))
    if author:
        df100 = add_datafield(record, "100", "1", " ")
        add_subfield(df100, "a", author)

    title = clean(row.get("Title"))
    subtitle = clean(row.get("Subtitle (if applicable)"))
    if title:
        df245 = add_datafield(record, "245", "1" if author else "0", "0")
        add_subfield(df245, "a", title)
        add_subfield(df245, "b", subtitle)

    series_title = clean(row.get("Series Title"))
    if series_title:
        df490 = add_datafield(record, "490", "0", " ")
        add_subfield(df490, "a", series_title)

    pages = clean(row.get("Number of Pages"))
    if pages:
        df300 = add_datafield(record, "300", " ", " ")
        add_subfield(df300, "a", f"{pages} pages")

    binding = clean(row.get("Binding"))
    if binding:
        df500 = add_datafield(record, "500", " ", " ")
        add_subfield(df500, "a", f"Binding: {binding}")

    category = clean(row.get("Category"))
    if category:
        df650 = add_datafield(record, "650", " ", "0")
        add_subfield(df650, "a", category)

    category_list = clean(row.get("Category List"))
    if category_list:
        for raw in [c.strip() for c in category_list.split(",") if c.strip()]:
            df650 = add_datafield(record, "650", " ", "0")
            add_subfield(df650, "a", raw)

    category_desc = clean(row.get("Category Description"))
    if category_desc:
        df520 = add_datafield(record, "520", " ", " ")
        add_subfield(df520, "a", category_desc)

    copies = max(1, safe_int(row.get("Number of Copies"), default=1))
    if create_items:
        for i in range(copies):
            df952 = add_datafield(record, "952", " ", " ")
            add_subfield(df952, "a", branch)
            add_subfield(df952, "b", branch)
            add_subfield(df952, "y", itemtype)
            barcode = f"{book_id}-{i + 1:03d}" if book_id else f"AUTO-{i + 1:03d}"
            add_subfield(df952, "p", barcode)

    return record


def indent(elem: ET.Element, level: int = 0) -> None:
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            indent(child, level + 1)
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def convert(csv_path: Path, output_path: Path, create_items: bool, branch: str, itemtype: str) -> int:
    rows = load_rows(csv_path)

    collection = ET.Element(f"{{{MARC_NS}}}collection")
    for row in rows:
        record = build_record(row, create_items=create_items, branch=branch, itemtype=itemtype)
        collection.append(record)

    indent(collection)
    tree = ET.ElementTree(collection)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return len(rows)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert catalog CSV to Koha-importable MARCXML (.dmarc)."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to source CSV file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output .dmarc file (MARCXML content).",
    )
    parser.add_argument(
        "--create-items",
        action="store_true",
        help="Create Koha 952 item records from Number of Copies.",
    )
    parser.add_argument(
        "--branch",
        default="MAIN",
        help="Koha branch code for 952$a and 952$b when --create-items is used (default: MAIN).",
    )
    parser.add_argument(
        "--itemtype",
        default="BOOK",
        help="Koha item type code for 952$y when --create-items is used (default: BOOK).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    csv_path = Path(args.input)
    output_path = Path(args.output)

    if not csv_path.exists():
        print(f"Input file not found: {csv_path}", file=sys.stderr)
        return 1

    try:
        count = convert(
            csv_path=csv_path,
            output_path=output_path,
            create_items=args.create_items,
            branch=args.branch,
            itemtype=args.itemtype,
        )
    except Exception as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {count} MARC records to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
