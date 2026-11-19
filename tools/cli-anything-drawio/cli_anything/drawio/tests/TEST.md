# TEST.md — cli-anything-drawio

## Part 1 — Test plan (written before the test code)

### Test inventory

| File | Planned | Needs draw.io |
|------|---------|---------------|
| `test_core.py` | about 150 unit tests | no |
| `test_full_e2e.py` | about 45 E2E tests | yes (hard dependency — tests fail, never skip) |

### Unit test plan (`test_core.py`)

| Module | What is tested | Planned |
|--------|----------------|---------|
| `utils/drawio_xml.py` | style string parse/format/set/unset, bare tokens, invalid keys and values, `KEY=VALUE` parsing; compress/decompress round trip incl. non-ASCII, bad payloads; label escaping and plain-text extraction; number formatting; cell iteration through `<object>` wrappers, id generation and validation, wrapping a cell | 25 |
| `utils/fileio.py` | locked write creates, truncates and replaces; JSON save | 3 |
| `utils/drawio_backend.py` | discovery order (`DRAWIO_PATH`, PATH, default locations), missing-app error text, `Error` line filtering (no process is started) | 6 |
| `core/project.py` | page presets and sizes, create, save/open round trip, compressed files, bare `<mxGraphModel>`, malformed / foreign / unsafe XML, missing file, page-filtered XML, info counts, structural check | 18 |
| `core/pages.py` | list, add (default name, inherited size, preset, position), remove (and the last-page guard), rename, duplicate independence, move, setup, page resolution by index / name / id incl. ambiguous and out-of-range | 16 |
| `core/layers.py` | list, add, rename, show/hide, lock/unlock, remove with contents and cross-layer connectors, last-layer guard, assign, nested-cell guard, resolution errors | 12 |
| `core/shapes.py` | registry integrity (every style parses, type guess is stable), add with defaults / style options / container parent / custom id, auto placement, label escaping (plain, HTML, non-HTML style), list filters, info, label, move, resize, style, custom data, cascading remove, error messages | 26 |
| `core/connectors.py` | presets and arrows, add with preset / raw style / arrows / waypoints / layer, validation errors, list filter, info, label, style, remove | 16 |
| `core/export.py` | format resolution (extension, flag, alias, conflict), every flag/format rule, page selectors, page-range parsing, output verification against synthetic good and bad files | 20 |
| `core/session.py` | undo/redo, redo invalidation, level cap, transaction rollback, history persistence, history discarded after an external edit, status, clear | 12 |
| `drawio_cli.py` (Click `CliRunner`) | help/version, `--json` on success and error, exit codes, auto-save, `--dry-run`, missing project, undo across invocations, human tables, page addressing | 16 |

### E2E test plan (`test_full_e2e.py`)

**Intermediate files** — the `.drawio` the CLI writes is well-formed, has the
`mxfile > diagram > mxGraphModel > root` structure, base cells and geometry,
and reloads identically.

**True backend** — every test runs the real draw.io app and inspects the file
it produced:

| Area | Verified |
|------|----------|
| Backend | executable found, version string, garbage input is reported as an error even though draw.io exits 0 |
| Formats | PNG signature + IHDR size, JPEG SOI, SVG contains the label text, PDF header + page count, XML parses back to the same cells, VSDX is a ZIP |
| Options | `--scale 2` doubles pixel size, `--border` adds 2×border px, `--width` fits, `--transparent` yields an alpha channel, `--embed-diagram` embeds `mxfile`, page selection, `--all-pages`, `--page-range`, layer filter, hidden layer, overwrite guard |
| Unicode | Korean labels survive into the SVG |
| Round trip | CLI file → real draw.io XML export → reload: same ids and labels |
| Convert | `.drawio` → PNG, PNG with embedded diagram → SVG |

**CLI subprocess** — the installed `cli-anything-drawio` command, resolved
with `_resolve_cli()`, no `cwd`: help, version, JSON project creation, full
build-and-export workflows producing real PNG/PDF files, error exit codes,
`--dry-run`, undo across processes, non-ASCII output on a legacy console
encoding.

### Realistic workflow scenarios

1. **Flowchart authoring**
   - Simulates: an agent drawing an approval flow.
   - Operations: new project → start/decision/end shapes with colors →
     labelled connectors → relabel → move → export PNG.
   - Verified: element counts, PNG signature and dimensions, label text in SVG.
