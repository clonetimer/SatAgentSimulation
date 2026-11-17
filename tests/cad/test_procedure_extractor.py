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
