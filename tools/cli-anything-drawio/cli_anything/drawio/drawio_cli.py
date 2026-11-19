#!/usr/bin/env python3
"""cli-anything-drawio — agent-native command line for the draw.io desktop app.

One-shot:   cli-anything-drawio --json --project flow.drawio shape add -l "Start"
REPL:       cli-anything-drawio            (default when no subcommand is given)

The project file is a native .drawio file. One-shot mutations are saved
automatically; pass --dry-run to try a command without saving. Rendering is
always done by the real draw.io app (see utils/drawio_backend.py).
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import sys

import click

from cli_anything.drawio import __version__
from cli_anything.drawio.core import connectors as conn_mod
from cli_anything.drawio.core import export as export_mod
from cli_anything.drawio.core import layers as layer_mod
from cli_anything.drawio.core import pages as page_mod
from cli_anything.drawio.core import project as proj_mod
from cli_anything.drawio.core import shapes as shape_mod
from cli_anything.drawio.core.session import Session, state_dir
from cli_anything.drawio.utils import drawio_backend as backend
from cli_anything.drawio.utils import drawio_xml as dx

_session = None
_repl_mode = False
_repl_json = False

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


# ── Session and context helpers ──────────────────────────────────────


def get_session():
    global _session
    if _session is None:
        _session = Session()
    return _session


def reset_session():
    """Drop all in-memory state (used by tests)."""
    global _session, _repl_mode, _repl_json
    _session = None
    _repl_mode = False
    _repl_json = False


def _root_obj():
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return {}
    return ctx.find_root().obj or {}


def _use_json():
    return bool(_root_obj().get("json"))


def _dry_run():
    return bool(_root_obj().get("dry_run"))


def _project():
    """The session with a project loaded (loads --project on first use)."""
    sess = get_session()
    if not sess.has_project():
        path = _root_obj().get("project_path")
        if not path:
            if _repl_mode:
                raise ValueError(
                    "No project open. Use: project open FILE.drawio  or  project new"
                )
            raise ValueError(
                "No project loaded. Pass --project FILE.drawio "
                "(create one with: project new -o FILE.drawio)."
            )
        sess.set_project(proj_mod.open_project(path), path)
    return sess


def _page(sess, spec):
    if spec is None:
        return sess.current_page
    return page_mod.resolve_page(sess.root, spec)


def _commit(sess, result):
    """Auto-save after a one-shot mutation unless --dry-run was given."""
    saved = False
    if not _repl_mode and not _dry_run() and sess.project_path:
        sess.save_session()
        saved = True
    if isinstance(result, dict):
        result = dict(result)
        result["saved"] = saved
        if _dry_run():
            result["dry_run"] = True
    return result


def _mutate(label, operation):
    """Run ``operation(sess)`` in a transaction (rolled back on failure)."""
    sess = _project()
    with sess.transaction(label):
        result = operation(sess)
    return _commit(sess, result)


def _style_props(assignments, fill=None, stroke=None, font_color=None,
                 font_size=None):
    props = {}
    if fill is not None:
        props["fillColor"] = fill
    if stroke is not None:
        props["strokeColor"] = stroke
    if font_color is not None:
        props["fontColor"] = font_color
    if font_size is not None:
        props["fontSize"] = str(font_size)
    props.update(dx.parse_assignments(assignments))
    return props


# ── Output ───────────────────────────────────────────────────────────


def _scalar(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _clip(text, limit=48):
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _print_table(rows, indent=0):
    pad = "  " * indent
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    cells = [[_clip(_scalar(row.get(c))) for c in columns] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells])
              for i, c in enumerate(columns)]
    click.echo(pad + "  ".join(c.ljust(w) for c, w in zip(columns, widths)).rstrip())
    click.echo(pad + "  ".join("-" * w for w in widths))
    for row in cells:
        click.echo(pad + "  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())


def _print_human(data, indent=0):
    pad = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and all(
                    isinstance(i, dict) for i in value):
                click.echo(f"{pad}{key}:")
                _print_table(value, indent + 1)
            elif isinstance(value, (dict, list)) and value:
                click.echo(f"{pad}{key}:")
                _print_human(value, indent + 1)
            else:
                click.echo(f"{pad}{key}: {_scalar(value)}")
    elif isinstance(data, list):
        if data and all(isinstance(i, dict) for i in data):
            _print_table(data, indent)
        else:
            for item in data:
                click.echo(f"{pad}- {_scalar(item)}")
    else:
        click.echo(f"{pad}{_scalar(data)}")


def emit(data):
    """Print a command result as JSON (--json) or human-readable text."""
    if _use_json():
        click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        _print_human(data)


def _fail(exc):
    message = str(exc)
    if isinstance(exc, KeyError):
        message = message.strip("'\"")
    if _use_json():
        click.echo(json.dumps({"error": message, "type": type(exc).__name__},
                              ensure_ascii=False))
    else:
        click.echo(f"Error: {message}", err=True)
    if not _repl_mode:
        sys.exit(1)


def handle_error(func):
    """Turn exceptions into a clear message (JSON-aware) and exit code 1."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (click.ClickException, click.exceptions.Exit, click.Abort):
            raise
        except Exception as exc:  # noqa: BLE001 — every failure must be reported
            _fail(exc)
            return None

    return wrapper


