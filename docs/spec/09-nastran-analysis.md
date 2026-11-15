# 09 — Nastran Analysis Block

## 1. Purpose

Today the pipeline starts at a finished result file: the analyst runs MSC Nastran by hand,
looks at the `.f06`, and only then points a *Nastran HDF5 results* block at the `.h5`. This
step puts the analysis **into** the pipeline. A block points at the input deck (`.bdf`),
runs Nastran when needed, delivers the result files, and checks the `.f06` for warnings and
errors, so that a changed load table or model later flows all the way to the report.

It is the first of the *solver blocks*. Abaqus follows the same pattern later (§3.10), and
the shared parts are written once.

**Important limitation.** MSC Nastran is not available where the tool is developed. The
block is built and tested against a **fake Nastran** (a small script that behaves like the
program: files, messages, exit code) and against the real result files of the test case.
The first run with the real program, and the exact command line it needs at your site
(§3.3), have to be verified by you (open question 1).

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| NRN-01 | Block `nastran.run` "MSC Nastran analysis": input = a `.bdf` file; **run mode** `never` / `if_changed` / `always` (§3.2) | M |
| NRN-02 | Outputs: `h5` (path), `f06` (path), `messages` (table of warnings and errors), `status` (`OK` / `WARNINGS` / `FATAL`) | M |
| NRN-03 | The `.f06` is checked: messages classified and grouped, job completion verified, the block fails on fatal errors; the analyst can also fail on warnings and can ignore known messages (§3.5) | M |
| NRN-04 | Nastran is started with a **shell command** the analyst can set (default as typed at work: `nastran job.bdf`), with a **path setting** for the folder that holds the program. Both can be set in the block and as a default for the computer in a config file; the panel shows whether the program is found (§3.3) | M |
| NRN-05 | The command runs in the folder of the input deck with its output streamed into the block log; *Stop* ends it and everything it started; an optional time limit; a non-zero exit code is an error (§3.4) | M |
| NRN-06 | "If input changed" compares the input deck **and the files it `INCLUDE`s** with the ones used for the existing results; results that were not made by this tool are never overwritten unless the mode is `always` (§3.2, §3.6) | M |
| NRN-07 | Mode `never` uses existing result files as they are (the case of the test case), and says so if the input changed after they were made | M |
| NRN-08 | Before a run the deck is checked for an HDF5 request (`HDF5OUT` or `MDLPRM,HDF5`); after a run the `.h5` must exist | M |
| NRN-09 | Blocks downstream of the analysis become stale when the result files change, also within one run (engine, §3.7) | M |
| NRN-10 | *Nastran HDF5 results* and the related blocks accept the `.h5` path from a connector instead of a typed path (§3.8) | M |
| NRN-11 | GUI: run mode, status lines (program found? results present and current?), open the `.f06` and the folder, the messages as a table (§3.9) | M |
| NRN-12 | The parts that are not Nastran-specific (process handling, input fingerprint, run-mode logic, run record) live in a shared base for solver blocks (§3.10) | S |
| NRN-13 | Abaqus block (`.inp` → `.odb`/`.dat`/`.fil`), cluster or remote submission, job queues, OP2/PCH outputs | L |

## 3. Design

### 3.1 The block

**`nastran.run`** — category *Input/Nastran*.

| | |
|---|---|
| Inputs | none |
| Outputs | `h5: Text`, `f06: Text`, `messages: Table`, `status: Text` |

| Parameter | Meaning |
|-----------|---------|
| `input_file` | The `.bdf` (or `.dat`) input deck, relative to the pipeline. The results are written next to it |
| `run_mode` | `never`, `if_changed` (default) or `always` (§3.2) |
| `command` | The shell command that starts Nastran, with placeholders (§3.3). Empty = the default of the computer (config file), else `nastran {input} batch=no old=no scr=yes {options}` |
| `path` | Folder(s) that hold the Nastran program, put in front of `PATH` for the command (e.g. `C:\MSC.Software\MSC_Nastran\2023.3\bin`). Empty = the default of the computer, else `PATH` as it is |
| `options` | Extra Nastran keywords for the `{options}` placeholder, each `key=value` (e.g. `mem=4gb`); no spaces, no shell characters |
| `timeout_minutes` | Stop Nastran after this time (empty = no limit) |
| `fail_on` | `fatal` (default: only fatal errors fail the block), `warning` (warnings fail it too) or `never` (nothing fails it; the status says what was found) |
| `ignore_messages` | Message numbers that are not counted as warnings, e.g. `[9058]` |
| `include_information` | Also list the `INFORMATION` messages in `messages` (default no) |

