#!/usr/bin/env python3
"""Convert a PDF into a Markdown file with selectable extraction modes.

This utility is meant for official grant rules, instructions, templates, and
presentation PDFs. It supports:
- `auto`: choose the best extraction path per page
- `text`: use only the embedded text layer
- `text+images`: combine the text layer with a vision model when useful
- `images`: use a vision model on rendered pages

For image-only pages, the tool can fall back to OCR or an OpenAI-compatible
vision endpoint.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

import pdfplumber


TESSERACT_BIN = shutil.which("tesseract")
PDFTOPPM_BIN = shutil.which("pdftoppm")
DEFAULT_LLM_MODEL = "qwen3.5"


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_real_text(page_text: str) -> bool:
    compact = re.sub(r"\s+", "", page_text)
    return len(compact) >= 24


def has_image_content(page) -> bool:
    return bool(getattr(page, "images", None))


def clean_ocr_text(page_text: str) -> str:
    lines: list[str] = []
    for raw_line in page_text.split("\n"):
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue

        if line.startswith(("•", "-", "–", "—", "·")):
            lines.append(line)
            continue

        letter_count = len(re.findall(r"[A-Za-zА-Яа-яЁё]", line))

        if re.search(r"[А-Яа-яЁё]", line):
            if letter_count < 2:
                continue
            lines.append(line)
            continue

        if re.search(r"(@|://|www\.|\.ru\b|\.рф\b|\+7\b|MariM)", line):
            lines.append(line)
            continue

        if re.fullmatch(r"[\d\s().+-]+", line):
            lines.append(line)
            continue

        if letter_count < 2:
            continue

    return normalize_text("\n".join(lines))


def ocr_page(input_path: Path, page_number: int) -> str:
    if not PDFTOPPM_BIN:
        raise SystemExit("pdftoppm is required for OCR fallback but was not found in PATH")
    if not TESSERACT_BIN:
        raise SystemExit("tesseract is required for OCR fallback but was not found in PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = Path(tmpdir) / f"page_{page_number}"
        subprocess.run(
            [
                PDFTOPPM_BIN,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                "300",
                "-singlefile",
                "-png",
                str(input_path),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        image_path = prefix.with_suffix(".png")
        result = subprocess.run(
            [
                TESSERACT_BIN,
                str(image_path),
                "stdout",
                "-l",
                "rus+eng",
                "--psm",
                "11",
                "-c",
                "preserve_interword_spaces=1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return clean_ocr_text(result.stdout)


def render_page_png_bytes(input_path: Path, page_number: int, dpi: int) -> bytes:
    if not PDFTOPPM_BIN:
        raise SystemExit("pdftoppm is required but was not found in PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = Path(tmpdir) / f"page_{page_number}"
        subprocess.run(
            [
                PDFTOPPM_BIN,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                str(dpi),
                "-singlefile",
                "-png",
                str(input_path),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        image_path = prefix.with_suffix(".png")
        if not image_path.exists():
            raise SystemExit(f"Failed to render page {page_number} to PNG")
        return image_path.read_bytes()


def get_llm_api_key(args: argparse.Namespace) -> str | None:
    for candidate in (
        args.llm_api_key,
        os.environ.get("PDF_TO_MD_LLM_API_KEY"),
        os.environ.get("LLM_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def get_llm_base_url(args: argparse.Namespace) -> str | None:
    for candidate in (
        args.llm_base_url,
        os.environ.get("PDF_TO_MD_LLM_BASE_URL"),
        os.environ.get("LLM_BASE_URL"),
        os.environ.get("OPENAI_BASE_URL"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def call_llm_markdown(
    *,
    input_path: Path,
    page_number: int,
    page_text: str,
    args: argparse.Namespace,
) -> str:
    api_key = get_llm_api_key(args)
    base_url = get_llm_base_url(args)
    if not base_url:
        raise SystemExit(
            "LLM mode requested but no base URL was provided. Set --llm-base-url or PDF_TO_MD_LLM_BASE_URL."
        )
    if not api_key:
        raise SystemExit(
            "LLM mode requested but no API key was provided. Set --llm-api-key or PDF_TO_MD_LLM_API_KEY."
        )

    prompt_parts = [
        "Convert this PDF page into clean Markdown.",
        "Preserve the slide/page title, section hierarchy, bullets, and short inline emphasis if obvious.",
        "Do not invent content.",
        "Remove OCR noise and decorative symbols.",
        "If the page is a presentation slide, use a Markdown heading for the slide title and bullet lists for bullets.",
        "If the page contains tables, preserve them as a simple Markdown table only if the structure is clear; otherwise use short bullet points.",
    ]
    if page_text:
        prompt_parts.append("Embedded text layer for reference:")
        prompt_parts.append(page_text)

    payload = {
        "model": args.llm_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise document conversion assistant. "
                    "Return Markdown only, with no commentary."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "\n\n".join(prompt_parts)},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64.b64encode(render_page_png_bytes(input_path, page_number, args.render_dpi)).decode('ascii')}"
                        },
                    },
                ],
            },
        ],
        "temperature": args.llm_temperature,
        "max_tokens": args.llm_max_tokens,
    }

    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=args.llm_timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload_text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"LLM HTTP {exc.code} for page {page_number}: {payload_text[:800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"LLM request failed for page {page_number}: {exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise SystemExit(f"LLM returned no choices for page {page_number}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SystemExit(f"LLM returned empty content for page {page_number}")
    return normalize_text(content)


def format_slide_markdown(page_text: str) -> str:
    lines = [line.strip() for line in page_text.split("\n")]
    formatted: list[str] = []

    for line in lines:
        if not line:
            if formatted and formatted[-1] != "":
                formatted.append("")
            continue

        slide_match = re.match(r"^(Слайд|Slide)\s+(\d+)\.?\s*(.*)$", line, re.IGNORECASE)
        if slide_match:
            slide_number = slide_match.group(2)
            slide_title = slide_match.group(3).strip()
            heading = f"### Слайд {slide_number}"
            if slide_title:
                heading = f"{heading}. {slide_title}"
            formatted.append(heading)
            formatted.append("")
            continue

        bullet_match = re.match(r"^[•\-\u2013\u2014·]\s*(.*)$", line)
        if bullet_match:
            formatted.append(f"- {bullet_match.group(1).strip()}")
            continue

        numbered_match = re.match(r"^(\d+)[\).\s]\s*(.*)$", line)
        if numbered_match and numbered_match.group(2):
            formatted.append(f"{numbered_match.group(1)}. {numbered_match.group(2).strip()}")
            continue

        formatted.append(line)

    body = "\n".join(formatted)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def extract_page_text(page, input_path: Path, page_number: int, args: argparse.Namespace) -> str:
    text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
    text = normalize_text(text)
    page_has_images = has_image_content(page)
    mode = args.mode

    if mode == "text":
        return text
    if mode == "images":
        return call_llm_markdown(
            input_path=input_path,
            page_number=page_number,
            page_text="",
            args=args,
        )
    if mode == "text+images":
        if looks_like_real_text(text) and page_has_images:
            return call_llm_markdown(
                input_path=input_path,
                page_number=page_number,
                page_text=text,
                args=args,
            )
        if looks_like_real_text(text):
            return text
        if args.llm_api_key or get_llm_api_key(args):
            return call_llm_markdown(
                input_path=input_path,
                page_number=page_number,
                page_text=text,
                args=args,
            )
        return ocr_page(input_path, page_number)

    if looks_like_real_text(text) and not page_has_images:
        return text
    if looks_like_real_text(text) and page_has_images and (args.llm_api_key or get_llm_api_key(args)):
        return call_llm_markdown(
            input_path=input_path,
            page_number=page_number,
            page_text=text,
            args=args,
        )
    if args.llm_api_key or get_llm_api_key(args):
        return call_llm_markdown(
            input_path=input_path,
            page_number=page_number,
            page_text=text,
            args=args,
        )
    return ocr_page(input_path, page_number)


def page_to_markdown(page_text: str, page_number: int) -> str:
    page_text = normalize_text(page_text)
    if not page_text:
        return f"## Page {page_number}\n\n[No extractable text]\n"

    body = format_slide_markdown(page_text)
    return f"## Page {page_number}\n\n{body}\n"


def convert_pdf(input_path: Path, args: argparse.Namespace) -> str:
    title = input_path.stem
    pages = []

    with pdfplumber.open(str(input_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = extract_page_text(page, input_path, index, args)
            pages.append(page_to_markdown(text, index))

    return f"# {title}\n\n" + "\n".join(pages).strip() + "\n"


def convert_pdf_legacy(input_path: Path) -> str:
    # Kept as a fallback path for environments without pdfplumber.
    from pypdf import PdfReader

    reader = PdfReader(str(input_path))
    title = input_path.stem
    pages = []

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(page_to_markdown(text, index))

    return f"# {title}\n\n" + "\n".join(pages).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a PDF into Markdown."
    )
    parser.add_argument("input", help="Path to the source .pdf file")
    parser.add_argument(
        "-o",
        "--output",
        help="Optional output .md path. Defaults next to the source file.",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "text", "text+images", "images"],
        help="Extraction mode: auto, text, text+images, or images.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help="OpenAI-compatible LLM base URL. Can also come from PDF_TO_MD_LLM_BASE_URL.",
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_LLM_MODEL,
        help=f"LLM model name. Default: {DEFAULT_LLM_MODEL}",
    )
    parser.add_argument(
        "--llm-api-key",
        help="API key for the OpenAI-compatible LLM endpoint. Can also come from PDF_TO_MD_LLM_API_KEY.",
    )
    parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=1200,
        help="Maximum tokens for the LLM response.",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=0.0,
        help="LLM temperature.",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=120,
        help="LLM request timeout in seconds.",
    )
    parser.add_argument(
        "--render-dpi",
        type=int,
        default=300,
        help="PNG render DPI used for OCR and image-based LLM modes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()

    if not input_path.exists():
        print(f"Input file does not exist: {input_path}", file=sys.stderr)
        return 1

    if input_path.suffix.lower() != ".pdf":
        print(f"Expected a .pdf file, got: {input_path.name}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.with_suffix(".md")
    )

    try:
        markdown = convert_pdf(input_path, args)
    except Exception:
        markdown = convert_pdf_legacy(input_path)
    output_path.write_text(markdown, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