page_option = click.option(
    "--page", "-P", "page_spec", default=None,
    help="Page index (from 0) or page name. Default: the current page.")


# ── Root group ───────────────────────────────────────────────────────


@click.group(invoke_without_command=True, context_settings=CONTEXT_SETTINGS)
@click.option("--json", "use_json", is_flag=True,
              help="Output machine-readable JSON.")
@click.option("--project", "-p", "project_path",
              type=click.Path(dir_okay=False), default=None,
              help="Path to the .drawio project file to work on.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Run the command without saving changes to disk.")
@click.version_option(__version__, "--version", "-V",
                      prog_name="cli-anything-drawio")
@click.pass_context
def cli(ctx, use_json, project_path, dry_run):
    """Create, edit and export draw.io diagrams from the command line.

    Without a subcommand an interactive REPL starts.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json or (_repl_mode and _repl_json)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["project_path"] = None if _repl_mode else project_path
    if _repl_mode and project_path:
        raise click.UsageError("Inside the REPL use: project open FILE.drawio")
    if ctx.invoked_subcommand is None and not _repl_mode:
        ctx.invoke(repl)


@cli.result_callback()
def auto_save_on_exit(result, use_json, project_path, dry_run, **kwargs):
    """Auto-save project after one-shot commands if state was modified."""
    if _repl_mode:
        return
    if dry_run:
        return
    sess = get_session()
    if sess.has_project() and sess._modified and sess.project_path:
        try:
            sess.save_session()
        except Exception as e:  # noqa: BLE001
            click.echo(f"Error: Auto-save failed: {e}", err=True)
            sys.exit(1)


# ── project ──────────────────────────────────────────────────────────


@cli.group()
def project():
    """Project management: new, open, info, save, xml, check, presets."""


@project.command("new")
@click.option("--output", "-o", type=click.Path(dir_okay=False), default=None,
              help="Path of the .drawio file to create.")
@click.option("--name", default="Page-1", show_default=True,
              help="Name of the first page.")
@click.option("--format", "page_format", default="a4", show_default=True,
              help="Page size preset (see: project presets).")
@click.option("--width", type=int, default=None, help="Custom page width in px.")
@click.option("--height", type=int, default=None, help="Custom page height in px.")
@click.option("--landscape", is_flag=True, help="Swap width and height.")
@click.option("--background", default=None, help="Page background, e.g. #ffffff.")
@click.option("--no-grid", is_flag=True, help="Disable the editor grid.")
@click.option("--grid-size", type=int, default=10, show_default=True)
@click.option("--overwrite", is_flag=True, help="Replace an existing file.")
@handle_error
def project_new(output, name, page_format, width, height, landscape,
                background, no_grid, grid_size, overwrite):
    """Create a new blank diagram."""
    if not _repl_mode and not output:
        raise ValueError("Give -o FILE.drawio so the new project is saved.")
    if output and os.path.exists(output) and not overwrite:
        raise FileExistsError(
            f"File exists: {output}. Pass --overwrite to replace it.")
    root = proj_mod.create_project(
        name=name, page_format=page_format, width=width, height=height,
        landscape=landscape, grid=not no_grid, grid_size=grid_size,
        background=background)
    sess = get_session()
    sess.set_project(root, output, load_history=False)
    saved = False
    if output and not _dry_run():
        sess.clear_history()
        sess.save_session()
        saved = True
    elif _repl_mode:
        sess.mark_modified()
    info = proj_mod.project_info(sess.root, sess.project_path)
    info["saved"] = saved
    if _dry_run():
        info["dry_run"] = True
    emit(info)


@project.command("open")
@click.argument("path", type=click.Path(dir_okay=False))
@handle_error
def project_open(path):
    """Open an existing .drawio file (mainly for the REPL)."""
    sess = get_session()
    sess.set_project(proj_mod.open_project(path), path)
    emit(proj_mod.project_info(sess.root, sess.project_path))


@project.command("info")
@handle_error
def project_info():
    """Show pages, sizes and element counts of the project."""
    sess = _project()
    info = proj_mod.project_info(sess.root, sess.project_path)
    info["modified_in_session"] = sess._modified
    emit(info)


@project.command("save")
@click.option("--output", "-o", type=click.Path(dir_okay=False), default=None,
              help="Save to another file (save as).")
@click.option("--compressed", is_flag=True,
              help="Store pages as compressed (deflate+base64) diagrams.")
@click.option("--overwrite", is_flag=True,
              help="Replace an existing file when saving to another path.")
@handle_error
def project_save(output, compressed, overwrite):
    """Save the project (to its own file or to --output)."""
    sess = _project()
    target = output or sess.project_path
    if _dry_run():
        emit({"saved": False, "dry_run": True, "path": target})
        return
    if (output and os.path.exists(output) and not overwrite
            and os.path.abspath(output) != sess.project_path):
        raise FileExistsError(
            f"File exists: {output}. Pass --overwrite to replace it.")
    saved = sess.save_session(output, compressed=compressed)
    emit({"saved": True, "path": saved, "compressed": compressed,
          "file_size": os.path.getsize(saved)})


@project.command("xml")
@click.option("--page", "-P", "page_spec", default=None,
              help="Only this page (index or name). Default: all pages.")
@click.option("--compressed", is_flag=True, help="Print compressed diagrams.")
@handle_error
def project_xml(page_spec, compressed):
    """Print the raw draw.io XML of the project."""
    sess = _project()
    page = None if page_spec is None else page_mod.resolve_page(sess.root, page_spec)
    xml = proj_mod.to_xml(sess.root, compressed=compressed, page=page)
    if _use_json():
        emit({"xml": xml})
    else:
        click.echo(xml.rstrip("\n"))


@project.command("check")
@handle_error
def project_check():
    """Validate structure: duplicate ids, dangling connectors, layers."""
    sess = _project()
    result = proj_mod.check_project(sess.root)
    emit(result)
    if not result["ok"] and not _repl_mode:
        sys.exit(1)


@project.command("presets")
@handle_error
def project_presets():
    """List available page size presets."""
    emit({"presets": proj_mod.list_presets()})


# ── page ─────────────────────────────────────────────────────────────


@cli.group()
def page():
    """Page operations: list, add, remove, rename, duplicate, move, setup, use."""


@page.command("list")
@handle_error
def page_list():
    """List all pages."""
    sess = _project()
    pages = page_mod.list_pages(sess.root)
    emit({"count": len(pages), "current_page": sess.current_page,
          "pages": pages})


@page.command("add")
@click.option("--name", "-n", default=None, help="Page name (default: Page-N).")
@click.option("--format", "page_format", default=None,
              help="Page size preset (default: same size as the last page).")
@click.option("--width", type=int, default=None)
@click.option("--height", type=int, default=None)
@click.option("--landscape", is_flag=True)
@click.option("--background", default=None)
@click.option("--at", "index", type=int, default=None,
              help="Insert position (default: at the end).")
@handle_error
def page_add(name, page_format, width, height, landscape, background, index):
    """Add a new page."""
    emit(_mutate("page add", lambda sess: page_mod.add_page(
        sess.root, name=name, page_format=page_format, width=width,
        height=height, landscape=landscape, background=background,
        index=index)))


@page.command("remove")
@click.argument("page_spec", metavar="PAGE")
@handle_error
def page_remove(page_spec):
    """Remove a page by index or name."""
    def operation(sess):
        result = page_mod.remove_page(sess.root, page_spec)
        sess._clamp_page()
        return result
    emit(_mutate("page remove", operation))


@page.command("rename")
@click.argument("page_spec", metavar="PAGE")
@click.argument("name")
@handle_error
def page_rename(page_spec, name):
    """Rename a page."""
    emit(_mutate("page rename", lambda sess: page_mod.rename_page(
        sess.root, page_spec, name)))


@page.command("duplicate")
@click.argument("page_spec", metavar="PAGE")
@click.option("--name", "-n", default=None, help="Name of the copy.")
@handle_error
def page_duplicate(page_spec, name):
    """Duplicate a page (the copy is inserted right after it)."""
    emit(_mutate("page duplicate", lambda sess: page_mod.duplicate_page(
        sess.root, page_spec, name)))


@page.command("move")
@click.argument("page_spec", metavar="PAGE")
@click.argument("to_index", type=int)
@handle_error
def page_move(page_spec, to_index):
    """Move a page to another position."""
    emit(_mutate("page move", lambda sess: page_mod.move_page(
        sess.root, page_spec, to_index)))


@page.command("setup")
@click.argument("page_spec", metavar="PAGE")
@click.option("--format", "page_format", default=None, help="Page size preset.")
@click.option("--width", type=int, default=None)
@click.option("--height", type=int, default=None)
@click.option("--landscape/--portrait", default=None)
@click.option("--background", default=None, help="Color, or 'none' to clear.")
@click.option("--grid/--no-grid", default=None)
@click.option("--grid-size", type=int, default=None)
@click.option("--math/--no-math", default=None, help="Math typesetting.")
@click.option("--shadow/--no-shadow", default=None, help="Shadows on shapes.")
@handle_error
def page_setup(page_spec, page_format, width, height, landscape, background,
               grid, grid_size, math, shadow):
    """Change page size, background, grid, math or shadow settings."""
    emit(_mutate("page setup", lambda sess: page_mod.setup_page(
        sess.root, page_spec, page_format=page_format, width=width,
        height=height, landscape=landscape, background=background, grid=grid,
        grid_size=grid_size, math=math, shadow=shadow)))


@page.command("use")
@click.argument("page_spec", metavar="PAGE")
@handle_error
def page_use(page_spec):
    """Set the current page for following commands (REPL)."""
    sess = _project()
    sess.current_page = page_mod.resolve_page(sess.root, page_spec)
    emit({"current_page": sess.current_page,
          "name": proj_mod.get_page(sess.root, sess.current_page).get("name")})


# ── layer ────────────────────────────────────────────────────────────


@cli.group()
def layer():
    """Layer operations: list, add, remove, rename, show, hide, lock, unlock, assign."""


@layer.command("list")
@page_option
@handle_error
def layer_list(page_spec):
    """List the layers of a page."""
    sess = _project()
    index = _page(sess, page_spec)
    layers = layer_mod.list_layers(sess.root, index)
    emit({"page": index, "count": len(layers), "layers": layers})


@layer.command("add")
@click.argument("name")
@click.option("--id", "cell_id", default=None, help="Custom layer id.")
@page_option
@handle_error
def layer_add(name, cell_id, page_spec):
    """Add a layer on top of the existing ones."""
    emit(_mutate("layer add", lambda sess: layer_mod.add_layer(
        sess.root, _page(sess, page_spec), name, cell_id)))


@layer.command("remove")
@click.argument("layer_spec", metavar="LAYER")
@page_option
@handle_error
def layer_remove(layer_spec, page_spec):
    """Remove a layer and everything on it."""
    emit(_mutate("layer remove", lambda sess: layer_mod.remove_layer(
        sess.root, _page(sess, page_spec), layer_spec)))


@layer.command("rename")
@click.argument("layer_spec", metavar="LAYER")
@click.argument("name")
@page_option
@handle_error
def layer_rename(layer_spec, name, page_spec):
    """Rename a layer."""
    emit(_mutate("layer rename", lambda sess: layer_mod.rename_layer(
        sess.root, _page(sess, page_spec), layer_spec, name)))


@layer.command("show")
@click.argument("layer_spec", metavar="LAYER")
@page_option
@handle_error
def layer_show(layer_spec, page_spec):
    """Make a layer visible."""
    emit(_mutate("layer show", lambda sess: layer_mod.set_visible(
        sess.root, _page(sess, page_spec), layer_spec, True)))


@layer.command("hide")
@click.argument("layer_spec", metavar="LAYER")
@page_option
@handle_error
def layer_hide(layer_spec, page_spec):
    """Hide a layer (it is skipped when exporting)."""
    emit(_mutate("layer hide", lambda sess: layer_mod.set_visible(
        sess.root, _page(sess, page_spec), layer_spec, False)))


@layer.command("lock")
@click.argument("layer_spec", metavar="LAYER")
@page_option
@handle_error
def layer_lock(layer_spec, page_spec):
    """Lock a layer against editing in the GUI."""
    emit(_mutate("layer lock", lambda sess: layer_mod.set_locked(
        sess.root, _page(sess, page_spec), layer_spec, True)))


@layer.command("unlock")
@click.argument("layer_spec", metavar="LAYER")
@page_option
@handle_error
def layer_unlock(layer_spec, page_spec):
    """Unlock a layer."""
    emit(_mutate("layer unlock", lambda sess: layer_mod.set_locked(
        sess.root, _page(sess, page_spec), layer_spec, False)))


@layer.command("assign")
@click.argument("layer_spec", metavar="LAYER")
@click.argument("cell_ids", nargs=-1, required=True, metavar="ID...")
@page_option
@handle_error
def layer_assign(layer_spec, cell_ids, page_spec):
    """Move shapes or connectors to a layer."""
    emit(_mutate("layer assign", lambda sess: layer_mod.assign_cells(
        sess.root, _page(sess, page_spec), layer_spec, list(cell_ids))))


# ── shape ────────────────────────────────────────────────────────────


@cli.group()
def shape():
    """Shape operations: types, add, list, info, label, move, resize, style, data, remove."""


@shape.command("types")
@handle_error
def shape_types():
    """List all available shape types."""
    types = shape_mod.list_shape_types()
    emit({"count": len(types), "types": types})


@shape.command("add")
@click.option("--type", "-t", "shape_type", default="rectangle",
              show_default=True, help="Shape type (see: shape types).")
@click.option("--label", "-l", default=None, help="Label text.")
@click.option("--x", type=float, default=None, help="Left position in px.")
@click.option("--y", type=float, default=None, help="Top position in px.")
@click.option("--width", "-w", type=float, default=None)
@click.option("--height", type=float, default=None)
@click.option("--fill", default=None, help="Fill color, e.g. #dae8fc or none.")
@click.option("--stroke", default=None, help="Line color, e.g. #6c8ebf or none.")
@click.option("--font-color", default=None)
@click.option("--font-size", type=int, default=None)
@click.option("--set", "assignments", multiple=True, metavar="KEY=VALUE",
              help="Extra style property (repeatable).")
@click.option("--style", "raw_style", default=None,
              help="Raw draw.io style string (replaces the type's style).")
@click.option("--parent", default=None,
              help="Id of a container shape to nest in (x/y become relative).")
@click.option("--layer", default=None, help="Layer id or name.")
@click.option("--id", "cell_id", default=None, help="Custom shape id.")
@click.option("--html", "html_label", is_flag=True,
              help="Treat the label as raw HTML instead of plain text.")
@page_option
@handle_error
def shape_add(shape_type, label, x, y, width, height, fill, stroke, font_color,
              font_size, assignments, raw_style, parent, layer, cell_id,
              html_label, page_spec):
    """Add a shape. Without --x/--y it is placed below the existing shapes."""
    props = _style_props(assignments, fill, stroke, font_color, font_size)
    emit(_mutate(f"shape add {shape_type}", lambda sess: shape_mod.add_shape(
        sess.root, _page(sess, page_spec), shape_type=shape_type, label=label,
        x=x, y=y, width=width, height=height, style=raw_style,
        set_props=props, parent=parent, layer=layer, cell_id=cell_id,
        html_label=html_label)))


@shape.command("list")
@click.option("--layer", default=None, help="Only shapes on this layer.")
@click.option("--type", "-t", "shape_type", default=None,
              help="Only shapes of this type.")
@click.option("--contains", default=None,
              help="Only shapes whose label contains this text.")
@page_option
@handle_error
def shape_list(layer, shape_type, contains, page_spec):
    """List the shapes on a page."""
    sess = _project()
    index = _page(sess, page_spec)
    shapes = shape_mod.list_shapes(sess.root, index, layer=layer,
                                   contains=contains, shape_type=shape_type)
    emit({"page": index, "count": len(shapes), "shapes": shapes})


@shape.command("info")
@click.argument("shape_id", metavar="ID")
@page_option
@handle_error
def shape_info(shape_id, page_spec):
    """Show geometry, style, data and connections of a shape."""
    sess = _project()
    emit(shape_mod.get_shape(sess.root, _page(sess, page_spec), shape_id))


@shape.command("label")
@click.argument("shape_id", metavar="ID")
@click.argument("text")
@click.option("--html", "html_label", is_flag=True,
              help="Treat the text as raw HTML.")
@page_option
@handle_error
def shape_label(shape_id, text, html_label, page_spec):
    """Change a shape's label text."""
    emit(_mutate("shape label", lambda sess: shape_mod.set_label(
        sess.root, _page(sess, page_spec), shape_id, text, html_label)))


