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
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


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
        try:
            relationships_xml = archive.read("word/_rels/document.xml.rels")
            relationships_root = ET.fromstring(relationships_xml)
            relationships = {
                relation.attrib.get("Id"): relation.attrib.get("Target")
                for relation in relationships_root
            }
        except KeyError:
            relationships = {}
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
            if text or list(child.iter(A + "blip")):
                image_refs = []
                for blip in child.iter(A + "blip"):
                    rel_id = blip.attrib.get(R + "embed")
                    if rel_id:
                        image_refs.append({"relationship_id": rel_id, "target": relationships.get(rel_id)})
                records.append({
                    "block_index": block_index,
                    "kind": "paragraph",
                    "paragraph_index": paragraph_index,
                    "text": text,
                    "embedded_media_refs": image_refs,
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
            image_refs = []
            for blip in child.iter(A + "blip"):
                rel_id = blip.attrib.get(R + "embed")
                if rel_id:
                    image_refs.append({"relationship_id": rel_id, "target": relationships.get(rel_id)})
            records.append({
                "block_index": block_index,
                "kind": "table",
                "table_index": table_index,
                "rows": rows,
                "embedded_media_refs": image_refs,
            })
            block_index += 1
    return records


KEYWORD_TERMS = (
    "structural testing model", "battery", "reaction wheel",
    "perovskite solar cells", "-y panel", "axis definition",
    "maya-2", "tsuru", "guarani-1",
)

def build_keyword_index(blocks: list[dict]) -> dict[str, list[dict]]:
    """Return raw source locators only; do not interpret or approve claims."""
    index: dict[str, list[dict]] = {term: [] for term in KEYWORD_TERMS}
    for block in blocks:
        if block["kind"] == "paragraph":
            text = block["text"]
            for term in KEYWORD_TERMS:
                if term in text.casefold():
                    index[term].append({"kind": "paragraph", "block_index": block["block_index"], "paragraph_index": block["paragraph_index"], "text": text})
        elif block["kind"] == "table":
            for row in block["rows"]:
                for cell in row["cells"]:
                    text = cell["text"]
                    for term in KEYWORD_TERMS:
                        if term in text.casefold():
                            index[term].append({"kind": "table_cell", "block_index": block["block_index"], "table_index": block["table_index"], "row_index": row["row_index"], "cell_index": cell["cell_index"], "text": text})
    return index


def extract_docx_media(path: Path, output_dir: Path) -> list[dict]:
    """Extract embedded Word media unchanged and record hashes; no image interpretation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("word/media/") or name.endswith("/"):
                continue
            data = archive.read(name)
            target = output_dir / Path(name).name
            target.write_bytes(data)
            records.append({"source_member": name, "output_path": str(target), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "review_status": "not_visually_reviewed"})
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-git-blob-sha1", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--media-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.docx.stat().st_size != args.expected_size:
        raise ValueError(f"P9M3_PROCEDURE_SIZE_MISMATCH:{args.docx.stat().st_size}")
    actual_git_sha1 = git_blob_sha1(args.docx)
    if actual_git_sha1 != args.expected_git_blob_sha1:
        raise ValueError(f"P9M3_PROCEDURE_GIT_BLOB_SHA1_MISMATCH:{actual_git_sha1}")
    blocks = extract_docx(args.docx)
    keyword_index = build_keyword_index(blocks)
    media = extract_docx_media(args.docx, args.media_dir) if args.media_dir else []
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
            "keyword_index_status": "source_locators_only_not_claim_approval",
            "keyword_hit_count": sum(len(matches) for matches in keyword_index.values()),
            "embedded_media_count": len(media),
            "embedded_media_status": "extracted_unchanged_not_visually_reviewed",
        },
        "keyword_index": keyword_index,
        "embedded_media": media,
        "blocks": blocks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paragraph_count": report["extraction"]["paragraph_count"],
                      "table_count": report["extraction"]["table_count"],
                      "keyword_hit_count": report["extraction"]["keyword_hit_count"],
                      "embedded_media_count": report["extraction"]["embedded_media_count"],
                      "source_sha256": report["source"]["sha256"],
                      "human_review_status": report["extraction"]["human_review_status"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
