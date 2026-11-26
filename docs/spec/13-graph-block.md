# 13 — Graph Block

Status: **first proposal** (2026-11-26), for round 7.

## 1. Purpose

Turn a table into a plot for the report without writing Python. Today a plot needs a Python
block (template *Plot*, spec 04). Plots of results are needed in almost every report
(effective mass per mode, margins per fastener, forces per load case), so a block with a
choice of plot types belongs in the library. The Python block stays the way to make
anything the Graph block cannot.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| GR-01 | Block *Graph*: one table in, one figure out (`Figure` port, goes to the Word report like any figure) | M |
| GR-02 | Plot types: line, scatter, bar (grouped or stacked), horizontal bar, step, area, histogram, box | M |
| GR-03 | X column and one or more Y columns, picked from the columns of the connected table | M |
| GR-04 | *Series by*: one series per value of a column (e.g. `SUBCASE`, `LABEL`, `EID`) | M |
| GR-05 | Axis labels default to `column [unit]` from the table's units; title, labels and legend can be set; `{placeholders}` as in the report are not needed | M |
| GR-06 | Linear or log axes; optional axis limits; grid on/off | M |
| GR-07 | Reference lines (horizontal or vertical) with label, e.g. `MS = 0` or a frequency requirement; the value can come from a connected scalar | M |
| GR-08 | Figure size in cm and resolution (dpi), so the fonts have their true size in the report | M |
| GR-09 | One house style for all figures (fonts, colours safe for colour-blind readers, line widths), shared with the Python *Plot* template | S |
| GR-10 | The panel shows only the fields of the chosen plot type, and the preview updates while editing (the block re-runs from the stored input) | S |
| GR-11 | Mass columns are plotted in kg, as in the other outputs (spec 00 §5.1, switch *Mass in kg*) | S |
| GR-12 | Bar labels (value written on each bar) and highlighting of bars below a limit (e.g. negative margins in red) | S |
| GR-13 | Several tables in one graph (e.g. two analyses compared) | L |
| GR-14 | Interactive plots (zoom, hover) in the GUI preview | L |

## 3. Design

### 3.1 Block

`figure.graph`, title *Graph*, category **Figures** (new; later also the FE view block,
spec 14). Library: **matplotlib** (already a dependency, renders headless with `Agg`).

| Port | Type | |
|------|------|-|
| `table` (in) | Table | The data |
| `reference` (in, optional, multiple) | Scalar | Values for reference lines (GR-07) |
| `figure` (out) | Figure | The plot |

### 3.2 Parameters

| Parameter | Type / values | Used by | Default |
|-----------|---------------|---------|---------|
| `kind` | `line`, `scatter`, `bar`, `barh`, `step`, `area`, `histogram`, `box` | all | `line` |
| `x` | column | all but histogram, box | first column |
| `y` | list of columns | all | first numeric column after `x` |
| `series_by` | column or empty | all but histogram | empty |
| `stacked` | bool | bar, barh, area | false |
| `bins` | int or `auto` | histogram | `auto` |
| `markers` | bool | line, step | true when ≤ 30 points |
| `sort_x` | bool | line, step, area | true |
| `title` | text | all | empty (the report caption is the usual title) |
| `x_label`, `y_label` | text | all | `column [unit]`; several Y columns with the same unit: `unit` only |
| `legend` | `auto`, `off`, `inside`, `below` | all | `auto` (shown when there is more than one series) |
| `x_scale`, `y_scale` | `linear`, `log` | all | `linear` |
| `x_min`, `x_max`, `y_min`, `y_max` | number or empty | all | empty = automatic |
| `grid` | bool | all | true |
| `reference_lines` | rows: `axis` (`x`/`y`), `value` (number or name of a connected scalar), `label`, `style` (`solid`/`dashed`) | all | none |
| `bar_labels` | `off`, `value`, format (e.g. `0.00`, same forms as spec 05) | bar, barh | `off` |
| `highlight_below` | number or empty | bar, barh | empty |
| `width_cm`, `height_cm` | number | all | 16 × 9 |
| `dpi` | int | all | 200 |
| `mass_in_kg` | bool | all | true |

Validation before the run (TAB-04): columns must exist, Y columns must be numeric (X may
be text: then categories), log axes need positive values (clear message naming the column
and the first bad row).

### 3.3 Series and categories

- **Several Y columns, no *Series by*:** one series per Y column (legend = column name).
- ***Series by* set:** the table is grouped by that column; one series per group and Y
  column (legend = group value, or `group – column` when there are several Y columns).
  Groups keep the order of first appearance; at most 20 series (more → error suggesting a
  filter), to keep the plot readable.
- **Text X column (bar/barh/box):** categories in table order (e.g. `EID` as text).
- **Bar, grouped:** bars side by side per category; **stacked**: on top of each other.

### 3.4 Style (GR-09)

`apb.figures.style` holds one `rcParams` set used by the Graph block and offered to Python
scripts (`from apb.figures import house_style`): sans-serif font 9 pt at the set size,
Okabe–Ito colour cycle (colour-blind safe), thin grid, no top/right spines, tight layout.
The figure is created at `width_cm × height_cm`, so a 16 cm wide figure placed at 16 cm in
Word shows 9 pt text. The report figure section's `width_cm` defaults to the figure's width
when the figure carries it (`fig.apb_width_cm`).

### 3.5 Panel (GR-10)

Standard panel (spec 11): *Data* (kind as icon buttons, X, Y, *Series by*), *Axes*
(labels, scales, limits, grid), *Reference lines* (row editor), *Look* (legend, markers,
bar labels, highlight, size, dpi) under *Advanced*. Fields that do not apply to the kind are
hidden, not disabled. When the block's input is stored from an earlier run, a change re-runs
only this block (debounced 0.5 s) and the preview shows the new figure.

### 3.6 Example

The test case (spec 07) gets two graphs into the report: effective mass fraction per mode
(bar, Y = `T1_FRAC, T2_FRAC, T3_FRAC`, X = `MODE`) and the CBUSH margins (bar, X = `EID`,
*Series by* = load case, reference line `MS = 0`, highlight below 0).

## 4. Out of scope (v1)

Several tables in one graph (GR-13), interactive plots (GR-14), contour/3D plots, two Y
axes, subplots in one figure, plot templates saved by the user (with the user block library).

## 5. Open questions

1. Is a fixed house style enough, or should a company style file (`.mplstyle`) be selectable
   in *Settings*? Proposal: house style now, style file with the user settings later.
2. Default figure size 16 × 9 cm at 200 dpi: right for your reports?
