#!/usr/bin/env python3
"""
Convert a .docx file into a simple Markdown document.

This utility is intentionally generic and reusable for grant attachments:
- synopsis
- characters
- references
- marketing strategy
- partner letters or supporting descriptions
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from docx import Document


HEADING_STYLES = {
    "title": "#",
    "heading 1": "#",
    "heading 2": "##",
    "heading 3": "###",
    "heading 4": "####",
}


def paragraph_to_markdown(paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""

    style_name = (paragraph.style.name or "").strip().lower()
    if style_name in HEADING_STYLES:
        return f"{HEADING_STYLES[style_name]} {text}"

    if style_name.startswith("list bullet"):
        return f"- {text}"

    if style_name.startswith("list number"):
        return f"1. {text}"

    return text


def table_to_markdown(table) -> str:
    rows = []
    for row in table.rows:
        values = [cell.text.strip().replace("\n", "<br>") for cell in row.cells]
        rows.append(values)

    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def document_to_markdown(path: Path) -> str:
    doc = Document(str(path))
    parts: list[str] = []

    paragraph_count = len(doc.paragraphs)
    table_count = len(doc.tables)
    paragraph_index = 0
    table_index = 0

    for element in doc.element.body:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = doc.paragraphs[paragraph_index]
            paragraph_index += 1
            rendered = paragraph_to_markdown(paragraph)
            if rendered:
                parts.append(rendered)
        elif tag == "tbl":
            table = doc.tables[table_index]
            table_index += 1
            rendered = table_to_markdown(table)
            if rendered:
                parts.append(rendered)

    if paragraph_index != paragraph_count or table_index != table_count:
        # Fallback safety: if the traversal missed something, append plain paragraphs.
        rendered_paragraphs = [paragraph_to_markdown(p) for p in doc.paragraphs]
        parts = [part for part in rendered_paragraphs if part]

    return "\n\n".join(parts).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a .docx file into Markdown."
    )
    parser.add_argument("input", help="Path to the source .docx file")
    parser.add_argument(
        "-o",
        "--output",
        help="Optional output .md path. Defaults next to the source file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()

    if not input_path.exists():
        print(f"Input file does not exist: {input_path}", file=sys.stderr)
        return 1

    if input_path.suffix.lower() != ".docx":
        print(f"Expected a .docx file, got: {input_path.name}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.with_suffix(".md")
    )

    markdown = document_to_markdown(input_path)
    output_path.write_text(markdown, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
