"""E2E tests for cli-anything-drawio.

These tests run the REAL draw.io desktop app. It is a hard dependency: when
it is missing the tests fail — they are never skipped.
"""

import base64
import json
import os
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from cli_anything.drawio.core import connectors as conn
from cli_anything.drawio.core import export as exp
from cli_anything.drawio.core import layers as lay
from cli_anything.drawio.core import pages as pg
from cli_anything.drawio.core import project as proj
from cli_anything.drawio.core import session as sess_mod
from cli_anything.drawio.core import shapes as shp
from cli_anything.drawio.core.session import Session
from cli_anything.drawio.utils import drawio_backend as backend
from cli_anything.drawio.utils import drawio_xml as dx


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Undo history goes to a temp dir (inherited by subprocesses too)."""
    monkeypatch.setenv(sess_mod.ENV_STATE_DIR, str(tmp_path / "state"))


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


def build_flow():
    d = proj.create_project(name="Flow")
    shp.add_shape(d, 0, "rounded", label="Start", x=40, y=40,
                  set_props={"fillColor": "#d5e8d4", "strokeColor": "#82b366"})
    shp.add_shape(d, 0, "diamond", label="Approved?", x=60, y=160)
    shp.add_shape(d, 0, "rectangle", label="End", x=40, y=300)
    conn.add_connector(d, 0, "s1", "s2")
    conn.add_connector(d, 0, "s2", "s3", label="yes")
    return d


def png_size(path):
    with open(path, "rb") as f:
        data = f.read(26)
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG file"
    return struct.unpack(">II", data[16:24])


def read(path):
    with open(path, "rb") as f:
        return f.read()


def show(kind, result):
    print(f"\n  {kind}: {result['output']} ({result['file_size']:,} bytes)")


# ── Intermediate files ───────────────────────────────────────────────


class TestIntermediateFiles:
    def test_saved_file_has_native_structure(self, tmp_dir):
        path = os.path.join(tmp_dir, "flow.drawio")
        proj.save_project(build_flow(), path)
        mxfile = ET.parse(path).getroot()
        assert mxfile.tag == "mxfile"
        diagram = mxfile.find("diagram")
        assert diagram.get("name") == "Flow"
        root_el = diagram.find("mxGraphModel").find("root")
        cells = root_el.findall("mxCell")
        assert cells[0].attrib == {"id": "0"}
        assert cells[1].attrib == {"id": "1", "parent": "0"}
        vertex = root_el.find("mxCell[@id='s1']")
        assert vertex.get("vertex") == "1"
        assert vertex.find("mxGeometry").attrib == {
            "x": "40", "y": "40", "width": "120", "height": "60", "as": "geometry"}
        edge = root_el.find("mxCell[@id='e2']")
        assert (edge.get("edge"), edge.get("source"), edge.get("target")) == \
            ("1", "s2", "s3")
        assert edge.find("mxGeometry").get("relative") == "1"

    def test_reload_gives_identical_model(self, tmp_dir):
        original = build_flow()
        path = os.path.join(tmp_dir, "flow.drawio")
        proj.save_project(original, path)
        loaded = proj.open_project(path)
        assert shp.list_shapes(loaded, 0) == shp.list_shapes(original, 0)
        assert conn.list_connectors(loaded, 0) == conn.list_connectors(original, 0)

    def test_compressed_file_holds_deflated_model(self, tmp_dir):
        path = os.path.join(tmp_dir, "flow.drawio")
        proj.save_project(build_flow(), path, compressed=True)
        diagram = ET.parse(path).getroot().find("diagram")
        assert len(diagram) == 0
        model = dx.parse_xml(dx.decompress(diagram.text))
        assert model.tag == "mxGraphModel" and len(model.find("root")) == 7


# ── True backend ─────────────────────────────────────────────────────


class TestBackend:
    def test_real_app_is_found_and_reports_version(self):
        info = backend.backend_info()
        assert info["found"] is True, info
        assert os.path.isfile(info["executable"])
        assert re.match(r"^\d+\.\d+\.\d+", info["version"]), info
        print(f"\n  draw.io {info['version']} at {info['executable']}")

    def test_app_exits_zero_on_failure_so_output_is_checked(self, tmp_dir):
        """The fact the backend is built around: errors do not set the exit code."""
        missing = os.path.join(tmp_dir, "missing.drawio")
        result = backend.run_drawio(
            ["--export", "--format", "png", "--output",
             os.path.join(tmp_dir, "x.png"), missing])
        assert result["returncode"] == 0
        assert backend.error_lines(result["stderr"]) == [
            "Error: input file/directory not found"]
        assert not os.path.exists(os.path.join(tmp_dir, "x.png"))

    def test_garbage_input_is_reported_as_failure(self, tmp_dir):
        garbage = os.path.join(tmp_dir, "garbage.drawio")
        with open(garbage, "w", encoding="utf-8") as f:
            f.write("this is not a diagram at all")
        out = os.path.join(tmp_dir, "garbage.png")
        with pytest.raises(RuntimeError, match="draw.io export failed"):
            backend.export_file(garbage, out, "png", timeout=60)
        assert not os.path.exists(out)


class TestRenderFormats:
    def test_png(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.png"))
        assert result["format"] == "png" and result["method"] == "draw.io-cli"
        assert result["verified"] is True and result["file_size"] > 1000
        assert png_size(result["output"]) == (
            result["image_width"], result["image_height"])
        assert result["image_width"] > 100 and result["image_height"] > 300
        show("PNG", result)

    def test_jpg(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.jpg"),
                            quality=70)
        assert read(result["output"])[:3] == b"\xff\xd8\xff"
        show("JPG", result)

    def test_svg_contains_the_labels(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.svg"))
        data = read(result["output"])
        ET.fromstring(data)  # well-formed XML
        assert b"<svg" in data
        for label in (b"Start", b"Approved?", b"End", b"yes"):
            assert label in data
        show("SVG", result)

    def test_pdf(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.pdf"))
        data = read(result["output"])
        assert data[:5] == b"%PDF-" and b"%%EOF" in data[-1024:]
        assert result["pdf_pages"] == 1
        show("PDF", result)

    def test_xml_export_reloads_with_the_same_cells(self, tmp_dir):
        """XML written by the real app must be readable by the harness."""
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.xml"))
        text = read(result["output"]).decode("utf-8")
        assert text.startswith("<mxfile") and 'host="Electron"' in text
        reloaded = proj.open_project(result["output"])
        assert [s["id"] for s in shp.list_shapes(reloaded, 0)] == ["s1", "s2", "s3"]
        assert shp.get_shape(reloaded, 0, "s2")["label"] == "Approved?"
        assert [c["id"] for c in conn.list_connectors(reloaded, 0)] == ["e1", "e2"]
        show("XML", result)

    def test_vsdx_is_a_visio_package(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "flow.vsdx"))
        with zipfile.ZipFile(result["output"]) as archive:
            names = archive.namelist()
        assert "[Content_Types].xml" in names
        assert any(name.startswith("visio/pages/") for name in names)
        show("VSDX", result)


class TestRenderOptions:
    def test_scale_doubles_pixel_size(self, tmp_dir):
        """The app keeps a few unscaled edge pixels, so compare the ratio."""
        base = exp.render(build_flow(), os.path.join(tmp_dir, "x1.png"))
        doubled = exp.render(build_flow(), os.path.join(tmp_dir, "x2.png"), scale=2)
        assert 1.9 < doubled["image_width"] / base["image_width"] < 2.1
        assert 1.9 < doubled["image_height"] / base["image_height"] < 2.1

    def test_width_fits_the_image(self, tmp_dir):
        result = exp.render(build_flow(), os.path.join(tmp_dir, "w.png"), width=400)
        assert abs(result["image_width"] - 400) <= 1

    def test_transparent_background_has_alpha_channel(self, tmp_dir):
        opaque = exp.render(build_flow(), os.path.join(tmp_dir, "o.png"))
        clear = exp.render(build_flow(), os.path.join(tmp_dir, "t.png"),
                           transparent=True)
        assert clear["has_alpha"] is True
        assert read(opaque["output"]) != read(clear["output"])

    def test_embed_diagram_makes_png_editable(self, tmp_dir):
        plain = exp.render(build_flow(), os.path.join(tmp_dir, "p.png"))
        embedded = exp.render(build_flow(), os.path.join(tmp_dir, "e.png"),
                              embed_diagram=True)
        assert plain["embedded_diagram"] is False and plain["png_complete"] is True
        assert plain["warnings"] == []
        assert embedded["embedded_diagram"] is True
        assert embedded["file_size"] > plain["file_size"]
        # A PNG the app left without IEND must be reported, never hidden.
        assert (exp.PNG_NO_IEND_WARNING in embedded["warnings"]) == \
            (not embedded["png_complete"])

    def test_embedded_payload_uses_the_compression_scheme_we_implement(self, tmp_dir):
        """Real deflated data from the app decodes with drawio_xml.decompress."""
        result = exp.render(build_flow(), os.path.join(tmp_dir, "e.png"),
                            embed_diagram=True)
        data = read(result["output"])
        start = data.index(b"zTXtmxGraphModel\x00") - 4
        length = struct.unpack(">I", data[start:start + 4])[0]
        body = data[start + 8:start + 8 + length]
        deflated = body.split(b"\x00", 1)[1][1:]   # skip compression-method byte
        xml = dx.decompress(base64.b64encode(deflated).decode("ascii"))
        model = dx.parse_xml(xml)
        assert model.tag in ("mxGraphModel", "mxfile")
        assert "Approved?" in xml and 'id="s1"' in xml

    def _two_pages(self):
        d = proj.create_project(name="Small")
        shp.add_shape(d, 0, label="Small", x=0, y=0, width=100, height=50)
        pg.add_page(d, name="Wide")
        shp.add_shape(d, 1, label="Wide", x=0, y=0, width=600, height=50)
        return d

    def test_page_selection(self, tmp_dir):
        d = self._two_pages()
        first = exp.render(d, os.path.join(tmp_dir, "p0.png"))
        second = exp.render(d, os.path.join(tmp_dir, "p1.png"), page_index=1)
        assert first["image_width"] < 150 and second["image_width"] > 550

    def test_pdf_page_selectors(self, tmp_dir):
        d = self._two_pages()
        pg.add_page(d, name="Third")
        shp.add_shape(d, 2, label="Third")
        every = exp.render(d, os.path.join(tmp_dir, "all.pdf"), all_pages=True)
        some = exp.render(d, os.path.join(tmp_dir, "range.pdf"), page_range=(0, 1))
        one = exp.render(d, os.path.join(tmp_dir, "one.pdf"), page_index=2)
        assert (every["pdf_pages"], some["pdf_pages"], one["pdf_pages"]) == (3, 2, 1)
        show("PDF (3 pages)", every)

    def _layered(self):
        d = proj.create_project()
        shp.add_shape(d, 0, label="Base", x=0, y=0, width=100, height=50)
        lay.add_layer(d, 0, "Overlay")
        shp.add_shape(d, 0, label="Far", x=700, y=0, width=100, height=50,
                      layer="Overlay")
        return d

    def test_layer_filter(self, tmp_dir):
        d = self._layered()
        full = exp.render(d, os.path.join(tmp_dir, "full.png"))
        base_only = exp.render(d, os.path.join(tmp_dir, "base.png"), layers=[0])
        assert full["image_width"] > 750 and base_only["image_width"] < 150

    def test_hidden_layer_is_not_rendered(self, tmp_dir):
        d = self._layered()
        lay.set_visible(d, 0, "Overlay", False)
        result = exp.render(d, os.path.join(tmp_dir, "hidden.png"))
        assert result["image_width"] < 150

    def test_overwrite_guard_and_replacement(self, tmp_dir):
        target = os.path.join(tmp_dir, "o.png")
        small = proj.create_project()
        shp.add_shape(small, 0, label="A", width=50, height=50)
        first = exp.render(small, target)
        with pytest.raises(FileExistsError):
            exp.render(build_flow(), target)
        assert png_size(target)[0] == first["image_width"]
        second = exp.render(build_flow(), target, overwrite=True)
        assert png_size(target)[0] == second["image_width"] != first["image_width"]

    def test_korean_labels_reach_the_output(self, tmp_dir):
        d = proj.create_project(name="결재")
        shp.add_shape(d, 0, "rounded", label="기안 작성")
        shp.add_shape(d, 0, "diamond", label="승인?")
        conn.add_connector(d, 0, "s1", "s2", label="상신")
        result = exp.render(d, os.path.join(tmp_dir, "ko.svg"))
        text = read(result["output"]).decode("utf-8")
        for label in ("기안 작성", "승인?", "상신"):
            assert label in text
        show("SVG (Korean)", result)

    def test_shape_styles_are_applied_by_the_renderer(self, tmp_dir):
        d = proj.create_project()
        shp.add_shape(d, 0, label="Colored",
                      set_props={"fillColor": "#ff00aa", "strokeColor": "#00aa55"})
        text = read(exp.render(d, os.path.join(tmp_dir, "c.svg"))["output"]).decode("utf-8")
        assert "#ff00aa" in text and "#00aa55" in text


class TestRoundTrip:
    def test_file_survives_the_real_app(self, tmp_dir):
        """CLI-made file -> draw.io re-saves it -> CLI reads it back unchanged."""
        d = build_flow()
        shp.add_shape(d, 0, "container", label="Zone", x=300, y=40, cell_id="zone")
        shp.add_shape(d, 0, label="Inner", parent="zone", x=20, y=50, cell_id="inner")
        shp.set_data(d, 0, "s1", {"owner": "kim"})
        conn.add_connector(d, 0, "s3", "inner", label="handoff",
                           waypoints=[(250, 330), (250, 120)])
        out = exp.render(d, os.path.join(tmp_dir, "rt.xml"))
        back = proj.open_project(out["output"])
        assert proj.check_project(back)["ok"]
        for key in ("id", "label", "parent", "x", "y", "width", "height"):
            assert [s[key] for s in shp.list_shapes(back, 0)] == \
                [s[key] for s in shp.list_shapes(d, 0)], key
        assert shp.get_shape(back, 0, "s1")["data"] == {"owner": "kim"}
        edge = conn.get_connector(back, 0, "e3")
        assert (edge["source"], edge["target"], edge["label"]) == \
            ("s3", "inner", "handoff")
        assert edge["waypoints"] == [{"x": 250, "y": 330}, {"x": 250, "y": 120}]


class TestConvert:
    def test_drawio_file_to_png(self, tmp_dir):
        source = os.path.join(tmp_dir, "in.drawio")
        proj.save_project(build_flow(), source)
        result = exp.convert_file(source, os.path.join(tmp_dir, "out.png"))
        assert result["input"] == os.path.abspath(source)
        assert png_size(result["output"])[1] > 300

    def test_png_with_embedded_diagram_to_svg(self, tmp_dir):
        png = exp.render(build_flow(), os.path.join(tmp_dir, "e.png"),
                         embed_diagram=True)
        result = exp.convert_file(png["output"], os.path.join(tmp_dir, "back.svg"))
        assert b"Approved?" in read(result["output"])

    def test_missing_input(self, tmp_dir):
        with pytest.raises(FileNotFoundError):
            exp.convert_file(os.path.join(tmp_dir, "nope.drawio"),
                             os.path.join(tmp_dir, "x.png"))


# ── Realistic workflows ──────────────────────────────────────────────


class TestWorkflows:
    def test_flowchart_authoring(self, tmp_dir):
        d = proj.create_project(name="Approval")
        shp.add_shape(d, 0, "rounded", label="Request", x=200, y=20,
                      set_props={"fillColor": "#dae8fc"})
        shp.add_shape(d, 0, "diamond", label="Amount > 1000?", x=180, y=130,
                      width=160, height=100)
        shp.add_shape(d, 0, "rectangle", label="Manager", x=40, y=290)
        shp.add_shape(d, 0, "rectangle", label="Auto approve", x=360, y=290)
        shp.add_shape(d, 0, "ellipse", label="Done", x=200, y=420)
        conn.add_connector(d, 0, "s1", "s2")
        conn.add_connector(d, 0, "s2", "s3", label="yes")
        conn.add_connector(d, 0, "s2", "s4", label="no")
        conn.add_connector(d, 0, "s3", "s5")
        conn.add_connector(d, 0, "s4", "s5", style="dashed", end_arrow="classic")
        shp.set_label(d, 0, "s3", "Manager approval")
        shp.move_shape(d, 0, "s5", dy=30)
        info = proj.project_info(d)
        assert (info["shape_count"], info["connector_count"]) == (5, 5)
        png = exp.render(d, os.path.join(tmp_dir, "approval.png"), scale=2)
        svg = exp.render(d, os.path.join(tmp_dir, "approval.svg"))
        assert png["image_width"] > 800 and png["image_height"] > 900
        text = read(svg["output"]).decode("utf-8")
        assert "Manager approval" in text and "Amount &gt; 1000?" in text
        show("PNG", png)

    def test_multi_page_architecture_document(self, tmp_dir):
        d = proj.create_project(name="Context", page_format="a4", landscape=True)
        shp.add_shape(d, 0, "actor", label="User", x=40, y=80)
        shp.add_shape(d, 0, "container", label="System", x=200, y=40,
                      width=320, height=240, cell_id="system")
        shp.add_shape(d, 0, label="API", parent="system", x=20, y=50, cell_id="api")
        shp.add_shape(d, 0, "cylinder", label="DB", parent="system", x=220, y=120,
                      cell_id="db")
        conn.add_connector(d, 0, "s1", "api")
        conn.add_connector(d, 0, "api", "db", style="straight")
        pg.add_page(d, name="Deployment")
        shp.add_shape(d, 1, "cube", label="Server")
        pg.duplicate_page(d, "Deployment", name="Deployment (DR)")
        pg.move_page(d, "Deployment (DR)", 1)
        assert [p["name"] for p in pg.list_pages(d)] == [
            "Context", "Deployment (DR)", "Deployment"]
        every = exp.render(d, os.path.join(tmp_dir, "arch.pdf"), all_pages=True)
        first_two = exp.render(d, os.path.join(tmp_dir, "arch-2.pdf"),
                               page_range=(0, 1))
        assert every["pdf_pages"] == 3 and first_two["pdf_pages"] == 2
        assert every["file_size"] > first_two["file_size"]
        show("PDF", every)

    def test_layered_diagram(self, tmp_dir):
        d = proj.create_project(name="Network")
        shp.add_shape(d, 0, "cloud", label="Internet", x=0, y=0)
        shp.add_shape(d, 0, label="Router", x=200, y=10)
        conn.add_connector(d, 0, "s1", "s2")
        lay.add_layer(d, 0, "Notes")
        shp.add_shape(d, 0, "note", label="Replace in Q3", x=600, y=0, layer="Notes")
        shp.add_shape(d, 0, "callout", label="Check ACL", x=40, y=300)
        lay.assign_cells(d, 0, "Notes", ["s4"])
        assert lay.list_layers(d, 0)[1]["cells"] == 2
        full = exp.render(d, os.path.join(tmp_dir, "net-full.png"))
        base = exp.render(d, os.path.join(tmp_dir, "net-base.png"), layers=[0])
        assert full["image_width"] > 650 and full["image_height"] > 350
        assert base["image_width"] < 350 and base["image_height"] < 120
        lay.set_visible(d, 0, "Notes", False)
        hidden = exp.render(d, os.path.join(tmp_dir, "net-hidden.png"))
        assert (hidden["image_width"], hidden["image_height"]) == \
            (base["image_width"], base["image_height"])

    def test_iterative_refinement_with_heavy_undo_redo(self, tmp_dir):
        session = Session()
        path = os.path.join(tmp_dir, "iter.drawio")
        session.set_project(proj.create_project(), path)
        for i in range(10):
            with session.transaction(f"add {i}"):
                shp.add_shape(session.root, 0, label=f"Step {i}", x=40, y=40 + i * 90)
        for i in range(9):
            with session.transaction(f"connect {i}"):
                conn.add_connector(session.root, 0, f"s{i + 1}", f"s{i + 2}")
        for i in range(5):
            with session.transaction(f"restyle {i}"):
                shp.set_style(session.root, 0, f"s{i + 1}", {"fillColor": "#fff2cc"})
        with session.transaction("remove s10"):
            shp.remove_shape(session.root, 0, "s10")
        for label in ("re-add", "relabel", "move", "resize", "connect again", "label"):
            with session.transaction(label):
                if label == "re-add":
                    shp.add_shape(session.root, 0, label="Step 9b", cell_id="s10")
                elif label == "relabel":
                    shp.set_label(session.root, 0, "s10", "Final step")
                elif label == "move":
                    shp.move_shape(session.root, 0, "s10", x=300, y=760)
                elif label == "resize":
                    shp.resize_shape(session.root, 0, "s10", width=200)
                elif label == "connect again":
                    conn.add_connector(session.root, 0, "s9", "s10", cell_id="last")
                else:
                    conn.set_label(session.root, 0, "last", "finally")
        final_xml = dx.serialize(session.root, pretty=False)
        assert session.history()["undo_levels"] == 31
        for _ in range(31):
            session.undo()
        assert proj.project_info(session.root)["shape_count"] == 0
        for _ in range(31):
            session.redo()
        assert dx.serialize(session.root, pretty=False) == final_xml
        session.save_session()
        reopened = proj.open_project(path)
        assert proj.project_info(reopened)["shape_count"] == 10
        result = exp.render(reopened, os.path.join(tmp_dir, "iter.png"))
        assert result["image_height"] > 700   # shapes span y=40..820
        show("PNG", result)

    def test_complex_project_save_load_round_trip(self, tmp_dir):
        d = build_flow()
        lay.add_layer(d, 0, "Overlay")
        shp.add_shape(d, 0, "container", label="Zone", x=300, y=40, cell_id="zone",
                      layer="Overlay")
        shp.add_shape(d, 0, label="Inner <b>raw</b>", parent="zone", x=20, y=50,
                      cell_id="inner", html_label=True)
        shp.set_data(d, 0, "inner", {"tier": "gold"})
        conn.add_connector(d, 0, "s1", "inner", waypoints=[(250, 60)])
        pg.add_page(d, name="두 번째")
        shp.add_shape(d, 1, "cloud", label="클라우드")
        path = os.path.join(tmp_dir, "complex.drawio")
        proj.save_project(d, path, compressed=True)
        loaded = proj.open_project(path)
        for page in (0, 1):
            assert shp.list_shapes(loaded, page) == shp.list_shapes(d, page)
            assert conn.list_connectors(loaded, page) == conn.list_connectors(d, page)
        assert lay.list_layers(loaded, 0) == lay.list_layers(d, 0)
        assert shp.get_shape(loaded, 0, "inner")["data"] == {"tier": "gold"}
        assert pg.list_pages(loaded)[1]["name"] == "두 번째"
        via_app = exp.render(loaded, os.path.join(tmp_dir, "complex.xml"))
        again = proj.open_project(via_app["output"])
        assert [s["id"] for s in shp.list_shapes(again, 0)] == \
            [s["id"] for s in shp.list_shapes(d, 0)]
        assert shp.get_shape(again, 1, "s1")["label"] == "클라우드"
        pdf = exp.render(loaded, os.path.join(tmp_dir, "complex.pdf"), all_pages=True)
        assert pdf["pdf_pages"] == 2


# ── CLI subprocess tests (the installed command) ─────────────────────


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-drawio")

    def _run(self, args, check=True, env=None):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True, encoding="utf-8",
            check=check, env=env,
        )

    def _json(self, args):
        return json.loads(self._run(["--json"] + args).stdout)

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "Create, edit and export draw.io diagrams" in result.stdout

    def test_version(self):
        assert "cli-anything-drawio, version 1.0.0" in self._run(["--version"]).stdout

    def test_backend_info_json(self):
        info = self._json(["backend", "info"])
        assert info["found"] is True and re.match(r"^\d+\.", info["version"])

    def test_project_new_json(self, tmp_dir):
        out = os.path.join(tmp_dir, "test.drawio")
        data = self._json(["project", "new", "-o", out, "--name", "Main"])
        assert data["saved"] is True and data["page_count"] == 1
        assert data["pages"][0]["name"] == "Main" and os.path.isfile(out)

    def test_full_flowchart_to_png_workflow(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "flow.drawio")
        png_path = os.path.join(tmp_dir, "flow.png")
        self._run(["project", "new", "-o", proj_path])
        start = self._json(["--project", proj_path, "shape", "add", "-t", "rounded",
                            "-l", "Start", "--x", "40", "--y", "40",
                            "--fill", "#d5e8d4"])
        check = self._json(["--project", proj_path, "shape", "add", "-t", "diamond",
                            "-l", "Valid?", "--x", "60", "--y", "160"])
        end = self._json(["--project", proj_path, "shape", "add", "-l", "End",
                          "--x", "40", "--y", "300"])
        self._run(["--project", proj_path, "connect", "add", start["id"], check["id"]])
        edge = self._json(["--project", proj_path, "connect", "add", check["id"],
                           end["id"], "-l", "yes", "--end-arrow", "block"])
        assert edge["end_arrow"] == "block"
        listing = self._json(["--project", proj_path, "shape", "list"])
        assert [s["label"] for s in listing["shapes"]] == ["Start", "Valid?", "End"]
        result = self._json(["--project", proj_path, "export", "render", png_path,
                             "--scale", "2"])
        assert result["verified"] is True and result["method"] == "draw.io-cli"
        assert os.path.exists(png_path)
        assert png_size(png_path) == (result["image_width"], result["image_height"])
        assert result["image_height"] > 600
        print(f"\n  PNG: {png_path} ({os.path.getsize(png_path):,} bytes)")

    def test_multi_page_pdf_workflow(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "doc.drawio")
        pdf_path = os.path.join(tmp_dir, "doc.pdf")
        self._run(["project", "new", "-o", proj_path, "--name", "Overview"])
        self._run(["--project", proj_path, "shape", "add", "-t", "cloud", "-l", "Web"])
        self._run(["--project", proj_path, "page", "add", "--name", "Detail"])
        self._run(["--project", proj_path, "shape", "add", "-t", "cylinder",
                   "-l", "DB", "--page", "Detail"])
        self._run(["--project", proj_path, "export", "render", pdf_path,
                   "--all-pages"])
        assert os.path.exists(pdf_path)
        with open(pdf_path, "rb") as f:
            assert f.read(5) == b"%PDF-"
        assert exp.verify_output(pdf_path, "pdf")["pdf_pages"] == 2
        print(f"\n  PDF: {pdf_path} ({os.path.getsize(pdf_path):,} bytes)")

    def test_error_exit_code_and_json(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "e.drawio")
        self._run(["project", "new", "-o", proj_path])
        result = self._run(["--json", "--project", proj_path, "connect", "add",
                            "a", "b"], check=False)
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["type"] == "ValueError" and "Shape 'a' not found" in payload["error"]
        png_path = os.path.join(tmp_dir, "e.png")
        result = self._run(["--project", proj_path, "export", "render", png_path,
                            "--crop"], check=False)
        assert result.returncode == 1 and "only applies to pdf" in result.stderr
        assert not os.path.exists(png_path)

    def test_dry_run_and_undo_across_processes(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "u.drawio")
        self._run(["project", "new", "-o", proj_path])
        self._run(["--project", proj_path, "shape", "add", "-l", "Keep"])
        dry = self._json(["--project", proj_path, "--dry-run", "shape", "add",
                          "-l", "Ghost"])
        assert dry["saved"] is False
        assert self._json(["--project", proj_path, "shape", "list"])["count"] == 1
        self._run(["--project", proj_path, "shape", "add", "-l", "Mistake"])
        undone = self._json(["--project", proj_path, "session", "undo"])
        assert undone["undone"] == "shape add rectangle"
        labels = [s["label"] for s in
                  self._json(["--project", proj_path, "shape", "list"])["shapes"]]
        assert labels == ["Keep"]

    def test_non_ascii_output_on_legacy_console_encoding(self, tmp_dir):
        """Piped human output must not crash where the locale is cp949/cp936."""
        proj_path = os.path.join(tmp_dir, "ko.drawio")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        self._run(["project", "new", "-o", proj_path, "--name", "결재선"], env=env)
        self._run(["--project", proj_path, "shape", "add", "-l", "기안 → 승인 ✓"],
                  env=env)
        result = self._run(["--project", proj_path, "shape", "list"], env=env)
        assert result.returncode == 0 and "기안 → 승인 ✓" in result.stdout
        svg_path = os.path.join(tmp_dir, "ko.svg")
        self._run(["--project", proj_path, "export", "render", svg_path], env=env)
        with open(svg_path, encoding="utf-8") as f:
            assert "기안 → 승인 ✓" in f.read()

    def test_repl_over_a_pipe(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "repl.drawio")
        png_path = os.path.join(tmp_dir, "repl.png")
        script = "\n".join([
            "project new", "shape add -l One", "shape add -l Two",
            "connect add s1 s2", f'save -o "{proj_path}"',
            f'export render "{png_path}"', "quit"]) + "\n"
        result = subprocess.run(self.CLI_BASE, input=script, capture_output=True,
                                text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        assert proj.project_info(proj.open_project(proj_path))["connector_count"] == 1
        assert png_size(png_path)[1] > 100
