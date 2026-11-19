"""Export pipeline: project -> temp .drawio -> real draw.io -> verified file.

Rendering is always done by the draw.io desktop app. This module only
validates options, prepares the input file and verifies what comes back.

The real CLI silently ignores options that do not apply to the chosen format
(e.g. ``--transparent`` for PDF). For an agent that is a trap, so invalid
combinations are rejected here with an explicit message.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
import zlib

from cli_anything.drawio.core import project as proj
from cli_anything.drawio.utils import drawio_backend as backend

FORMATS = {
    "png": {"extension": ".png", "description": "PNG raster image",
            "options": ["page", "layers", "scale", "width", "height",
                        "transparent", "embed-diagram"]},
    "jpg": {"extension": ".jpg", "description": "JPEG raster image",
            "options": ["page", "layers", "scale", "width", "height",
                        "quality"]},
    "svg": {"extension": ".svg", "description": "SVG vector image",
            "options": ["page", "layers", "scale", "embed-diagram",
                        "embed-svg-images", "svg-theme", "svg-links-target"]},
    "pdf": {"extension": ".pdf", "description": "PDF document",
            "options": ["page", "all-pages", "page-range", "layers", "scale",
                        "width", "height", "crop", "embed-diagram"]},
    "vsdx": {"extension": ".vsdx", "description": "Microsoft Visio drawing",
             "options": ["page", "layers"]},
    "xml": {"extension": ".xml", "description": "draw.io XML (uncompressed)",
            "options": []},
}
# Not wrapped on purpose: draw.io's --border and --uncompressed. Measured
# against 24.7.17 they change nothing (same pixels / same bytes with and
# without them), and an option that silently does nothing misleads agents.
ALIASES = {"jpeg": "jpg"}
SVG_THEMES = ("light", "dark")
SVG_LINK_TARGETS = ("auto", "new-win", "same-win")


def list_formats():
    return [{"format": name, **spec} for name, spec in FORMATS.items()]


def normalize_format(fmt=None, output=None):
    """Decide the export format from ``--format`` and/or the output name."""
    from_ext = None
    if output:
        ext = os.path.splitext(output)[1].lower().lstrip(".")
        ext = ALIASES.get(ext, ext)
        if ext in FORMATS:
            from_ext = ext
    if fmt:
        chosen = ALIASES.get(fmt.lower(), fmt.lower())
        if chosen not in FORMATS:
            raise ValueError(
                f"Unknown export format '{fmt}'. Available: {', '.join(FORMATS)}"
            )
        if from_ext and from_ext != chosen:
            raise ValueError(
                f"--format {chosen} conflicts with the output extension "
                f"'.{from_ext}'. Make them agree."
            )
        return chosen
    if from_ext:
        return from_ext
    raise ValueError(
        "Cannot tell the export format: give --format or an output name "
        f"ending in one of {', '.join(s['extension'] for s in FORMATS.values())}"
    )


def _only(fmt, allowed, option):
    if fmt not in allowed:
        raise ValueError(
            f"{option} only applies to {'/'.join(allowed)} export, not {fmt}."
        )


def build_flags(fmt, page_index=None, all_pages=False, page_range=None,
                layers=None, scale=None, width=None, height=None,
                transparent=False, crop=False, quality=None,
                embed_diagram=False, embed_svg_images=False, svg_theme=None,
                svg_links_target=None):
    """Validate export options and return draw.io command-line flags."""
    flags = []
    selectors = sum([page_index is not None, bool(all_pages),
                     page_range is not None])
    if selectors > 1:
        raise ValueError("Use only one of --page, --all-pages and --page-range.")
    if page_index is not None:
        if page_index < 0:
            raise ValueError("Page index must be 0 or greater.")
        flags += ["--page-index", str(page_index)]
    if all_pages:
        _only(fmt, ("pdf",), "--all-pages")
        flags.append("--all-pages")
    if page_range is not None:
        _only(fmt, ("pdf",), "--page-range")
        start, end = page_range
        if start < 0 or end < start:
            raise ValueError("Page range must be FROM..TO with 0 <= FROM <= TO.")
        flags += ["--page-range", f"{start}..{end}"]
    if layers:
        if any(i < 0 for i in layers):
            raise ValueError("Layer indexes must be 0 or greater.")
        flags += ["--layers", ",".join(str(i) for i in layers)]
    if scale is not None:
        _only(fmt, ("png", "jpg", "svg", "pdf"), "--scale")
        if scale <= 0:
            raise ValueError("--scale must be greater than 0.")
        flags += ["--scale", repr(float(scale))]
    for name, value in (("--width", width), ("--height", height)):
        if value is not None:
            _only(fmt, ("png", "jpg", "pdf"), name)
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0.")
            flags += [name, str(int(value))]
    if transparent:
        _only(fmt, ("png",), "--transparent")
        flags.append("--transparent")
    if crop:
        _only(fmt, ("pdf",), "--crop")
        flags.append("--crop")
    if quality is not None:
        _only(fmt, ("jpg",), "--quality")
        if not 1 <= quality <= 100:
            raise ValueError("--quality must be between 1 and 100.")
        flags += ["--quality", str(int(quality))]
    if embed_diagram:
        _only(fmt, ("png", "svg", "pdf"), "--embed-diagram")
        flags.append("--embed-diagram")
    if embed_svg_images:
        _only(fmt, ("svg",), "--embed-svg-images")
        flags.append("--embed-svg-images")
    if svg_theme is not None:
        _only(fmt, ("svg",), "--svg-theme")
        if svg_theme not in SVG_THEMES:
            raise ValueError(f"--svg-theme must be one of: {', '.join(SVG_THEMES)}")
        flags += ["--svg-theme", svg_theme]
    if svg_links_target is not None:
        _only(fmt, ("svg",), "--svg-links-target")
        if svg_links_target not in SVG_LINK_TARGETS:
            raise ValueError(
                f"--svg-links-target must be one of: {', '.join(SVG_LINK_TARGETS)}"
            )
        flags += ["--svg-links-target", svg_links_target]
    return flags


def parse_page_range(text):
    """Parse ``FROM..TO`` (the real CLI's syntax) into a tuple."""
    match = re.fullmatch(r"\s*(\d+)\s*\.\.\s*(\d+)\s*", text or "")
    if not match:
        raise ValueError(f"Page range must look like 0..2, got: {text!r}")
    return int(match.group(1)), int(match.group(2))


PNG_NO_IEND_WARNING = (
    "draw.io wrote this PNG without its closing IEND chunk (seen with "
    "--embed-diagram in 24.7.17). Browsers and draw.io open it; strict PNG "
    "decoders may reject it."
)


def _png_details(data):
    """Size, alpha, embedded diagram and completeness from the PNG chunks."""
    width, height = struct.unpack(">II", data[16:24])
    chunk_types, embedded, pos = [], False, 8
    while pos + 8 <= len(data):
        length, chunk_type = struct.unpack(">I4s", data[pos:pos + 8])
        chunk_types.append(chunk_type)
        if chunk_type in (b"zTXt", b"tEXt", b"iTXt"):
            keyword = data[pos + 8:pos + 8 + length].split(b"\x00", 1)[0]
            if keyword in (b"mxGraphModel", b"mxfile"):
                embedded = True
        pos += 12 + length
    return {
        "image_width": width,
        "image_height": height,
        "has_alpha": data[25] in (4, 6),
        "embedded_diagram": embedded,
        "png_complete": b"IEND" in chunk_types,
    }


def _count_pdf_pages(data):
    """Count page objects, including those inside compressed object streams.

    draw.io post-processes its PDFs with a library that stores the page
    dictionaries in ``/ObjStm`` streams, so a plain byte search finds none.
    """
    chunks = [data]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    pattern = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
    return sum(len(pattern.findall(chunk)) for chunk in chunks)


def verify_output(path, fmt):
    """Check that an exported file really is what it claims to be.

    Returns format-specific details; raises RuntimeError on a bad file.
    """
    if not os.path.isfile(path):
        raise RuntimeError(f"Export produced no file: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise RuntimeError(f"Export produced an empty file: {path}")
    with open(path, "rb") as f:
        data = f.read()
    details = {}

    def bad(expected):
        raise RuntimeError(
            f"Exported file is not valid {fmt.upper()} ({expected}); "
            f"first bytes: {data[:12]!r}"
        )

    if fmt == "png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
            bad("missing PNG signature")
        details.update(_png_details(data))
    elif fmt == "jpg":
        if not data.startswith(b"\xff\xd8\xff"):
            bad("missing JPEG SOI marker")
    elif fmt == "pdf":
        if not data.startswith(b"%PDF-"):
            bad("missing %PDF- header")
        pages = _count_pdf_pages(data)
        if pages:
            details["pdf_pages"] = pages
    elif fmt == "svg":
        if b"<svg" not in data[:2000]:
            bad("no <svg> element")
    elif fmt == "vsdx":
        if not data.startswith(b"PK\x03\x04"):
            bad("not a ZIP container")
    elif fmt == "xml":
        if b"<mxfile" not in data[:2000]:
            bad("no <mxfile> element")
    return details


def _deliver(tmp_out, output):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    shutil.copyfile(tmp_out, output)


def _run_export(input_path, output, fmt, flags, overwrite, timeout):
    output = os.path.abspath(output)
    if os.path.isdir(output):
        raise ValueError(f"Output path is a directory: {output}")
    if os.path.exists(output) and not overwrite:
        raise FileExistsError(
            f"Output file exists: {output}. Pass --overwrite to replace it."
        )
    with tempfile.TemporaryDirectory(prefix="cli-anything-drawio-") as tmp:
        tmp_out = os.path.join(tmp, "export" + FORMATS[fmt]["extension"])
        result = backend.export_file(input_path, tmp_out, fmt, flags,
                                     timeout=timeout)
        details = verify_output(tmp_out, fmt)
        _deliver(tmp_out, output)
    result["output"] = output
    result["file_size"] = os.path.getsize(output)
    result["verified"] = True
    result.update(details)
    if details.get("png_complete") is False:
        result["warnings"].append(PNG_NO_IEND_WARNING)
    return result


def render(mxfile, output, fmt=None, overwrite=False, timeout=120, **options):
    """Export a project to ``output`` with the real draw.io app.

    ``options`` are the keyword arguments of :func:`build_flags`.
    """
    fmt = normalize_format(fmt, output)
    page_count = len(proj.get_pages(mxfile))
    page_index = options.get("page_index")
    if page_index is not None and page_index >= page_count:
        raise ValueError(
            f"Page index {page_index} out of range "
            f"(project has {page_count} page(s))."
        )
    page_range = options.get("page_range")
    if page_range is not None and page_range[1] >= page_count:
        raise ValueError(
            f"Page range {page_range[0]}..{page_range[1]} exceeds the "
            f"project's {page_count} page(s)."
        )
    flags = build_flags(fmt, **options)
    with tempfile.TemporaryDirectory(prefix="cli-anything-drawio-src-") as tmp:
        source = os.path.join(tmp, "diagram.drawio")
        with open(source, "w", encoding="utf-8", newline="") as f:
            f.write(proj.to_xml(mxfile))
        result = _run_export(source, output, fmt, flags, overwrite, timeout)
    result["page_count"] = page_count
    return result


def convert_file(input_path, output, fmt=None, overwrite=False, timeout=120,
                 **options):
    """Convert any file draw.io can read (.drawio, .xml, .vsdx, .csv, .png)."""
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    fmt = normalize_format(fmt, output)
    flags = build_flags(fmt, **options)
    result = _run_export(os.path.abspath(input_path), output, fmt, flags,
                         overwrite, timeout)
    result["input"] = os.path.abspath(input_path)
    return result