@shape.command("move")
@click.argument("shape_id", metavar="ID")
@click.option("--x", type=float, default=None, help="New left position.")
@click.option("--y", type=float, default=None, help="New top position.")
@click.option("--dx", "delta_x", type=float, default=None, help="Horizontal offset.")
@click.option("--dy", "delta_y", type=float, default=None, help="Vertical offset.")
@page_option
@handle_error
def shape_move(shape_id, x, y, delta_x, delta_y, page_spec):
    """Move a shape to a position (--x/--y) or by an offset (--dx/--dy)."""
    emit(_mutate("shape move", lambda sess: shape_mod.move_shape(
        sess.root, _page(sess, page_spec), shape_id, x=x, y=y,
        dx_=delta_x, dy=delta_y)))


@shape.command("resize")
@click.argument("shape_id", metavar="ID")
@click.option("--width", "-w", type=float, default=None)
@click.option("--height", type=float, default=None)
@page_option
@handle_error
def shape_resize(shape_id, width, height, page_spec):
    """Resize a shape."""
    emit(_mutate("shape resize", lambda sess: shape_mod.resize_shape(
        sess.root, _page(sess, page_spec), shape_id, width, height)))


@shape.command("style")
@click.argument("shape_id", metavar="ID")
@click.argument("assignments", nargs=-1, metavar="[KEY=VALUE]...")
@click.option("--unset", multiple=True, metavar="KEY",
              help="Remove a style property (repeatable).")
