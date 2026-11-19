"""Project management: create, open, save and inspect ``.drawio`` files.

The project *is* the native draw.io file — there is no intermediate format.
A project is the parsed ``<mxfile>`` element; diagrams are always held
uncompressed in memory.
"""

from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from cli_anything.drawio.utils import drawio_xml as dx
from cli_anything.drawio.utils.fileio import locked_write_text

HARNESS_HOST = "cli-anything-drawio"
COMPAT_VERSION = "24.7.17"

# PageSetupDialog.getFormats (Editor.js) — width x height in px at 100 dpi.
PAGE_FORMATS = {
    "letter": (850, 1100, 'US-Letter (8.5" x 11")'),
    "legal": (850, 1400, 'US-Legal (8.5" x 14")'),
    "tabloid": (1100, 1700, 'US-Tabloid (11" x 17")'),
    "executive": (700, 1000, 'US-Executive (7" x 10")'),
    "a0": (3300, 4681, "A0 (841 mm x 1189 mm)"),
    "a1": (2339, 3300, "A1 (594 mm x 841 mm)"),
    "a2": (1654, 2336, "A2 (420 mm x 594 mm)"),
    "a3": (1169, 1654, "A3 (297 mm x 420 mm)"),
    "a4": (827, 1169, "A4 (210 mm x 297 mm)"),
    "a5": (583, 827, "A5 (148 mm x 210 mm)"),
    "a6": (413, 583, "A6 (105 mm x 148 mm)"),
    "a7": (291, 413, "A7 (74 mm x 105 mm)"),
    "b4": (980, 1390, "B4 (250 mm x 353 mm)"),
    "b5": (690, 980, "B5 (176 mm x 250 mm)"),
    "16-9": (900, 1600, "16:9 (1600 x 900, use --landscape)"),
    "16-10": (1200, 1920, "16:10 (1920 x 1200, use --landscape)"),
    "4-3": (1200, 1600, "4:3 (1600 x 1200, use --landscape)"),
}

# Editor.prototype.getGraphXml defaults.
MODEL_DEFAULTS = {
    "grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1",
    "connect": "1", "arrows": "1", "fold": "1", "page": "1",
    "pageScale": "1", "math": "0", "shadow": "0",
}


def list_presets():
    return [
        {"name": key, "width": w, "height": h, "title": title}
        for key, (w, h, title) in PAGE_FORMATS.items()
    ]


def resolve_page_size(page_format=None, width=None, height=None, landscape=False):
    """Return ``(width, height)`` for a preset or a custom size."""
    if (width is None) != (height is None):
        raise ValueError("Give both --width and --height for a custom page size.")
    if width is not None:
        if width <= 0 or height <= 0:
            raise ValueError("Page width and height must be positive.")
        size = (int(width), int(height))
    else:
        key = (page_format or "a4").lower()
        if key not in PAGE_FORMATS:
            raise ValueError(
                f"Unknown page format '{page_format}'. "
                f"Available: {', '.join(PAGE_FORMATS)}"
            )
        size = PAGE_FORMATS[key][:2]
    return (size[1], size[0]) if landscape else size


def _unique_page_id(mxfile):
    used = {d.get("id") for d in mxfile.findall("diagram")}
    n = 1
    while f"page-{n}" in used:
        n += 1
    return f"page-{n}"


def new_diagram(mxfile, name, width, height, grid=True, grid_size=10,
                background=None):
    """Build a ``<diagram>`` with an empty model and one default layer."""
    diagram = ET.Element("diagram", {"id": _unique_page_id(mxfile), "name": name})
    attrs = dict(MODEL_DEFAULTS)
    attrs["grid"] = "1" if grid else "0"
    attrs["gridSize"] = str(int(grid_size))
    attrs["pageWidth"] = str(int(width))
    attrs["pageHeight"] = str(int(height))
    if background:
        attrs["background"] = background
    model = ET.SubElement(diagram, "mxGraphModel", attrs)
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    return diagram


