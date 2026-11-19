"""Layer operations.

In the mxGraph model a layer is any cell whose parent is the root cell.
Visibility is the ``visible="0"`` attribute; locking is the style key
``locked=1``. Layer order (and the indexes draw.io's ``--layers`` export
option uses) is document order.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from cli_anything.drawio.core import project as proj
from cli_anything.drawio.utils import drawio_xml as dx


def get_layers(root_el):
    root_id = proj.root_cell_id(root_el)
    return [c for c in dx.iter_cells(root_el)
            if c.parent is not None and c.parent == root_id]


def descendants(root_el, cell_id):
    """Ids of all cells below ``cell_id`` (children, grandchildren, ...)."""
    children = {}
    for cell in dx.iter_cells(root_el):
        children.setdefault(cell.parent, []).append(cell.id)
    result = []
    stack = list(children.get(cell_id, []))
    while stack:
        current = stack.pop(0)
        result.append(current)
        stack.extend(children.get(current, []))
    return result


def layer_of(root_el, cell):
    """Id of the layer a cell ultimately belongs to."""
    layer_ids = {layer.id for layer in get_layers(root_el)}
    current = cell
    hops = 0
    while current is not None and hops < 1000:
        if current.id in layer_ids:
            return current.id
        current = dx.find_cell(root_el, current.parent) if current.parent else None
        hops += 1
    return None


def _layer_summary(root_el, layer, index):
    return {
        "index": index,
        "id": layer.id,
        "name": layer.label or ("Background" if index == 0 else ""),
        "visible": layer.cell.get("visible", "1") != "0",
        "locked": dx.style_get(layer.style, "locked") == "1",
        "cells": len(descendants(root_el, layer.id)),
    }


def list_layers(mxfile, page):
    root_el = proj.page_root(mxfile, page)
    return [_layer_summary(root_el, layer, i)
            for i, layer in enumerate(get_layers(root_el))]


def resolve_layer(root_el, spec):
    """Resolve a layer spec (id or name). ``None`` is the first layer."""
    layers = get_layers(root_el)
    if not layers:
        raise ValueError("Page has no layers.")
    if spec is None:
        return layers[0]
    spec = str(spec)
    for layer in layers:
        if layer.id == spec:
            return layer
    by_name = [layer for layer in layers if layer.label == spec]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise ValueError(f"Layer name '{spec}' is ambiguous; use the layer id.")
    available = ", ".join(
        f"{layer.id}" + (f" ({layer.label})" if layer.label else "")
        for layer in layers
    )
    raise ValueError(f"Layer '{spec}' not found. Layers: {available}")


def layer_index(root_el, spec):
    layer = resolve_layer(root_el, spec)
    return [l.id for l in get_layers(root_el)].index(layer.id)


def add_layer(mxfile, page, name, cell_id=None):
    if not name or not name.strip():
        raise ValueError("Layer name must not be empty.")
    root_el = proj.page_root(mxfile, page)
    root_id = proj.root_cell_id(root_el)
    if root_id is None:
        raise ValueError("Page has no root cell.")
    new_id = (dx.validate_new_id(root_el, cell_id) if cell_id
              else dx.next_id(root_el, "layer"))
    element = ET.Element("mxCell", {"id": new_id, "value": name,
                                    "parent": root_id})
    root_el.append(element)
    layers = get_layers(root_el)
    return _layer_summary(root_el, layers[-1], len(layers) - 1)


def remove_layer(mxfile, page, spec):
    root_el = proj.page_root(mxfile, page)
    layers = get_layers(root_el)
    if len(layers) == 1:
        raise ValueError("Cannot remove the only layer of a page.")
    layer = resolve_layer(root_el, spec)
    doomed = set(descendants(root_el, layer.id)) | {layer.id}
    # Connectors on other layers that point at removed shapes go too.
    for cell in dx.iter_cells(root_el):
        if cell.is_edge and (cell.source in doomed or cell.target in doomed):
            doomed.add(cell.id)
            doomed.update(descendants(root_el, cell.id))
    for cell in list(dx.iter_cells(root_el)):
        if cell.id in doomed:
            root_el.remove(cell.holder)
    return {"removed": layer.id, "name": layer.label,
            "removed_cells": len(doomed) - 1}


def rename_layer(mxfile, page, spec, name):
    if not name or not name.strip():
        raise ValueError("Layer name must not be empty.")
    root_el = proj.page_root(mxfile, page)
    layer = resolve_layer(root_el, spec)
    old = layer.label
    layer.label = name
    return {"id": layer.id, "old_name": old, "name": name}


def set_visible(mxfile, page, spec, visible):
    root_el = proj.page_root(mxfile, page)
    layer = resolve_layer(root_el, spec)
    if visible:
        layer.cell.attrib.pop("visible", None)
    else:
        layer.cell.set("visible", "0")
    return _layer_summary(root_el, layer, layer_index(root_el, layer.id))


def set_locked(mxfile, page, spec, locked):
    root_el = proj.page_root(mxfile, page)
    layer = resolve_layer(root_el, spec)
    if locked:
        layer.style = dx.style_set(layer.style, "locked", "1")
    else:
        style = dx.style_unset(layer.style, "locked")
        if style:
            layer.style = style
        else:
            layer.cell.attrib.pop("style", None)
    return _layer_summary(root_el, layer, layer_index(root_el, layer.id))


def assign_cells(mxfile, page, spec, cell_ids):
    """Move top-level cells (shapes or connectors) to another layer."""
    root_el = proj.page_root(mxfile, page)
    layer = resolve_layer(root_el, spec)
    layer_ids = {l.id for l in get_layers(root_el)}
    moved = []
    cells = []
    for cell_id in cell_ids:
        cell = dx.find_cell(root_el, cell_id)
        if cell is None:
            raise ValueError(f"Cell '{cell_id}' not found on this page.")
        if cell.id in layer_ids or cell.parent is None:
            raise ValueError(f"'{cell_id}' is a layer, not a shape or connector.")
        if cell.parent not in layer_ids:
            raise ValueError(
                f"'{cell_id}' is nested inside '{cell.parent}'; "
                "assign its top-level container instead."
            )
        cells.append(cell)
    for cell in cells:
        cell.parent = layer.id
        moved.append(cell.id)
    return {"layer": layer.id, "moved": moved}