@click.option("--fill", default=None)
@click.option("--stroke", default=None)
@click.option("--font-color", default=None)
@click.option("--font-size", type=int, default=None)
@page_option
@handle_error
def shape_style(shape_id, assignments, unset, fill, stroke, font_color,
                font_size, page_spec):
    """Set or remove style properties, e.g. fillColor=#fff2cc rounded=1."""
    props = _style_props(assignments, fill, stroke, font_color, font_size)
    emit(_mutate("shape style", lambda sess: shape_mod.set_style(
        sess.root, _page(sess, page_spec), shape_id, props, list(unset))))


@shape.command("data")
@click.argument("shape_id", metavar="ID")
@click.argument("assignments", nargs=-1, metavar="[KEY=VALUE]...")
@click.option("--unset", multiple=True, metavar="KEY",
              help="Remove a custom property (repeatable).")
@page_option
@handle_error
def shape_data(shape_id, assignments, unset, page_spec):
    """Show or set custom properties (the GUI's Edit Data dialog)."""
    props = dx.parse_assignments(assignments)
    if not props and not unset:
        sess = _project()
        emit(shape_mod.set_data(sess.root, _page(sess, page_spec), shape_id))
        return
    emit(_mutate("shape data", lambda sess: shape_mod.set_data(
        sess.root, _page(sess, page_spec), shape_id, props, list(unset))))