def create_project(name="Page-1", page_format="a4", width=None, height=None,
                   landscape=False, grid=True, grid_size=10, background=None):
    """Create a new blank project (an ``<mxfile>`` with one page)."""
    if not name or not name.strip():
        raise ValueError("Page name must not be empty.")
    w, h = resolve_page_size(page_format, width, height, landscape)
    mxfile = ET.Element("mxfile", {
        "host": HARNESS_HOST,
        "agent": f"{HARNESS_HOST}/1.0.0",
        "version": COMPAT_VERSION,
    })
    mxfile.append(new_diagram(mxfile, name, w, h, grid, grid_size, background))
    return mxfile


def _normalize(mxfile):
    """Make every diagram hold an uncompressed ``<mxGraphModel>`` child."""
    diagrams = mxfile.findall("diagram")
    if not diagrams:
        raise dx.DrawioFormatError("File contains no <diagram> pages.")
    for index, diagram in enumerate(diagrams):
        model = diagram.find("mxGraphModel")
        if model is None:
            text = (diagram.text or "").strip()
            if not text:
                raise dx.DrawioFormatError(f"Page {index} is empty.")
            model = dx.parse_xml(dx.decompress(text))
            if model.tag != "mxGraphModel":
                raise dx.DrawioFormatError(
                    f"Page {index}: expected <mxGraphModel>, found <{model.tag}>."
                )
            diagram.text = None
            diagram.append(model)
        if model.find("root") is None:
            raise dx.DrawioFormatError(f"Page {index} has no <root> element.")
        if not diagram.get("name"):
            diagram.set("name", f"Page-{index + 1}")
        if not diagram.get("id"):
            diagram.set("id", _unique_page_id(mxfile))
    return mxfile


def load_project(data):
    """Parse draw.io XML (text or bytes) into a normalised ``<mxfile>``."""
    element = dx.parse_xml(data)
    if element.tag == "mxGraphModel":
        mxfile = ET.Element("mxfile", {"host": HARNESS_HOST,
                                       "version": COMPAT_VERSION})
        diagram = ET.SubElement(mxfile, "diagram",
                                {"id": "page-1", "name": "Page-1"})
        diagram.append(element)
        element = mxfile
    if element.tag != "mxfile":
        raise dx.DrawioFormatError(
            f"Not a draw.io file: root element is <{element.tag}>, "
            "expected <mxfile>."
        )
    return _normalize(element)


def open_project(path):
    """Open a ``.drawio`` file."""
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Project file not found: {path}. "
            f"Create it with: cli-anything-drawio project new -o {path}"
        )
    with open(path, "rb") as f:
        data = f.read()
    try:
        return load_project(data)
    except dx.DrawioFormatError as exc:
        raise dx.DrawioFormatError(f"{path}: {exc}") from exc


def to_xml(mxfile, compressed=False, page=None):
    """Serialise the project. ``page`` limits output to one page index."""
    tree = copy.deepcopy(mxfile)
    diagrams = tree.findall("diagram")
    if page is not None:
        for index, diagram in enumerate(diagrams):
            if index != page:
                tree.remove(diagram)
        diagrams = tree.findall("diagram")
    if len(diagrams) > 1:
        tree.set("pages", str(len(diagrams)))
    else:
        tree.attrib.pop("pages", None)
    if compressed:
        for diagram in diagrams:
            model = diagram.find("mxGraphModel")
            payload = dx.compress(dx.serialize(model, pretty=False))
            diagram.remove(model)
            diagram.text = payload
        return dx.serialize(tree, pretty=False)
    return dx.serialize(tree, pretty=True) + "\n"


def save_project(mxfile, path, compressed=False):
    """Write the project to ``path`` (locked write). Returns the abs path."""
    mxfile.set(
        "modified",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )
    locked_write_text(path, to_xml(mxfile, compressed=compressed))
    return os.path.abspath(path)


# ── Page access ──────────────────────────────────────────────────────


def get_pages(mxfile):
    return mxfile.findall("diagram")