The job name is the file name of the deck without extension (`modal.bdf` → job `modal`);
the results are `<deck folder>/<job>.f06` and `<job>.h5`, plus `<job>.f04` and `<job>.log`
which Nastran writes and the block keeps. Results always go **next to the deck**, as in the
example files; a separate result folder may come later. Nastran's other files (scratch,
`.MASTER`, `.DBALL`) are not the block's business: the default command asks for scratch
files to be deleted (`scr=yes`).

### 3.2 Run modes

| Mode | What the block does |
|------|---------------------|
| `never` | Does not start Nastran. Uses `<job>.f06` and `<job>.h5` as they are, checks the `.f06`, delivers the paths. A missing file is an error ("Result files not found: … Set the run mode to *If input changed* to run the analysis"). If the run record (§3.6) shows that the input changed after the results were made, the block still works but logs a **warning** saying so |
| `if_changed` | Runs Nastran only if needed. The engine's cache (step 5) already skips the block when its inputs are unchanged. When the block does run, the rules below decide whether Nastran really starts |
| `always` | Starts Nastran every time the block runs, i.e. at every *Run* of the pipeline that reaches it (this can take long: the panel says so) |

When the block runs in `if_changed` mode, the **run record** (§3.6) decides if there is one,
the **file times** decide if there is not (the rule Nastran users and `make` know: results
that are newer than their inputs are up to date):

| Situation | Result |
|-----------|--------|
| `<job>.f06` or `<job>.h5` missing | run Nastran |
| Both exist and a run record that is **finished** with the **same input fingerprint and options** | **no run**: "Results are up to date: made 2026-11-15 14:25 from the same input" |
| Both exist and a record that is **running/interrupted** or has a **different fingerprint** | run Nastran (the old results are outdated) |
| Both exist and **no usable record** (made by hand or by another tool), and **both are newer than the deck and every file it includes**, and the `.f06` ends with `END OF JOB` | **no run**: the results are **adopted** — used as they are, and a record with status `adopted` and the current fingerprint is written, so from now on the fingerprint decides: "Results are newer than the input and are used (no run record: made outside the tool)" |
| Both exist, no usable record, and **one of them is older than (or as old as) the deck or an included file**, or the `.f06` is incomplete | run Nastran: "Results are older than the input" |

So results made by hand are recognised as long as nothing was edited after them. Setting
the time of the result files to "now" tells the tool "these belong to the current input",
exactly as for a program that only reruns what is outdated. A record always beats the file
times.

`always` replaces existing results without asking, since choosing it is the decision. In
mode `never` the same time comparison produces the warning: with a record, "the input changed
after the results were made"; without one, "the input is newer than the results".

### 3.3 Starting Nastran (NRN-04)

Nastran is started with a **shell command**, as the analyst would type it. At work that is

```
nastran job_name.bdf
```

so the default command is `nastran {input} batch=no old=no scr=yes {options}`. Placeholders:

| Placeholder | Becomes |
|-------------|---------|
| `{input}` | the file name of the deck (`modal.bdf`), quoted for the shell if needed; the command runs in the deck's folder |
| `{input_path}` | the full path of the deck |
| `{job}` | the job name (`modal`) |
| `{folder}` | the deck's folder |
| `{options}` | the block's `options` (and those of the config file), joined |

Everything else in the command is passed to the shell unchanged, so a command can be a
wrapper script, a module call or several steps (`module load nastran && nastran {input}`).
The command runs through the shell of the system (`sh -c` on Linux, `cmd /c` on Windows).
The block log starts with the exact command that is run.

**Path setting.** `path` names the folder(s) that contain the program; they are put in front
of `PATH` for the command only. This is what makes `nastran` work on a computer where the
program is not on the `PATH`.

**Where the settings are.** In the block (specific to this analysis) or as the **default of
the computer** in the user's config file — Linux/macOS `~/.config/apb/config.toml`, Windows
`%APPDATA%\apb\config.toml`:

```toml
[nastran]
command = "nastran {input} batch=no old=no scr=yes {options}"
path = "C:/MSC.Software/MSC_Nastran/2023.3/bin"
options = ["mem=8gb", "parallel=4"]     # added to every run on this computer
```