2. **Multi-page architecture document**
   - Simulates: a design doc with one page per view.
   - Operations: add pages, rename, containers with nested shapes,
     duplicate a page, reorder → export PDF with all pages and a page range.
   - Verified: PDF page counts match the page selection.
3. **Layered diagram**
   - Simulates: base network diagram with an annotation overlay.
   - Operations: add layer, assign shapes, hide layer, export with and
     without the layer filter.
   - Verified: image size changes when the layer is excluded or hidden.
4. **Iterative refinement with heavy undo/redo**
   - Simulates: an agent correcting its own work.
   - Operations: add, modify, remove, re-add; 30 edits, undo all, redo all.
   - Verified: state after redo equals state before undo; file exports.
5. **Save / load round trip of a complex project**
   - Simulates: handing a diagram between agent and human.
   - Operations: build pages, layers, containers, custom data, waypoints;
     save compressed; reopen; let draw.io re-export to XML; reopen.
   - Verified: ids, labels, parents, data and waypoints are preserved.


## Part 2 — Test results (appended after the run)

Command (installed command required, artifact paths printed):

```
CLI_ANYTHING_FORCE_INSTALLED=1 python -m pytest cli_anything/drawio/tests -v --tb=short -s
```

Environment: Windows 11, Python 3.11.9, draw.io desktop 24.7.17.

### Summary

| | |
|---|---|
| Total | 272 tests |
| `test_core.py` (unit) | 229 |
| `test_full_e2e.py` (real draw.io) | 43 |
| Pass rate | 100% (272 passed, 0 failed, 0 skipped) |
| Execution time | 203.75 s |

`[_resolve_cli] Using installed command: ...cli-anything-drawio.exe` in the
log below confirms the subprocess tests ran against the installed command.

### What the first E2E run caught

The first run against the real app had 33 passes and 10 failures. They are
recorded here because each one was a wrong assumption about draw.io that
only running the real software could expose:

| Finding (draw.io 24.7.17) | Action |
|---|---|
| PDFs keep page objects in compressed `/ObjStm` streams, so the page counter found 0 pages | Harness bug — `verify_output` now inflates the streams (unit + E2E tests added) |
| `--border` changes nothing for PNG, JPG, SVG or PDF (identical size with 0 and 30) | Option removed from the harness and documented |
| XML export is uncompressed with or without `--uncompressed` (identical bytes) | Option removed from the harness and documented |
| `--embed-diagram` PNGs carry the diagram in a `zTXt` chunk named `mxGraphModel` and are written **without an IEND chunk** | `verify_output` reports `embedded_diagram` / `png_complete`; a warning is returned when IEND is missing |
| `--scale 2` gives 245 px for a 124 px image (a few edge pixels are not scaled) | Test compares the ratio instead of exact doubling |
| My arithmetic for an expected image height was off | Test corrected |

### Coverage notes

- Not covered: `preview` bundles (not implemented), folder/recursive export,
  CSV and VSDX **input**, Linux/macOS backends (only Windows was run), the
  interactive prompt_toolkit REPL (the piped REPL is tested).
- `--svg-theme`, `--svg-links-target` and `--embed-svg-images` are validated
  and passed through, but their visual effect is not asserted.
- The PNG page-count/size checks are structural; no pixel-level comparison
  against a reference image is done.

### Full output

