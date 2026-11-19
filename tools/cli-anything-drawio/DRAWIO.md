# DRAWIO.md — draw.io harness SOP

Software-specific analysis and design for `cli-anything-drawio`.
Methodology: `cli-anything-plugin/HARNESS.md` (not duplicated here).

Analysed sources (tag `v24.7.17`, matching the installed desktop app):

- `jgraph/drawio-desktop` — `src/main/electron.js` (Electron main process, CLI export)
- `jgraph/drawio` — `src/main/webapp/js/grapheditor/{Graph,Editor,Sidebar,Format}.js`,
  `src/main/webapp/js/diagramly/{EditorUi,Pages,DrawioFile}.js`

## Phase 1 — Codebase analysis

### Backend engine

draw.io desktop is an Electron shell around the mxGraph-based web editor.
Rendering happens inside a hidden `BrowserWindow`; there is no separate
rendering library that could be called from Python. The only headless entry
point is the desktop app's own command line.

### Existing CLI (the building block)

`electron.js` registers these options with commander (lines 416-464):

| Flag | Meaning | Applies to |
|------|---------|------------|
| `-x, --export` | export mode (boolean; input is positional) | all |
| `-o, --output <file/folder>` | output path; a valid extension decides the format | all |
| `-f, --format <fmt>` | `pdf` (default), `png`, `jpg`/`jpeg`, `svg`, `vsdx`, `xml` | all |
| `-p, --page-index <n>` | one page (images default to the first page) | all |
| `-a, --all-pages` | every page | pdf |
| `-g, --page-range <a>..<b>` | page range | pdf |
| `-l, --layers <i,j>` | layer indexes to include | all |
| `-s, --scale <f>` | scale factor | png/jpg/svg/pdf |
| `--width`, `--height` | fit into a size, keeps aspect ratio | png/jpg/pdf |
| `-b, --border <n>` | border width | **not wrapped** — measured no-op, see below |
| `-t, --transparent` | transparent background | png |
| `--crop` | crop to diagram size | pdf |
| `-q, --quality <n>` | JPEG quality (default 90) | jpg |
| `-e, --embed-diagram` | embed a copy of the diagram | png/svg/pdf |
| `--embed-svg-images` | inline images | svg |
| `--svg-theme <dark\|light>` | SVG theme | svg |
| `--svg-links-target <auto\|new-win\|same-win>` | link target | svg |
| `-u, --uncompressed` | uncompressed XML | **not wrapped** — measured no-op, see below |
| `-r, --recursive`, `-k, --check`, `-c, --create`, `-z, --zoom`, `--enable-plugins` | folder / GUI options | not wrapped |

Accepted inputs (lines 639-687): `.drawio`/XML text, `.vsdx` (imported),
`.csv` (CSV import), `.png` (embedded diagram).

### Behaviour verified against the real app (24.7.17, Windows)

These were measured, not assumed, and they shape the backend module:

1. **The exit code is always 0.** A missing input prints
   `Error: input file/directory not found` on stderr and still exits 0
   (the error path only calls `dummyWin.destroy()`). The backend therefore
   never trusts the exit code: it scans for `Error` lines and requires the
   output file to exist and be non-empty.
2. **Existing outputs are silently overwritten.** The source throws
   `Error: Output file already exists`, but the `throw` sits inside its own
   `try/catch`, so it is swallowed. The harness implements its own
   `--overwrite` guard and always exports to a fresh temp file first.
3. **stderr contains Chromium noise** (`GPU state invalid ...`) on
   successful runs, so stderr content alone is not a failure signal.
4. One export takes about 1.3-2.6 s.
5. `draw.io --version` prints the bare version (`24.7.17`).
6. On Windows the installer does **not** put `draw.io.exe` on `PATH`
   (`C:\Program Files\draw.io\draw.io.exe`), so discovery must also check
   the default install locations.
7. **`--border` does nothing.** PNG, JPG, SVG and PDF (cropped or not) come
   out with identical dimensions for border 0 and 30. The source maps the
   option to `pageMargin` (electron.js:558), which the export path does not
   use. The option is therefore not wrapped.
8. **`--uncompressed` does nothing.** XML export is uncompressed either way
   (byte-identical output), so it is not wrapped either.
9. **PDF page objects live in compressed `/ObjStm` streams.** A byte search
   for `/Type /Page` finds nothing; `verify_output` inflates the streams
   before counting pages.
10. **`--embed-diagram` PNGs are written without an IEND chunk.** The
    diagram is stored in a `zTXt` chunk named `mxGraphModel` (raw deflate of
    the URI-encoded XML — the `Graph.compress` scheme without base64), and
    the file ends 8 bytes short. Browsers and draw.io still open it. The
    harness does not rewrite the file; it reports `png_complete: false` and
    a warning.
11. `--scale` leaves about 3 edge pixels unscaled (124 px becomes 245 px at
    scale 2), so sizes are not exact multiples.

### Data model

Native format: `.drawio` — XML.

