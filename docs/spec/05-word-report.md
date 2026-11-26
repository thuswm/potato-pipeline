# 05 — Word Report Block

## 1. Purpose

The final block of a pipeline: produce a Word (`.docx`) technical report containing tables,
values, figures and text from the pipeline. v1 generates the document itself; a later version
fills in a **user-supplied Word template**. The design is made so that the template mode can be
added without changing pipelines.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| REP-01 | Output a `.docx` file that opens in Microsoft Word (and LibreOffice) | M |
| REP-02 | Inputs: any number of report **items** — tables, scalars, text, figures | M |
| REP-03 | Each item has a **name** (key), caption/heading and formatting options | M |
| REP-04 | **Generated mode (v1):** document built from an ordered list of sections: heading, paragraph text, table, figure, value | M |
| REP-05 | Tables: column selection, number format per column (e.g. `0.00`, `0.0E+00`), max rows, optional highlighting of rows where a condition holds (e.g. `MS < 0`) | M |
| REP-06 | Figures inserted as PNG at a configurable width and DPI, with caption | M |
| REP-07 | Scalars inserted in text with a format and unit, e.g. "Minimum margin of safety: {min_ms:.2f}" | M |
| REP-08 | Report metadata: title, document number, revision, author, date — fillable from pipeline variables | S |
| REP-09 | Uses the styles of a base `.docx` if given (fonts, heading styles, page layout, header/footer) | S |
| REP-10 | **Template mode (later):** the user supplies a Word file with placeholders; the tool fills them in | L |
| REP-11 | **The report contains only what the user places in it** — nothing is added automatically | M |
| REP-12 | Optional section kind `traceability` the user *may* place: pipeline file, input files (modification date / hash), Nastran version, tool version | S |
| REP-13 | Units are always visible: table headers carry units (`VON_MISES [MPa]`), scalars are printed with their unit, and a section kind `units` states the unit system (spec 00 §5.1) | M |
| REP-14 | **Section editor** in the property panel instead of JSON: add, remove, duplicate, reorder sections, fields per section kind (§3.4) | M |
| REP-15 | **Items list:** connected items with name, type and source; the name can be set (stored as the connector name), duplicates and unused items are shown (§3.4) | M |
| REP-16 | Item drop-downs filtered by type; table columns offered from the last run; per-column formats (§3.4) | M |
| REP-17 | Text fields: *Insert value* for `{placeholders}`, live validation, live preview with the values of the last run (§3.4) | M |
| REP-18 | Problems (missing item, wrong type, unknown column, invalid format, unknown placeholder) shown on the section before the run, by the same function the run uses (§3.4) | M |
| REP-19 | Report metadata edited in the same panel | M |
| REP-20 | Drag and drop reordering, report outline preview, copying sections between reports | L |

## 3. Design

### 3.1 Block

**`report.word`** — "Word report"

| | |
|---|---|
| Inputs | `items: Any` (multiple) — every connection has a name (connection name, else derived from the source; spec 01 §3.2) |
| Outputs | `file: Text` (path of the written report); the block always runs (ENG-17) |
| Params | `output_file` (relative to the output folder), `mode` (`generated`; `template` later), `base_document` (optional .docx for styles, relative to the pipeline), `metadata` (`title`, `document_number`, `revision`, `author`, `date` — empty date = run date), `sections` (ordered list) |

### 3.2 Generated mode (v1) — library: python-docx

`sections` is an ordered list edited in the GUI, e.g.:

```json
[
  {"kind": "heading",   "text": "1 Introduction", "level": 1},
  {"kind": "paragraph", "text": "This report presents the stress check of rib {rib_id}."},
  {"kind": "table",     "item": "margins", "caption": "Margins of safety",
   "columns": ["EID", "SUBCASE", "VON_MISES", "MS"], "formats": {"VON_MISES": "0.0", "MS": "0.00"},
   "highlight": "MS < 0"},
  {"kind": "figure",    "item": "plot", "caption": "Margin per element", "width_cm": 15},
  {"kind": "paragraph", "text": "Minimum margin of safety: {min_ms:.2f}"},
  {"kind": "units"},
  {"kind": "traceability"}
]
```

Section kinds: `title` (title and a small metadata table), `heading` (level 1–3),
`paragraph`, `value` (`label: value unit`), `table`, `figure`, `page_break`, `units`,
`traceability`.

`{name}` / `{name:format}` placeholders in text refer to connected scalar/text items and to
the metadata fields (Python format-string syntax, evaluated safely — attribute access and
indexing are not allowed; unknown names give an error listing the available ones).