@shape.command("remove")
@click.argument("shape_id", metavar="ID")
@page_option
@handle_error
def shape_remove(shape_id, page_spec):
    """Remove a shape, its children and its connectors."""
    emit(_mutate("shape remove", lambda sess: shape_mod.remove_shape(
        sess.root, _page(sess, page_spec), shape_id)))


# ── connect ──────────────────────────────────────────────────────────


@cli.group()
def connect():
    """Connector operations: styles, arrows, add, list, info, label, style, remove."""


@connect.command("styles")
@handle_error
def connect_styles():
    """List available connector style presets."""
    styles = conn_mod.list_edge_styles()
    emit({"count": len(styles), "styles": styles})


@connect.command("arrows")
@handle_error
def connect_arrows():
    """List available arrow head names."""
    emit({"arrows": conn_mod.list_arrows()})


@connect.command("add")
@click.argument("source")
@click.argument("target")
@click.option("--label", "-l", default=None, help="Connector label.")
@click.option("--style", "-s", "style", default="orthogonal", show_default=True,
              help="Style preset (see: connect styles).")
@click.option("--raw-style", default=None,
              help="Raw draw.io style string (replaces the preset).")
@click.option("--start-arrow", default=None, help="Arrow at the source end.")
@click.option("--end-arrow", default=None, help="Arrow at the target end.")
@click.option("--dashed", is_flag=True, help="Dashed line.")
@click.option("--set", "assignments", multiple=True, metavar="KEY=VALUE",
              help="Extra style property (repeatable).")
