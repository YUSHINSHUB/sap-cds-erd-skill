"""Shape (vertex) operations: add, list, inspect, edit, remove.

The shape registry mirrors the General / Misc / Advanced palettes defined in
draw.io's ``Sidebar.js`` (``createVertexTemplateEntry`` calls): each entry is
the exact style string and default size the GUI uses when the shape is
dragged from the sidebar.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from cli_anything.drawio.core import layers as layer_mod
from cli_anything.drawio.core import project as proj
from cli_anything.drawio.utils import drawio_xml as dx


def _t(style, width, height, title, label=""):
    return {"style": style, "width": width, "height": height,
            "title": title, "label": label}


SHAPE_TYPES = {
    # General palette
    "rectangle": _t("rounded=0;whiteSpace=wrap;html=1;", 120, 60, "Rectangle"),
    "rounded": _t("rounded=1;whiteSpace=wrap;html=1;", 120, 60, "Rounded Rectangle"),
    "text": _t("text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;", 60, 30, "Text", "Text"),
    "ellipse": _t("ellipse;whiteSpace=wrap;html=1;", 120, 80, "Ellipse"),
    "square": _t("whiteSpace=wrap;html=1;aspect=fixed;", 80, 80, "Square"),
    "circle": _t("ellipse;whiteSpace=wrap;html=1;aspect=fixed;", 80, 80, "Circle"),
    "process": _t("shape=process;whiteSpace=wrap;html=1;backgroundOutline=1;", 120, 60, "Process"),
    "diamond": _t("rhombus;whiteSpace=wrap;html=1;", 80, 80, "Diamond"),
    "parallelogram": _t("shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;fixedSize=1;", 120, 60, "Parallelogram"),
    "hexagon": _t("shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;", 120, 80, "Hexagon"),
    "triangle": _t("triangle;whiteSpace=wrap;html=1;", 60, 80, "Triangle"),
    "cylinder": _t("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;", 60, 80, "Cylinder"),
    "cloud": _t("ellipse;shape=cloud;whiteSpace=wrap;html=1;", 120, 80, "Cloud"),
    "document": _t("shape=document;whiteSpace=wrap;html=1;boundedLbl=1;", 120, 80, "Document"),
    "internal-storage": _t("shape=internalStorage;whiteSpace=wrap;html=1;backgroundOutline=1;", 80, 80, "Internal Storage"),
    "cube": _t("shape=cube;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;darkOpacity=0.05;darkOpacity2=0.1;", 120, 80, "Cube"),
    "step": _t("shape=step;perimeter=stepPerimeter;whiteSpace=wrap;html=1;fixedSize=1;", 120, 80, "Step"),
    "trapezoid": _t("shape=trapezoid;perimeter=trapezoidPerimeter;whiteSpace=wrap;html=1;fixedSize=1;", 120, 60, "Trapezoid"),
    "tape": _t("shape=tape;whiteSpace=wrap;html=1;", 120, 100, "Tape"),
    "note": _t("shape=note;whiteSpace=wrap;html=1;backgroundOutline=1;darkOpacity=0.05;", 80, 100, "Note"),
    "card": _t("shape=card;whiteSpace=wrap;html=1;", 80, 100, "Card"),
    "callout": _t("shape=callout;whiteSpace=wrap;html=1;perimeter=calloutPerimeter;", 120, 80, "Callout"),
    "actor": _t("shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;", 30, 60, "Actor", "Actor"),
    "or": _t("shape=xor;whiteSpace=wrap;html=1;", 60, 80, "Or"),
    "and": _t("shape=or;whiteSpace=wrap;html=1;", 60, 80, "And"),
    "data-storage": _t("shape=dataStorage;whiteSpace=wrap;html=1;fixedSize=1;", 100, 80, "Data Storage"),
    "container": _t("swimlane;whiteSpace=wrap;html=1;", 200, 200, "Vertical Container", "Container"),
    "hcontainer": _t("swimlane;horizontal=0;whiteSpace=wrap;html=1;", 200, 200, "Horizontal Container", "Container"),
    # Misc palette
    "title": _t("text;strokeColor=none;fillColor=none;html=1;fontSize=24;fontStyle=1;verticalAlign=middle;align=center;", 100, 40, "Title", "Title"),
    "double-rectangle": _t("shape=ext;double=1;rounded=0;whiteSpace=wrap;html=1;", 120, 80, "Double Rectangle"),
    "double-ellipse": _t("ellipse;shape=doubleEllipse;whiteSpace=wrap;html=1;", 100, 60, "Double Ellipse"),
    "hline": _t("line;strokeWidth=2;html=1;", 160, 10, "Horizontal Line"),
    "vline": _t("line;strokeWidth=2;direction=south;html=1;", 10, 160, "Vertical Line"),
    # Advanced palette
    "manual-input": _t("shape=manualInput;whiteSpace=wrap;html=1;", 80, 80, "Manual Input"),
    "loop-limit": _t("shape=loopLimit;whiteSpace=wrap;html=1;", 100, 80, "Loop Limit"),
    "off-page": _t("shape=offPageConnector;whiteSpace=wrap;html=1;", 80, 80, "Off Page Connector"),
    "delay": _t("shape=delay;whiteSpace=wrap;html=1;", 80, 40, "Delay"),
    "display": _t("shape=display;whiteSpace=wrap;html=1;", 80, 40, "Display"),
    "arrow-left": _t("shape=singleArrow;direction=west;whiteSpace=wrap;html=1;", 100, 60, "Arrow Left"),
    "arrow-right": _t("shape=singleArrow;whiteSpace=wrap;html=1;", 100, 60, "Arrow Right"),
    "arrow-up": _t("shape=singleArrow;direction=north;whiteSpace=wrap;html=1;", 60, 100, "Arrow Up"),
    "arrow-down": _t("shape=singleArrow;direction=south;whiteSpace=wrap;html=1;", 60, 100, "Arrow Down"),
    "double-arrow": _t("shape=doubleArrow;whiteSpace=wrap;html=1;", 100, 60, "Double Arrow"),
    "user": _t("shape=actor;whiteSpace=wrap;html=1;", 40, 60, "User"),
    "cross": _t("shape=cross;whiteSpace=wrap;html=1;", 80, 80, "Cross"),
    "data-store": _t("shape=datastore;whiteSpace=wrap;html=1;", 60, 60, "Data Store"),
    "sum": _t("shape=sumEllipse;perimeter=ellipsePerimeter;whiteSpace=wrap;html=1;backgroundOutline=1;", 80, 80, "Sum"),
    "sort": _t("shape=sortShape;perimeter=rhombusPerimeter;whiteSpace=wrap;html=1;", 80, 80, "Sort"),
    "collate": _t("shape=collate;whiteSpace=wrap;html=1;", 80, 80, "Collate"),
    "switch": _t("shape=switch;whiteSpace=wrap;html=1;", 60, 60, "Switch"),
    # UML palette
    "module": _t("shape=module;align=left;spacingLeft=20;align=center;verticalAlign=top;whiteSpace=wrap;html=1;", 100, 50, "Module", "Module"),
}

_IDENTITY_KEYS = ("shape", "direction", "double", "horizontal", "aspect")
_DATA_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def list_shape_types():
    return [
        {"name": name, "title": spec["title"], "width": spec["width"],
         "height": spec["height"], "style": spec["style"]}
        for name, spec in SHAPE_TYPES.items()
    ]


def _identity(style):
    items = dx.parse_style(style)
    ident = {k: v for k, v in items if v is None or k in _IDENTITY_KEYS}
    if ("rounded", "1") in items:
        ident["rounded"] = "1"
    return ident


def guess_type(style):
    """Best matching registry name for a style string, or ``custom``."""
    ident = _identity(style)
    distinctive = {k for k, v in ident.items() if v is None or k == "shape"}
    best, best_score = None, -1
    for name, spec in SHAPE_TYPES.items():
        reg = _identity(spec["style"])
        if not distinctive <= set(reg):
            continue
        if any(ident.get(k, "\0") != v for k, v in reg.items()):
            continue
        if len(reg) > best_score:
            best, best_score = name, len(reg)
    return best or "custom"


def _encode_label(text, style, html_label):
    if text is None:
        return ""
    if html_label or dx.style_get(style, "html") != "1":
        return text
    return dx.plain_to_label(text)


def _is_shape(root_el, cell, edge_ids=None):
    if not cell.is_vertex:
        return False
    if edge_ids is None:
        edge_ids = {c.id for c in dx.iter_cells(root_el) if c.is_edge}
    return cell.parent not in edge_ids


def iter_shapes(root_el):
    edge_ids = {c.id for c in dx.iter_cells(root_el) if c.is_edge}
    for cell in dx.iter_cells(root_el):
        if _is_shape(root_el, cell, edge_ids):
            yield cell


def require_shape(root_el, cell_id):
    cell = dx.find_cell(root_el, cell_id)
    if cell is None or not _is_shape(root_el, cell):
        ids = [c.id for c in iter_shapes(root_el)]
        shown = ", ".join(ids[:20]) + (" ..." if len(ids) > 20 else "")
        kind = "is not a shape" if cell is not None else "not found"
        raise ValueError(
            f"Shape '{cell_id}' {kind} on this page. "
            f"Shapes: {shown or '(none)'}"
        )
    return cell


def shape_summary(root_el, cell):
    geo = cell.geometry_dict()
    return {
        "id": cell.id,
        "type": guess_type(cell.style),
        "label": dx.label_to_plain(cell.label),
        "x": geo["x"], "y": geo["y"],
        "width": geo["width"], "height": geo["height"],
        "parent": cell.parent,
        "layer": layer_mod.layer_of(root_el, cell),
    }


def _auto_position(root_el, parent_id):
    """Next free spot: below the lowest sibling, at the left margin."""
    bottom = 0
    for cell in iter_shapes(root_el):
        if cell.parent == parent_id:
            geo = cell.geometry_dict()
            bottom = max(bottom, geo["y"] + geo["height"])
    return 40, (bottom + 40 if bottom else 40)


def add_shape(mxfile, page, shape_type="rectangle", label=None, x=None, y=None,
              width=None, height=None, style=None, set_props=None, parent=None,
              layer=None, cell_id=None, html_label=False):
    """Add a shape to a page and return its summary."""
    root_el = proj.page_root(mxfile, page)
    if shape_type not in SHAPE_TYPES:
        raise ValueError(
            f"Unknown shape type '{shape_type}'. "
            f"Available: {', '.join(SHAPE_TYPES)}"
        )
    spec = SHAPE_TYPES[shape_type]
    final_style = style if style is not None else spec["style"]
    if style is not None:
        final_style = dx.format_style(dx.parse_style(style))
    final_style = dx.apply_style_changes(final_style, set_props)

    if parent is not None and layer is not None:
        raise ValueError("Use either --parent or --layer, not both.")
    if parent is not None:
        parent_id = require_shape(root_el, parent).id
    else:
        parent_id = layer_mod.resolve_layer(root_el, layer).id

    w = spec["width"] if width is None else width
    h = spec["height"] if height is None else height
    if w <= 0 or h <= 0:
        raise ValueError("Shape width and height must be positive.")
    if x is None or y is None:
        auto_x, auto_y = _auto_position(root_el, parent_id)
        x = auto_x if x is None else x
        y = auto_y if y is None else y

    new_id = (dx.validate_new_id(root_el, cell_id) if cell_id
              else dx.next_id(root_el, "s"))
    text = spec["label"] if label is None else label
    element = ET.Element("mxCell", {
        "id": new_id,
        "value": _encode_label(text, final_style, html_label),
        "style": final_style,
        "vertex": "1",
        "parent": parent_id,
    })
    ET.SubElement(element, "mxGeometry", {
        "x": dx.fmt_num(x), "y": dx.fmt_num(y),
        "width": dx.fmt_num(w), "height": dx.fmt_num(h),
        "as": "geometry",
    })
    root_el.append(element)
    summary = shape_summary(root_el, dx.Cell(element, element))
    summary["style"] = final_style
    return summary


def list_shapes(mxfile, page, layer=None, contains=None, shape_type=None):
    root_el = proj.page_root(mxfile, page)
    layer_id = layer_mod.resolve_layer(root_el, layer).id if layer else None
    result = []
    for cell in iter_shapes(root_el):
        summary = shape_summary(root_el, cell)
        if layer_id and summary["layer"] != layer_id:
            continue
        if shape_type and summary["type"] != shape_type:
            continue
        if contains and contains.lower() not in summary["label"].lower():
            continue
        result.append(summary)
    return result


def get_shape(mxfile, page, cell_id):
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    info = shape_summary(root_el, cell)
    info["raw_label"] = cell.label
    info["style"] = cell.style
    info["style_properties"] = dx.style_to_dict(cell.style)
    info["data"] = cell.data
    info["children"] = [c.id for c in dx.iter_cells(root_el)
                        if c.parent == cell.id]
    info["outgoing"] = [c.id for c in dx.iter_cells(root_el)
                        if c.is_edge and c.source == cell.id]
    info["incoming"] = [c.id for c in dx.iter_cells(root_el)
                        if c.is_edge and c.target == cell.id]
    return info


def set_label(mxfile, page, cell_id, text, html_label=False):
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    old = dx.label_to_plain(cell.label)
    cell.label = _encode_label(text, cell.style, html_label)
    return {"id": cell.id, "old_label": old,
            "label": dx.label_to_plain(cell.label)}


def _require_geometry(cell):
    geo = cell.geometry
    if geo is None:
        geo = ET.SubElement(cell.cell, "mxGeometry", {"as": "geometry"})
    return geo


def move_shape(mxfile, page, cell_id, x=None, y=None, dx_=None, dy=None):
    """Move to absolute ``x``/``y`` or by a relative ``dx_``/``dy`` offset."""
    if x is None and y is None and dx_ is None and dy is None:
        raise ValueError("Give --x/--y for an absolute move or --dx/--dy for an offset.")
    if (x is not None or y is not None) and (dx_ is not None or dy is not None):
        raise ValueError("Use either --x/--y or --dx/--dy, not both.")
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    geo = _require_geometry(cell)
    current = cell.geometry_dict()
    new_x = current["x"] + (dx_ or 0) if x is None else x
    new_y = current["y"] + (dy or 0) if y is None else y
    geo.set("x", dx.fmt_num(new_x))
    geo.set("y", dx.fmt_num(new_y))
    return {"id": cell.id, "old": {"x": current["x"], "y": current["y"]},
            "x": dx.to_num(new_x), "y": dx.to_num(new_y)}


def resize_shape(mxfile, page, cell_id, width=None, height=None):
    if width is None and height is None:
        raise ValueError("Give --width and/or --height.")
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    geo = _require_geometry(cell)
    current = cell.geometry_dict()
    new_w = current["width"] if width is None else width
    new_h = current["height"] if height is None else height
    if new_w <= 0 or new_h <= 0:
        raise ValueError("Shape width and height must be positive.")
    geo.set("width", dx.fmt_num(new_w))
    geo.set("height", dx.fmt_num(new_h))
    return {"id": cell.id,
            "old": {"width": current["width"], "height": current["height"]},
            "width": dx.to_num(new_w), "height": dx.to_num(new_h)}


def set_style(mxfile, page, cell_id, set_props=None, unset=None):
    if not set_props and not unset:
        raise ValueError("Nothing to change: give KEY=VALUE pairs and/or --unset KEY.")
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    old = cell.style
    cell.style = dx.apply_style_changes(old, set_props, unset)
    return {"id": cell.id, "old_style": old, "style": cell.style,
            "style_properties": dx.style_to_dict(cell.style)}


def set_data(mxfile, page, cell_id, set_props=None, unset=None):
    """Read or change custom properties (the GUI's "Edit Data" dialog)."""
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    for key in list(set_props or {}) + list(unset or []):
        if key in dx.RESERVED_DATA_KEYS or not _DATA_KEY.fullmatch(key):
            raise ValueError(
                f"Invalid data key '{key}': use letters, digits and '_' "
                f"(reserved: {', '.join(dx.RESERVED_DATA_KEYS)})."
            )
    if set_props or unset:
        if set_props:
            cell = dx.wrap_cell(root_el, cell)
        for key in unset or []:
            if cell.wrapped:
                cell.holder.attrib.pop(key, None)
        for key, value in (set_props or {}).items():
            cell.holder.set(key, value)
    return {"id": cell.id, "data": cell.data}


def remove_shape(mxfile, page, cell_id):
    """Remove a shape, its children and every connector attached to them."""
    root_el = proj.page_root(mxfile, page)
    cell = require_shape(root_el, cell_id)
    doomed = [cell.id] + layer_mod.descendants(root_el, cell.id)
    doomed_set = set(doomed)
    removed_connectors = []
    for other in dx.iter_cells(root_el):
        if other.is_edge and other.id not in doomed_set and (
                other.source in doomed_set or other.target in doomed_set):
            removed_connectors.append(other.id)
    for edge_id in removed_connectors:
        doomed_set.add(edge_id)
        doomed_set.update(layer_mod.descendants(root_el, edge_id))
    for other in list(dx.iter_cells(root_el)):
        if other.id in doomed_set:
            root_el.remove(other.holder)
    return {"removed": cell.id,
            "removed_children": doomed[1:],
            "removed_connectors": removed_connectors}
