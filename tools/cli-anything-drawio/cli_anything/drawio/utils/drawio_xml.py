"""Low-level helpers for draw.io's native mxGraph XML format.

A ``.drawio`` file is an ``<mxfile>`` element holding one ``<diagram>`` per
page. Each diagram holds an ``<mxGraphModel><root>...</root></mxGraphModel>``
tree, either as child elements (uncompressed) or as
``base64(deflateRaw(encodeURIComponent(xml)))`` text (compressed — see
``Graph.compress`` in the draw.io source, Graph.js).
"""

from __future__ import annotations

import base64
import binascii
import copy
import html
import re
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import quote, unquote

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

# Characters JavaScript's encodeURIComponent leaves unescaped (besides
# letters and digits).
_URI_SAFE = "-_.!~*'()"

WRAPPER_TAGS = ("object", "UserObject")
RESERVED_DATA_KEYS = ("id", "label", "placeholders")


class DrawioFormatError(ValueError):
    """Raised when data is not a valid draw.io document."""


# ── XML parsing / serialisation ──────────────────────────────────────


def parse_xml(data):
    """Parse XML text or bytes safely (entity expansion is rejected)."""
    try:
        return SafeET.fromstring(data)
    except ET.ParseError as exc:
        raise DrawioFormatError(f"Not well-formed XML: {exc}") from exc
    except DefusedXmlException as exc:
        raise DrawioFormatError(
            f"Unsafe XML rejected ({type(exc).__name__})"
        ) from exc


def serialize(element, pretty=True):
    """Serialise an element to a unicode string."""
    if pretty:
        element = copy.deepcopy(element)
        ET.indent(element, space="  ")
    return ET.tostring(element, encoding="unicode")


# ── Diagram compression (Graph.compress / Graph.decompress) ──────────


def compress(xml_text):
    """Return base64(deflateRaw(encodeURIComponent(xml_text)))."""
    encoded = quote(xml_text, safe=_URI_SAFE).encode("ascii")
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = deflater.compress(encoded) + deflater.flush()
    return base64.b64encode(raw).decode("ascii")


def decompress(data):
    """Inverse of :func:`compress`."""
    try:
        raw = base64.b64decode(data.strip(), validate=False)
        inflated = zlib.decompress(raw, -15).decode("utf-8")
    except (binascii.Error, zlib.error, UnicodeDecodeError) as exc:
        raise DrawioFormatError(
            f"Diagram content is neither XML nor compressed XML: {exc}"
        ) from exc
    return unquote(inflated)


# ── Style strings ────────────────────────────────────────────────────


def parse_style(style):
    """Parse ``a=1;ellipse;b=2;`` into ``[("a", "1"), ("ellipse", None), ...]``."""
    items = []
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            items.append((key.strip(), value))
        else:
            items.append((part, None))
    return items


def format_style(items):
    """Inverse of :func:`parse_style`."""
    parts = [key if value is None else f"{key}={value}" for key, value in items]
    return ";".join(parts) + (";" if parts else "")


def style_to_dict(style):
    """Style as a dict; bare tokens map to ``True``."""
    return {k: (True if v is None else v) for k, v in parse_style(style)}


def style_get(style, key, default=None):
    for k, v in parse_style(style):
        if k == key:
            return True if v is None else v
    return default


def _check_style_part(key, value):
    if not key or any(c in key for c in ";=") or key != key.strip():
        raise ValueError(f"Invalid style key: {key!r}")
    if value is not None and ";" in str(value):
        raise ValueError(f"Style value for '{key}' must not contain ';': {value!r}")


def style_set(style, key, value):
    """Set ``key`` to ``value`` (``None`` makes it a bare token)."""
    value = None if value is None else str(value)
    _check_style_part(key, value)
    items = parse_style(style)
    for i, (k, _) in enumerate(items):
        if k == key:
            items[i] = (key, value)
            break
    else:
        items.append((key, value))
    return format_style(items)


def style_unset(style, key):
    return format_style([(k, v) for k, v in parse_style(style) if k != key])


def apply_style_changes(style, set_items=None, unset=None):
    for key in unset or []:
        style = style_unset(style, key)
    for key, value in (set_items or {}).items():
        style = style_set(style, key, value)
    return style


def parse_assignments(pairs):
    """Turn ``["fillColor=#fff", "rounded=1"]`` into a dict."""
    result = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Expected KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Expected KEY=VALUE, got: {pair!r}")
        result[key] = value
    return result