@click.option("--waypoint", "waypoints", multiple=True, metavar="X,Y",
              help="Route through this point (repeatable, in order).")
@click.option("--layer", default=None,
              help="Layer id or name (default: the source shape's layer).")
@click.option("--id", "cell_id", default=None, help="Custom connector id.")
@click.option("--html", "html_label", is_flag=True,
              help="Treat the label as raw HTML.")
@page_option
@handle_error
def connect_add(source, target, label, style, raw_style, start_arrow,
                end_arrow, dashed, assignments, waypoints, layer, cell_id,
                html_label, page_spec):
    """Connect shape SOURCE to shape TARGET."""
    props = dx.parse_assignments(assignments)
    points = [conn_mod.parse_waypoint(w) for w in waypoints]
    emit(_mutate("connect add", lambda sess: conn_mod.add_connector(
        sess.root, _page(sess, page_spec), source, target, label=label,
        style=style, raw_style=raw_style, start_arrow=start_arrow,
        end_arrow=end_arrow, dashed=dashed, set_props=props,
        waypoints=points, layer=layer, cell_id=cell_id,
        html_label=html_label)))


@connect.command("list")
@click.option("--shape", "shape_id", default=None,
              help="Only connectors attached to this shape.")
@page_option
@handle_error
def connect_list(shape_id, page_spec):
    """List the connectors on a page."""
    sess = _project()
    index = _page(sess, page_spec)
    connectors = conn_mod.list_connectors(sess.root, index, shape=shape_id)
    emit({"page": index, "count": len(connectors), "connectors": connectors})


@connect.command("info")
@click.argument("connector_id", metavar="ID")
@page_option
@handle_error
def connect_info(connector_id, page_spec):
    """Show endpoints, style and waypoints of a connector."""
    sess = _project()
    emit(conn_mod.get_connector(sess.root, _page(sess, page_spec), connector_id))


@connect.command("label")
@click.argument("connector_id", metavar="ID")
@click.argument("text")
@click.option("--html", "html_label", is_flag=True)
@page_option
@handle_error
def connect_label(connector_id, text, html_label, page_spec):
    """Change a connector's label."""
    emit(_mutate("connect label", lambda sess: conn_mod.set_label(
        sess.root, _page(sess, page_spec), connector_id, text, html_label)))


@connect.command("style")
@click.argument("connector_id", metavar="ID")
@click.argument("assignments", nargs=-1, metavar="[KEY=VALUE]...")
@click.option("--unset", multiple=True, metavar="KEY")
@click.option("--start-arrow", default=None)
@click.option("--end-arrow", default=None)
@page_option
@handle_error
def connect_style(connector_id, assignments, unset, start_arrow, end_arrow,
                  page_spec):
    """Set or remove style properties of a connector."""
    props = dx.parse_assignments(assignments)
    emit(_mutate("connect style", lambda sess: conn_mod.set_style(
        sess.root, _page(sess, page_spec), connector_id, props, list(unset),
        start_arrow=start_arrow, end_arrow=end_arrow)))


@connect.command("remove")
@click.argument("connector_id", metavar="ID")
@page_option
@handle_error
def connect_remove(connector_id, page_spec):
    """Remove a connector."""
    emit(_mutate("connect remove", lambda sess: conn_mod.remove_connector(
        sess.root, _page(sess, page_spec), connector_id)))


