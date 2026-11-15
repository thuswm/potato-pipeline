"""End-to-end GUI tests in a real (headless) browser with Playwright.

Skipped when Playwright's browser is not installed (``uv run playwright install chromium``).
Each test starts ``apb gui`` on a free port with a copy of the example pipelines.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copytree(
        EXAMPLES, tmp_path / "examples", ignore=shutil.ignore_patterns("output", ".apb")
    )
    return tmp_path / "examples"


class ServerUrl(str):
    """URL of the test server; ``proc`` is the server process."""

    proc: subprocess.Popen[bytes]


def launch_gui(workdir: Path, pipeline: str | None) -> ServerUrl:
    """Start ``apb gui`` on a free port (optionally with a pipeline) and wait until it answers."""
    port = _free_port()
    command = [sys.executable, "-m", "apb.cli", "gui", "--no-browser", "--port", str(port)]
    if pipeline is not None:
        command.insert(4, str(workdir / pipeline))
    log = workdir / f"gui-{port}.log"
    proc = subprocess.Popen(
        command,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        # Without PYTEST_CURRENT_TEST, so that NiceGUI runs as a normal server.
        env={k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
        | {"PYTHONUTF8": "1"},
    )
    url = ServerUrl(f"http://127.0.0.1:{port}/")
    url.proc = proc
    deadline = time.time() + 30
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return url
        time.sleep(0.2)
    proc.kill()
    pytest.fail("GUI server did not start:\n" + log.read_text())


@pytest.fixture
def server(workdir: Path, request: pytest.FixtureRequest) -> Iterator[ServerUrl]:
    """The GUI server; ``indirect`` parametrisation chooses the pipeline to open."""
    started: list[ServerUrl] = []
    pipeline = getattr(request, "param", "fastener_loads.apb.json")
    first = launch_gui(workdir, pipeline)
    started.append(first)
    first.restart = lambda name=pipeline: started.append(launch_gui(workdir, name)) or started[-1]  # type: ignore[attr-defined]
    yield first
    for url in started:
        if url.proc.poll() is None:
            url.proc.terminate()
        url.proc.wait(timeout=10)


@pytest.fixture
def page(server: str):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"Playwright browser not available: {exc}")
        pg = browser.new_page(viewport={"width": 1500, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(server)
        pg.wait_for_selector(".drawflow-node")
        pg.wait_for_timeout(500)  # connection redraw + fit
        yield pg
        assert errors == [], errors
        browser.close()


def node(pg, label: str):
    return pg.locator(".drawflow-node", has=pg.locator(".apb-label", has_text=label)).first


def drag(pg, source, target) -> None:
    a, b = source.bounding_box(), target.bounding_box()
    pg.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=8)
    pg.mouse.up()
    pg.wait_for_timeout(400)


def counts(pg) -> tuple[int, int]:
    return pg.locator(".drawflow-node").count(), pg.locator(".drawflow .connection").count()


def test_loads_example(page):
    assert counts(page) == (16, 24)
    assert "fastener_loads.apb.json" in page.locator(".q-header").inner_text()


def test_add_connect_reject_delete_save(page, workdir: Path):
    page.locator(".q-drawer .q-item", has_text="Sort").first.click()
    page.wait_for_selector(".drawflow-node:has(.apb-label:text-is('Sort'))")
    assert counts(page) == (17, 24)
    assert "*" in page.locator(".q-header").inner_text()  # unsaved changes

    sort = node(page, "Sort")
    # Move the new block to free space so it does not cover other ports.
    head = sort.locator(".apb-head").bounding_box()
    page.mouse.move(head["x"] + 20, head["y"] + 10)
    page.mouse.down()
    area = page.locator(".apb-canvas").bounding_box()
    page.mouse.move(area["x"] + 140, area["y"] + area["height"] - 90, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)

    # Invalid: Scalar output -> Table input is refused with a message.
    drag(page, node(page, "Safety factor").locator(".output_1"), sort.locator(".input_1"))
    assert counts(page) == (17, 24)
    assert page.get_by_text("Cannot connect a Scalar output to a Table input").is_visible()

    # Valid: Table -> Table.
    drag(page, node(page, "Drop GRID").locator(".output_1"), sort.locator(".input_1"))
    assert counts(page) == (17, 25)

    # A single input refuses a second connection.
    drag(page, node(page, "CBUSH forces").locator(".output_1"), sort.locator(".input_1"))
    assert counts(page) == (17, 25)
    assert page.get_by_text("is already connected").is_visible()

    # Rename in the side panel.
    sort.locator(".apb-head").click()
    label = page.get_by_label("Label")
    label.fill("Sorted loads")
    label.press("Enter")
    page.wait_for_timeout(300)
    assert node(page, "Sorted loads").count() == 1

    # Delete with the keyboard: block and its connection disappear.
    node(page, "Sorted loads").locator(".apb-head").click()
    page.keyboard.press("Delete")
    page.wait_for_timeout(400)
    assert counts(page) == (16, 24)

    # Save via File > Save writes the file and clears the unsaved marker.
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save", exact=True).click()
    page.wait_for_timeout(500)
    assert "*" not in page.locator(".q-header").inner_text()
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    assert len(saved["blocks"]) == 16 and len(saved["connections"]) == 24


def test_move_block_is_saved(page, workdir: Path):
    block = node(page, "Load case names").locator(".apb-head")
    box = block.bounding_box()
    page.mouse.move(box["x"] + 30, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 30, box["y"] + 110, steps=6)
    page.mouse.up()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save", exact=True).click()
    page.wait_for_timeout(500)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    names = next(b for b in saved["blocks"] if b["id"] == "names")
    assert names["position"][1] > 300


def select(pg, label: str) -> None:
    node(pg, label).locator(".apb-head").click()
    pg.wait_for_timeout(400)


def run_all(pg) -> None:
    pg.get_by_role("button", name="Run", exact=True).click()
    pg.wait_for_selector("text=/Run (finished|stopped)/", timeout=30000)
    pg.wait_for_timeout(500)


def states(pg) -> set[str]:
    classes = pg.eval_on_selector_all(
        ".drawflow-node",
        "els => els.map(e => [...e.classList].find(c => c.startsWith('apb-state-')) || '')",
    )
    return set(classes)


def test_run_states_and_preview(page, workdir: Path):
    run_all(page)
    assert states(page) == {"apb-state-done"}
    assert (workdir / "output" / "fastener_report.docx").exists()
    select(page, "Fastener margins")
    preview = page.locator(".q-tab-panels").first
    assert page.get_by_role("tab", name="min_ms").is_visible()
    assert "SHEAR [N]" in preview.inner_text()
    page.get_by_role("tab", name="min_ms").click()
    page.wait_for_timeout(300)
    assert "0.234568" in page.locator(".q-tab-panels").first.inner_text()
    page.get_by_role("tab", name="plot").click()
    page.wait_for_timeout(300)
    assert page.locator(".q-tab-panels img").count() == 1


def test_edit_parameter_makes_blocks_stale_and_rerun(page):
    run_all(page)
    select(page, "Shear allowable")
    value = page.get_by_role("textbox", name="Value *")
    assert value.input_value() == "${allowable_shear}"
    value.fill("1800")
    value.press("Enter")
    page.wait_for_timeout(1000)  # states are refreshed twice a second
    stale = page.locator(".drawflow-node.apb-state-stale")
    assert node(page, "Fastener margins").get_attribute("class").count("apb-state-stale") == 1
    assert stale.count() >= 3  # constant, margins and everything downstream
    assert node(page, "CBUSH forces").get_attribute("class").count("apb-state-done") == 1
    run_all(page)
    select(page, "Fastener margins")
    page.get_by_role("tab", name="min_ms").click()
    page.wait_for_timeout(300)
    # R_S = 1.5 * 1350 / 1800 = 1.125 -> MS = 1/1.125 - 1 = -0.111
    assert "-0.111111" in page.locator(".q-tab-panels").first.inner_text()


def test_error_is_shown_and_log_selects_block(page):
    select(page, "CBUSH forces")
    file = page.get_by_role("textbox", name="File", exact=True)
    file.fill("missing/model.h5")
    file.press("Enter")
    page.wait_for_timeout(300)
    run_all(page)
    assert "apb-state-error" in node(page, "CBUSH forces").get_attribute("class")
    assert "apb-state-skipped" in node(page, "Fastener report").get_attribute("class")
    assert "apb-state-done" in node(page, "RBE2 loads").get_attribute("class")
    select(page, "Shear allowable")
    page.locator(".apb-log-line", has_text="✘ CBUSH forces").click()
    page.wait_for_timeout(400)
    assert page.get_by_label("Label").input_value() == "CBUSH forces"
    assert "not found" in page.locator(".q-tab-panels, .text-red-700").first.inner_text()


def test_invalid_parameters_block_the_run(page):
    page.locator(".q-drawer .q-item", has_text="Sort").first.click()
    page.wait_for_timeout(500)
    assert page.locator(".text-red-700", has_text="not connected").count() >= 1
    run_all_attempt = page.get_by_role("button", name="Run", exact=True)
    run_all_attempt.click()
    page.wait_for_selector("text=Cannot run", timeout=10000)
    assert "apb-state-error" in node(page, "Sort").get_attribute("class")


def test_python_block_port_change_updates_canvas(page):
    select(page, "Fastener margins")
    page.get_by_role("button", name="Add port").first.click()
    page.wait_for_timeout(500)
    margins = node(page, "Fastener margins")
    assert margins.locator(".inputs .input").count() == 5
    assert margins.locator(".apb-in", has_text="value5").count() == 1
    assert page.locator(".drawflow .connection").count() == 24  # existing connections kept


def test_result_browser_lists_selects_and_peeks(page, workdir: Path):
    select(page, "CBUSH forces")
    # The block's file comes from a pipeline variable: the synthetic joint file.
    assert "synthetic test file" in page.locator(".apb-h5-info").inner_text()
    results = page.locator(".apb-h5-results")
    assert "CBUSH forces" in page.locator(".apb-h5-selected").inner_text()
    assert "EID present: 1001 to 1004 (4 IDs)" in page.locator(".apb-h5-ids").inner_text()

    # Point the block at a real file: the list follows, the old result is reported missing.
    box = page.get_by_role("textbox", name="File", exact=True)
    box.fill("fem/modal.h5")
    box.press("Enter")
    page.wait_for_timeout(600)
    assert "MSC Nastran 2023.3 · SOL 103 · modal.bdf" in page.locator(".apb-h5-info").inner_text()
    assert "Eigenvectors (mode shapes)" in results.inner_text()
    assert "6 x 6" in results.inner_text()
    assert page.get_by_text("is not in this file").is_visible()

    # Search, then click a result: it is selected and previewed without running.
    page.get_by_placeholder("Search results").fill("effective mass")
    page.wait_for_timeout(400)
    assert "Eigenvectors" not in results.inner_text()
    results.get_by_text("Modal effective mass", exact=True).click()
    page.wait_for_timeout(800)
    assert "Modal effective mass" in page.locator(".apb-h5-selected").inner_text()
    assert "not yet run" in page.locator(".apb-peek").inner_text()
    preview = page.locator(".q-table").first.inner_text()
    assert "FREQUENCY [Hz]" in preview
    assert "T1 [t]" in preview  # pipeline default unit system is SI_mm
    assert "25.7119" in preview
    assert page.locator(".apb-h5-ids").count() == 0  # no IDs in this result

    # The choice is stored in the pipeline file.
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save", exact=True).click()
    page.wait_for_timeout(500)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    block = next(b for b in saved["blocks"] if b.get("label") == "CBUSH forces")
    assert block["params"]["file"] == "fem/modal.h5"
    assert block["params"]["result"] == "MODAL/MODAL_EFFECTIVE_MASS"


def test_result_browser_unreadable_file_keeps_text_fields(page):
    select(page, "CBUSH forces")
    box = page.get_by_role("textbox", name="File", exact=True)
    box.fill("missing/model.h5")
    box.press("Enter")
    page.wait_for_timeout(600)
    assert "File not found" in page.locator(".apb-h5-error").inner_text()
    assert (
        page.get_by_role("textbox", name="Result *").input_value() == "ELEMENTAL/ELEMENT_FORCE/BUSH"
    )


def test_block_type_is_shown_on_blocks_and_in_the_panel(page):
    # The block type line is drawn even when the label equals the type name (GUI-18).
    names = node(page, "Load case names")
    assert names.locator(".apb-sub").inner_text() == "Load case names"
    assert node(page, "CBUSH forces").locator(".apb-sub").inner_text() == "Nastran HDF5 results"
    select(page, "Load case names")
    line = page.locator(".apb-block-type")
    assert line.inner_text() == "Block type: Load case names · Table / Load cases"
    line.hover()
    page.wait_for_timeout(500)
    assert "block id" in page.locator(".q-tooltip").inner_text()


def test_save_as_can_create_a_new_folder(page, workdir: Path):
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save as…", exact=True).click()
    dialog = page.locator(".q-dialog")
    dialog.get_by_label("New folder").click()
    page.get_by_label("Folder name").fill("results")
    page.get_by_role("button", name="Create").click()
    page.wait_for_timeout(500)
    assert (workdir / "results").is_dir()
    assert dialog.locator("text=results").first.is_visible()  # the dialog is now in the folder
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(800)
    assert (workdir / "results" / "fastener_loads.apb.json").is_file()


def test_new_folder_refuses_bad_names(page, workdir: Path):
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save as…", exact=True).click()
    dialog = page.locator(".q-dialog")
    for bad in ("a/b", "data"):  # separator; exists already
        dialog.get_by_label("New folder").click()
        page.get_by_label("Folder name").fill(bad)
        page.get_by_role("button", name="Create").click()
        page.wait_for_timeout(400)
        assert page.locator(".q-notification").count() >= 1
    assert not (workdir / "a").exists()


def test_quit_warns_about_unsaved_changes_then_stops_the_server(page, server):
    node(page, "Shear allowable").locator(".apb-head").click()
    page.wait_for_timeout(400)
    value = page.get_by_role("textbox", name="Value *")
    value.fill("1800")
    value.press("Enter")
    page.wait_for_timeout(500)
    assert "*" in page.locator(".q-header").inner_text()

    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Quit", exact=True).click()
    assert "Unsaved changes in: fastener_loads.apb.json" in page.locator(".q-dialog").inner_text()
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(500)
    assert server.proc.poll() is None  # still running

    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Quit", exact=True).click()
    page.get_by_role("button", name="Quit without saving").click()
    page.wait_for_selector("text=You can close this tab")
    assert server.proc.wait(timeout=15) == 0 or server.proc.returncode is not None
    page.evaluate("window.onbeforeunload = null")


def test_quit_without_changes_needs_no_confirmation(page, server):
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Quit", exact=True).click()
    page.wait_for_selector("text=You can close this tab")
    server.proc.wait(timeout=15)
    assert server.proc.returncode is not None


def python_block_panel_text(page) -> str:
    return page.locator(".apb-script").inner_text()


def test_python_block_shows_code_and_checks_the_script(page, workdir: Path):
    select(page, "Fastener margins")
    code = page.locator(".apb-script-code")
    assert "def run(" in code.inner_text()
    assert "Fastener margins" in code.inner_text()
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()
    assert "changed 20" in page.locator(".apb-script-info").inner_text()
    assert page.get_by_role("button", name="Open in editor").is_visible()
    assert page.get_by_role("button", name="Edit here").is_visible()

    # An edit in an external editor shows up after "Check"; nothing is executed.
    script = workdir / "scripts" / "fastener_margins.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "safety_factor: float,", "safety_factor: float,\n    extra,"
        ),
        encoding="utf-8",
    )
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    assert (
        "run() argument 'extra' has no matching input port"
        in page.locator(".apb-script-check").inner_text()
    )
    assert "extra," in page.locator(".apb-script-code").inner_text()

    script.write_text("def run(:\n", encoding="utf-8")
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    assert (
        "Syntax error in fastener_margins.py, line 1"
        in page.locator(".apb-script-check").inner_text()
    )


def add_python_block(page) -> None:
    page.locator(".q-drawer--left .q-expansion-item__content .q-item", has_text="Python").click()
    page.wait_for_timeout(600)


def test_new_python_block_from_template_and_never_overwrites(page, workdir: Path):
    add_python_block(page)
    assert "The block has no script yet" in python_block_panel_text(page)
    assert page.get_by_role("button", name="Use existing file…").is_visible()

    # A template, then a name that exists: refused, the file is untouched.
    page.get_by_role("button", name="New from template…").click()
    page.get_by_text("Table summary — ").click()
    page.get_by_role("button", name="Next…").click()
    dialog = page.locator(".q-dialog")
    existing = workdir / "scripts" / "fastener_margins.py"
    before = existing.read_text(encoding="utf-8")
    dialog.get_by_label("File name").fill("fastener_margins.py")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(600)
    assert page.locator(".q-notification", has_text="already exists").count() == 1
    assert existing.read_text(encoding="utf-8") == before
    assert "The block has no script yet" in python_block_panel_text(page)

    # A new name: file created, linked, ports added, code shown.
    page.get_by_role("button", name="New from template…").click()
    page.get_by_text("Table summary — ").click()
    page.get_by_role("button", name="Next…").click()
    dialog.get_by_label("File name").fill("worst_shear.py")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(1000)
    created = workdir / "scripts" / "worst_shear.py"
    assert created.is_file()
    assert "def run(table: pd.DataFrame)" in page.locator(".apb-script-code").inner_text()
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()
    block = page.locator(".drawflow-node", has=page.locator(".apb-sub", has_text="Python")).last
    assert block.locator(".apb-in", has_text="table").count() == 1
    assert block.locator(".apb-out", has_text="max_value").count() == 1
    assert block.locator(".apb-out", has_text="where").count() == 1
    assert "scripts/worst_shear.py" in page.get_by_role("textbox", name="Script").input_value()


def test_python_block_use_existing_file(page, workdir: Path):
    add_python_block(page)
    page.get_by_role("button", name="Use existing file…").click()
    dialog = page.locator(".q-dialog")
    dialog.get_by_text("fastener_margins.py").click()
    page.wait_for_timeout(800)
    assert "def run(" in page.locator(".apb-script-code").inner_text()
    # The block has no ports, the script needs some: shown by the check, ports not touched.
    assert "has no matching input port" in page.locator(".apb-script-check").inner_text()


# ---- report section editor (spec 05 §3.4) ------------------------------------------------


def save_file(page) -> None:
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save", exact=True).click()
    page.wait_for_timeout(600)


def report_params(workdir: Path) -> dict:
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    return next(b for b in saved["blocks"] if b["type_id"] == "report.word")["params"]


def section_row(page, index: int):
    return page.locator(".apb-section").nth(index)


def open_section(page, index: int) -> None:
    section_row(page, index).locator(".apb-section-summary").click()
    page.wait_for_timeout(500)


def summaries(page) -> list[str]:
    return [t.strip() for t in page.locator(".apb-section-summary").all_inner_texts()]


def test_report_editor_lists_items_and_sections(page):
    select(page, "Fastener report")
    names = [i.input_value() for i in page.locator(".apb-item-name input").all()]
    assert names == [
        "load_table",
        "critical_margins",
        "plot",
        "min_ms",
        "allowable_tension",
        "allowable_shear",
        "safety_factor",
    ]
    assert "Table · Fastener load table" in page.locator(".apb-item").first.inner_text()
    assert "Figure · Fastener margins (plot)" in page.locator(".apb-item").nth(2).inner_text()
    found = summaries(page)
    assert found[0] == "Title"
    assert found[1] == "H1 · 1 Introduction"
    assert found[4] == "H1 · 2 Fastener loads"
    assert found[6] == "Table · load_table · “Fastener loads per load case”"
    assert found[-1] == "Traceability"
    assert page.locator(".apb-unused").count() == 0  # every item is used by the example report
    assert page.locator(".apb-section-warning:visible").count() == 0


def test_report_editor_add_move_duplicate_delete_and_save(page, workdir: Path):
    select(page, "Fastener report")
    before = len(summaries(page))
    open_section(page, 1)  # the first heading
    page.get_by_role("button", name="Add section").click()
    page.locator(".q-menu").get_by_text("Heading", exact=True).click()  # after the open section
    page.wait_for_timeout(500)
    assert summaries(page)[2] == "H1 · New heading"
    text = page.locator(".apb-text-text input").first
    text.fill("1.1 Scope")
    text.press("Tab")
    page.wait_for_timeout(400)
    assert summaries(page)[2] == "H1 · 1.1 Scope"

    section_row(page, 2).get_by_label("Move up").click()
    page.wait_for_timeout(400)
    assert summaries(page)[1:3] == ["H1 · 1.1 Scope", "H1 · 1 Introduction"]
    section_row(page, 1).get_by_label("Duplicate").click()
    page.wait_for_timeout(400)
    assert summaries(page)[1:4] == ["H1 · 1.1 Scope", "H1 · 1.1 Scope", "H1 · 1 Introduction"]
    section_row(page, 2).get_by_label("Delete").click()
    page.wait_for_timeout(400)
    assert len(summaries(page)) == before + 1
    assert summaries(page)[1:3] == ["H1 · 1.1 Scope", "H1 · 1 Introduction"]

    save_file(page)
    sections = report_params(workdir)["sections"]
    assert sections[1] == {"kind": "heading", "text": "1.1 Scope", "level": 1}
    assert sections[2]["text"] == "1 Introduction"
    assert len(sections) == before + 1


def test_report_editor_table_columns_formats_and_warnings(page, workdir: Path):
    run_all(page)
    select(page, "Fastener report")
    open_section(page, 8)  # table critical_margins
    columns = page.locator(".apb-columns")
    assert "EID" in columns.inner_text() and "TENSION" in columns.inner_text()
    # The formats of the chosen columns are listed with the unit of the table column.
    formats = page.locator(".apb-formats")
    assert "TENSION [N]" in formats.inner_text()
    assert page.locator(".apb-format-column input").count() == 5
    box = page.locator(".apb-format-column input").nth(3)  # EID, SUBCASE, TENSION, SHEAR, MS
    assert box.input_value() == "0.0"
    box.fill("0.0.0")
    box.press("Tab")
    page.wait_for_timeout(300)
    assert page.locator(".apb-formats .q-field--error").count() == 1  # invalid: not stored
    box.fill("0.000")
    box.press("Tab")
    page.wait_for_timeout(400)
    save_file(page)
    assert report_params(workdir)["sections"][8]["formats"]["SHEAR"] == "0.000"

    # A column that is not in the table gives a warning on the section before any run.
    caption_box = page.locator(".apb-text-caption input").first
    caption_box.fill("Critical {nope}")
    caption_box.press("Tab")
    page.wait_for_timeout(400)
    assert "Unknown placeholder {nope}" in page.locator(".apb-text-status").first.inner_text()
    title = section_row(page, 8).locator(".apb-section-warning").get_attribute("title")
    assert "Unknown placeholder {nope}" in title


def test_report_editor_insert_value_and_live_preview(page):
    run_all(page)
    select(page, "Fastener report")
    open_section(page, 10)  # paragraph: The minimum margin of safety is {min_ms:.2f}.
    status = page.locator(".apb-text-status").first
    assert "→ The minimum margin of safety is 0.23." in status.inner_text()
    area = page.locator(".apb-text-text textarea").first
    area.fill("Minimum: ")
    area.press("End")
    page.get_by_label("Insert value").first.click()
    page.locator(".q-menu").get_by_text("min_ms", exact=True).click()
    page.wait_for_timeout(600)
    assert area.input_value() == "Minimum: {min_ms:.2f}"
    assert "→ Minimum: 0.23" in status.inner_text()
    assert summaries(page)[10] == "Minimum: {min_ms:.2f}"


def test_report_editor_rename_item_updates_references(page, workdir: Path):
    select(page, "Fastener report")
    plot = page.locator(".apb-item-name input").nth(2)
    assert plot.input_value() == "plot"
    plot.fill("margin_plot")
    plot.press("Enter")
    page.wait_for_selector("text=reference(s) in the sections will be changed")
    page.get_by_role("button", name="Rename").click()
    page.wait_for_timeout(800)
    assert [i.input_value() for i in page.locator(".apb-item-name input").all()][2] == "margin_plot"
    assert "Figure · margin_plot" in summaries(page)[9]
    assert page.locator(".apb-section-warning:visible").count() == 0
    save_file(page)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    assert next(
        c
        for c in saved["connections"]
        if c["target"]["block"] == "report" and c.get("name") == "margin_plot"
    )
    assert report_params(workdir)["sections"][9]["item"] == "margin_plot"
    run_all(page)  # the report still builds with the new name
    assert states(page) == {"apb-state-done"}


def test_report_editor_refuses_a_name_in_use_and_flags_missing_items(page):
    select(page, "Fastener report")
    box = page.locator(".apb-item-name input").nth(3)
    box.fill("plot")
    box.press("Enter")
    page.wait_for_selector("text=already used by another item")
    assert page.locator(".apb-item-name input").nth(3).input_value() == "min_ms"
    # A new table section has no item yet: the warning says so.
    page.get_by_role("button", name="Add section").click()
    page.locator(".q-menu").get_by_text("Table", exact=True).click()
    page.wait_for_timeout(500)
    last = page.locator(".apb-section").last
    assert "Table · (no item)" in last.inner_text()
    assert "Choose an item" in last.locator(".apb-section-warning").get_attribute("title")


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_test_case_pipeline_runs_in_the_gui(page, workdir: Path):
    assert counts(page) == (16, 26)
    assert node(page, "Frequency requirement").locator(".apb-sub").inner_text() == (
        "Requirement check"
    )
    run_all(page)
    assert states(page) == {"apb-state-done"}
    assert (workdir / "output" / "test_case_report.docx").exists()
    assert (workdir / "output" / "test_case_modal.xlsx").exists()

    select(page, "Frequency requirement")
    preview = page.locator(".q-tab-panels").first
    assert "REQ-DYN-001 First eigenfrequency" in preview.inner_text()
    assert "PASS" in preview.inner_text()
    assert "VALUE [Hz]" in preview.inner_text()
    page.get_by_role("tab", name="margin").click()
    page.wait_for_timeout(300)
    assert "0.285593" in page.locator(".q-tab-panels").first.inner_text()

    select(page, "Global margins")
    assert "def run(" in page.locator(".apb-script-code").inner_text()
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()

    # The report editor knows the items by the names given on the connectors.
    select(page, "Test case report")
    names = [i.input_value() for i in page.locator(".apb-item-name input").all()]
    assert names == [
        "effective_mass",
        "f1",
        "min_frequency",
        "requirement",
        "req_status",
        "safety_factor",
        "global_margins",
        "min_ms_global",
        "local_margins",
        "nastran_messages",
    ]
    assert page.locator(".apb-unused").count() == 0
    assert page.locator(".apb-section-warning:visible").count() == 0
    open_section(page, 7)  # the modal effective mass table: columns known from the run
    assert "FREQUENCY" in page.locator(".apb-columns").inner_text()
    assert page.locator(".apb-format-column input").count() == 8


# ---- stored outputs (spec 08 §3.9) -------------------------------------------------------


def state_of(page, label: str) -> str:
    classes = node(page, label).get_attribute("class") or ""
    return next(c for c in classes.split() if c.startswith("apb-state-")).removeprefix("apb-state-")


def reopen(page, server) -> None:
    """Stop the server and start a new one on the same folder: the tool was closed and opened."""
    server.proc.terminate()
    server.proc.wait(timeout=10)
    page.goto(server.restart())
    page.wait_for_selector(".drawflow-node")
    page.wait_for_timeout(1500)  # the states are refreshed twice a second


def log_text(page) -> str:
    return page.locator(".apb-log-line").all_inner_texts().__str__()


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_reopened_pipeline_shows_stored_results_without_running(page, server, workdir: Path):
    run_all(page)
    assert states(page) == {"apb-state-done"}
    assert (workdir / ".apb" / "test_case" / "req" / "meta.json").is_file()
    assert (workdir / ".apb" / ".gitignore").read_text() == "*\n"

    reopen(page, server)
    assert states(page) == {"apb-state-done"}  # recognised as run, nothing was run
    assert "Stored outputs found for 16 of 16 blocks" in log_text(page)
    assert not (workdir / "output" / "x").exists()

    select(page, "Frequency requirement")
    origin = page.locator(".apb-origin").first.inner_text()
    assert origin.startswith("Done · stored 20")
    preview = page.locator(".q-tab-panels").first
    assert (
        "REQ-DYN-001 First eigenfrequency" in preview.inner_text()
        and "PASS" in preview.inner_text()
    )
    page.get_by_role("tab", name="margin").click()
    page.wait_for_timeout(300)
    assert "0.285593" in page.locator(".q-tab-panels").first.inner_text()
    select(page, "Global margins")
    assert "Joint B (M5 aluminium)" in page.locator(".q-table").first.inner_text()

    # Run: the blocks that are up to date come from the store; the writers run again.
    run_all(page)
    assert "14 up to date" in log_text(page)
    assert states(page) == {"apb-state-done"}


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_changes_after_reopening_make_results_outdated(page, server):
    run_all(page)
    reopen(page, server)
    select(page, "Required frequency")
    value = page.get_by_role("textbox", name="Value *")
    value.fill("30")
    value.press("Enter")
    page.wait_for_timeout(1000)
    assert state_of(page, "Required frequency") == "stale"
    assert state_of(page, "Frequency requirement") == "stale"
    assert state_of(page, "Modal results") == "done"
    select(page, "Frequency requirement")
    assert "Outdated" in page.locator(".apb-origin").first.inner_text()
    assert "REQ-DYN-001" in page.locator(".q-tab-panels").first.inner_text()  # still viewable
    run_all(page)
    assert states(page) == {"apb-state-done"}
    page.get_by_role("tab", name="status").click()
    page.wait_for_timeout(300)
    assert "FAIL" in page.locator(".q-tab-panels").first.inner_text()


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_clear_output_of_a_block_clears_everything_downstream(page, workdir: Path):
    run_all(page)
    select(page, "Global margins")
    assert "Stored output: 20" in page.locator(".apb-stored").inner_text()
    page.get_by_role("button", name="Clear output").click()
    dialog = page.locator(".q-dialog")
    assert "Clear the stored output of 'Global margins'" in dialog.inner_text()
    assert "downstream: Test case report" in dialog.inner_text()
    dialog.get_by_role("button", name="Clear", exact=True).click()
    page.wait_for_timeout(1000)
    assert state_of(page, "Global margins") == "idle"
    assert state_of(page, "Test case report") == "idle"
    assert state_of(page, "Global CBUSH forces") == "done"
    assert state_of(page, "Local margins") == "done"
    assert not (workdir / ".apb" / "test_case" / "global_margins").exists()
    assert (workdir / ".apb" / "test_case" / "local_margins").exists()
    assert page.get_by_text("Not run yet").count() == 1
    assert page.locator(".apb-stored").count() == 0  # nothing stored for this block now
    assert (workdir / "output" / "test_case_report.docx").exists()  # written files stay
    run_all(page)
    assert states(page) == {"apb-state-done"}


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_stored_outputs_dialog_and_clear_all(page, workdir: Path):
    run_all(page)
    page.get_by_role("button", name="Pipeline", exact=True).click()
    page.get_by_text("Stored outputs…", exact=True).click()
    dialog = page.locator(".q-dialog")
    assert ".apb/test_case" in dialog.locator(".apb-stored-folder").inner_text()
    assert "16 block(s)" in dialog.locator(".apb-stored-facts").inner_text()
    dialog.get_by_role("button", name="Clear all…").click()
    page.wait_for_selector("text=Delete the stored outputs of 16 block(s)")
    page.get_by_role("button", name="Clear all", exact=True).click()
    page.wait_for_timeout(1000)
    assert {state_of(page, n) for n in ("Modal results", "Global margins", "Test case report")} == {
        "idle"
    }
    assert not (workdir / ".apb").exists()
    assert (workdir / "output" / "test_case_report.docx").exists()


def test_an_unsaved_pipeline_is_stored_at_the_first_save(page, workdir: Path):
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("New", exact=True).click()
    page.wait_for_timeout(500)
    page.locator(".q-drawer--left .q-expansion-item__content .q-item", has_text="Constant").click()
    page.wait_for_selector(".drawflow-node")
    value = page.get_by_role("textbox", name="Value *")  # the new block's panel is open
    value.fill("2.5")
    value.press("Enter")
    page.wait_for_timeout(500)
    run_all(page)
    assert states(page) == {"apb-state-done"}
    assert not (workdir / ".apb").exists()  # nothing next to an unsaved pipeline

    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save as…", exact=True).click()
    dialog = page.locator(".q-dialog")
    dialog.get_by_label("File name").fill("scratch.apb.json")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(1000)
    entries = list((workdir / ".apb" / "scratch").glob("*/meta.json"))
    assert len(entries) == 1  # the block that was done is stored now
    assert states(page) == {"apb-state-done"}


# ---- Nastran analysis block (spec 09 §3.9) -----------------------------------------------


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_analysis_blocks_in_the_gui(page):
    notice = page.locator(".q-notification").first.inner_text()
    assert "Python blocks and commands that start programs" in notice
    run_all(page)
    assert states(page) == {"apb-state-done"}
    # Only the analysis with a warning gets the mark.
    assert "apb-warn" in node(page, "Local analysis").get_attribute("class")
    assert "apb-warn" not in node(page, "Modal analysis").get_attribute("class")
    assert "apb-warn" not in node(page, "Global analysis").get_attribute("class")

    select(page, "Local analysis")
    assert (
        "nastran local_loads.bdf batch=no old=no scr=yes"
        in page.locator(".apb-analysis-command").inner_text()
    )
    assert "(default)" in page.locator(".apb-analysis-command").inner_text()
    assert "local_loads.bdf" in page.locator(".apb-analysis-input").inner_text()
    assert "HDF5 request found" in page.locator(".apb-analysis-input").inner_text()
    results = page.locator(".apb-analysis-results").inner_text()
    assert "local_loads.f06 ✔" in results and "local_loads.h5 ✔" in results
    assert page.get_by_role("button", name="Open f06").is_visible()
    assert page.get_by_role("button", name="Open folder").is_visible()

    page.get_by_role("tab", name="messages").click()
    page.wait_for_timeout(300)
    preview = page.locator(".q-tab-panels").first
    assert "9058" in preview.inner_text() and "SUBDMAP SESTATIC" in preview.inner_text()
    page.get_by_role("tab", name="status").click()
    page.wait_for_timeout(300)
    assert "WARNINGS" in page.locator(".q-tab-panels").first.inner_text()
    page.get_by_role("tab", name="f06").click()
    page.wait_for_timeout(300)
    assert "local_loads.f06" in page.locator(".q-tab-panels").first.inner_text()
    assert page.locator(".q-tab-panels").get_by_role("button", name="Open").is_visible()


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_analysis_panel_checks_the_program_and_the_input(page, fake_nastran, workdir):
    select(page, "Modal analysis")
    program = page.locator(".apb-analysis-program")
    assert program.count() == 1  # found or not on this computer: it says which
    path_box = page.get_by_role("textbox", name="Path")
    path_box.fill(str(workdir / "no_such_folder"))
    path_box.press("Enter")
    page.wait_for_timeout(500)
    assert "'nastran' not found" in page.locator(".apb-analysis-program").inner_text()
    path_box.fill(str(fake_nastran.bin))
    path_box.press("Enter")
    page.wait_for_timeout(500)
    found = page.locator(".apb-analysis-program").inner_text()
    assert str(fake_nastran.bin) in found and "(path: block)" in found
    command = page.get_by_role("textbox", name="Command")
    command.fill("nastran {input} jid={job}")
    command.press("Enter")
    page.wait_for_timeout(500)
    text = page.locator(".apb-analysis-command").inner_text()
    assert "nastran modal.bdf jid=modal" in text and "(block)" in text
    deck = page.get_by_role("textbox", name="Input file")
    deck.fill("fem/missing.bdf")
    deck.press("Enter")
    page.wait_for_timeout(500)
    assert "Input deck not found" in page.locator(".apb-analysis-problem").inner_text()
    command.fill("nastran {deck}")
    command.press("Enter")
    page.wait_for_timeout(500)
    assert "Unknown placeholder" in page.locator(".apb-analysis-problem").inner_text()


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_result_browser_follows_the_connected_analysis(page):
    select(page, "Modal results")
    assert "Result file from block 'Modal analysis'" in page.locator("body").inner_text()
    assert "(that block has not run yet)" in page.locator("body").inner_text()
    run_all(page)
    select(page, "Global margins")  # leave and come back: the panel is built again
    select(page, "Modal results")
    assert "modal.h5" in page.locator("body").inner_text()
    assert "MSC Nastran 2023.3" in page.locator(".apb-h5-info").inner_text()
    assert "Modal effective mass" in page.locator(".apb-h5-results").inner_text()
