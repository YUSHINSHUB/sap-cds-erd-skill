"""Connector (edge) operations.

Style presets come from draw.io's own definitions:
``Graph.prototype.defaultEdgeStyle`` (Graph.js) for the default orthogonal
connector and the ``createEdgeTemplateEntry`` palette entries in
``Sidebar.js`` for the rest. Arrow names are the markers listed in
``Format.js``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from cli_anything.drawio.core import layers as layer_mod
from cli_anything.drawio.core import project as proj
from cli_anything.drawio.core import shapes as shape_mod
from cli_anything.drawio.utils import drawio_xml as dx

EDGE_STYLES = {
    "orthogonal": ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;", "Orthogonal connector (GUI default)"),
    "straight": ("endArrow=classic;html=1;rounded=0;", "Straight directional connector"),
    "curved": ("curved=1;endArrow=classic;html=1;", "Curved connector"),
    "elbow-horizontal": ("edgeStyle=elbowEdgeStyle;elbow=horizontal;endArrow=classic;html=1;curved=0;rounded=0;endSize=8;startSize=8;", "Horizontal elbow"),
    "elbow-vertical": ("edgeStyle=elbowEdgeStyle;elbow=vertical;endArrow=classic;html=1;curved=0;rounded=0;endSize=8;startSize=8;", "Vertical elbow"),
    "entity-relation": ("edgeStyle=entityRelationEdgeStyle;html=1;endArrow=ERoneToMany;startArrow=ERmandOne;", "Entity-relation connector"),
    "isometric": ("edgeStyle=isometricEdgeStyle;endArrow=none;html=1;", "Isometric edge"),
    "line": ("endArrow=none;html=1;", "Plain line, no arrows"),
    "dashed": ("endArrow=none;dashed=1;html=1;", "Dashed line"),
    "dotted": ("endArrow=none;dashed=1;html=1;dashPattern=1 3;strokeWidth=2;", "Dotted line"),
    "bidirectional": ("endArrow=classic;startArrow=classic;html=1;", "Arrows on both ends"),
    "link": ("shape=link;html=1;", "Double-line link"),
    "flex-arrow": ("shape=flexArrow;endArrow=classic;html=1;", "Block arrow"),
    "dependency": ("endArrow=open;endSize=12;dashed=1;html=1;", "UML dependency"),
    "generalization": ("endArrow=block;endSize=16;endFill=0;html=1;", "UML generalization"),
    "implementation": ("endArrow=block;dashed=1;endFill=0;endSize=12;html=1;", "UML implementation"),
    "aggregation": ("endArrow=diamondThin;endFill=0;endSize=24;html=1;", "UML aggregation"),
    "composition": ("endArrow=diamondThin;endFill=1;endSize=24;html=1;", "UML composition"),
    "association": ("endArrow=open;endFill=1;endSize=12;html=1;", "UML association"),
}

ARROWS = (
    "none", "classic", "classicThin", "open", "openThin", "openAsync",
    "block", "blockThin", "async", "oval", "diamond", "diamondThin", "box",
    "halfCircle", "dash", "cross", "circlePlus", "circle", "baseDash",
    "ERone", "ERmandOne", "ERmany", "ERoneToMany", "ERzeroToOne",
    "ERzeroToMany", "doubleBlock",
)


def list_edge_styles():
    return [{"name": name, "title": title, "style": style}
            for name, (style, title) in EDGE_STYLES.items()]


def list_arrows():
    return list(ARROWS)


def _check_arrow(value, option):
    if value is not None and value not in ARROWS:
        raise ValueError(
            f"Unknown arrow '{value}' for {option}. "
            f"Available: {', '.join(ARROWS)}"
        )


def guess_style_name(style):
    wanted = dx.style_to_dict(style)
    for name, (preset, _) in EDGE_STYLES.items():
        if dx.style_to_dict(preset) == wanted:
            return name
    edge_style = wanted.get("edgeStyle")
    if edge_style == "orthogonalEdgeStyle":
        return "orthogonal"
    return "custom"


def iter_connectors(root_el):
    for cell in dx.iter_cells(root_el):
        if cell.is_edge:
            yield cell


def require_connector(root_el, cell_id):
    cell = dx.find_cell(root_el, cell_id)
    if cell is None or not cell.is_edge:
        ids = [c.id for c in iter_connectors(root_el)]
        shown = ", ".join(ids[:20]) + (" ..." if len(ids) > 20 else "")
        kind = "is not a connector" if cell is not None else "not found"
        raise ValueError(
            f"Connector '{cell_id}' {kind} on this page. "
            f"Connectors: {shown or '(none)'}"
        )
    return cell


def _waypoints(cell):
    geo = cell.geometry
    if geo is None:
        return []
    points = []
    for array in geo.findall("Array"):
        if array.get("as") == "points":
            for point in array.findall("mxPoint"):
                points.append({"x": dx.to_num(point.get("x")),
                               "y": dx.to_num(point.get("y"))})
    return points


def connector_summary(root_el, cell):
    style = dx.style_to_dict(cell.style)
    return {
        "id": cell.id,
        "label": dx.label_to_plain(cell.label),
        "source": cell.source,
        "target": cell.target,
        "style_name": guess_style_name(cell.style),
        "start_arrow": style.get("startArrow", "none"),
        "end_arrow": style.get("endArrow", "classic"),
        "dashed": style.get("dashed") == "1",
        "layer": layer_mod.layer_of(root_el, cell),
    }


def parse_waypoint(text):
    try:
        x_text, y_text = text.split(",", 1)
        return float(x_text), float(y_text)
    except ValueError:
        raise ValueError(f"Waypoint must be 'X,Y', got: {text!r}") from None


def add_connector(mxfile, page, source, target, label=None, style="orthogonal",
                  raw_style=None, start_arrow=None, end_arrow=None,
                  dashed=False, set_props=None, waypoints=None, layer=None,
                  cell_id=None, html_label=False):
    """Connect two shapes and return the connector summary."""
    root_el = proj.page_root(mxfile, page)
    source_cell = shape_mod.require_shape(root_el, source)
    target_cell = shape_mod.require_shape(root_el, target)
    if raw_style is not None:
        final_style = dx.format_style(dx.parse_style(raw_style))
    else:
        if style not in EDGE_STYLES:
            raise ValueError(
                f"Unknown connector style '{style}'. "
                f"Available: {', '.join(EDGE_STYLES)}"
            )
        final_style = EDGE_STYLES[style][0]
    _check_arrow(start_arrow, "--start-arrow")
    _check_arrow(end_arrow, "--end-arrow")
    changes = {}
    if start_arrow is not None:
        changes["startArrow"] = start_arrow
    if end_arrow is not None:
        changes["endArrow"] = end_arrow
    if dashed:
        changes["dashed"] = "1"
    changes.update(set_props or {})
    final_style = dx.apply_style_changes(final_style, changes)

    if layer is not None:
        parent_id = layer_mod.resolve_layer(root_el, layer).id
    else:
        parent_id = (layer_mod.layer_of(root_el, source_cell)
                     or layer_mod.resolve_layer(root_el, None).id)

    new_id = (dx.validate_new_id(root_el, cell_id) if cell_id
              else dx.next_id(root_el, "e"))
    attrs = {"id": new_id}
    if label:
        attrs["value"] = shape_mod._encode_label(label, final_style, html_label)
    attrs.update({"style": final_style, "edge": "1", "parent": parent_id,
                  "source": source_cell.id, "target": target_cell.id})
    element = ET.Element("mxCell", attrs)
    geo = ET.SubElement(element, "mxGeometry",
                        {"relative": "1", "as": "geometry"})
    if waypoints:
        array = ET.SubElement(geo, "Array", {"as": "points"})
        for x, y in waypoints:
            ET.SubElement(array, "mxPoint",
                          {"x": dx.fmt_num(x), "y": dx.fmt_num(y)})
    root_el.append(element)
    cell = dx.Cell(element, element)
    summary = connector_summary(root_el, cell)
    summary["waypoints"] = _waypoints(cell)
    summary["style"] = final_style
    return summary


def list_connectors(mxfile, page, shape=None):
    """List connectors; ``shape`` limits to those attached to a shape id."""
    root_el = proj.page_root(mxfile, page)
    if shape is not None:
        shape_mod.require_shape(root_el, shape)
    result = []
    for cell in iter_connectors(root_el):
        if shape is not None and shape not in (cell.source, cell.target):
            continue
        result.append(connector_summary(root_el, cell))
    return result


def get_connector(mxfile, page, cell_id):
    root_el = proj.page_root(mxfile, page)
    cell = require_connector(root_el, cell_id)
    info = connector_summary(root_el, cell)
    info["raw_label"] = cell.label
    info["waypoints"] = _waypoints(cell)
    info["style"] = cell.style
    info["style_properties"] = dx.style_to_dict(cell.style)
    info["data"] = cell.data
    return info


def set_label(mxfile, page, cell_id, text, html_label=False):
    root_el = proj.page_root(mxfile, page)
    cell = require_connector(root_el, cell_id)
    old = dx.label_to_plain(cell.label)
    cell.label = shape_mod._encode_label(text, cell.style, html_label)
    return {"id": cell.id, "old_label": old,
            "label": dx.label_to_plain(cell.label)}


def set_style(mxfile, page, cell_id, set_props=None, unset=None,
              start_arrow=None, end_arrow=None):
    root_el = proj.page_root(mxfile, page)
    cell = require_connector(root_el, cell_id)
    _check_arrow(start_arrow, "--start-arrow")
    _check_arrow(end_arrow, "--end-arrow")
    changes = dict(set_props or {})
    if start_arrow is not None:
        changes["startArrow"] = start_arrow
    if end_arrow is not None:
        changes["endArrow"] = end_arrow
    if not changes and not unset:
        raise ValueError(
            "Nothing to change: give KEY=VALUE pairs, --unset KEY, "
            "--start-arrow or --end-arrow."
        )
    old = cell.style
    cell.style = dx.apply_style_changes(old, changes, unset)
    return {"id": cell.id, "old_style": old, "style": cell.style,
            "style_properties": dx.style_to_dict(cell.style)}


def remove_connector(mxfile, page, cell_id):
    root_el = proj.page_root(mxfile, page)
    cell = require_connector(root_el, cell_id)
    doomed = {cell.id} | set(layer_mod.descendants(root_el, cell.id))
    for other in list(dx.iter_cells(root_el)):
        if other.id in doomed:
            root_el.remove(other.holder)
    return {"removed": cell.id, "source": cell.source, "target": cell.target}
