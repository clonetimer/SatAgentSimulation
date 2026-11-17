#!/usr/bin/env python3
"""用途：校验并提取固定版本 BIRDS-4 FM Assembly Procedure 的段落/表格文字。
参数：--docx、--expected-size、--expected-git-blob-sha1、--source-commit、--output。
输出：来源校验收据及按文档顺序编号的正文段落/表格文本；不自动解释或签发装配结论。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paragraph_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(W + "t")).strip()


def extract_docx(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml")
        except KeyError as error:
            raise ValueError("P9M3_DOCX_MAIN_DOCUMENT_MISSING") from error
    root = ET.fromstring(xml)
    body = root.find(".//w:body", NS)
    if body is None:
        raise ValueError("P9M3_DOCX_BODY_MISSING")
    records: list[dict] = []
    paragraph_index = 0
    table_index = 0
    block_index = 0
    for child in list(body):
        if child.tag == W + "p":
            paragraph_index += 1
            text = _paragraph_text(child)
            if text:
                records.append({
                    "block_index": block_index,
                    "kind": "paragraph",
                    "paragraph_index": paragraph_index,
                    "text": text,
                })
            block_index += 1
        elif child.tag == W + "tbl":
            table_index += 1
            rows = []
            for row_index, row in enumerate(child.findall("./w:tr", NS), start=1):
                cells = []
                for cell_index, cell in enumerate(row.findall("./w:tc", NS), start=1):
                    paragraphs = [_paragraph_text(p) for p in cell.findall(".//w:p", NS)]
                    cells.append({
                        "cell_index": cell_index,
                        "paragraphs": [text for text in paragraphs if text],
                        "text": "\n".join(text for text in paragraphs if text),
                    })
                rows.append({"row_index": row_index, "cells": cells})
            records.append({
                "block_index": block_index,
                "kind": "table",
                "table_index": table_index,
                "rows": rows,
            })
            block_index += 1
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-git-blob-sha1", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.docx.stat().st_size != args.expected_size:
        raise ValueError(f"P9M3_PROCEDURE_SIZE_MISMATCH:{args.docx.stat().st_size}")
    actual_git_sha1 = git_blob_sha1(args.docx)
    if actual_git_sha1 != args.expected_git_blob_sha1:
        raise ValueError(f"P9M3_PROCEDURE_GIT_BLOB_SHA1_MISMATCH:{actual_git_sha1}")
    blocks = extract_docx(args.docx)
    report = {
        "schema_version": "birds4-fm-assembly-procedure-extract.v1",
        "source": {
            "repository": "https://github.com/BIRDSOpenSource/BIRDS4-ProceduresAndReports",
            "path": "Procedures/BIRDS4-FM Assembly Procedure.docx",
            "source_commit": args.source_commit,
            "size_bytes": args.docx.stat().st_size,
            "git_blob_sha1": actual_git_sha1,
            "sha256": _sha256(args.docx),
            "verification_status": "source_bytes_verified",
        },
        "extraction": {
            "method": "OOXML main document body; paragraph and table order preserved",
            "paragraph_count": sum(1 for row in blocks if row["kind"] == "paragraph"),
            "table_count": sum(1 for row in blocks if row["kind"] == "table"),
            "human_review_status": "not_reviewed",
            "pagination_status": "not_available_from_docx_body_extraction",
            "interpretation_status": "raw_text_evidence_only",
        },
        "blocks": blocks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paragraph_count": report["extraction"]["paragraph_count"],
                      "table_count": report["extraction"]["table_count"],
                      "source_sha256": report["source"]["sha256"],
                      "human_review_status": report["extraction"]["human_review_status"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