# ── Labels ───────────────────────────────────────────────────────────


def plain_to_label(text):
    """Escape plain text for an ``html=1`` label (newlines become <br>)."""
    return html.escape(text or "", quote=False).replace("\n", "<br>")


def label_to_plain(label):
    """Best-effort plain text of an HTML label."""
    text = re.sub(r"<br\s*/?>", "\n", label or "", flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# ── Numbers ──────────────────────────────────────────────────────────


def fmt_num(value):
    """Format a number the way draw.io writes it (no trailing ``.0``)."""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return repr(round(number, 4))


def to_num(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if number == int(number) else number


# ── Cells ────────────────────────────────────────────────────────────


class Cell:
    """A graph cell: an ``<mxCell>``, optionally wrapped in ``<object>``.

    ``holder`` is the element that sits directly under ``<root>`` and carries
    the id; ``cell`` is the ``<mxCell>`` carrying style, parent and geometry.
    They are the same element for unwrapped cells.
    """

    __slots__ = ("holder", "cell")

    def __init__(self, holder, cell):
        self.holder = holder
        self.cell = cell

    @property
    def wrapped(self):
        return self.holder is not self.cell

    @property
    def id(self):
        return self.holder.get("id")

    @property
    def label(self):
        if self.wrapped:
            return self.holder.get("label", "")
        return self.cell.get("value", "")

    @label.setter
    def label(self, value):
        if self.wrapped:
            self.holder.set("label", value)
        else:
            self.cell.set("value", value)

    @property
    def style(self):
        return self.cell.get("style", "")

    @style.setter
    def style(self, value):
        self.cell.set("style", value)

    @property
    def parent(self):
        return self.cell.get("parent")

    @parent.setter
    def parent(self, value):
        self.cell.set("parent", value)

    @property
    def is_vertex(self):
        return self.cell.get("vertex") == "1"

    @property
    def is_edge(self):
        return self.cell.get("edge") == "1"

    @property
    def source(self):
        return self.cell.get("source")

    @property
    def target(self):
        return self.cell.get("target")

    @property
    def geometry(self):
        return self.cell.find("mxGeometry")

    def geometry_dict(self):
        geo = self.geometry
        if geo is None:
            return {"x": 0, "y": 0, "width": 0, "height": 0}
        return {
            "x": to_num(geo.get("x")),
            "y": to_num(geo.get("y")),
            "width": to_num(geo.get("width")),
            "height": to_num(geo.get("height")),
        }

    @property
    def data(self):
        if not self.wrapped:
            return {}
        return {
            k: v for k, v in self.holder.attrib.items()
            if k not in RESERVED_DATA_KEYS
        }


def iter_cells(root_el):
    """Yield every :class:`Cell` under a ``<root>`` element, in order."""
    for child in root_el:
        if child.tag == "mxCell":
            yield Cell(child, child)
        elif child.tag in WRAPPER_TAGS:
            inner = child.find("mxCell")
            if inner is not None:
                yield Cell(child, inner)


def find_cell(root_el, cell_id):
    for cell in iter_cells(root_el):
        if cell.id == cell_id:
            return cell
    return None


def all_ids(root_el):
    return [cell.id for cell in iter_cells(root_el)]


def next_id(root_el, prefix):
    """First unused id of the form ``<prefix><n>`` (n counts from 1)."""
    used = set(all_ids(root_el))
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def validate_new_id(root_el, cell_id):
    if not cell_id or not re.fullmatch(r"[A-Za-z0-9_.:\-]+", cell_id):
        raise ValueError(
            f"Invalid id {cell_id!r}: use letters, digits, '_', '-', '.' or ':'."
        )
    if cell_id in set(all_ids(root_el)):
        raise ValueError(f"Id already in use on this page: {cell_id}")
    return cell_id


def wrap_cell(root_el, cell):
    """Wrap an ``<mxCell>`` in ``<object>`` so it can carry custom data."""
    if cell.wrapped:
        return cell
    inner = cell.cell
    index = list(root_el).index(inner)
    holder = ET.Element("object")
    holder.set("label", inner.attrib.pop("value", ""))
    holder.set("id", inner.attrib.pop("id"))
    holder.tail = inner.tail
    inner.tail = None
    root_el.remove(inner)
    holder.append(inner)
    root_el.insert(index, holder)
    return Cell(holder, inner)