# ── export ───────────────────────────────────────────────────────────


@cli.group()
def export():
    """Export operations: formats, render, convert (uses the real draw.io app)."""


def _export_options(func):
    options = [
        click.option("--format", "-f", "fmt", default=None,
                     help="png, jpg, svg, pdf, vsdx or xml "
                          "(default: from the output extension)."),
        click.option("--all-pages", is_flag=True, help="All pages (pdf only)."),
        click.option("--page-range", default=None, metavar="FROM..TO",
                     help="Page range, e.g. 0..2 (pdf only)."),
        click.option("--scale", type=float, default=None, help="Scale factor."),
        click.option("--width", type=int, default=None,
                     help="Fit into this width (png/jpg/pdf)."),
        click.option("--height", type=int, default=None,
                     help="Fit into this height (png/jpg/pdf)."),
        click.option("--transparent", is_flag=True,
                     help="Transparent background (png only)."),
        click.option("--crop", is_flag=True,
                     help="Crop to the diagram size (pdf only)."),
        click.option("--quality", type=int, default=None,
                     help="JPEG quality 1-100 (jpg only)."),
        click.option("--embed-diagram", is_flag=True,
                     help="Embed an editable copy (png/svg/pdf)."),
        click.option("--embed-svg-images", is_flag=True, help="svg only."),
        click.option("--svg-theme", default=None, help="light or dark (svg only)."),
        click.option("--svg-links-target", default=None,
                     help="auto, new-win or same-win (svg only)."),
        click.option("--overwrite", is_flag=True,
                     help="Replace an existing output file."),
        click.option("--timeout", type=int, default=120, show_default=True,
                     help="Seconds to wait for draw.io."),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _export_kwargs(all_pages, page_range, scale, width, height, transparent,
                   crop, quality, embed_diagram, embed_svg_images, svg_theme,
                   svg_links_target):
    return {
        "all_pages": all_pages,
        "page_range": (export_mod.parse_page_range(page_range)
                       if page_range else None),
        "scale": scale, "width": width, "height": height,
        "transparent": transparent, "crop": crop, "quality": quality,
        "embed_diagram": embed_diagram, "embed_svg_images": embed_svg_images,
        "svg_theme": svg_theme, "svg_links_target": svg_links_target,
    }


@export.command("formats")
@handle_error
def export_formats():
    """List export formats and the options each one accepts."""
    emit({"formats": export_mod.list_formats()})


@export.command("render")
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--page", "-P", "page_spec", default=None,
              help="Page index or name to export (default: the current page "
                   "for images, the app's default for pdf).")
@click.option("--layer", "layer_specs", multiple=True,
              help="Only include this layer (id or name, repeatable).")
@_export_options
@handle_error
def export_render(output, page_spec, layer_specs, fmt, all_pages, page_range,
                  scale, width, height, transparent, crop, quality,
                  embed_diagram, embed_svg_images, svg_theme, svg_links_target,
                  overwrite, timeout):
    """Render the project to OUTPUT with the real draw.io app."""
    sess = _project()
    kwargs = _export_kwargs(all_pages, page_range, scale, width, height,
                            transparent, crop, quality, embed_diagram,
                            embed_svg_images, svg_theme, svg_links_target)
    chosen = export_mod.normalize_format(fmt, output)
    page_index = None
    if page_spec is not None:
        page_index = page_mod.resolve_page(sess.root, page_spec)
    elif (not all_pages and page_range is None and chosen != "xml"
          and sess.current_page != 0):
        page_index = sess.current_page
    layers = None
    if layer_specs:
        root_el = proj_mod.page_root(sess.root, page_index or 0)
        layers = sorted({layer_mod.layer_index(root_el, spec)
                         for spec in layer_specs})
    emit(export_mod.render(sess.root, output, fmt=fmt, overwrite=overwrite,
                           timeout=timeout, page_index=page_index,
                           layers=layers, **kwargs))


@export.command("convert")
@click.argument("input_path", metavar="INPUT",
                type=click.Path(dir_okay=False))
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--page-index", type=int, default=None,
              help="Page index to export (from 0).")
@click.option("--layers", "layer_indexes", default=None, metavar="I,J",
              help="Comma separated layer indexes to include.")
@_export_options
@handle_error
def export_convert(input_path, output, page_index, layer_indexes, fmt,
                   all_pages, page_range, scale, width, height, transparent,
                   crop, quality, embed_diagram, embed_svg_images, svg_theme,
                   svg_links_target, overwrite, timeout):
    """Convert any file draw.io can read (.drawio, .vsdx, .csv, .png) to OUTPUT."""
    kwargs = _export_kwargs(all_pages, page_range, scale, width, height,
                            transparent, crop, quality, embed_diagram,
                            embed_svg_images, svg_theme, svg_links_target)
    layers = None
    if layer_indexes:
        try:
            layers = [int(part) for part in layer_indexes.split(",")]
        except ValueError:
            raise ValueError(
                f"--layers must be comma separated integers, got: {layer_indexes!r}"
            ) from None
    emit(export_mod.convert_file(input_path, output, fmt=fmt,
                                 overwrite=overwrite, timeout=timeout,
                                 page_index=page_index, layers=layers,
                                 **kwargs))


