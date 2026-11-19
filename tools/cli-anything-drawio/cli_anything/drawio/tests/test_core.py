"""Unit tests for cli-anything-drawio (synthetic data, draw.io not needed)."""

import base64
import json
import os
import struct
import zlib
from urllib.parse import unquote

import pytest
from click.testing import CliRunner

from cli_anything.drawio import drawio_cli
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
from cli_anything.drawio.utils import fileio


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep undo history out of the real home directory."""
    monkeypatch.setenv(sess_mod.ENV_STATE_DIR, str(tmp_path / "state"))
    drawio_cli.reset_session()
    yield
    drawio_cli.reset_session()


@pytest.fixture
def doc():
    return proj.create_project()


@pytest.fixture
def flow():
    d = proj.create_project(name="Flow")
    shp.add_shape(d, 0, "rounded", label="Start", x=40, y=40)
    shp.add_shape(d, 0, "diamond", label="OK?", x=40, y=160)
    shp.add_shape(d, 0, "rectangle", label="End", x=40, y=300)
    conn.add_connector(d, 0, "s1", "s2")
    conn.add_connector(d, 0, "s2", "s3", label="yes")
    return d


def shape_ids(d, page=0):
    return [s["id"] for s in shp.list_shapes(d, page)]


def connector_ids(d, page=0):
    return [c["id"] for c in conn.list_connectors(d, page)]


# ── utils/drawio_xml.py ──────────────────────────────────────────────


class TestStyle:
    def test_parse_and_format_round_trip(self):
        style = "ellipse;whiteSpace=wrap;html=1;"
        assert dx.parse_style(style) == [
            ("ellipse", None), ("whiteSpace", "wrap"), ("html", "1")]
        assert dx.format_style(dx.parse_style(style)) == style

    def test_parse_tolerates_spaces_and_missing_semicolon(self):
        assert dx.parse_style("a=1; b=2") == [("a", "1"), ("b", "2")]

    def test_empty_style(self):
        assert dx.parse_style("") == []
        assert dx.parse_style(None) == []
        assert dx.format_style([]) == ""

    def test_value_may_contain_equals(self):
        assert dx.parse_style("image=data:x=y;") == [("image", "data:x=y")]

    def test_style_get(self):
        style = "ellipse;html=1;"
        assert dx.style_get(style, "html") == "1"
        assert dx.style_get(style, "ellipse") is True
        assert dx.style_get(style, "missing", "dflt") == "dflt"

    def test_style_set_replaces_in_place_and_appends(self):
        style = "rounded=0;html=1;"
        assert dx.style_set(style, "rounded", "1") == "rounded=1;html=1;"
        assert dx.style_set(style, "fillColor", "#fff") == \
            "rounded=0;html=1;fillColor=#fff;"

    def test_style_set_none_makes_bare_token(self):
        assert dx.style_set("html=1;", "ellipse", None) == "html=1;ellipse;"

    def test_style_unset(self):
        assert dx.style_unset("a=1;b=2;c;", "b") == "a=1;c;"
        assert dx.style_unset("a=1;", "missing") == "a=1;"

    def test_apply_changes_unsets_then_sets(self):
        result = dx.apply_style_changes("a=1;b=2;", {"c": "3", "a": "9"}, ["b"])
        assert result == "a=9;c=3;"

    @pytest.mark.parametrize("key", ["", "a;b", "a=b", " a"])
    def test_invalid_style_key_rejected(self, key):
        with pytest.raises(ValueError):
            dx.style_set("", key, "1")

    def test_value_with_semicolon_rejected(self):
        with pytest.raises(ValueError):
            dx.style_set("", "fillColor", "#fff;evil=1")

    def test_parse_assignments(self):
        assert dx.parse_assignments(["a=1", "url=x=y"]) == {"a": "1", "url": "x=y"}

    @pytest.mark.parametrize("pair", ["novalue", "=x"])
    def test_parse_assignments_rejects_bad_pairs(self, pair):
        with pytest.raises(ValueError):
            dx.parse_assignments([pair])


class TestCompression:
    @pytest.mark.parametrize("text", [
        "<mxGraphModel><root/></mxGraphModel>",
        '<a v="한글 라벨 &amp; émoji ✓"/>',
        "a b+c/d?e=f&g~h!i*j'k(l)",
    ])
    def test_round_trip(self, text):
        assert dx.decompress(dx.compress(text)) == text

    def test_matches_draw_io_scheme(self):
        """base64 -> raw deflate -> URI-encoded text (Graph.compress)."""
        payload = dx.compress("<a b='c d'/>")
        inflated = zlib.decompress(base64.b64decode(payload), -15).decode("ascii")
        assert "%3C" in inflated and " " not in inflated
        assert unquote(inflated) == "<a b='c d'/>"

    def test_garbage_raises_format_error(self):
        with pytest.raises(dx.DrawioFormatError):
            dx.decompress("this is not compressed data")


class TestLabelsAndNumbers:
    def test_plain_to_label_escapes_and_breaks_lines(self):
        assert dx.plain_to_label("a < b & c\nnext") == "a &lt; b &amp; c<br>next"

    def test_label_to_plain_strips_markup(self):
        assert dx.label_to_plain("<b>Bold</b><br/>line &amp; more") == \
            "Bold\nline & more"

    def test_plain_round_trip(self):
        text = "x < y && z > 1"
        assert dx.label_to_plain(dx.plain_to_label(text)) == text

    @pytest.mark.parametrize("value,expected", [
        (120, "120"), (120.0, "120"), (10.5, "10.5"), (-3.0, "-3")])
    def test_fmt_num(self, value, expected):
        assert dx.fmt_num(value) == expected


class TestCells:
    XML = (
        '<root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<object label="Wrapped" id="w1" owner="kim">'
        '<mxCell style="rounded=1;" vertex="1" parent="1">'
        '<mxGeometry x="1" y="2" width="3" height="4" as="geometry"/>'
        '</mxCell></object>'
        '<mxCell id="p1" value="Plain" vertex="1" parent="1"/></root>'
    )

    def test_iter_cells_sees_through_object_wrapper(self):
        root_el = dx.parse_xml(self.XML)
        cells = {c.id: c for c in dx.iter_cells(root_el)}
        assert list(cells) == ["0", "1", "w1", "p1"]
        assert cells["w1"].wrapped and cells["w1"].label == "Wrapped"
        assert cells["w1"].is_vertex and cells["w1"].data == {"owner": "kim"}
        assert cells["w1"].geometry_dict() == {
            "x": 1, "y": 2, "width": 3, "height": 4}
        assert not cells["p1"].wrapped and cells["p1"].label == "Plain"

    def test_next_id_skips_used_ids(self):
        root_el = dx.parse_xml(
            '<root><mxCell id="s1"/><mxCell id="s2"/><mxCell id="s4"/></root>')
        assert dx.next_id(root_el, "s") == "s3"

    def test_validate_new_id(self):
        root_el = dx.parse_xml(self.XML)
        assert dx.validate_new_id(root_el, "fresh-1") == "fresh-1"
        with pytest.raises(ValueError, match="already in use"):
            dx.validate_new_id(root_el, "p1")
        with pytest.raises(ValueError, match="Invalid id"):
            dx.validate_new_id(root_el, "has space")

    def test_wrap_cell_keeps_position_label_and_id(self):
        root_el = dx.parse_xml(self.XML)
        wrapped = dx.wrap_cell(root_el, dx.find_cell(root_el, "p1"))
        assert wrapped.wrapped and wrapped.id == "p1" and wrapped.label == "Plain"
        assert [c.id for c in dx.iter_cells(root_el)] == ["0", "1", "w1", "p1"]
        assert "value" not in wrapped.cell.attrib

    def test_parse_rejects_entity_expansion(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
                '<!ENTITY b "&a;&a;&a;">]><mxfile>&b;</mxfile>')
        with pytest.raises(dx.DrawioFormatError, match="Unsafe"):
            dx.parse_xml(bomb)


# ── utils/fileio.py ──────────────────────────────────────────────────


class TestFileIO:
    def test_creates_file_and_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "dir" / "a.txt"
        fileio.locked_write_text(str(target), "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_rewrite_truncates_longer_content(self, tmp_path):
        target = tmp_path / "a.txt"
        fileio.locked_write_text(str(target), "a much longer first version")
        fileio.locked_write_text(str(target), "short")
        assert target.read_text(encoding="utf-8") == "short"

    def test_locked_save_json(self, tmp_path):
        target = tmp_path / "s.json"
        fileio.locked_save_json(str(target), {"k": ["한글", 1]}, ensure_ascii=False)
        assert json.loads(target.read_text(encoding="utf-8")) == {"k": ["한글", 1]}


# ── utils/drawio_backend.py (discovery only, nothing is executed) ────


class TestBackendDiscovery:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        fake = tmp_path / "my-drawio.exe"
        fake.write_bytes(b"")
        monkeypatch.setenv(backend.ENV_PATH, str(fake))
        assert backend.find_drawio() == str(fake)

    def test_env_override_pointing_nowhere_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv(backend.ENV_PATH, str(tmp_path / "missing.exe"))
        with pytest.raises(RuntimeError, match=backend.ENV_PATH):
            backend.find_drawio()

    def test_falls_back_to_default_install_location(self, tmp_path, monkeypatch):
        fake = tmp_path / "draw.io.exe"
        fake.write_bytes(b"")
        monkeypatch.delenv(backend.ENV_PATH, raising=False)
        monkeypatch.setattr(backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(backend, "default_locations", lambda: [str(fake)])
        assert backend.find_drawio() == str(fake)

    def test_missing_app_error_has_install_instructions(self, monkeypatch):
        monkeypatch.delenv(backend.ENV_PATH, raising=False)
        monkeypatch.setattr(backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(backend, "default_locations", lambda: [])
        with pytest.raises(RuntimeError) as err:
            backend.find_drawio()
        assert "not installed" in str(err.value)
        assert "winget install JGraph.Draw" in str(err.value)
        info = backend.backend_info()
        assert info["found"] is False and info["executable"] is None

    def test_default_locations_are_listed_for_this_platform(self):
        locations = backend.default_locations()
        assert locations and len(locations) == len(set(locations))

    def test_error_lines_ignore_chromium_noise(self):
        text = ("[1:2:ERROR:command_buffer_proxy_impl.cc(325)] GPU state invalid\n"
                "Error: input file/directory not found\n  Error: Export failed: x\n")
        assert backend.error_lines(text) == [
            "Error: input file/directory not found", "Error: Export failed: x"]


# ── core/project.py ──────────────────────────────────────────────────


class TestProject:
    def test_create_defaults(self, doc):
        info = proj.project_info(doc)
        assert info["page_count"] == 1 and info["host"] == "cli-anything-drawio"
        page = info["pages"][0]
        assert (page["name"], page["width"], page["height"]) == ("Page-1", 827, 1169)
        assert (page["layers"], page["shapes"], page["connectors"]) == (1, 0, 0)

    def test_presets_come_from_the_page_setup_dialog(self):
        presets = {p["name"]: (p["width"], p["height"]) for p in proj.list_presets()}
        assert presets["letter"] == (850, 1100)
        assert presets["a3"] == (1169, 1654)
        assert len(presets) == 17

    def test_landscape_swaps_size(self):
        assert proj.resolve_page_size("a4", landscape=True) == (1169, 827)

    def test_custom_size(self):
        assert proj.resolve_page_size(width=640, height=480) == (640, 480)

    def test_unknown_format_lists_choices(self):
        with pytest.raises(ValueError, match="Available: letter"):
            proj.resolve_page_size("a99")

    def test_half_custom_size_rejected(self):
        with pytest.raises(ValueError, match="both"):
            proj.resolve_page_size(width=640)

    def test_empty_page_name_rejected(self):
        with pytest.raises(ValueError):
            proj.create_project(name="  ")

    def test_save_open_round_trip(self, flow, tmp_path):
        path = str(tmp_path / "flow.drawio")
        proj.save_project(flow, path)
        loaded = proj.open_project(path)
        assert shape_ids(loaded) == ["s1", "s2", "s3"]
        assert connector_ids(loaded) == ["e1", "e2"]
        assert shp.get_shape(loaded, 0, "s1")["label"] == "Start"

    def test_saved_file_is_plain_uncompressed_xml(self, flow, tmp_path):
        path = tmp_path / "flow.drawio"
        proj.save_project(flow, str(path))
        text = path.read_text(encoding="utf-8")
        assert text.startswith("<mxfile ") and "<mxGraphModel " in text
        assert 'value="Start"' in text

    def test_compressed_save_and_reopen(self, flow, tmp_path):
        path = tmp_path / "flow.drawio"
        proj.save_project(flow, str(path), compressed=True)
        text = path.read_text(encoding="utf-8")
        assert "<mxGraphModel" not in text and "Start" not in text
        loaded = proj.open_project(str(path))
        assert shape_ids(loaded) == ["s1", "s2", "s3"]
        assert proj.project_info(loaded)["connector_count"] == 2

    def test_open_bare_graph_model(self):
        loaded = proj.load_project(
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '</root></mxGraphModel>')
        assert loaded.tag == "mxfile"
        assert proj.project_info(loaded)["page_count"] == 1

    @pytest.mark.parametrize("data,message", [
        ("this is not xml", "Not well-formed"),
        ("<svg></svg>", "root element is <svg>"),
        ("<mxfile></mxfile>", "no <diagram>"),
        ("<mxfile><diagram>!!notbase64!!</diagram></mxfile>", "neither XML nor compressed"),
    ])
    def test_open_rejects_invalid_content(self, data, message):
        with pytest.raises(dx.DrawioFormatError, match=message):
            proj.load_project(data)

    def test_open_missing_file_says_how_to_create_it(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="project new -o"):
            proj.open_project(str(tmp_path / "nope.drawio"))

    def test_to_xml_can_limit_to_one_page(self, flow):
        pg.add_page(flow, name="Second")
        assert proj.to_xml(flow).count("<diagram ") == 2
        only = proj.to_xml(flow, page=1)
        assert only.count("<diagram ") == 1 and 'name="Second"' in only

    def test_info_counts_elements(self, flow):
        info = proj.project_info(flow)
        assert (info["shape_count"], info["connector_count"]) == (3, 2)

    def test_check_passes_for_valid_project(self, flow):
        result = proj.check_project(flow)
        assert result == {"ok": True, "errors": 0, "warnings": 0, "issues": []}

    def test_check_reports_dangling_and_duplicate(self, flow):
        root_el = proj.page_root(flow, 0)
        dx.find_cell(root_el, "e1").cell.set("target", "ghost")
        dx.find_cell(root_el, "s3").cell.set("id", "s2")
        result = proj.check_project(flow)
        messages = " | ".join(i["message"] for i in result["issues"])
        assert result["ok"] is False and result["errors"] >= 2
        assert "'ghost' does not exist" in messages and "Duplicate cell id" in messages


# ── core/pages.py ────────────────────────────────────────────────────


class TestPages:
    def test_list(self, flow):
        pages = pg.list_pages(flow)
        assert [(p["index"], p["name"], p["shapes"]) for p in pages] == [(0, "Flow", 3)]

    def test_add_uses_next_name_and_inherits_size(self):
        d = proj.create_project(page_format="letter")
        page = pg.add_page(d)
        assert (page["index"], page["name"]) == (1, "Page-2")
        assert (page["width"], page["height"]) == (850, 1100)

    def test_add_with_format_and_landscape(self, doc):
        page = pg.add_page(doc, name="Wide", page_format="a3", landscape=True)
        assert (page["width"], page["height"]) == (1654, 1169)

    def test_add_at_position(self, doc):
        pg.add_page(doc, name="First", index=0)
        assert [p["name"] for p in pg.list_pages(doc)] == ["First", "Page-1"]

    def test_added_pages_get_unique_ids(self, doc):
        pg.add_page(doc)
        pg.add_page(doc)
        ids = [p["id"] for p in pg.list_pages(doc)]
        assert len(set(ids)) == 3

    def test_remove(self, doc):
        pg.add_page(doc, name="Extra")
        result = pg.remove_page(doc, "Extra")
        assert result["page_count"] == 1 and result["removed"]["name"] == "Extra"

    def test_cannot_remove_only_page(self, doc):
        with pytest.raises(ValueError, match="only page"):
            pg.remove_page(doc, 0)

    def test_rename(self, doc):
        assert pg.rename_page(doc, 0, "Overview") == {
            "index": 0, "old_name": "Page-1", "name": "Overview"}
        with pytest.raises(ValueError):
            pg.rename_page(doc, 0, "")

    def test_duplicate_is_independent(self, flow):
        copy = pg.duplicate_page(flow, 0)
        assert (copy["index"], copy["name"], copy["shapes"]) == (1, "Flow Copy", 3)
        shp.remove_shape(flow, 1, "s1")
        assert len(shape_ids(flow, 0)) == 3 and len(shape_ids(flow, 1)) == 2

    def test_move(self, doc):
        pg.add_page(doc, name="B")
        pg.add_page(doc, name="C")
        assert pg.move_page(doc, "C", 0) == {"name": "C", "from": 2, "to": 0}
        assert [p["name"] for p in pg.list_pages(doc)] == ["C", "Page-1", "B"]

    def test_resolve_by_index_name_and_id(self, doc):
        pg.add_page(doc, name="Second")
        assert pg.resolve_page(doc, None) == 0
        assert pg.resolve_page(doc, "1") == 1
        assert pg.resolve_page(doc, "Second") == 1
        assert pg.resolve_page(doc, "page-2") == 1

    def test_resolve_out_of_range(self, doc):
        with pytest.raises(ValueError, match="out of range"):
            pg.resolve_page(doc, 5)

    def test_resolve_unknown_name_lists_pages(self, doc):
        with pytest.raises(ValueError, match="0:Page-1"):
            pg.resolve_page(doc, "Nope")

    def test_resolve_ambiguous_name(self, doc):
        pg.add_page(doc, name="Page-1")
        with pytest.raises(ValueError, match="ambiguous"):
            pg.resolve_page(doc, "Page-1")

    def test_setup_size_background_and_grid(self, doc):
        result = pg.setup_page(doc, 0, page_format="letter", background="#eeeeee",
                               grid=False, grid_size=20)
        assert (result["width"], result["height"]) == (850, 1100)
        assert result["background"] == "#eeeeee" and result["grid"] is False
        assert result["grid_size"] == 20
        assert pg.setup_page(doc, 0, background="none")["background"] is None

    def test_setup_orientation_only(self, doc):
        assert pg.setup_page(doc, 0, landscape=True)["width"] == 1169
        assert pg.setup_page(doc, 0, landscape=False)["width"] == 827


# ── core/layers.py ───────────────────────────────────────────────────


class TestLayers:
    def test_default_layer(self, flow):
        layers = lay.list_layers(flow, 0)
        assert len(layers) == 1
        assert layers[0] == {"index": 0, "id": "1", "name": "Background",
                             "visible": True, "locked": False, "cells": 5}

    def test_add(self, doc):
        layer = lay.add_layer(doc, 0, "Notes")
        assert (layer["index"], layer["id"], layer["name"]) == (1, "layer1", "Notes")

    def test_add_with_custom_id_and_empty_name(self, doc):
        assert lay.add_layer(doc, 0, "Top", cell_id="top")["id"] == "top"
        with pytest.raises(ValueError):
            lay.add_layer(doc, 0, " ")

    def test_rename(self, doc):
        lay.add_layer(doc, 0, "Notes")
        assert lay.rename_layer(doc, 0, "Notes", "Comments")["name"] == "Comments"
        assert lay.list_layers(doc, 0)[1]["name"] == "Comments"

    def test_hide_and_show(self, doc):
        assert lay.set_visible(doc, 0, "1", False)["visible"] is False
        assert 'visible="0"' in proj.to_xml(doc)
        assert lay.set_visible(doc, 0, "1", True)["visible"] is True
        assert "visible=" not in proj.to_xml(doc)

    def test_lock_and_unlock(self, doc):
        assert lay.set_locked(doc, 0, "1", True)["locked"] is True
        assert "locked=1" in proj.to_xml(doc)
        assert lay.set_locked(doc, 0, "1", False)["locked"] is False
        assert "locked" not in proj.to_xml(doc)

    def test_remove_takes_contents_and_cross_layer_connectors(self, flow):
        lay.add_layer(flow, 0, "Overlay")
        shp.add_shape(flow, 0, label="Note", layer="Overlay")      # s4
        conn.add_connector(flow, 0, "s1", "s4")                    # e3 on layer 1
        result = lay.remove_layer(flow, 0, "Overlay")
        assert result["removed"] == "layer1" and result["removed_cells"] == 2
        assert shape_ids(flow) == ["s1", "s2", "s3"]
        assert connector_ids(flow) == ["e1", "e2"]
        assert proj.check_project(flow)["ok"]

    def test_cannot_remove_only_layer(self, doc):
        with pytest.raises(ValueError, match="only layer"):
            lay.remove_layer(doc, 0, "1")

    def test_assign_moves_cells(self, flow):
        lay.add_layer(flow, 0, "Overlay")
        assert lay.assign_cells(flow, 0, "Overlay", ["s3", "e2"]) == {
            "layer": "layer1", "moved": ["s3", "e2"]}
        assert [s["id"] for s in shp.list_shapes(flow, 0, layer="Overlay")] == ["s3"]

    def test_assign_rejects_nested_and_unknown_cells(self, doc):
        lay.add_layer(doc, 0, "Overlay")
        shp.add_shape(doc, 0, "container", cell_id="box")
        shp.add_shape(doc, 0, label="Inner", parent="box", cell_id="inner")
        with pytest.raises(ValueError, match="nested inside 'box'"):
            lay.assign_cells(doc, 0, "Overlay", ["inner"])
        with pytest.raises(ValueError, match="not found"):
            lay.assign_cells(doc, 0, "Overlay", ["ghost"])
        with pytest.raises(ValueError, match="is a layer"):
            lay.assign_cells(doc, 0, "Overlay", ["1"])

    def test_resolve_unknown_layer_lists_layers(self, doc):
        lay.add_layer(doc, 0, "Notes")
        with pytest.raises(ValueError, match=r"layer1 \(Notes\)"):
            lay.resolve_layer(proj.page_root(doc, 0), "Nope")

    def test_layer_index_and_layer_of_nested_cell(self, doc):
        lay.add_layer(doc, 0, "Notes")
        shp.add_shape(doc, 0, "container", cell_id="box", layer="Notes")
        shp.add_shape(doc, 0, label="Inner", parent="box", cell_id="inner")
        root_el = proj.page_root(doc, 0)
        assert lay.layer_index(root_el, "Notes") == 1
        assert lay.layer_of(root_el, dx.find_cell(root_el, "inner")) == "layer1"


# ── core/shapes.py ───────────────────────────────────────────────────


class TestShapes:
    def test_registry_styles_are_well_formed(self):
        for name, spec in shp.SHAPE_TYPES.items():
            assert spec["style"].endswith(";"), name
            assert dx.format_style(dx.parse_style(spec["style"])) == spec["style"], name
            assert spec["width"] > 0 and spec["height"] > 0, name

    def test_type_guess_is_stable_for_every_registry_entry(self):
        for name, spec in shp.SHAPE_TYPES.items():
            guessed = shp.guess_type(spec["style"])
            assert guessed != "custom", name
            assert shp._identity(shp.SHAPE_TYPES[guessed]["style"]) == \
                shp._identity(spec["style"]), name

    @pytest.mark.parametrize("style,expected", [
        ("rounded=1;whiteSpace=wrap;html=1;fillColor=#fff;", "rounded"),
        ("ellipse;whiteSpace=wrap;html=1;aspect=fixed;", "circle"),
        ("swimlane;horizontal=0;", "hcontainer"),
        ("shape=mxgraph.aws4.lambda;html=1;", "custom"),
    ])
    def test_type_guess(self, style, expected):
        assert shp.guess_type(style) == expected

    def test_add_uses_palette_defaults(self, doc):
        shape = shp.add_shape(doc, 0, "ellipse")
        assert shape["id"] == "s1" and shape["type"] == "ellipse"
        assert (shape["width"], shape["height"]) == (120, 80)
        assert shape["style"] == "ellipse;whiteSpace=wrap;html=1;"
        assert (shape["parent"], shape["layer"]) == ("1", "1")

    def test_add_with_geometry_and_style_properties(self, doc):
        shape = shp.add_shape(doc, 0, "rectangle", label="Box", x=10.5, y=20,
                              width=200, height=50,
                              set_props={"fillColor": "#dae8fc", "rounded": "1"})
        assert (shape["x"], shape["y"], shape["width"]) == (10.5, 20, 200)
        assert shape["style"] == "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;"
        assert shape["type"] == "rounded"

    def test_auto_placement_stacks_downwards(self, doc):
        first = shp.add_shape(doc, 0)
        second = shp.add_shape(doc, 0)
        assert (first["x"], first["y"]) == (40, 40)
        assert (second["x"], second["y"]) == (40, 140)

    def test_unknown_type_lists_available(self, doc):
        with pytest.raises(ValueError, match="Available: rectangle, rounded"):
            shp.add_shape(doc, 0, "hexagram")

    def test_custom_id_and_duplicate_id(self, doc):
        assert shp.add_shape(doc, 0, cell_id="db")["id"] == "db"
        with pytest.raises(ValueError, match="already in use"):
            shp.add_shape(doc, 0, cell_id="db")

    def test_non_positive_size_rejected(self, doc):
        with pytest.raises(ValueError, match="positive"):
            shp.add_shape(doc, 0, width=0)

    def test_nest_in_container(self, doc):
        shp.add_shape(doc, 0, "container", label="Zone", cell_id="zone")
        inner = shp.add_shape(doc, 0, label="Inner", parent="zone", x=10, y=40)
        assert inner["parent"] == "zone" and inner["layer"] == "1"
        assert shp.get_shape(doc, 0, "zone")["children"] == [inner["id"]]

    def test_parent_and_layer_are_exclusive(self, doc):
        shp.add_shape(doc, 0, "container", cell_id="zone")
        with pytest.raises(ValueError, match="either --parent or --layer"):
            shp.add_shape(doc, 0, parent="zone", layer="1")

    def test_plain_label_is_escaped_for_html_styles(self, doc):
        shp.add_shape(doc, 0, label="a < b & c\nline2")
        info = shp.get_shape(doc, 0, "s1")
        assert info["raw_label"] == "a &lt; b &amp; c<br>line2"
        assert info["label"] == "a < b & c\nline2"

    def test_html_label_is_kept_raw(self, doc):
        shp.add_shape(doc, 0, label="<b>Bold</b>", html_label=True)
        info = shp.get_shape(doc, 0, "s1")
        assert info["raw_label"] == "<b>Bold</b>" and info["label"] == "Bold"

    def test_label_is_not_escaped_without_html_style(self, doc):
        shp.add_shape(doc, 0, label="a < b", style="rounded=1")
        info = shp.get_shape(doc, 0, "s1")
        assert info["raw_label"] == "a < b" and info["style"] == "rounded=1;"

    def test_default_label_comes_from_palette(self, doc):
        assert shp.add_shape(doc, 0, "actor")["label"] == "Actor"

    def test_list_filters(self, flow):
        lay.add_layer(flow, 0, "Overlay")
        shp.add_shape(flow, 0, "note", label="Remember", layer="Overlay")
        assert len(shp.list_shapes(flow, 0)) == 4
        assert [s["label"] for s in shp.list_shapes(flow, 0, contains="STA")] == ["Start"]
        assert [s["id"] for s in shp.list_shapes(flow, 0, shape_type="diamond")] == ["s2"]
        assert [s["label"] for s in shp.list_shapes(flow, 0, layer="Overlay")] == ["Remember"]

    def test_info_lists_connections(self, flow):
        info = shp.get_shape(flow, 0, "s2")
        assert info["incoming"] == ["e1"] and info["outgoing"] == ["e2"]
        assert info["style_properties"]["rhombus"] is True

    def test_missing_shape_error_lists_ids(self, flow):
        with pytest.raises(ValueError, match="Shapes: s1, s2, s3"):
            shp.get_shape(flow, 0, "nope")
        with pytest.raises(ValueError, match="is not a shape"):
            shp.get_shape(flow, 0, "e1")

    def test_set_label(self, flow):
        assert shp.set_label(flow, 0, "s1", "Begin") == {
            "id": "s1", "old_label": "Start", "label": "Begin"}

    def test_move_absolute_and_relative(self, flow):
        assert shp.move_shape(flow, 0, "s1", x=200)["x"] == 200
        moved = shp.move_shape(flow, 0, "s1", dx_=-50, dy=10.5)
        assert (moved["x"], moved["y"]) == (150, 50.5)

    def test_move_requires_one_mode(self, flow):
        with pytest.raises(ValueError, match="either"):
            shp.move_shape(flow, 0, "s1", x=1, dx_=1)
        with pytest.raises(ValueError, match="Give --x/--y"):
            shp.move_shape(flow, 0, "s1")

    def test_resize(self, flow):
        result = shp.resize_shape(flow, 0, "s1", width=300)
        assert (result["width"], result["height"]) == (300, 60)
        with pytest.raises(ValueError, match="positive"):
            shp.resize_shape(flow, 0, "s1", height=-1)
        with pytest.raises(ValueError, match="Give --width"):
            shp.resize_shape(flow, 0, "s1")

    def test_style_set_and_unset(self, flow):
        result = shp.set_style(flow, 0, "s1", {"fillColor": "#fff2cc"}, ["rounded"])
        assert result["style"] == "whiteSpace=wrap;html=1;fillColor=#fff2cc;"
        with pytest.raises(ValueError, match="Nothing to change"):
            shp.set_style(flow, 0, "s1")

    def test_custom_data_survives_save_and_load(self, flow, tmp_path):
        assert shp.set_data(flow, 0, "s1")["data"] == {}
        result = shp.set_data(flow, 0, "s1", {"owner": "kim", "cost": "12"})
        assert result["data"] == {"owner": "kim", "cost": "12"}
        path = str(tmp_path / "d.drawio")
        proj.save_project(flow, path)
        loaded = proj.open_project(path)
        info = shp.get_shape(loaded, 0, "s1")
        assert info["data"] == {"owner": "kim", "cost": "12"}
        assert info["label"] == "Start" and info["incoming"] == []
        assert shp.set_data(loaded, 0, "s1", unset=["cost"])["data"] == {"owner": "kim"}

    @pytest.mark.parametrize("key", ["id", "label", "1abc", "bad-key", "sp ace"])
    def test_invalid_data_keys_rejected(self, flow, key):
        with pytest.raises(ValueError, match="Invalid data key"):
            shp.set_data(flow, 0, "s1", {key: "x"})

    def test_remove_cascades_to_children_and_connectors(self, doc):
        shp.add_shape(doc, 0, "container", cell_id="zone")
        shp.add_shape(doc, 0, label="Inner", parent="zone", cell_id="inner")
        shp.add_shape(doc, 0, label="Outside", cell_id="out")
        conn.add_connector(doc, 0, "inner", "out", label="x")
        result = shp.remove_shape(doc, 0, "zone")
        assert result == {"removed": "zone", "removed_children": ["inner"],
                          "removed_connectors": ["e1"]}
        assert shape_ids(doc) == ["out"] and connector_ids(doc) == []
        assert proj.check_project(doc)["ok"]


# ── core/connectors.py ───────────────────────────────────────────────


class TestConnectors:
    def test_presets_are_well_formed(self):
        names = [s["name"] for s in conn.list_edge_styles()]
        assert names[0] == "orthogonal" and "dashed" in names
        for name, (style, _) in conn.EDGE_STYLES.items():
            assert dx.format_style(dx.parse_style(style)) == style, name
            assert conn.guess_style_name(style) == name, name

    def test_default_preset_is_the_gui_default_edge_style(self):
        props = dx.style_to_dict(conn.EDGE_STYLES["orthogonal"][0])
        assert props["edgeStyle"] == "orthogonalEdgeStyle"
        assert (props["rounded"], props["jettySize"], props["orthogonalLoop"]) == \
            ("0", "auto", "1")

    def test_arrows(self):
        arrows = conn.list_arrows()
        assert {"none", "classic", "block", "diamondThin", "ERoneToMany"} <= set(arrows)

    def test_add_default(self, flow):
        edge = conn.add_connector(flow, 0, "s3", "s1")
        assert edge["id"] == "e3" and (edge["source"], edge["target"]) == ("s3", "s1")
        assert (edge["style_name"], edge["start_arrow"], edge["end_arrow"]) == \
            ("orthogonal", "none", "classic")
        assert edge["layer"] == "1" and edge["waypoints"] == []

    def test_add_with_preset_arrows_and_dash(self, flow):
        edge = conn.add_connector(flow, 0, "s1", "s3", style="straight",
                                  start_arrow="oval", end_arrow="block",
                                  dashed=True, set_props={"strokeColor": "#ff0000"})
        assert (edge["start_arrow"], edge["end_arrow"], edge["dashed"]) == \
            ("oval", "block", True)
        assert edge["style"].endswith("strokeColor=#ff0000;")

    def test_raw_style_replaces_preset(self, flow):
        edge = conn.add_connector(flow, 0, "s1", "s3", raw_style="endArrow=oval")
        assert edge["style"] == "endArrow=oval;" and edge["style_name"] == "custom"

    def test_unknown_style_and_arrow(self, flow):
        with pytest.raises(ValueError, match="Unknown connector style 'zigzag'"):
            conn.add_connector(flow, 0, "s1", "s2", style="zigzag")
        with pytest.raises(ValueError, match="Unknown arrow 'spear' for --end-arrow"):
            conn.add_connector(flow, 0, "s1", "s2", end_arrow="spear")

    def test_endpoints_must_be_shapes(self, flow):
        with pytest.raises(ValueError, match="Shape 'ghost' not found"):
            conn.add_connector(flow, 0, "s1", "ghost")
        with pytest.raises(ValueError, match="is not a shape"):
            conn.add_connector(flow, 0, "e1", "s1")
        assert connector_ids(flow) == ["e1", "e2"]

    def test_waypoints(self, flow):
        edge = conn.add_connector(flow, 0, "s1", "s3",
                                  waypoints=[(300, 70), (300, 330.5)])
        assert edge["waypoints"] == [{"x": 300, "y": 70}, {"x": 300, "y": 330.5}]
        assert '<mxPoint x="300" y="330.5"' in proj.to_xml(flow)

    def test_parse_waypoint(self):
        assert conn.parse_waypoint("10,20.5") == (10.0, 20.5)
        with pytest.raises(ValueError, match="'X,Y'"):
            conn.parse_waypoint("10;20")

    def test_connector_inherits_source_layer(self, doc):
        lay.add_layer(doc, 0, "Overlay")
        shp.add_shape(doc, 0, label="A", layer="Overlay")
        shp.add_shape(doc, 0, label="B")
        assert conn.add_connector(doc, 0, "s1", "s2")["layer"] == "layer1"
        assert conn.add_connector(doc, 0, "s1", "s2", layer="1")["layer"] == "1"

    def test_list_can_filter_by_shape(self, flow):
        assert [c["id"] for c in conn.list_connectors(flow, 0, shape="s1")] == ["e1"]
        assert len(conn.list_connectors(flow, 0, shape="s2")) == 2

    def test_info(self, flow):
        info = conn.get_connector(flow, 0, "e2")
        assert info["label"] == "yes" and info["style_properties"]["html"] == "1"
        with pytest.raises(ValueError, match="Connectors: e1, e2"):
            conn.get_connector(flow, 0, "s1")

    def test_set_label(self, flow):
        assert conn.set_label(flow, 0, "e1", "go")["label"] == "go"
        assert conn.get_connector(flow, 0, "e1")["label"] == "go"

    def test_set_style(self, flow):
        result = conn.set_style(flow, 0, "e1", {"strokeWidth": "2"},
                                ["jettySize"], end_arrow="none")
        assert result["style_properties"]["endArrow"] == "none"
        assert "jettySize" not in result["style_properties"]
        with pytest.raises(ValueError, match="Nothing to change"):
            conn.set_style(flow, 0, "e1")

    def test_remove(self, flow):
        assert conn.remove_connector(flow, 0, "e1") == {
            "removed": "e1", "source": "s1", "target": "s2"}
        assert connector_ids(flow) == ["e2"] and len(shape_ids(flow)) == 3


# ── core/export.py (validation and verification, no backend) ─────────


def _chunk(chunk_type, body=b""):
    return struct.pack(">I", len(body)) + chunk_type + body + b"\x00\x00\x00\x00"


def _png_bytes(width=200, height=100, color_type=6, extra=b"", iend=True):
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + extra
    data += _chunk(b"IDAT", b"pixels")
    return data + (_chunk(b"IEND") if iend else b"\x00\x00\x00\x00")


class TestExportValidation:
    @pytest.mark.parametrize("fmt,output,expected", [
        ("png", None, "png"), (None, "a.PNG", "png"), ("jpeg", None, "jpg"),
        (None, "dir/a.jpeg", "jpg"), ("pdf", "out.dat", "pdf"),
        ("svg", "x.svg", "svg"),
    ])
    def test_format_resolution(self, fmt, output, expected):
        assert exp.normalize_format(fmt, output) == expected

    @pytest.mark.parametrize("fmt,output,message", [
        ("pdf", "a.png", "conflicts"), ("gif", None, "Unknown export format"),
        (None, "a.txt", "Cannot tell"), (None, None, "Cannot tell"),
    ])
    def test_format_resolution_errors(self, fmt, output, message):
        with pytest.raises(ValueError, match=message):
            exp.normalize_format(fmt, output)

    def test_flags_for_valid_png_options(self):
        assert exp.build_flags("png", page_index=1, scale=2, transparent=True,
                               embed_diagram=True, layers=[0, 2]) == [
            "--page-index", "1", "--layers", "0,2", "--scale", "2.0",
            "--transparent", "--embed-diagram"]

    def test_flags_for_pdf_svg_jpg_xml(self):
        assert exp.build_flags("pdf", all_pages=True, crop=True) == [
            "--all-pages", "--crop"]
        assert exp.build_flags("pdf", page_range=(0, 2)) == ["--page-range", "0..2"]
        assert exp.build_flags("svg", svg_theme="dark", embed_svg_images=True,
                               svg_links_target="new-win") == [
            "--embed-svg-images", "--svg-theme", "dark",
            "--svg-links-target", "new-win"]
        assert exp.build_flags("jpg", quality=80, width=640) == [
            "--width", "640", "--quality", "80"]
        assert exp.build_flags("xml") == []
        assert exp.build_flags("vsdx", page_index=0) == ["--page-index", "0"]

    @pytest.mark.parametrize("option", ["border", "uncompressed"])
    def test_options_that_do_nothing_in_draw_io_are_not_offered(self, option):
        """Measured no-ops in draw.io 24.7.17 — see DRAWIO.md."""
        with pytest.raises(TypeError):
            exp.build_flags("png", **{option: 1})
        assert all(option not in spec["options"] for spec in exp.FORMATS.values())

    @pytest.mark.parametrize("fmt,options", [
        ("pdf", {"transparent": True}), ("png", {"crop": True}),
        ("png", {"all_pages": True}), ("png", {"page_range": (0, 1)}),
        ("png", {"quality": 80}), ("jpg", {"embed_diagram": True}),
        ("png", {"embed_svg_images": True}), ("png", {"svg_theme": "dark"}),
        ("pdf", {"svg_links_target": "auto"}), ("vsdx", {"scale": 2}),
        ("svg", {"width": 100}), ("xml", {"scale": 2}), ("xml", {"height": 5}),
    ])
    def test_option_for_wrong_format_is_rejected(self, fmt, options):
        with pytest.raises(ValueError, match="only applies to"):
            exp.build_flags(fmt, **options)

    @pytest.mark.parametrize("fmt,options", [
        ("png", {"scale": 0}), ("png", {"width": 0}), ("pdf", {"height": -1}),
        ("jpg", {"quality": 0}), ("jpg", {"quality": 101}),
        ("svg", {"svg_theme": "blue"}), ("svg", {"svg_links_target": "x"}),
        ("png", {"page_index": -1}), ("png", {"layers": [-1]}),
        ("pdf", {"page_range": (2, 1)}),
    ])
    def test_out_of_range_values_are_rejected(self, fmt, options):
        with pytest.raises(ValueError):
            exp.build_flags(fmt, **options)

    def test_page_selectors_are_exclusive(self):
        with pytest.raises(ValueError, match="only one of"):
            exp.build_flags("pdf", page_index=0, all_pages=True)

    def test_parse_page_range(self):
        assert exp.parse_page_range("0..2") == (0, 2)
        with pytest.raises(ValueError, match="0..2"):
            exp.parse_page_range("0-2")

    def test_render_checks_page_index_before_running(self, doc, tmp_path):
        with pytest.raises(ValueError, match="out of range"):
            exp.render(doc, str(tmp_path / "x.png"), page_index=3)
        with pytest.raises(ValueError, match="exceeds"):
            exp.render(doc, str(tmp_path / "x.pdf"), page_range=(0, 4))

    def test_render_refuses_to_overwrite(self, doc, tmp_path):
        target = tmp_path / "x.png"
        target.write_bytes(b"keep me")
        with pytest.raises(FileExistsError, match="--overwrite"):
            exp.render(doc, str(target))
        assert target.read_bytes() == b"keep me"

    def test_verify_png_reads_header(self, tmp_path):
        path = tmp_path / "a.png"
        path.write_bytes(_png_bytes(200, 100, 6))
        assert exp.verify_output(str(path), "png") == {
            "image_width": 200, "image_height": 100, "has_alpha": True,
            "embedded_diagram": False, "png_complete": True}
        path.write_bytes(_png_bytes(8, 8, 2))
        assert exp.verify_output(str(path), "png")["has_alpha"] is False

    def test_verify_png_sees_embedded_diagram_and_missing_iend(self, tmp_path):
        """The shape draw.io 24.7.17 gives --embed-diagram PNGs."""
        path = tmp_path / "e.png"
        embedded = _chunk(b"zTXt", b"mxGraphModel\x00\x00deflated-xml")
        path.write_bytes(_png_bytes(extra=embedded, iend=False))
        details = exp.verify_output(str(path), "png")
        assert details["embedded_diagram"] is True
        assert details["png_complete"] is False
        other = _chunk(b"tEXt", b"Software\x00something else")
        path.write_bytes(_png_bytes(extra=other))
        assert exp.verify_output(str(path), "png")["embedded_diagram"] is False

    def test_verify_pdf_counts_pages(self, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(b"%PDF-1.7\n1 0 obj<</Type /Pages/Kids[2 0 R 3 0 R]>>\n"
                         b"2 0 obj<</Type /Page>>\n3 0 obj<</Type/Page/Parent 1 0 R>>")
        assert exp.verify_output(str(path), "pdf") == {"pdf_pages": 2}

    def test_verify_pdf_counts_pages_inside_object_streams(self, tmp_path):
        """draw.io PDFs keep their page dictionaries in compressed streams."""
        objects = (b"<</Type /Pages /Count 3>> <</Type /Page>> "
                   b"<</Type /Page>> <</Type/Page>>")
        path = tmp_path / "o.pdf"
        path.write_bytes(
            b"%PDF-1.7\n1 0 obj\n<</Type /ObjStm /Filter /FlateDecode>>\n"
            b"stream\n" + zlib.compress(objects) + b"\nendstream\nendobj\n"
            b"2 0 obj\n<</Length 9>>\nstream\nnot zlib!\nendstream\nendobj\n%%EOF")
        assert exp.verify_output(str(path), "pdf") == {"pdf_pages": 3}

    @pytest.mark.parametrize("fmt,data", [
        ("jpg", b"\xff\xd8\xff\xe0rest"), ("svg", b'<?xml version="1.0"?><svg/>'),
        ("vsdx", b"PK\x03\x04rest"), ("xml", b"<mxfile><diagram/></mxfile>"),
    ])
    def test_verify_accepts_valid_signatures(self, tmp_path, fmt, data):
        path = tmp_path / ("a." + fmt)
        path.write_bytes(data)
        assert exp.verify_output(str(path), fmt) == {}

    @pytest.mark.parametrize("fmt", ["png", "jpg", "pdf", "svg", "vsdx", "xml"])
    def test_verify_rejects_wrong_content(self, tmp_path, fmt):
        path = tmp_path / ("a." + fmt)
        path.write_bytes(b"Error: Export failed")
        with pytest.raises(RuntimeError, match="not valid"):
            exp.verify_output(str(path), fmt)

    def test_verify_rejects_empty_and_missing(self, tmp_path):
        empty = tmp_path / "e.png"
        empty.write_bytes(b"")
        with pytest.raises(RuntimeError, match="empty file"):
            exp.verify_output(str(empty), "png")
        with pytest.raises(RuntimeError, match="no file"):
            exp.verify_output(str(tmp_path / "missing.png"), "png")


# ── core/session.py ──────────────────────────────────────────────────


@pytest.fixture
def session(tmp_path):
    s = Session()
    s.set_project(proj.create_project(), str(tmp_path / "s.drawio"))
    return s


def _add(session, label):
    with session.transaction(f"add {label}"):
        shp.add_shape(session.root, 0, label=label)


class TestSession:
    def test_empty_session(self):
        s = Session()
        assert s.status() == {"has_project": False, "project_path": None,
                              "modified": False, "current_page": 0,
                              "undo_levels": 0, "redo_levels": 0}
        with pytest.raises(ValueError, match="No project loaded"):
            s.get_root()

    def test_undo_and_redo(self, session):
        _add(session, "A")
        _add(session, "B")
        assert session.undo() == {"undone": "add B", "undo_levels": 1, "redo_levels": 1}
        assert shape_ids(session.root) == ["s1"]
        assert session.redo()["redone"] == "add B"
        assert shape_ids(session.root) == ["s1", "s2"]

    def test_nothing_to_undo_or_redo(self, session):
        with pytest.raises(ValueError, match="Nothing to undo"):
            session.undo()
        with pytest.raises(ValueError, match="Nothing to redo"):
            session.redo()

    def test_new_change_clears_redo(self, session):
        _add(session, "A")
        session.undo()
        _add(session, "B")
        assert session.history()["redo"] == []
        assert session.history()["undo"] == ["add B"]

    def test_undo_depth_is_capped(self, session):
        for i in range(sess_mod.MAX_UNDO + 5):
            session.snapshot(f"step {i}")
        history = session.history()
        assert history["undo_levels"] == sess_mod.MAX_UNDO
        assert history["undo"][0] == "step 5"

    def test_transaction_rolls_back_on_error(self, session):
        _add(session, "A")
        session._modified = False
        with pytest.raises(RuntimeError):
            with session.transaction("broken"):
                shp.add_shape(session.root, 0, label="half done")
                raise RuntimeError("boom")
        assert shape_ids(session.root) == ["s1"]
        assert session.history()["undo"] == ["add A"]
        assert session._modified is False

    def test_save_requires_a_path(self):
        s = Session()
        s.set_project(proj.create_project())
        with pytest.raises(ValueError, match="project save -o"):
            s.save_session()

    def test_history_persists_between_sessions(self, session):
        _add(session, "A")
        _add(session, "B")
        session.undo()
        path = session.save_session()
        assert session._modified is False
        again = Session()
        again.set_project(proj.open_project(path), path)
        assert again.history()["undo"] == ["add A"]
        assert again.history()["redo"] == ["add B"]
        again.redo()
        assert shape_ids(again.root) == ["s1", "s2"]

    def test_history_is_dropped_after_external_edit(self, session):
        _add(session, "A")
        path = session.save_session()
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n<!-- edited in the GUI -->\n")
        again = Session()
        again.set_project(proj.open_project(path), path)
        assert again.history()["undo_levels"] == 0

    def test_corrupt_history_file_is_ignored(self, session):
        _add(session, "A")
        path = session.save_session()
        with open(sess_mod.history_path(path), "w", encoding="utf-8") as f:
            f.write("{not json")
        again = Session()
        again.set_project(proj.open_project(path), path)
        assert again.history()["undo_levels"] == 0

    def test_clear_history_removes_sidecar(self, session):
        _add(session, "A")
        path = session.save_session()
        assert os.path.isfile(sess_mod.history_path(path))
        session.clear_history()
        assert not os.path.isfile(sess_mod.history_path(path))

    def test_undo_clamps_current_page(self, session):
        with session.transaction("page add"):
            pg.add_page(session.root)
        session.current_page = 1
        session.undo()
        assert session.current_page == 0
        assert session.status()["current_page_name"] == "Page-1"


# ── drawio_cli.py (in-process, Click CliRunner) ──────────────────────


def run(*args, input=None):
    """Invoke the CLI as a fresh process would (no shared in-memory state)."""
    drawio_cli.reset_session()
    return CliRunner().invoke(drawio_cli.cli, list(args), input=input)


def run_json(*args):
    result = run("--json", *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def project_file(tmp_path):
    path = str(tmp_path / "cli.drawio")
    assert run("project", "new", "-o", path).exit_code == 0
    return path


class TestCLI:
    def test_help_lists_all_groups(self):
        result = run("--help")
        assert result.exit_code == 0
        for group in ("project", "page", "layer", "shape", "connect", "export",
                      "backend", "session"):
            assert group in result.output

    def test_version(self):
        result = run("--version")
        assert result.exit_code == 0 and "1.0.0" in result.output

    def test_every_command_has_help(self):
        for group_name, group in drawio_cli.cli.commands.items():
            for name in getattr(group, "commands", {}):
                result = run(group_name, name, "--help")
                assert result.exit_code == 0, (group_name, name)
                assert "Usage:" in result.output

    def test_new_requires_output_in_one_shot_mode(self):
        result = run("project", "new")
        assert result.exit_code == 1 and "Give -o" in result.output

    def test_new_refuses_to_overwrite(self, project_file):
        result = run("--json", "project", "new", "-o", project_file)
        assert result.exit_code == 1
        assert json.loads(result.stdout)["type"] == "FileExistsError"
        assert run("project", "new", "-o", project_file, "--overwrite").exit_code == 0

    def test_new_and_info_json(self, tmp_path):
        path = str(tmp_path / "n.drawio")
        created = run_json("project", "new", "-o", path, "--name", "Main",
                           "--format", "letter", "--landscape")
        assert created["saved"] is True and created["pages"][0]["width"] == 1100
        info = run_json("--project", path, "project", "info")
        assert info["page_count"] == 1 and info["pages"][0]["name"] == "Main"
        assert info["file_size"] > 0

    def test_mutation_is_saved_automatically(self, project_file):
        added = run_json("--project", project_file, "shape", "add", "-l", "Saved",
                         "--fill", "#dae8fc")
        assert added["id"] == "s1" and added["saved"] is True
        on_disk = shp.get_shape(proj.open_project(project_file), 0, "s1")
        assert on_disk["label"] == "Saved"
        assert on_disk["style_properties"]["fillColor"] == "#dae8fc"

    def test_dry_run_does_not_touch_the_file(self, project_file):
        before = open(project_file, "rb").read()
        result = run_json("--project", project_file, "--dry-run",
                          "shape", "add", "-l", "Ghost")
        assert result["saved"] is False and result["dry_run"] is True
        assert open(project_file, "rb").read() == before

    def test_error_is_json_with_exit_code_1(self, project_file):
        result = run("--json", "--project", project_file, "shape", "info", "nope")
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["type"] == "ValueError" and "not found" in payload["error"]

    def test_failed_mutation_leaves_file_untouched(self, project_file):
        before = open(project_file, "rb").read()
        result = run("--project", project_file, "connect", "add", "a", "b")
        assert result.exit_code == 1 and "Error:" in result.output
        assert open(project_file, "rb").read() == before

    def test_missing_project_file_and_missing_option(self, tmp_path):
        result = run("--project", str(tmp_path / "nope.drawio"), "shape", "list")
        assert result.exit_code == 1 and "Project file not found" in result.output
        result = run("shape", "list")
        assert result.exit_code == 1 and "No project loaded" in result.output

    def test_unknown_command_is_a_usage_error(self):
        result = run("shape", "explode")
        assert result.exit_code == 2 and "No such command" in result.output

    def test_undo_works_across_invocations(self, project_file):
        run_json("--project", project_file, "shape", "add", "-l", "A")
        run_json("--project", project_file, "shape", "add", "-l", "B")
        undone = run_json("--project", project_file, "session", "undo")
        assert undone["undone"] == "shape add rectangle" and undone["saved"] is True
        assert run_json("--project", project_file, "shape", "list")["count"] == 1
        run_json("--project", project_file, "session", "redo")
        assert run_json("--project", project_file, "shape", "list")["count"] == 2
        history = run_json("--project", project_file, "session", "history")
        assert history["undo_levels"] == 2 and history["redo_levels"] == 0

    def test_human_output_is_a_table(self, project_file):
        run("--project", project_file, "shape", "add", "-l", "한글 라벨")
        result = run("--project", project_file, "shape", "list")
        lines = result.output.splitlines()
        assert lines[0] == "page: 0" and lines[1] == "count: 1"
        assert lines[3].split()[:3] == ["id", "type", "label"]
        assert "한글 라벨" in result.output

    def test_check_exit_code_reflects_result(self, project_file):
        assert run("--project", project_file, "project", "check").exit_code == 0
        d = proj.open_project(project_file)
        shp.add_shape(d, 0, label="A")
        shp.add_shape(d, 0, label="B")
        conn.add_connector(d, 0, "s1", "s2")
        dx.find_cell(proj.page_root(d, 0), "e1").cell.set("target", "ghost")
        proj.save_project(d, project_file)
        result = run("--json", "--project", project_file, "project", "check")
        assert result.exit_code == 1 and json.loads(result.stdout)["ok"] is False

    def test_page_option_accepts_names(self, project_file):
        run_json("--project", project_file, "page", "add", "--name", "Details")
        added = run_json("--project", project_file, "shape", "add", "-l", "On 2",
                         "--page", "Details")
        assert added["id"] == "s1"
        assert run_json("--project", project_file, "shape", "list", "-P", "0")["count"] == 0
        assert run_json("--project", project_file, "shape", "list", "-P", "1")["count"] == 1

    def test_layer_commands(self, project_file):
        assert run_json("--project", project_file, "layer", "add", "Notes")["id"] == "layer1"
        assert run_json("--project", project_file, "layer", "hide", "Notes")["visible"] is False
        assert run_json("--project", project_file, "layer", "lock", "Notes")["locked"] is True
        layers = run_json("--project", project_file, "layer", "list")["layers"]
        assert (layers[1]["visible"], layers[1]["locked"]) == (False, True)
        assert run_json("--project", project_file, "layer", "show", "Notes")["visible"] is True
        assert run_json("--project", project_file, "layer", "unlock", "Notes")["locked"] is False
        run_json("--project", project_file, "shape", "add", "-l", "A")
        moved = run_json("--project", project_file, "layer", "assign", "Notes", "s1")
        assert moved["moved"] == ["s1"]
        assert run_json("--project", project_file, "layer", "remove", "Notes")["removed_cells"] == 1

    def test_export_options_are_validated_before_draw_io_runs(self, project_file, tmp_path):
        result = run("--json", "--project", project_file, "export", "render",
                     str(tmp_path / "x.pdf"), "--transparent")
        assert result.exit_code == 1
        assert "only applies to png" in json.loads(result.stdout)["error"]

    def test_listing_commands_need_no_project(self):
        assert run_json("shape", "types")["count"] == len(shp.SHAPE_TYPES)
        assert run_json("connect", "styles")["count"] == len(conn.EDGE_STYLES)
        assert "classic" in run_json("connect", "arrows")["arrows"]
        assert len(run_json("project", "presets")["presets"]) == 17
        assert [f["format"] for f in run_json("export", "formats")["formats"]] == \
            ["png", "jpg", "svg", "pdf", "vsdx", "xml"]

    def test_repl_session_with_piped_input(self):
        script = "\n".join([
            "project new", "shape add -l A", "shape add -l B", "undo",
            "shape list", "shape info nope", "status", "quit"]) + "\n"
        result = run(input=script)
        assert result.exit_code == 0, result.output
        assert "count: 1" in result.output
        assert "Shape 'nope' not found" in result.output
        assert "modified: yes" in result.output

    def test_repl_save_and_reopen(self, tmp_path):
        path = str(tmp_path / "r.drawio")
        script = f'project new\nshape add -l "From REPL"\nsave -o "{path}"\nquit\n'
        assert run(input=script).exit_code == 0
        assert shp.get_shape(proj.open_project(path), 0, "s1")["label"] == "From REPL"
