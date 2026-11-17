#!/usr/bin/env python3
"""用途：提取固定来源的 BIRDS-4 飞行单元 VT/TVT 报告正文和表格定位。
参数：--receipts、--output-dir。
输出：逐文档原始文本提取与索引；不把振动/热真空测试记录解释为批准 BOM 或 as-built 装配图。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_birds4_procedure_evidence import build_keyword_index, extract_docx  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipts = json.loads(args.receipts.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for record in receipts["receipts"]:
        source_path = Path(record["local_path"])
        blocks = extract_docx(source_path)
        keyword_index = build_keyword_index(blocks)
        extraction = {
            "schema_version": "birds4-flight-unit-report-extract.v1",
            "source": {
                "repository": record["source_repository"],
                "path": record["path"],
                "source_commit": record["source_commit"],
                "source_unit": record["source_unit"],
                "document_role": record["document_role"],
                "size_bytes": record["size_bytes"],
                "git_blob_sha1": record["git_blob_sha1"],
                "sha256": record["sha256"],
                "verification_status": record["verification_status"],
                "configuration_authority": record["configuration_authority"],
            },
            "extraction": {
                "paragraph_count": sum(1 for row in blocks if row["kind"] == "paragraph"),
                "table_count": sum(1 for row in blocks if row["kind"] == "table"),
                "keyword_hit_count": sum(len(rows) for rows in keyword_index.values()),
                "human_review_status": "not_reviewed",
                "interpretation_status": "raw_text_evidence_only",
                "pagination_status": "not_available_from_docx_body_extraction",
            },
            "keyword_index": keyword_index,
            "blocks": blocks,
        }
        output_name = source_path.stem + ".evidence.json"
        output_path = args.output_dir / output_name
        output_path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        documents.append({
            "source_unit": record["source_unit"],
            "document_role": record["document_role"],
            "path": record["path"],
            "source_commit": record["source_commit"],
            "git_blob_sha1": record["git_blob_sha1"],
            "sha256": record["sha256"],
            "verification_status": "source_bytes_verified",
            "human_review_status": "not_reviewed",
            "configuration_authority": "test_report_not_approved_as_built_BOM",
            "paragraph_count": extraction["extraction"]["paragraph_count"],
            "table_count": extraction["extraction"]["table_count"],
            "keyword_hit_count": extraction["extraction"]["keyword_hit_count"],
            "extraction_path": str(output_path),
            "keyword_index": keyword_index,
        })
    index = {
        "schema_version": "birds4-flight-unit-report-index.v1",
        "source_commit": receipts["source_commit"],
        "document_count": len(documents),
        "human_review_status": "not_reviewed",
        "configuration_authority": "test_reports_are_not_approved_as_built_BOM",
        "documents": documents,
    }
    index_path = args.output_dir / "flight-unit-report-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"document_count": len(documents),
                      "human_review_status": "not_reviewed",
                      "index_path": str(index_path)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