# ── backend ──────────────────────────────────────────────────────────


@cli.group("backend")
def backend_group():
    """Backend operations: info about the installed draw.io app."""


@backend_group.command("info")
@handle_error
def backend_info():
    """Show where draw.io is installed and its version."""
    info = backend.backend_info()
    emit(info)
    if not info["found"] and not _repl_mode:
        sys.exit(1)


# ── session ──────────────────────────────────────────────────────────


@cli.group()
def session():
    """Session management: status, undo, redo, history."""


@session.command("status")
@handle_error
def session_status():
    """Show the open project, modified flag and undo depth."""
    sess = get_session()
    if not sess.has_project() and _root_obj().get("project_path"):
        sess = _project()
    emit(sess.status())


@session.command("undo")
@handle_error
def session_undo():
    """Undo the last change (works across one-shot commands)."""
    sess = _project()
    emit(_commit(sess, sess.undo()))


@session.command("redo")
@handle_error
def session_redo():
    """Redo the last undone change."""
    sess = _project()
    emit(_commit(sess, sess.redo()))


@session.command("history")
@handle_error
def session_history():
    """List the undo and redo stacks."""
    emit(_project().history())


# ── REPL ─────────────────────────────────────────────────────────────

REPL_ALIASES = {
    "undo": ["session", "undo"],
    "redo": ["session", "redo"],
    "status": ["session", "status"],
    "save": ["project", "save"],
}


def _repl_commands():
    commands = {}
    for group_name, group in cli.commands.items():
        if isinstance(group, click.Group):
            subs = " | ".join(group.commands)
            commands[group_name] = subs
    commands["undo / redo / status / save"] = "shortcuts"
    commands["help"] = "show this help"
    commands["quit"] = "leave the REPL"
    return commands


def _split_line(line):
    if os.name == "nt":
        parts = shlex.split(line, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'"
                else p for p in parts]
    return shlex.split(line)


@cli.command()
@click.pass_context
def repl(ctx):
    """Start the interactive REPL (default when no subcommand is given)."""
    global _repl_mode, _repl_json
    from cli_anything.drawio.utils.repl_skin import ReplSkin

    root = ctx.find_root().obj or {}
    project_path = root.get("project_path")
    _repl_json = bool(root.get("json"))
    os.makedirs(state_dir(), exist_ok=True)
    skin = ReplSkin("drawio", version=__version__,
                    history_file=os.path.join(state_dir(), "history"))
    skin.print_banner()
    sess = get_session()
    if project_path:
        try:
            sess.set_project(proj_mod.open_project(project_path), project_path)
            skin.success(f"Opened {project_path}")
        except Exception as exc:  # noqa: BLE001
            skin.error(str(exc))
    _repl_mode = True
    # prompt_toolkit needs a terminal; piped input (agents, tests) uses input().
    interactive = sys.stdin is not None and sys.stdin.isatty()
    prompt_session = skin.create_prompt_session() if interactive else None
    try:
        while True:
            sess = get_session()
            name = (os.path.basename(sess.project_path)
                    if sess.project_path else
                    ("untitled" if sess.has_project() else ""))
            try:
                line = skin.get_input(prompt_session, project_name=name,
                                      modified=sess._modified)
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line in ("quit", "exit", "q"):
                break
            if line in ("help", "?"):
                skin.help(_repl_commands())
                continue
            try:
                args = _split_line(line)
            except ValueError as exc:
                skin.error(f"Cannot parse command: {exc}")
                continue
            args = REPL_ALIASES.get(args[0], [args[0]]) + args[1:]
            try:
                cli.main(args=args, prog_name="cli-anything-drawio",
                         standalone_mode=False)
            except click.ClickException as exc:
                skin.error(exc.format_message())
            except click.Abort:
                continue
            except SystemExit:
                continue
            except Exception as exc:  # noqa: BLE001
                skin.error(str(exc))
        if get_session()._modified:
            skin.warning("Unsaved changes were discarded (use: save).")
        skin.print_goodbye()
    finally:
        _repl_mode = False
        _repl_json = False


# ── Entry point ──────────────────────────────────────────────────────


def _ensure_utf8_stdio():
    """Keep non-ASCII output from crashing on legacy consoles (cp949, cp936)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    # Piped input (REPL scripts from agents) is read as UTF-8, not the locale.
    stdin = getattr(sys, "stdin", None)
    try:
        if (stdin is not None and hasattr(stdin, "reconfigure")
                and not stdin.isatty()):
            stdin.reconfigure(encoding="utf-8-sig", errors="replace")
    except (OSError, ValueError):
        pass


def main():
    _ensure_utf8_stdio()
    cli(prog_name="cli-anything-drawio")


if __name__ == "__main__":
    main()