The location of the config file can be overridden with the environment variable `APB_CONFIG`.
A setting in the block wins over the config file, which wins over the built-in default. A
pipeline can therefore be shared with the program's location left empty, so each computer
uses its own.

**Is it found?** The panel checks, without running anything, whether the first word of the
command is found on the (extended) `PATH` and shows the full path or "not found: set *path*
in the block or in *config file*".

**The command must wait.** The block needs the command to return when the analysis is
finished. `batch=no` (in the default command) makes MSC Nastran run in the foreground; if
your `nastran` returns at once and keeps running in the background, the `.f06` is incomplete
when the block checks it and the block fails with "f06 ends without END OF JOB". The tool's
default keywords are taken from MSC's documentation and **not verified here** (open
question 1).

**Trust.** A pipeline that contains a command is as powerful as a script: opening and running
a pipeline from an untrusted source is like running an untrusted program (spec 04 §3.3
says the same for Python blocks). The notice shown when a pipeline with Python blocks is
opened is extended to pipelines with analysis blocks. (An earlier draft kept the command out
of the pipeline file; the analyst needs it in the block, and the risk is the same one the
tool already has.)

### 3.4 Running the process (NRN-05)

- Working folder: the folder of the input deck. The environment is inherited
  (licence variables such as `MSC_LICENSE_FILE` work as in the user's shell), plus the `path` setting.
- The program's output (stdout and stderr) is streamed line by line into the **block log**,
  so the analyst sees Nastran work; the log is also stored with the results (step 5).
- *Stop* ends the shell, the program and everything it started (process group on Linux,
  `taskkill /T` on Windows), removes nothing, and leaves the run record as *interrupted*, so the results
  count as outdated.
- A time limit ends it the same way, with a clear message.
- A non-zero exit code is an error even if the `.f06` looks fine; the message gives the
  code and the last lines of Nastran's output.
- Before the run the old result files of the same job (`.f06`, `.f04`, `.log`, `.h5`) are
  **deleted**, so a failing run can never be mistaken for a fresh one.

### 3.5 The `.f06` check (NRN-03, NRN-08)

Nastran writes its messages as blocks in the `.f06`:

```
 *** USER WARNING MESSAGE 9058 (SUBDMAP SESTATIC)
     THE SOLUTION FOR THE RESIDUAL STRUCTURE AND THE APPLIED LOADS FOR THE CURRENT SUBCASE ARE ZERO.
```

Rules (checked against the three real files of the test case):

- A message starts with `*** (USER|SYSTEM) (INFORMATION|WARNING|ALARM|FATAL) MESSAGE`, an
  optional number, and the source in parentheses; its text is the indented lines below, up
  to a blank line, the next message or a page header (at most 10 lines, joined).
- **Severity:** `FATAL` → fatal; `WARNING` and `ALARM` → warning; `INFORMATION` → information.
- **Information messages are left out** unless `include_information` is set: the real files
  have 50 to 65 of them per run (data block bookkeeping) and only one real warning.
- **Grouping:** the same severity, number, source and text counts once with `COUNT`; the
  table keeps the line of the first occurrence.
- **Completion:** the `.f06` must contain `* * * END OF JOB * * *`; without it the job did
  not finish and the block fails ("f06 ends without END OF JOB").
- **Decision:** fatal messages (or a missing end of job, or a non-zero exit code) fail the
  block; with `fail_on = warning` warnings fail it too, except those in `ignore_messages`;
  `status` is `FATAL`, `WARNINGS` (warnings that are not ignored) or `OK`.
- The **HDF5 request** is checked in the deck before running (`HDF5OUT`, or `MDLPRM,HDF5`;
  fixed, free and comma format, any case); if there is none the block fails before the run
  ("The input has no HDF5 request … add `HDF5OUT` or `MDLPRM,HDF5,0`") since a run without it
  would not deliver an `.h5`. After a run the `.h5` must exist.

The `messages` table has `SEVERITY`, `NUMBER`, `MODULE`, `COUNT`, `LINE`, `TEXT` (`MODULE` is the
module or subDMAP Nastran names in parentheses; the name `SOURCE` stays free for the file name
that *Stack tables* adds), sorted by
severity and line. It is empty when the analysis is clean. Every warning and error is also a
line in the block log.

### 3.6 The run record (NRN-06)

Next to the results the block keeps `<job>.apb-run.json`:

```json
{"schema": 1, "status": "finished", "tool_version": "0.1.0.dev0",
 "inputs": [{"path": "model.bdf", "sha256": "…"}, {"path": "loads/loads.bdf", "sha256": "…"}],
 "input_hash": "…", "options": ["mem=8gb"],
 "command": "nastran model.bdf batch=no old=no scr=yes", "started": "…", "finished": "…",
 "exit_code": 0, "duration": 412.0}
```

- Written with status `running` before Nastran starts, completed (`finished`) when it ends.
  A crash or *Stop* leaves `running`: outdated. Results taken over from outside the tool get
  the status `adopted` (§3.2); it counts like `finished`.
- **Input fingerprint:** the SHA-256 of the deck and of every file it includes. `INCLUDE`
  lines (`INCLUDE 'file'`, either quote, any case, relative to the deck's folder, nested)
  are followed; a missing include is an error before the run; a cycle is an error. The same
  list is the block's `input_files` for the engine's cache, so editing an included load file
  also makes the block stale.
- The `options` are part of the comparison; the command and the times are for the record
  and for the analyst's traceability.

### 3.7 Engine changes (NRN-09)

1. **Delivered files make downstream blocks stale.** The key of a block includes the
   fingerprints of the files its upstream blocks *deliver* (the `output_files` hook of step 5:
   for the analysis block the `.f06` and the `.h5`, also in mode `never`, where they are not
   written but delivered). So a rewritten `.h5` makes *Nastran HDF5 results* and everything
   after it stale.
2. **Keys are computed just before a block runs**, not for all blocks at the start of a run,
   so a block downstream of an analysis that re-ran *in the same run* sees the new files and
   is recomputed in that run, not at the next one. The states shown in the GUI use the
   recorded delivered files, as today for the file-writing blocks.
3. `Block.always_runs(params)` (default: the class attribute `always_run`) lets the run mode
   `always` bypass the cache while the other modes use it.

### 3.8 Connecting to the HDF5 blocks (NRN-10)

*Nastran HDF5 results*, *eigenvalues*, *RBE2 loads* and *point masses* get an optional input
`file: Text`. If connected, it replaces the `file` parameter (which may then stay empty):
the pipeline reads `analysis → h5 → results`. A typed path keeps working unchanged. The
result browser of spec 02 §3.7 shows the results of the file the connected block delivered
(from its last or stored output); until that block has run it says so.

### 3.9 GUI

The panel is the generated form plus status lines that are computed without running Nastran:

```
Command      nastran modal.bdf batch=no old=no scr=yes     (command from the config file)
Nastran      C:\MSC.Software\…\bin\nastran.exe found      (path from the block)
             — or —  not found: set the path in the block or in C:\Users\…\config.toml
Input        modal.bdf + 2 included file(s) · changed 2026-11-15 14:19 · HDF5 request found ✔
Results      modal.f06 ✔ 2026-11-15 14:25 · modal.h5 ✔ 2026-11-15 14:25
             made by this tool, input unchanged   (or: input changed since / not made by this tool)
[ Open f06 ]  [ Open folder ]
```

*Run mode* has a help text per mode; `always` says "Runs Nastran every time the pipeline is
run". While Nastran runs the log shows its output and *Stop* works. After a run the preview
shows the `messages` table (and the usual origin line); a block with warnings gets a small
warning mark on the canvas next to its done state. The existing *Open* button of the preview
is extended to `.f06`, `.f04`, `.log` and `.bdf` paths.

### 3.10 Modules and the shared base (NRN-12)

```
apb/blocks/solvers/        process.py      run a child process: streaming, Stop, timeout, kill tree
                           fingerprint.py  hash of input files incl. includes (parser per solver)
                           record.py       the run record (§3.6)
                           runmode.py      the decision table of §3.2 (pure function)
apb/blocks/nastran/        analysis.py     the block
                           f06.py          the message parser (pure)
                           deck.py         INCLUDE and HDF5 request scanner (pure)
                           command.py      command template, placeholders, path, config file
```

An Abaqus block later needs only its own `deck`, `command` and message parser (`.dat`,
`.msg`, `.sta`; results `.odb`/`.fil`).

### 3.11 Testing without Nastran

`tests/fake_nastran.py` is a small program (Python script with a launcher for each platform)
that takes the same arguments, writes `<job>.f06`, `<job>.h5` (copied from the real files in
`examples/fem` or generated), `.f04` and `.log`, prints progress lines, and can be told by
environment variables to exit with a code, write a fatal or a warning message, omit
`END OF JOB`, write no `.h5`, or hang (for *Stop* and the time limit). The tests point
the block's `command` and `path` at it (`nastran` found through `path`, as at work). Nothing in
the tests needs a licence.

### 3.12 Implementation notes

Decisions made while implementing, none of which changes the agreed behaviour:

- The `messages` table calls the module column `MODULE` (not `SOURCE`), see §3.5.
- The log line of a message is `WARNING: 9058 (SUBDMAP SESTATIC): THE SOLUTION ...` (the
  severity once, as prefix); information messages, if requested, are logged without prefix.
- **Warning mark on the canvas:** any block whose log has a line starting with `WARNING:` gets
  the small mark (the analysis block, and also e.g. a failed requirement check).
- **Traceability:** the files an upstream block *delivers* (for the analysis: `.f06` and `.h5`)
  are listed with the files read upstream, so the report's traceability section still lists
  the `.h5` with the Nastran version although the result block gets its path from a
  connector.
- **File times in the cache:** the content hash of a file is reused while its size and
  modification time are unchanged (the GUI refreshes the states twice a second), except for
  files changed within the last two seconds, which are always read again.
- **Examples:** `examples/fem` holds a run record (`<job>.apb-run.json`, status `adopted`) next
  to each deck, so the test case does not depend on file times, which differ between
  checkouts and copies. `tools/make_test_case.py` writes them; a test checks that they still
  match the decks.
- **Not verified with real Nastran** (open since the start): the keywords `batch=no old=no
  scr=yes`. The block log shows the exact command, and the `command` setting can be changed.

## 4. Out of scope (v1)

Abaqus, OP2/PCH/XDB output, submitting to a cluster or queue, running several jobs in
parallel, restarts and `dmap`/superelement workflows, editing the deck (the *model update*
block is a later step), and a progress bar (Nastran does not report one; the streamed log is
the progress).

## 5. Open questions

Decided: the command is a shell command with a path setting, in the block and as a default
of the computer (§3.3); `nastran job.bdf` waits until the analysis is finished at the
analyst's site; results next to the deck (§3.1); results without a run record are used if
they are newer than the input (§3.2, option C); only fatal errors fail the block by default
(§3.5); information messages are hidden (§3.5).

None open. To check on the first real run: the exact keywords of the default command
(`batch=no old=no scr=yes`, §3.3).

## 6. Test plan

- **f06 parser (unit):** the three real files — `modal` and `global_loads`: no warning, no
  fatal, end of job found; `local_loads`: exactly one warning, 9058, with its text; the
  information counts (65, 52, 50) with `include_information`; synthetic cases: fatal and
  alarm messages, the same message repeated (grouped), continuation lines and a page header
  in the text, a message without number, a file without `END OF JOB`, an empty file, CRLF.
- **Deck scanner (unit):** `HDF5OUT` / `MDLPRM,HDF5` in fixed, free and comma format and any
  case, not in comments; `INCLUDE` with both quotes, relative paths, nested, missing,
  cyclic; the fingerprint changes when an included file changes.
- **Command and configuration (unit):** the default command, the placeholders (also file names
  with spaces and non-ASCII characters, quoted for `sh` and for `cmd`), `{options}` from the
  config file and the block, precedence block → config file → default, the `path` put in
  front of `PATH`, the "found / not found" check, the config file (missing, invalid TOML),
  refusal of `options` with spaces or shell characters.
- **Run modes (with the fake):** every row of §3.2: first run, up to date through the run
  record, changed input, changed include, results without a record that are newer (adopted,
  record written, Nastran not started) or older or as old (run), an incomplete `.f06`,
  `always`, `never` with and without files, `never` with a changed or newer input (warning);
  old results deleted before a run; interrupted record; *Stop*; time limit; exit code;
  fatal message; no end of job; no `.h5`; no HDF5 request; program not found.
- **Engine:** a downstream block becomes stale when the delivered `.h5` changes; with
  `always` it is recomputed in the same run; keys are computed just before running; the
  states after reopening (step 5) agree.
- **HDF5 blocks:** the file from a connector, the typed path still works, the browser follows
  the connected block.
- **Test case (spec 07):** three analysis blocks in mode `never` in front of the HDF5
  blocks; statuses `OK`, `OK`, `WARNINGS`; the messages table with the one 9058 row.
- **Browser:** the panel's status lines (program not found, then found), run, the messages
  table, *Stop* of a long fake run, the warning mark on the canvas.
