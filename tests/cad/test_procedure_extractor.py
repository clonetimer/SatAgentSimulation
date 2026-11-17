from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cad/extract_birds4_procedure_evidence.py"
SPEC = importlib.util.spec_from_file_location("p9m3_procedure_extractor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_docx(path: Path):
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Assembly sequence step 1</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Battery</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Install on frame</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>Torque specification</w:t></w:r></w:p>
      </w:body>
    </w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml.encode("utf-8"))


def test_extracts_docx_body_paragraphs_and_tables_in_source_order(tmp_path):
    path = tmp_path / "procedure.docx"
    _write_docx(path)
    blocks = MODULE.extract_docx(path)
    assert [row["kind"] for row in blocks] == ["paragraph", "table", "paragraph"]
    assert blocks[0]["text"] == "Assembly sequence step 1"
    assert blocks[1]["rows"][0]["cells"][0]["text"] == "Battery"
    assert blocks[1]["rows"][0]["cells"][1]["text"] == "Install on frame"
    assert blocks[2]["text"] == "Torque specification"


def test_git_blob_sha1_uses_git_object_header(tmp_path):
    path = tmp_path / "procedure.docx"
    _write_docx(path)
    import hashlib
    data = path.read_bytes()
    expected = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()
    assert MODULE.git_blob_sha1(path) == expected


def test_keyword_index_returns_stable_paragraph_and_table_cell_locators():
    blocks = [
        {"kind": "paragraph", "block_index": 1, "paragraph_index": 20, "text": "Structural Testing Model assembly procedure"},
        {"kind": "table", "block_index": 2, "table_index": 9, "rows": [
            {"row_index": 7, "cells": [
                {"cell_index": 1, "text": "Perovskite solar cells"},
                {"cell_index": 2, "text": "-Y panel"},
            ]}
        ]},
    ]
    index = MODULE.build_keyword_index(blocks)
    assert index["structural testing model"][0]["paragraph_index"] == 20
    assert index["perovskite solar cells"][0]["table_index"] == 9
    assert index["perovskite solar cells"][0]["row_index"] == 7
    assert index["-y panel"][0]["cell_index"] == 2


def test_embedded_docx_media_is_extracted_unchanged_with_hash_receipt(tmp_path):
    path = tmp_path / "procedure.docx"
    media = b"\x89PNG\r\n\x1a\nsynthetic-figure-bytes"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", b"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body/></w:document>")
        archive.writestr("word/media/image1.png", media)
    records = MODULE.extract_docx_media(path, tmp_path / "media")
    assert len(records) == 1
    assert (tmp_path / "media" / "image1.png").read_bytes() == media
    assert records[0]["review_status"] == "not_visually_reviewed"
    assert records[0]["sha256"] == __import__("hashlib").sha256(media).hexdigest()


def test_embedded_figure_relationship_is_tied_to_source_paragraph_locator(tmp_path):
    path = tmp_path / "procedure.docx"
    document = b"""<?xml version="1.0"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p><w:r><w:t>Axis definition</w:t></w:r></w:p>
        <w:p><w:r><w:drawing><a:graphic><a:graphicData><a:blip r:embed="rId7"/></a:graphicData></a:graphic></w:drawing></w:r></w:p>
      </w:body>
    </w:document>"""
    relationships = b"""<?xml version="1.0"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image7.png"/>
    </Relationships>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/media/image7.png", b"figure")
    blocks = MODULE.extract_docx(path)
    assert blocks[1]["paragraph_index"] == 2
    assert blocks[1]["embedded_media_refs"] == [{"relationship_id": "rId7", "target": "media/image7.png"}]
