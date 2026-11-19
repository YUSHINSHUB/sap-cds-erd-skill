"""Page operations: list, add, remove, rename, duplicate, move, setup."""

from __future__ import annotations

import copy

from cli_anything.drawio.core import project as proj
from cli_anything.drawio.utils import drawio_xml as dx


def list_pages(mxfile):
    return [proj.page_summary(mxfile, i)
            for i in range(len(proj.get_pages(mxfile)))]


def resolve_page(mxfile, spec):
    """Resolve a page spec (index, name or id) to an index.

    ``None`` means the first page. An all-digit spec is an index.
    """
    pages = proj.get_pages(mxfile)
    if spec is None:
        return 0
    if isinstance(spec, int) or str(spec).lstrip("-").isdigit():
        index = int(spec)
        if not 0 <= index < len(pages):
            raise ValueError(
                f"Page index {index} out of range "
                f"(project has {len(pages)} page(s), indexes start at 0)."
            )
        return index
    spec = str(spec)
    by_name = [i for i, p in enumerate(pages) if p.get("name") == spec]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise ValueError(
            f"Page name '{spec}' is ambiguous ({len(by_name)} pages); "
            "use the page index."
        )
    by_id = [i for i, p in enumerate(pages) if p.get("id") == spec]
    if by_id:
        return by_id[0]
    names = ", ".join(f"{i}:{p.get('name')}" for i, p in enumerate(pages))
    raise ValueError(f"Page '{spec}' not found. Pages: {names}")


def add_page(mxfile, name=None, page_format=None, width=None, height=None,
             landscape=False, background=None, index=None):
    """Add a page. Without a size it inherits the last page's size."""
    pages = proj.get_pages(mxfile)
    if name is None:
        n = len(pages) + 1
        existing = {p.get("name") for p in pages}
        while f"Page-{n}" in existing:
            n += 1
        name = f"Page-{n}"
    if not name.strip():
        raise ValueError("Page name must not be empty.")
    last_model = proj.get_model(pages[-1])
    if page_format is None and width is None and height is None:
        w = int(dx.to_num(last_model.get("pageWidth"), 827))
        h = int(dx.to_num(last_model.get("pageHeight"), 1169))
        if landscape and h > w:
            w, h = h, w
    else:
        w, h = proj.resolve_page_size(page_format, width, height, landscape)
    diagram = proj.new_diagram(
        mxfile, name, w, h,
        grid=last_model.get("grid", "1") == "1",
        grid_size=int(dx.to_num(last_model.get("gridSize"), 10)),
        background=background,
    )
    if index is None:
        index = len(pages)
    if not 0 <= index <= len(pages):
        raise ValueError(f"Insert position {index} out of range (0..{len(pages)}).")
    _insert_page(mxfile, diagram, index)
    return proj.page_summary(mxfile, index)


def _insert_page(mxfile, diagram, index):
    pages = proj.get_pages(mxfile)
    if index >= len(pages):
        mxfile.append(diagram)
    else:
        position = list(mxfile).index(pages[index])
        mxfile.insert(position, diagram)


def remove_page(mxfile, spec):
    index = resolve_page(mxfile, spec)
    pages = proj.get_pages(mxfile)
    if len(pages) == 1:
        raise ValueError("Cannot remove the only page of a project.")
    summary = proj.page_summary(mxfile, index)
    mxfile.remove(pages[index])
    return {"removed": summary, "page_count": len(pages) - 1}


def rename_page(mxfile, spec, name):
    if not name or not name.strip():
        raise ValueError("Page name must not be empty.")
    index = resolve_page(mxfile, spec)
    diagram = proj.get_page(mxfile, index)
    old = diagram.get("name")
    diagram.set("name", name)
    return {"index": index, "old_name": old, "name": name}


def duplicate_page(mxfile, spec, name=None):
    index = resolve_page(mxfile, spec)
    source = proj.get_page(mxfile, index)
    clone = copy.deepcopy(source)
    clone.set("id", proj._unique_page_id(mxfile))
    clone.set("name", name or f"{source.get('name')} Copy")
    _insert_page(mxfile, clone, index + 1)
    return proj.page_summary(mxfile, index + 1)


def move_page(mxfile, spec, to_index):
    index = resolve_page(mxfile, spec)
    pages = proj.get_pages(mxfile)
    if not 0 <= to_index < len(pages):
        raise ValueError(
            f"Target position {to_index} out of range (0..{len(pages) - 1})."
        )
    diagram = pages[index]
    mxfile.remove(diagram)
    _insert_page(mxfile, diagram, to_index)
    return {"name": diagram.get("name"), "from": index, "to": to_index}


def setup_page(mxfile, spec, page_format=None, width=None, height=None,
               landscape=None, background=None, grid=None, grid_size=None,
               math=None, shadow=None):
    """Change page size, background, grid, math typesetting or shadows."""
    index = resolve_page(mxfile, spec)
    model = proj.get_model(proj.get_page(mxfile, index))
    if page_format is not None or width is not None or height is not None:
        w, h = proj.resolve_page_size(page_format, width, height,
                                      bool(landscape))
        model.set("pageWidth", str(w))
        model.set("pageHeight", str(h))
    elif landscape is not None:
        w = int(dx.to_num(model.get("pageWidth"), 827))
        h = int(dx.to_num(model.get("pageHeight"), 1169))
        if (landscape and h > w) or (not landscape and w > h):
            w, h = h, w
        model.set("pageWidth", str(w))
        model.set("pageHeight", str(h))
    if background is not None:
        if background.lower() in ("none", ""):
            model.attrib.pop("background", None)
        else:
            model.set("background", background)
    if grid is not None:
        model.set("grid", "1" if grid else "0")
    if grid_size is not None:
        if grid_size <= 0:
            raise ValueError("Grid size must be positive.")
        model.set("gridSize", str(int(grid_size)))
    if math is not None:
        model.set("math", "1" if math else "0")
    if shadow is not None:
        model.set("shadow", "1" if shadow else "0")
    summary = proj.page_summary(mxfile, index)
    summary["grid_size"] = dx.to_num(model.get("gridSize"), 10)
    summary["math"] = model.get("math", "0") == "1"
    summary["shadow"] = model.get("shadow", "0") == "1"
    return summary
