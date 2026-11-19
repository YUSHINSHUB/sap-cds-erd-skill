---
name: >-
  cli-anything-drawio
description: >-
  Create, edit and export draw.io diagrams (.drawio) from the command line: flowcharts, architecture diagrams, ERDs. Use when a task needs a diagram file or a PNG/SVG/PDF rendered by the real draw.io app.
---

# cli-anything-drawio

Edits native `.drawio` files directly and renders them with the real draw.io
desktop app. Files it writes open in the draw.io GUI, and files made in the
GUI can be edited with it.

## Prerequisites

- Python 3.10+
- draw.io desktop app (hard dependency for `export`). Check it is found:

```bash
cli-anything-drawio --json backend info
```

If `found` is false, install it (`winget install JGraph.Draw`,
`brew install --cask drawio`, `snap install drawio`) or set `DRAWIO_PATH`.

Install the CLI from its source folder: `pip install ./tools/cli-anything-drawio`.

## Calling convention

```bash
cli-anything-drawio [--json] [--project FILE.drawio] [--dry-run] GROUP COMMAND [ARGS]
```

- `--json`, `--project` and `--dry-run` go **before** the group name.
- Every command supports `--json`. Use it.
- A failed command exits with code 1 and, with `--json`, prints
  `{"error": "...", "type": "..."}` on **stdout**. Error messages list the
  valid choices (shape ids, page names, formats).
- Mutating commands save the file automatically and return `"saved": true`.
  `--dry-run` runs the command without saving.
- Pages are addressed with `--page` by index (from 0) or name. Layers, shapes
  and connectors are addressed by id. Ids are assigned in order (`s1`, `s2`,
  `e1`, `layer1`); pass `--id` to choose your own.

## Typical workflow

```bash
P=flow.drawio
cli-anything-drawio --json project new -o $P --name "Approval"
cli-anything-drawio --json --project $P shape add -t rounded -l "Request" --x 200 --y 20 --fill "#dae8fc"
cli-anything-drawio --json --project $P shape add -t diamond -l "Amount > 1000?" --x 180 --y 130 -w 160 --height 100
cli-anything-drawio --json --project $P shape add -l "Manager approval" --x 40 --y 290
cli-anything-drawio --json --project $P connect add s1 s2
cli-anything-drawio --json --project $P connect add s2 s3 -l "yes" --end-arrow block
cli-anything-drawio --json --project $P project check
cli-anything-drawio --json --project $P export render flow.png --scale 2
```

Inspect before changing: `project info`, `page list`, `layer list`,
`shape list`, `shape info ID`, `connect list`.

## Command groups

| Group | Commands |
|-------|----------|
| `project` | `new`, `open`, `info`, `save`, `xml`, `check`, `presets` |
| `page` | `list`, `add`, `remove`, `rename`, `duplicate`, `move`, `setup`, `use` |
| `layer` | `list`, `add`, `remove`, `rename`, `show`, `hide`, `lock`, `unlock`, `assign` |
| `shape` | `types`, `add`, `list`, `info`, `label`, `move`, `resize`, `style`, `data`, `remove` |
| `connect` | `styles`, `arrows`, `add`, `list`, `info`, `label`, `style`, `remove` |
| `export` | `formats`, `render`, `convert` |
| `backend` | `info` |
| `session` | `status`, `undo`, `redo`, `history` |

Run `cli-anything-drawio GROUP COMMAND --help` for the options of a command.

### Shapes

- `shape types` lists the registry (rectangle, rounded, ellipse, diamond,
  cylinder, cloud, document, actor, container, note, text, title, ...).
- `shape add` options: `-t TYPE`, `-l LABEL`, `--x --y -w --height`,
  `--fill --stroke --font-color --font-size`, `--set KEY=VALUE` (any draw.io
  style key, repeatable), `--style "RAW STYLE"` (replaces the type's style;
  use it for shapes that are not in the registry), `--parent ID` (nest in a
  container; x/y become relative to it), `--layer`, `--id`.
- Labels are plain text by default and are escaped for you. Pass `--html` to
  give raw HTML (`<b>`, `<br>`, tables).
- Without `--x/--y` a shape is placed below the existing ones.
- `shape style ID fillColor=#fff2cc rounded=1 --unset dashed` edits style
  keys; `shape data ID owner=kim` sets custom properties.
- `shape remove` also removes the shape's children and attached connectors.

### Connectors

- `connect add SOURCE TARGET` with `-s PRESET` (`connect styles`: orthogonal,
  straight, curved, dashed, entity-relation, UML presets, ...),
  `--start-arrow/--end-arrow NAME` (`connect arrows`: classic, block, open,
  diamondThin, ERone, ERzeroToMany, ...), `--dashed`, `--set KEY=VALUE`,
  `--raw-style "..."`, `--waypoint X,Y` (repeatable, routes the line).
- To control where a line attaches, set `exitX/exitY/entryX/entryY`
  (0..1 along the box) with `--set` or in `--raw-style`.

### Export

```bash
cli-anything-drawio --json --project $P export render out.png --scale 2 --transparent
cli-anything-drawio --json --project $P export render out.svg
cli-anything-drawio --json --project $P export render out.pdf --all-pages --crop
cli-anything-drawio --json --project $P export render page2.png --page 1 --layer Notes
cli-anything-drawio --json export convert legacy.vsdx legacy.png
```

- Formats: `png`, `jpg`, `svg`, `pdf`, `vsdx`, `xml` (from the output
  extension or `--format`). `export formats` lists which options each format
  accepts; an option that does not apply is rejected, not ignored.
- An existing output is not replaced unless `--overwrite` is given.
- The result is verified before success is reported and includes
  `image_width`/`image_height` (png), `pdf_pages` (pdf), `file_size` and a
  `warnings` list. Read `warnings`: draw.io 24.7.17 writes `--embed-diagram`
  PNGs without the closing IEND chunk.
- One export takes roughly 2-8 seconds.

### Undo

`session undo` / `session redo` work across separate commands (history is
kept per project file and dropped if the file is edited elsewhere).
`session history` lists what can be undone.

## REPL (many commands in one process)

Running `cli-anything-drawio` with no subcommand starts a REPL. It also reads
commands from a pipe (UTF-8), which is the fastest way to build a large
diagram:

```bash
printf '%s\n' 'project new --name Main' 'shape add -l A' 'shape add -l B' \
  'connect add s1 s2' 'save -o "out.drawio"' 'quit' | cli-anything-drawio
```

In the REPL nothing is saved until `save` (`save -o FILE`). Shortcuts:
`undo`, `redo`, `status`, `save`, `help`, `quit`. A failed command prints an
error and the session continues, so check the output for `Error`.

## Limits

- No `preview` commands.
- Folder / recursive export is not wrapped; export one file per call.
- `--border` and `--uncompressed` are not offered: they have no effect in
  draw.io 24.7.17.
- No automatic layout: you choose the coordinates.

## Version

1.0.0