```
<mxfile host agent version pages>
  <diagram id name>                      one per page
    <mxGraphModel grid gridSize guides tooltips connect arrows fold
                  page pageScale pageWidth pageHeight background math shadow>
      <root>
        <mxCell id="0"/>                 root cell (no parent)
        <mxCell id="1" parent="0"/>      layer (every child of the root cell)
        <mxCell id value style vertex="1" parent>   shape
          <mxGeometry x y width height as="geometry"/>
        </mxCell>
        <mxCell id value style edge="1" parent source target>   connector
          <mxGeometry relative="1" as="geometry">
            <Array as="points"><mxPoint x y/></Array>          waypoints
          </mxGeometry>
        </mxCell>
        <object label id key="value">    cell with custom data ("Edit Data")
          <mxCell .../>
        </object>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

- `Editor.prototype.getGraphXml` (Editor.js:890) writes the model attributes.
- A `<diagram>` may instead hold **compressed** text:
  `base64(deflateRaw(encodeURIComponent(xml)))` — `Graph.compress`
  (Graph.js:1808). The harness reads both and writes uncompressed by default.
- Appearance is a `key=value;` style string with optional bare tokens
  (`ellipse;`, `rhombus;`, `swimlane;`, `text;`).
- Labels are HTML when the style has `html=1`.
- Layer visibility is `visible="0"` on the layer cell; locking is the style
  key `locked=1`.

### GUI action → data mapping

| GUI action | Data change | CLI command |
|------------|-------------|-------------|
| File > New | new `<mxfile>` with one `<diagram>` | `project new` |
| File > Page Setup (`PageSetupDialog.getFormats`) | `pageWidth`/`pageHeight`/`background`/`grid` | `page setup`, `project presets` |
| Insert Page / Delete / Rename / Duplicate / reorder tabs | `<diagram>` elements | `page add/remove/rename/duplicate/move` |
| Drag a shape from the sidebar (`Sidebar.js` `createVertexTemplateEntry`) | vertex `mxCell` with the palette style and size | `shape add`, `shape types` |
| Double-click to edit text | `value` / `label` | `shape label`, `connect label` |
| Move / resize handles | `mxGeometry` | `shape move`, `shape resize` |
| Format panel (fill, line, font) | style keys | `shape style`, `connect style` |
| Edit Data dialog | `<object>` wrapper attributes | `shape data` |
| Drag between shapes (`Graph.prototype.defaultEdgeStyle`, Graph.js:9362) | edge `mxCell` with `source`/`target` | `connect add`, `connect styles`, `connect arrows` |
| Layers window | cells under the root cell, `visible`, `locked` | `layer add/remove/rename/show/hide/lock/unlock/assign` |
| Delete key | remove cell, its children and attached edges | `shape remove`, `connect remove` |
| File > Export as | hidden-window render | `export render`, `export convert` |
| Ctrl+Z / Ctrl+Y (`mxUndoManager`) | model snapshots | `session undo/redo/history` |

## Phase 2 — CLI architecture

- **Interaction model:** both. One-shot subcommands for agents and scripts;
  a REPL (default when no subcommand is given) for interactive sessions.
- **Project file:** the native `.drawio` file itself (`--project FILE`).
  There is no intermediate JSON project, so every file the CLI writes opens
  directly in the GUI and every GUI-made file can be edited by the CLI.
- **State:** `core/session.py` holds the parsed document, the modified flag,
  the current page and undo/redo stacks (50 levels). One-shot mutations
  auto-save; `--dry-run` suppresses the save. Undo history is persisted to
  `~/.cli-anything-drawio/sessions/<hash>.json`, keyed to the SHA-256 of the
  saved file, so `session undo` works across one-shot invocations and is
  discarded automatically when the file was edited elsewhere.
- **Output:** human-readable by default, `--json` on every command. Errors are
  `{"error", "type"}` with exit code 1.
- **Addressing:** pages by index or name; layers, shapes and connectors by id.
  Ids are short and deterministic (`s1`, `e1`, `layer1`, `page-2`).
- **Rendering:** `utils/drawio_backend.py` finds and runs the real app.
  `core/export.py` validates flag/format combinations up front (the real CLI
  silently ignores invalid ones), exports to a temp file, verifies the result
  (magic bytes, PNG header, PDF page count) and only then moves it into place.

### Command groups

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

### Rendering coverage

Every style the registries emit is a native draw.io style string taken from
the app's own palettes, so the native renderer applies all of them. There is
no translation layer and therefore no rendering gap.

## Known limitations

- Preview bundles (`preview ...`) are not implemented.
- Folder input, `--recursive`, `--check` and `--enable-plugins` are not wrapped.
- `--border` and `--uncompressed` are not wrapped because they have no
  effect in draw.io 24.7.17 (findings 7 and 8). Re-measure on other versions.
- There is no automatic layout; callers choose coordinates.
- Reading diagrams embedded in `.png`/`.svg` is only available through
  `export convert` (the real app does the extraction), not `--project`.
- Shape libraries beyond the General/Misc/Advanced palettes are reachable
  only through `shape add --style "<raw style>"`.
- On Linux the desktop app needs a display (`xvfb-run`) and, as root,
  `--no-sandbox` (added automatically).