Tables: caption above ("Table N: …"), figures: caption below ("Figure N: …"); numbers are
Word `SEQ` fields so Word cross-references work. Number formats accept Excel style (`0.00`,
`#,##0.0`, `0.0E+00`, `0.0%`), Python specs (`.3e`) or `auto` (4 significant digits).
`default_format` applies to decimal columns only; integer columns (EID, SUBCASE) are printed
as they are unless a column format is given. Header rows are bold, shaded and repeated on
each page; numbers are right-aligned; highlighted rows are shaded light red. Two-level
tables (load case tables) get two header rows with merged cells; highlighting is not
available for them. `max_rows` adds "(First N of M rows shown.)".

`traceability` lists: generation time and tool version, the pipeline file and every input
file read upstream of the report (Nastran results, Excel/CSV inputs, Python scripts, base
document) with modification time and SHA-256 prefix; for `.h5` files also the Nastran
version and a SYNTHETIC TEST DATA mark for synthetic files. Connected items that are used
nowhere are reported in the block log.

If `base_document` is set, the report is built on top of a copy of it, so its styles,
headers/footers and page setup are used (REP-09). This is already a light form of "company
template".

A placeholder for a scalar that carries a unit prints the number followed by its unit, e.g.
`{allowable:.1f}` → `410.0 MPa`.

**Test base document.** There is no company template yet, so the tool ships a simple test
base document `src/apb/blocks/report/resources/test_base.docx` (A4, heading styles 1–3,
table and caption styles, header with title/document number, footer with page number). It is
used **during development only**; you replace it with the real company template before
deployment (just a file swap, no code change). It is **generated by a script** in the repository (`tools/make_test_base.py`, python-docx) so it is
reproducible and diff-able, and it is the default when `base_document` is empty. A real
company template later simply replaces it.

**Windows note:** if the output `.docx` is open in Word, writing fails due to file locking;
the block reports a clear message (spec 00 §3.1).

### 3.3 Template mode (later) — library: docxtpl

The user writes a normal Word document with Jinja2 placeholders, e.g.:

```
Minimum margin of safety: {{ min_ms | fmt("0.00") }}
{{ table(margins, columns=["EID", "MS"]) }}
{{ figure(plot, width_cm=15) }}
{% if min_ms < 0 %}The structure does NOT meet the requirement.{% endif %}
```

The block renders the template with the same named items as in generated mode, so switching
a pipeline from generated to template mode only means changing the block's parameters — the
upstream pipeline is untouched. Template mode will get its own detailed spec when you supply
a template.

### 3.4 Report section editor (REP-14 … REP-20)

The report block's property panel replaces the JSON text field for `sections` with an
editor. The stored format does not change: the editor reads and writes the same ordered list
of sections (§3.2), so existing pipelines open as they are and the command line is
unaffected.

```
Word report
Output file  [ output/test_case_report.docx ]      Base document [ … ]
▸ Metadata   title · document number · revision · author · date

Items (connected to this block)
  effective_mass   Table   from Nastran HDF5 results            [ rename ]
  global_margins   Table   from Python (Global loads)           [ rename ]
  f1_check         Text    from Requirement check               [ rename ]
  min_ms_global    Scalar  from Python (Global loads)           [ rename ]  ⚠ used nowhere

Sections                                                     [ + Add section ▾ ]
  1  Title                                                     ↑ ↓ ⧉ 🗑
  2  H1  Introduction                                          ↑ ↓ ⧉ 🗑
  3  ¶   This report presents the modal analysis and …         ↑ ↓ ⧉ 🗑
  4  ▦   Table · effective_mass · "Modal effective mass"        ↑ ↓ ⧉ 🗑
       Item      [ effective_mass ▾ ]   Caption [ Modal effective mass ]
       Columns   [ MODE, FREQUENCY, T1, T2, T3, R1, R2, R3 ▾ ]   Max rows [   ]
       Formats   FREQUENCY [ 0.00 ]  T1 [ 0.0 ]  …            Default [ auto ]
       Highlight [ MS < 0 ]                               ☑ Units in header
  5  ▦   Table · global_margins · ⚠ column 'MSS' is not in the table
```

**Sections list (REP-14).** One row per section: number, kind icon and a one-line summary
(heading text, first words of a paragraph, kind · item · caption). Clicking a row expands
it (one open at a time) to the fields of its kind (§3.2): heading text and level; paragraph
text; value item, label and format; table item, caption, columns, formats, default format,
maximum rows, highlight expression, units in header; figure item, caption, width, DPI;
traceability heading. *Title*, *page break* and *units* have no fields. Per row: move up,
move down, duplicate, delete (no confirmation; the whole editor is covered by undo once
GUI-13 exists). "Add section" offers the nine kinds and inserts after the open row, else at
the end. Changing a field stores the sections immediately, like every other parameter, and
marks the block stale.

**Items (REP-15).** The list shows every connected item as the report knows it: name, type
and source block. The name is what sections and `{placeholders}` refer to. It defaults to the
name derived from the connector (spec 01 §3.2: source port name for a block with several
outputs, else the block label), which collides easily — two Python blocks both deliver
`margins`, and the engine then renames the second to `margins_2` depending on connector
order. *Rename* therefore sets the **explicit name of the connector** (`name` of the
connection in the pipeline file, which the engine already supports): letters, digits and
`_`, starting with a letter, unique among the items of the block. Renaming an item rewrites
the references to it in the sections (item fields and `{placeholders}`) after asking once.
Items that no section uses are marked "used nowhere" (the run only logged this before).

