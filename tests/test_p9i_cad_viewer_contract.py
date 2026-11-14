from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "sat_sim" / "web"


def test_birds_cad_reference_viewer_is_registered_in_scene_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="birdsCadViewer"' in html
    assert 'id="birdsCadFile"' in html
    assert 'id="birdsCadTree"' in html
    assert 'id="birdsCadInspector"' in html
    assert '/assets/birds-cad-viewer.js' in html
    assert '"three":"/assets/vendor/globe/three.module.js"' in html


def test_birds_cad_viewer_supports_glb_hierarchy_selection_and_camera_controls():
    source = (WEB / "birds-cad-viewer.js").read_text(encoding="utf-8")
    for token in (
        "GLB 2.0", "bufferViews", "accessors", "scenes", "children",
        "pointerdown", "pointermove", "pointerup", "wheel",
        "data-cad-view", "AxesHelper", "ray.intersectObject",
    ):
        assert token in source
    assert "BIRDS-4 CAD 参考模型" in source
    assert "Basilisk 仿真卫星实装配置" in source


def test_birds_cad_viewer_does_not_depend_on_external_model_hosting():
    source = (WEB / "birds-cad-viewer.js").read_text(encoding="utf-8")
    assert "https://" not in source
    assert "fetch(" not in source


def test_birds_cad_viewer_supports_search_visibility_and_isolation():
    source = (WEB / "birds-cad-viewer.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for token in ("birdsCadSearch", "birdsCadTree", "data-cad-tree-action", "isDescendant", "is-selected", "birds-cad-visibility"):
        assert token in source or token in html
    assert "隔离选中" in html and "恢复全部" in html


def test_viewer_uses_official_gltf_loader_and_meshopt_decoder():
    source = (WEB / "birds-cad-viewer.js").read_text(encoding="utf-8")
    vendor = (ROOT / "scripts" / "vendor_scene_assets.py").read_text(encoding="utf-8")
    assert "GLTFLoader" in source and "parseAsync" in source
    assert "setMeshoptDecoder(MeshoptDecoder)" in source
    assert "loaders/GLTFLoader.js" in vendor
    assert "meshopt_decoder.module.js" in vendor
    assert "function parse(buf)" not in source
    assert "function build(doc,bin)" not in source


def test_viewer_disposes_textures_and_keeps_selected_ancestors_visible():
    source = (WEB / "birds-cad-viewer.js").read_text(encoding="utf-8")
    assert "textures.forEach" in source and "x.dispose()" in source
    assert "isDescendant(n,selectedNode)||isDescendant(selectedNode,n)" in source
    assert "async function load" in source
