---
name: pdf-markdown-docx
description: "Use when converting PDFs to Markdown or Markdown/DOCX attachments, including text-layer PDFs, slide decks, scanned pages, and round-tripping revised content back into DOCX."
---

# PDF / Markdown / DOCX Conversion

Use this skill when you need a reliable document conversion loop:

- PDF -> Markdown for rules, templates, slide decks, and reference docs
- Markdown -> DOCX for final attachments
- DOCX -> Markdown for safe editing and comparison

## Choose the right mode

- `--mode auto` for unknown PDFs
- `--mode text` for clean text-layer PDFs
- `--mode ocr` for scans or image-only PDFs without LLM
- `--mode llm` for slide decks or complex pages that should be read by vision
- `--mode text+images` for mixed PDFs with both text and visuals
- `--mode images` as a backward-compatible alias for `llm`

## LLM-backed conversion

When using vision-based conversion, provide the endpoint and key through flags or env vars:

- `--llm-base-url` or `PDF_TO_MD_LLM_BASE_URL`
- `--llm-api-key` or `PDF_TO_MD_LLM_API_KEY`

Prefer `auto` unless you know the source class.
Use `text` when the PDF already has a good embedded text layer.
Use `ocr` when the PDF is basically a scan and you want no LLM dependency.
Use `llm` when layout, slides, or embedded visuals matter more than the text layer.
After revising Markdown, convert it back with `scripts/markdown_to_docx.py`.
