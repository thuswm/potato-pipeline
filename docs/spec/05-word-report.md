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

## 4. Out of scope (v1)

Template mode, PDF export (can later be done via LibreOffice headless or Word), table of
contents fields update (Word updates TOC on open/F9), equations, cross-references.

## 5. Open questions

None. Decided: test base document for development, replaced by the real template before
deployment (§3.2); the report contains only user-placed content (REP-11).