```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.0.3, pluggy-1.6.0 -- <home>\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe
rootdir: <home>\cli-anything-test\drawio\agent-harness
plugins: anyio-4.12.1, Faker-40.5.1
collecting ... [_resolve_cli] Using installed command: <python scripts>\cli-anything-drawio.EXE
collected 272 items

test_core.py::TestStyle::test_parse_and_format_round_trip PASSED
test_core.py::TestStyle::test_parse_tolerates_spaces_and_missing_semicolon PASSED
test_core.py::TestStyle::test_empty_style PASSED
test_core.py::TestStyle::test_value_may_contain_equals PASSED
test_core.py::TestStyle::test_style_get PASSED
test_core.py::TestStyle::test_style_set_replaces_in_place_and_appends PASSED
test_core.py::TestStyle::test_style_set_none_makes_bare_token PASSED
test_core.py::TestStyle::test_style_unset PASSED
test_core.py::TestStyle::test_apply_changes_unsets_then_sets PASSED
test_core.py::TestStyle::test_invalid_style_key_rejected[] PASSED
test_core.py::TestStyle::test_invalid_style_key_rejected[a;b] PASSED
test_core.py::TestStyle::test_invalid_style_key_rejected[a=b] PASSED
test_core.py::TestStyle::test_invalid_style_key_rejected[ a] PASSED
test_core.py::TestStyle::test_value_with_semicolon_rejected PASSED
test_core.py::TestStyle::test_parse_assignments PASSED
test_core.py::TestStyle::test_parse_assignments_rejects_bad_pairs[novalue] PASSED
test_core.py::TestStyle::test_parse_assignments_rejects_bad_pairs[=x] PASSED
test_core.py::TestCompression::test_round_trip[<mxGraphModel><root/></mxGraphModel>] PASSED
test_core.py::TestCompression::test_round_trip[<a v="\ud55c\uae00 \ub77c\ubca8 &amp; \xe9moji \u2713"/>] PASSED
test_core.py::TestCompression::test_round_trip[a b+c/d?e=f&g~h!i*j'k(l)] PASSED
test_core.py::TestCompression::test_matches_draw_io_scheme PASSED
test_core.py::TestCompression::test_garbage_raises_format_error PASSED
test_core.py::TestLabelsAndNumbers::test_plain_to_label_escapes_and_breaks_lines PASSED
test_core.py::TestLabelsAndNumbers::test_label_to_plain_strips_markup PASSED
test_core.py::TestLabelsAndNumbers::test_plain_round_trip PASSED
test_core.py::TestLabelsAndNumbers::test_fmt_num[120-120] PASSED
test_core.py::TestLabelsAndNumbers::test_fmt_num[120.0-120] PASSED
test_core.py::TestLabelsAndNumbers::test_fmt_num[10.5-10.5] PASSED
test_core.py::TestLabelsAndNumbers::test_fmt_num[-3.0--3] PASSED
test_core.py::TestCells::test_iter_cells_sees_through_object_wrapper PASSED
test_core.py::TestCells::test_next_id_skips_used_ids PASSED
test_core.py::TestCells::test_validate_new_id PASSED
test_core.py::TestCells::test_wrap_cell_keeps_position_label_and_id PASSED
test_core.py::TestCells::test_parse_rejects_entity_expansion PASSED
test_core.py::TestFileIO::test_creates_file_and_parent_dirs PASSED
test_core.py::TestFileIO::test_rewrite_truncates_longer_content PASSED
test_core.py::TestFileIO::test_locked_save_json PASSED
test_core.py::TestBackendDiscovery::test_env_override_wins PASSED
test_core.py::TestBackendDiscovery::test_env_override_pointing_nowhere_is_an_error PASSED
test_core.py::TestBackendDiscovery::test_falls_back_to_default_install_location PASSED
test_core.py::TestBackendDiscovery::test_missing_app_error_has_install_instructions PASSED
test_core.py::TestBackendDiscovery::test_default_locations_are_listed_for_this_platform PASSED
test_core.py::TestBackendDiscovery::test_error_lines_ignore_chromium_noise PASSED
test_core.py::TestProject::test_create_defaults PASSED
test_core.py::TestProject::test_presets_come_from_the_page_setup_dialog PASSED
test_core.py::TestProject::test_landscape_swaps_size PASSED
test_core.py::TestProject::test_custom_size PASSED
test_core.py::TestProject::test_unknown_format_lists_choices PASSED
test_core.py::TestProject::test_half_custom_size_rejected PASSED
test_core.py::TestProject::test_empty_page_name_rejected PASSED
test_core.py::TestProject::test_save_open_round_trip PASSED
test_core.py::TestProject::test_saved_file_is_plain_uncompressed_xml PASSED
test_core.py::TestProject::test_compressed_save_and_reopen PASSED
test_core.py::TestProject::test_open_bare_graph_model PASSED
test_core.py::TestProject::test_open_rejects_invalid_content[this is not xml-Not well-formed] PASSED
test_core.py::TestProject::test_open_rejects_invalid_content[<svg></svg>-root element is <svg>] PASSED
test_core.py::TestProject::test_open_rejects_invalid_content[<mxfile></mxfile>-no <diagram>] PASSED
test_core.py::TestProject::test_open_rejects_invalid_content[<mxfile><diagram>!!notbase64!!</diagram></mxfile>-neither XML nor compressed] PASSED
test_core.py::TestProject::test_open_missing_file_says_how_to_create_it PASSED
test_core.py::TestProject::test_to_xml_can_limit_to_one_page PASSED
test_core.py::TestProject::test_info_counts_elements PASSED
test_core.py::TestProject::test_check_passes_for_valid_project PASSED
test_core.py::TestProject::test_check_reports_dangling_and_duplicate PASSED
test_core.py::TestPages::test_list PASSED
test_core.py::TestPages::test_add_uses_next_name_and_inherits_size PASSED
test_core.py::TestPages::test_add_with_format_and_landscape PASSED
test_core.py::TestPages::test_add_at_position PASSED
test_core.py::TestPages::test_added_pages_get_unique_ids PASSED
test_core.py::TestPages::test_remove PASSED
test_core.py::TestPages::test_cannot_remove_only_page PASSED
test_core.py::TestPages::test_rename PASSED
test_core.py::TestPages::test_duplicate_is_independent PASSED
test_core.py::TestPages::test_move PASSED
test_core.py::TestPages::test_resolve_by_index_name_and_id PASSED
test_core.py::TestPages::test_resolve_out_of_range PASSED
test_core.py::TestPages::test_resolve_unknown_name_lists_pages PASSED
test_core.py::TestPages::test_resolve_ambiguous_name PASSED
test_core.py::TestPages::test_setup_size_background_and_grid PASSED
test_core.py::TestPages::test_setup_orientation_only PASSED
test_core.py::TestLayers::test_default_layer PASSED
test_core.py::TestLayers::test_add PASSED
test_core.py::TestLayers::test_add_with_custom_id_and_empty_name PASSED
test_core.py::TestLayers::test_rename PASSED
test_core.py::TestLayers::test_hide_and_show PASSED
test_core.py::TestLayers::test_lock_and_unlock PASSED
test_core.py::TestLayers::test_remove_takes_contents_and_cross_layer_connectors PASSED
test_core.py::TestLayers::test_cannot_remove_only_layer PASSED
test_core.py::TestLayers::test_assign_moves_cells PASSED
test_core.py::TestLayers::test_assign_rejects_nested_and_unknown_cells PASSED
test_core.py::TestLayers::test_resolve_unknown_layer_lists_layers PASSED
test_core.py::TestLayers::test_layer_index_and_layer_of_nested_cell PASSED
test_core.py::TestShapes::test_registry_styles_are_well_formed PASSED
test_core.py::TestShapes::test_type_guess_is_stable_for_every_registry_entry PASSED
test_core.py::TestShapes::test_type_guess[rounded=1;whiteSpace=wrap;html=1;fillColor=#fff;-rounded] PASSED
test_core.py::TestShapes::test_type_guess[ellipse;whiteSpace=wrap;html=1;aspect=fixed;-circle] PASSED
test_core.py::TestShapes::test_type_guess[swimlane;horizontal=0;-hcontainer] PASSED
test_core.py::TestShapes::test_type_guess[shape=mxgraph.aws4.lambda;html=1;-custom] PASSED
test_core.py::TestShapes::test_add_uses_palette_defaults PASSED
test_core.py::TestShapes::test_add_with_geometry_and_style_properties PASSED
test_core.py::TestShapes::test_auto_placement_stacks_downwards PASSED
test_core.py::TestShapes::test_unknown_type_lists_available PASSED
test_core.py::TestShapes::test_custom_id_and_duplicate_id PASSED
test_core.py::TestShapes::test_non_positive_size_rejected PASSED
test_core.py::TestShapes::test_nest_in_container PASSED
test_core.py::TestShapes::test_parent_and_layer_are_exclusive PASSED
test_core.py::TestShapes::test_plain_label_is_escaped_for_html_styles PASSED
test_core.py::TestShapes::test_html_label_is_kept_raw PASSED
test_core.py::TestShapes::test_label_is_not_escaped_without_html_style PASSED
test_core.py::TestShapes::test_default_label_comes_from_palette PASSED
test_core.py::TestShapes::test_list_filters PASSED
test_core.py::TestShapes::test_info_lists_connections PASSED
test_core.py::TestShapes::test_missing_shape_error_lists_ids PASSED
test_core.py::TestShapes::test_set_label PASSED
test_core.py::TestShapes::test_move_absolute_and_relative PASSED
test_core.py::TestShapes::test_move_requires_one_mode PASSED
test_core.py::TestShapes::test_resize PASSED
test_core.py::TestShapes::test_style_set_and_unset PASSED
test_core.py::TestShapes::test_custom_data_survives_save_and_load PASSED
test_core.py::TestShapes::test_invalid_data_keys_rejected[id] PASSED
test_core.py::TestShapes::test_invalid_data_keys_rejected[label] PASSED
test_core.py::TestShapes::test_invalid_data_keys_rejected[1abc] PASSED
test_core.py::TestShapes::test_invalid_data_keys_rejected[bad-key] PASSED
test_core.py::TestShapes::test_invalid_data_keys_rejected[sp ace] PASSED
test_core.py::TestShapes::test_remove_cascades_to_children_and_connectors PASSED
test_core.py::TestConnectors::test_presets_are_well_formed PASSED
test_core.py::TestConnectors::test_default_preset_is_the_gui_default_edge_style PASSED
test_core.py::TestConnectors::test_arrows PASSED
test_core.py::TestConnectors::test_add_default PASSED
test_core.py::TestConnectors::test_add_with_preset_arrows_and_dash PASSED
test_core.py::TestConnectors::test_raw_style_replaces_preset PASSED
test_core.py::TestConnectors::test_unknown_style_and_arrow PASSED
test_core.py::TestConnectors::test_endpoints_must_be_shapes PASSED
test_core.py::TestConnectors::test_waypoints PASSED
test_core.py::TestConnectors::test_parse_waypoint PASSED
test_core.py::TestConnectors::test_connector_inherits_source_layer PASSED
test_core.py::TestConnectors::test_list_can_filter_by_shape PASSED
test_core.py::TestConnectors::test_info PASSED
test_core.py::TestConnectors::test_set_label PASSED
test_core.py::TestConnectors::test_set_style PASSED
test_core.py::TestConnectors::test_remove PASSED
test_core.py::TestExportValidation::test_format_resolution[png-None-png] PASSED
test_core.py::TestExportValidation::test_format_resolution[None-a.PNG-png] PASSED
test_core.py::TestExportValidation::test_format_resolution[jpeg-None-jpg] PASSED
test_core.py::TestExportValidation::test_format_resolution[None-dir/a.jpeg-jpg] PASSED
test_core.py::TestExportValidation::test_format_resolution[pdf-out.dat-pdf] PASSED
test_core.py::TestExportValidation::test_format_resolution[svg-x.svg-svg] PASSED
test_core.py::TestExportValidation::test_format_resolution_errors[pdf-a.png-conflicts] PASSED
test_core.py::TestExportValidation::test_format_resolution_errors[gif-None-Unknown export format] PASSED
test_core.py::TestExportValidation::test_format_resolution_errors[None-a.txt-Cannot tell] PASSED
test_core.py::TestExportValidation::test_format_resolution_errors[None-None-Cannot tell] PASSED
test_core.py::TestExportValidation::test_flags_for_valid_png_options PASSED
test_core.py::TestExportValidation::test_flags_for_pdf_svg_jpg_xml PASSED
test_core.py::TestExportValidation::test_options_that_do_nothing_in_draw_io_are_not_offered[border] PASSED
test_core.py::TestExportValidation::test_options_that_do_nothing_in_draw_io_are_not_offered[uncompressed] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[pdf-options0] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options1] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options2] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options3] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options4] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[jpg-options5] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options6] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[png-options7] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[pdf-options8] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[vsdx-options9] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[svg-options10] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[xml-options11] PASSED
test_core.py::TestExportValidation::test_option_for_wrong_format_is_rejected[xml-options12] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[png-options0] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[png-options1] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[pdf-options2] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[jpg-options3] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[jpg-options4] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[svg-options5] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[svg-options6] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[png-options7] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[png-options8] PASSED
test_core.py::TestExportValidation::test_out_of_range_values_are_rejected[pdf-options9] PASSED
test_core.py::TestExportValidation::test_page_selectors_are_exclusive PASSED
test_core.py::TestExportValidation::test_parse_page_range PASSED
test_core.py::TestExportValidation::test_render_checks_page_index_before_running PASSED
test_core.py::TestExportValidation::test_render_refuses_to_overwrite PASSED
test_core.py::TestExportValidation::test_verify_png_reads_header PASSED
test_core.py::TestExportValidation::test_verify_png_sees_embedded_diagram_and_missing_iend PASSED
test_core.py::TestExportValidation::test_verify_pdf_counts_pages PASSED
test_core.py::TestExportValidation::test_verify_pdf_counts_pages_inside_object_streams PASSED
test_core.py::TestExportValidation::test_verify_accepts_valid_signatures[jpg-\xff\xd8\xff\xe0rest] PASSED
test_core.py::TestExportValidation::test_verify_accepts_valid_signatures[svg-<?xml version="1.0"?><svg/>] PASSED
test_core.py::TestExportValidation::test_verify_accepts_valid_signatures[vsdx-PK\x03\x04rest] PASSED
test_core.py::TestExportValidation::test_verify_accepts_valid_signatures[xml-<mxfile><diagram/></mxfile>] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[png] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[jpg] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[pdf] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[svg] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[vsdx] PASSED
test_core.py::TestExportValidation::test_verify_rejects_wrong_content[xml] PASSED
test_core.py::TestExportValidation::test_verify_rejects_empty_and_missing PASSED
test_core.py::TestSession::test_empty_session PASSED
test_core.py::TestSession::test_undo_and_redo PASSED
test_core.py::TestSession::test_nothing_to_undo_or_redo PASSED
test_core.py::TestSession::test_new_change_clears_redo PASSED
test_core.py::TestSession::test_undo_depth_is_capped PASSED
test_core.py::TestSession::test_transaction_rolls_back_on_error PASSED
test_core.py::TestSession::test_save_requires_a_path PASSED
test_core.py::TestSession::test_history_persists_between_sessions PASSED
test_core.py::TestSession::test_history_is_dropped_after_external_edit PASSED
test_core.py::TestSession::test_corrupt_history_file_is_ignored PASSED
test_core.py::TestSession::test_clear_history_removes_sidecar PASSED
test_core.py::TestSession::test_undo_clamps_current_page PASSED
test_core.py::TestCLI::test_help_lists_all_groups PASSED
test_core.py::TestCLI::test_version PASSED
test_core.py::TestCLI::test_every_command_has_help PASSED
test_core.py::TestCLI::test_new_requires_output_in_one_shot_mode PASSED
test_core.py::TestCLI::test_new_refuses_to_overwrite PASSED
test_core.py::TestCLI::test_new_and_info_json PASSED
test_core.py::TestCLI::test_mutation_is_saved_automatically PASSED
test_core.py::TestCLI::test_dry_run_does_not_touch_the_file PASSED
test_core.py::TestCLI::test_error_is_json_with_exit_code_1 PASSED
test_core.py::TestCLI::test_failed_mutation_leaves_file_untouched PASSED
test_core.py::TestCLI::test_missing_project_file_and_missing_option PASSED
test_core.py::TestCLI::test_unknown_command_is_a_usage_error PASSED
test_core.py::TestCLI::test_undo_works_across_invocations PASSED
test_core.py::TestCLI::test_human_output_is_a_table PASSED
test_core.py::TestCLI::test_check_exit_code_reflects_result PASSED
test_core.py::TestCLI::test_page_option_accepts_names PASSED
test_core.py::TestCLI::test_layer_commands PASSED
test_core.py::TestCLI::test_export_options_are_validated_before_draw_io_runs PASSED
test_core.py::TestCLI::test_listing_commands_need_no_project PASSED
test_core.py::TestCLI::test_repl_session_with_piped_input PASSED
test_core.py::TestCLI::test_repl_save_and_reopen PASSED
test_full_e2e.py::TestIntermediateFiles::test_saved_file_has_native_structure PASSED
test_full_e2e.py::TestIntermediateFiles::test_reload_gives_identical_model PASSED
test_full_e2e.py::TestIntermediateFiles::test_compressed_file_holds_deflated_model PASSED
test_full_e2e.py::TestBackend::test_real_app_is_found_and_reports_version
  draw.io 24.7.17 at C:\Program Files\draw.io\draw.io.exe
PASSED
test_full_e2e.py::TestBackend::test_app_exits_zero_on_failure_so_output_is_checked PASSED
test_full_e2e.py::TestBackend::test_garbage_input_is_reported_as_failure PASSED
test_full_e2e.py::TestRenderFormats::test_png
  PNG: <tmp>\pytest-3\test_png0\flow.png (8,568 bytes)
PASSED
test_full_e2e.py::TestRenderFormats::test_jpg
  JPG: <tmp>\pytest-3\test_jpg0\flow.jpg (4,473 bytes)
PASSED
test_full_e2e.py::TestRenderFormats::test_svg_contains_the_labels
  SVG: <tmp>\pytest-3\test_svg_contains_the_labels0\flow.svg (5,348 bytes)
PASSED
test_full_e2e.py::TestRenderFormats::test_pdf
  PDF: <tmp>\pytest-3\test_pdf0\flow.pdf (17,712 bytes)
PASSED
test_full_e2e.py::TestRenderFormats::test_xml_export_reloads_with_the_same_cells
  XML: <tmp>\pytest-3\test_xml_export_reloads_with_t0\flow.xml (1,662 bytes)
PASSED
test_full_e2e.py::TestRenderFormats::test_vsdx_is_a_visio_package
  VSDX: <tmp>\pytest-3\test_vsdx_is_a_visio_package0\flow.vsdx (55,746 bytes)
PASSED
test_full_e2e.py::TestRenderOptions::test_scale_doubles_pixel_size PASSED
test_full_e2e.py::TestRenderOptions::test_width_fits_the_image PASSED
test_full_e2e.py::TestRenderOptions::test_transparent_background_has_alpha_channel PASSED
test_full_e2e.py::TestRenderOptions::test_embed_diagram_makes_png_editable PASSED
test_full_e2e.py::TestRenderOptions::test_embedded_payload_uses_the_compression_scheme_we_implement PASSED
test_full_e2e.py::TestRenderOptions::test_page_selection PASSED
test_full_e2e.py::TestRenderOptions::test_pdf_page_selectors
  PDF (3 pages): <tmp>\pytest-3\test_pdf_page_selectors0\all.pdf (15,166 bytes)
PASSED
test_full_e2e.py::TestRenderOptions::test_layer_filter PASSED
test_full_e2e.py::TestRenderOptions::test_hidden_layer_is_not_rendered PASSED
test_full_e2e.py::TestRenderOptions::test_overwrite_guard_and_replacement PASSED
test_full_e2e.py::TestRenderOptions::test_korean_labels_reach_the_output
  SVG (Korean): <tmp>\pytest-3\test_korean_labels_reach_the_o0\ko.svg (4,056 bytes)
PASSED
test_full_e2e.py::TestRenderOptions::test_shape_styles_are_applied_by_the_renderer PASSED
test_full_e2e.py::TestRoundTrip::test_file_survives_the_real_app PASSED
test_full_e2e.py::TestConvert::test_drawio_file_to_png PASSED
test_full_e2e.py::TestConvert::test_png_with_embedded_diagram_to_svg PASSED
test_full_e2e.py::TestConvert::test_missing_input PASSED
test_full_e2e.py::TestWorkflows::test_flowchart_authoring
  PNG: <tmp>\pytest-3\test_flowchart_authoring0\approval.png (74,104 bytes)
PASSED
test_full_e2e.py::TestWorkflows::test_multi_page_architecture_document
  PDF: <tmp>\pytest-3\test_multi_page_architecture_d0\arch.pdf (27,629 bytes)
PASSED
test_full_e2e.py::TestWorkflows::test_layered_diagram PASSED
test_full_e2e.py::TestWorkflows::test_iterative_refinement_with_heavy_undo_redo
  PNG: <tmp>\pytest-3\test_iterative_refinement_with0\iter.png (26,974 bytes)
PASSED
test_full_e2e.py::TestWorkflows::test_complex_project_save_load_round_trip PASSED
test_full_e2e.py::TestCLISubprocess::test_help PASSED
test_full_e2e.py::TestCLISubprocess::test_version PASSED
test_full_e2e.py::TestCLISubprocess::test_backend_info_json PASSED
test_full_e2e.py::TestCLISubprocess::test_project_new_json PASSED
test_full_e2e.py::TestCLISubprocess::test_full_flowchart_to_png_workflow
  PNG: <tmp>\pytest-3\test_full_flowchart_to_png_wor0\flow.png (20,827 bytes)
PASSED
test_full_e2e.py::TestCLISubprocess::test_multi_page_pdf_workflow
  PDF: <tmp>\pytest-3\test_multi_page_pdf_workflow0\doc.pdf (12,420 bytes)
PASSED
test_full_e2e.py::TestCLISubprocess::test_error_exit_code_and_json PASSED
test_full_e2e.py::TestCLISubprocess::test_dry_run_and_undo_across_processes PASSED
test_full_e2e.py::TestCLISubprocess::test_non_ascii_output_on_legacy_console_encoding PASSED
test_full_e2e.py::TestCLISubprocess::test_repl_over_a_pipe PASSED

======================= 272 passed in 203.75s (0:03:23) =======================
```
