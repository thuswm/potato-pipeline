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
    shutil.copytree(EXAMPLES, tmp_path / "examples", ignore=shutil.ignore_patterns("output"))
    return tmp_path / "examples"


class ServerUrl(str):
    """URL of the test server; ``proc`` is the server process."""

    proc: subprocess.Popen[bytes]


@pytest.fixture
def server(workdir: Path) -> Iterator[ServerUrl]:
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "apb.cli",
            "gui",
            str(workdir / "fastener_loads.apb.json"),
            "--no-browser",
            "--port",
            str(port),
        ],
        stdout=(workdir / "gui.log").open("w"),
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
                break
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("GUI server did not start:\n" + (workdir / "gui.log").read_text())
    yield url
    if proc.poll() is None:
        proc.terminate()
    proc.wait(timeout=10)


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
    file = page.get_by_role("textbox", name="File *")
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
    box = page.get_by_role("textbox", name="File *")
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
    box = page.get_by_role("textbox", name="File *")
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
    page.get_by_role("button", name="Check").click()
    page.wait_for_timeout(500)
    assert (
        "run() argument 'extra' has no matching input port"
        in page.locator(".apb-script-check").inner_text()
    )
    assert "extra," in page.locator(".apb-script-code").inner_text()

    script.write_text("def run(:\n", encoding="utf-8")
    page.get_by_role("button", name="Check").click()
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