**Pickers (REP-16).** The item drop-down of a section lists only items that fit: *table* —
Table; *figure* — Figure; *value* — Scalar and Text; items of type `Any` fit everywhere.
**Columns** are offered from the table of the last run (or of the preview of the source
block); if the item has not been run yet the field is a plain comma-separated text field.
Formats are edited per column in a small grid listing the chosen columns (all columns if
none are chosen) with the unit of each column shown; the format fields accept the same
forms as §3.2 and show a red border for an invalid format. The grid is headed *Number
format per column* with a help line ("How the numbers of each column are written. Excel
style: `0.00` two decimals, `0.0E+00` exponent, `#,##0` thousands separator, `0.0%`
percent. Empty: the default format below."). An empty field shows as placeholder what
applies instead: the *Default format* (or `auto`) for decimal columns, `as is` for integer
and text columns. When the table of the last run is known, each row shows an example: the
column's first value as it will be written (`0.123456 → 0.12`), updated as the format is
typed (2026-11-26, Windows re-test).

**Mass in kg (2026-11-26).** A table section has `mass_in_kg: bool = True` (switch *Mass in
kg* next to *Units in header*): columns in `t` / `t·mm²` are written in `kg` / `kg·mm²`
(×1000), spec 00 §5.1; the format examples and the units in the format grid follow the
switch. When a table section converts mass, the units statement says "masses in t (kg in
the tables)" for `SI_mm`.

**Text and placeholders (REP-17).** Heading, paragraph, label and caption fields get an
*Insert value* button: a menu of the connected scalar and text items and the metadata fields
(`title`, `document_number`, `revision`, `author`, `date`) that inserts `{name}` — for a
scalar `{name:.2f}` — at the cursor. Placeholders are checked as the text is edited:
unknown names are underlined in red with the available names in the tooltip. If the item
has a value from the last run, the field shows the filled text underneath, e.g. "The
lowest eigenfrequency is 25.71 Hz."; otherwise it says "run to see the values".

**Problems before the run (REP-18).** Each section row carries a warning icon with the
message when: the item is not connected or has the wrong type, a column is not in the table
(only when the table of the last run is known), a format is invalid, or a placeholder is
unknown. The messages are the ones the run would give, produced by one shared function
`check_sections(sections, items, columns)` in `apb.blocks.report.editing`, which the
writer also uses before building the document, so editor and run cannot disagree.

**Metadata (REP-19)** is the existing group of fields (title, document number, revision,
author, date) in a collapsible group at the top of the panel; values may be `${variable}`.

**Not in v1 (REP-20):** drag and drop reordering (up/down buttons only), an outline or page
preview of the report, copying sections between reports, a rich text editor (paragraphs are
plain text with placeholders, as the writer expects). Opening the written file works as
today through the *Open* button in the block's preview.

Implementation notes: pure functions on the list of section dicts —
`new_section(kind)`, `insert`, `move`, `duplicate`, `delete`, `describe`, `check_sections`,
`rename_item_references` — in `blocks/report/editing.py` (tested without a GUI); the widgets
in `gui/report_editor.py`; `EditorState.item_infos(block_id)` and
`EditorState.rename_connector(key, name)`; item names from the engine's `input_names`, with
the type from the port types of the source block.

Decisions made while implementing:
- The **run uses the check too**: `WordReport.run` calls `check_sections` first and refuses
  to build with all problems listed ("Section 4 (Table · global_margins): Column(s) […] not
  in item …"), so a report is never half written. The writer's own errors remain as a
  safety net for what is only known while building (e.g. a highlight expression).
- **Renaming can rename more than one item.** With the derived names `margins` and
  `margins_2`, naming the first connector `global_margins` makes the second one `margins`.
  `rename_connector` therefore returns every old → new name that changed, and the editor
  rewrites the references of all of them *at once* (`rename_items`), so names can even swap.
  Only a name that another item already has is refused (the value would silently get a
  `_2` suffix). Invalid and taken names are refused before the confirmation question.
- Section fields that are empty are removed from the stored section (`caption`, `columns`,
  `formats` …), so the pipeline file only contains what was set.
- Item types come from the ports of the source blocks (known before any run); columns and
  values for the pickers and the live preview come from the last run.

## 4. Out of scope (v1)

Template mode, PDF export (can later be done via LibreOffice headless or Word), table of
contents fields update (Word updates TOC on open/F9), equations, cross-references.

## 5. Open questions

None. Decided: test base document for development, replaced by the real template before
deployment (§3.2); the report contains only user-placed content (REP-11). The editor (§3.4)
was added in review round 3.