def get_page(mxfile, index):
    pages = get_pages(mxfile)
    if not 0 <= index < len(pages):
        raise ValueError(
            f"Page index {index} out of range (project has {len(pages)} page(s))."
        )
    return pages[index]


def get_model(diagram):
    return diagram.find("mxGraphModel")


def page_root(mxfile, index):
    """The ``<root>`` element (cell container) of a page."""
    return get_model(get_page(mxfile, index)).find("root")


# ── Introspection ────────────────────────────────────────────────────


def root_cell_id(root_el):
    """Id of the root cell (the one cell without a parent)."""
    for cell in dx.iter_cells(root_el):
        if cell.parent is None:
            return cell.id
    return None


def count_cells(root_el):
    root_id = root_cell_id(root_el)
    edge_ids = {c.id for c in dx.iter_cells(root_el) if c.is_edge}
    counts = {"layers": 0, "shapes": 0, "connectors": 0}
    for cell in dx.iter_cells(root_el):
        if cell.parent is None:
            continue
        if cell.parent == root_id:
            counts["layers"] += 1
        elif cell.is_edge:
            counts["connectors"] += 1
        elif cell.is_vertex and cell.parent not in edge_ids:
            counts["shapes"] += 1
    return counts


def page_summary(mxfile, index):
    diagram = get_page(mxfile, index)
    model = get_model(diagram)
    counts = count_cells(model.find("root"))
    return {
        "index": index,
        "id": diagram.get("id"),
        "name": diagram.get("name"),
        "width": dx.to_num(model.get("pageWidth"), 0),
        "height": dx.to_num(model.get("pageHeight"), 0),
        "background": model.get("background"),
        "grid": model.get("grid", "1") == "1",
        **counts,
    }


def project_info(mxfile, path=None):
    pages = [page_summary(mxfile, i) for i in range(len(get_pages(mxfile)))]
    info = {
        "path": os.path.abspath(path) if path else None,
        "host": mxfile.get("host"),
        "version": mxfile.get("version"),
        "modified": mxfile.get("modified"),
        "page_count": len(pages),
        "shape_count": sum(p["shapes"] for p in pages),
        "connector_count": sum(p["connectors"] for p in pages),
        "pages": pages,
    }
    if path and os.path.isfile(path):
        info["file_size"] = os.path.getsize(path)
    return info


def check_project(mxfile):
    """Structural validation: duplicate ids, dangling references, layers."""
    issues = []

    def add(page, severity, cell, message):
        issues.append({"page": page, "severity": severity, "cell": cell,
                       "message": message})

    for index, diagram in enumerate(get_pages(mxfile)):
        root_el = get_model(diagram).find("root")
        cells = list(dx.iter_cells(root_el))
        ids = [c.id for c in cells]
        seen = set()
        for cell_id in ids:
            if cell_id is None:
                add(index, "error", None, "Cell without an id.")
            elif cell_id in seen:
                add(index, "error", cell_id, "Duplicate cell id.")
            seen.add(cell_id)
        roots = [c for c in cells if c.parent is None]
        if len(roots) != 1:
            add(index, "error", None,
                f"Expected exactly one root cell, found {len(roots)}.")
        root_id = roots[0].id if roots else None
        if not any(c.parent == root_id for c in cells if c.parent is not None):
            add(index, "error", None, "Page has no layer.")
        for cell in cells:
            if cell.parent is not None and cell.parent not in seen:
                add(index, "error", cell.id,
                    f"Parent '{cell.parent}' does not exist.")
            if cell.is_edge:
                for end in ("source", "target"):
                    ref = getattr(cell, end)
                    if ref is None:
                        add(index, "warning", cell.id,
                            f"Connector has no {end} (floating end).")
                    elif ref not in seen:
                        add(index, "error", cell.id,
                            f"Connector {end} '{ref}' does not exist.")
            if cell.is_vertex and cell.geometry is None:
                add(index, "warning", cell.id, "Shape has no geometry.")
    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = len(issues) - errors
    return {"ok": errors == 0, "errors": errors, "warnings": warnings,
            "issues": issues}
