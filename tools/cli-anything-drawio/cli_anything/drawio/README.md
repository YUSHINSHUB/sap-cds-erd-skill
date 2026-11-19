# cli-anything-drawio

A CLI harness for **draw.io** — create, edit and export diagrams from the
command line. It edits native `.drawio` files directly and uses the real
draw.io desktop app for every export, so what you get is exactly what the GUI
would produce.

## Requirements

- Python 3.10+
- **draw.io desktop app** (hard dependency — exports fail without it)

```bash
winget install JGraph.Draw        # Windows
brew install --cask drawio        # macOS
snap install drawio               # Linux
```

The app is found on `PATH`, in the default install locations
(`C:\Program Files\draw.io\draw.io.exe`, `/Applications/draw.io.app`, ...) or
through the `DRAWIO_PATH` environment variable. Check with:

```bash
cli-anything-drawio backend info
```

On headless Linux run under `xvfb-run`. `--no-sandbox` is added automatically
when running as root; extra app flags can be passed in `CLI_ANYTHING_DRAWIO_ARGS`.

## Install

```bash
cd tools/cli-anything-drawio
pip install -e .
cli-anything-drawio --help
```

## Usage

```bash
# Create a diagram
cli-anything-drawio project new -o flow.drawio --name "Approval"

# Add shapes (ids are returned: s1, s2, ...)
cli-anything-drawio --project flow.drawio shape add -t rounded -l "Start" --x 80 --y 40 --fill "#d5e8d4"
cli-anything-drawio --project flow.drawio shape add -t diamond -l "Approved?" --x 100 --y 160

# Connect them
cli-anything-drawio --project flow.drawio connect add s1 s2 -l "submit"

# Inspect
cli-anything-drawio --project flow.drawio shape list
cli-anything-drawio --json --project flow.drawio project info

# Export with the real draw.io app
cli-anything-drawio --project flow.drawio export render flow.png --scale 2
cli-anything-drawio --project flow.drawio export render flow.pdf --all-pages

# Undo the last change (works across separate commands)
cli-anything-drawio --project flow.drawio session undo
```

- Every command accepts `--json` for machine-readable output. Errors are
  `{"error": "...", "type": "..."}` with exit code 1.
- One-shot commands save automatically. `--dry-run` runs a command without
  saving.
- Running `cli-anything-drawio` with no subcommand starts an interactive REPL
  (`help`, `undo`, `redo`, `status`, `save`, `quit`).

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

Pages are addressed by index (from 0) or name with `--page`; layers, shapes
and connectors by id.

## Export formats

`png`, `jpg`, `svg`, `pdf`, `vsdx`, `xml`. Options that do not apply to the
chosen format are rejected instead of being silently ignored
(`cli-anything-drawio export formats` lists what each format accepts).
Exported files are verified (signature, PNG size, PDF page count) before the
command reports success, because draw.io itself exits with code 0 even when
an export fails.

## Tests

```bash
cd tools/cli-anything-drawio
python -m pytest cli_anything/drawio/tests/ -v
# require the installed command for the subprocess tests:
CLI_ANYTHING_FORCE_INSTALLED=1 python -m pytest cli_anything/drawio/tests/ -v -s
```

The E2E tests need the real draw.io app and fail (not skip) without it.

## Limitations

- No preview bundles (`preview ...`).
- Folder/recursive export is not wrapped; export one file per call.
- Only the General, Misc and Advanced shape palettes are in the registry;
  any other shape works through `shape add --style "<raw draw.io style>"`.
