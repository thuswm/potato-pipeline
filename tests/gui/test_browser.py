"""End-to-end GUI tests in a real (headless) browser with Playwright.

Skipped when Playwright's browser is not installed (``uv run playwright install chromium``).
Each test starts ``apb gui`` on a free port with a copy of the example pipelines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import expect

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
        # Own config file: the tests never read or write the user's settings.
        env={k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
        | {"PYTHONUTF8": "1", "APB_CONFIG": str(workdir.parent / "config.toml")},
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


def test_open_recent_lists_opened_and_saved_pipelines(page, workdir: Path):
    """File > Open recent (spec 06 GUI-38): newest first, missing files disabled, clear."""
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save as…", exact=True).click()
    dialog = page.locator(".q-dialog")
    dialog.get_by_label("File name").fill("copy.apb.json")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(800)
    (workdir / "fastener_loads.apb.json").rename(workdir / "gone.apb.json")

    def recent_items() -> list[str]:
        page.get_by_role("button", name="File", exact=True).click()
        page.locator(".apb-recent").click()
        page.wait_for_timeout(400)
        return page.locator(".apb-recent-file").all_inner_texts()

    assert recent_items() == ["copy.apb.json", "fastener_loads.apb.json"]
    missing = page.locator(".apb-recent-file", has_text="fastener_loads")
    assert "disabled" in (missing.get_attribute("class") or "")
    page.get_by_text("Clear list", exact=True).click()
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    assert recent_items() == []
    expect(page.get_by_text("No recent pipelines")).to_be_visible()


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


def test_scroll_pans_and_pinch_zooms_around_the_pointer(page):
    """Touchpad (spec 06 GUI-37): scroll pans, pinch (Ctrl + wheel) zooms at the pointer."""
    head = node(page, "Load case names").locator(".apb-head")
    before = head.bounding_box()
    page.mouse.move(before["x"] + 5, before["y"] + 5)
    page.mouse.wheel(30, 50)  # two-finger scroll: right and down
    page.wait_for_timeout(200)
    moved = head.bounding_box()
    assert moved["x"] == pytest.approx(before["x"] - 30, abs=1)
    assert moved["y"] == pytest.approx(before["y"] - 50, abs=1)
    # Pinch out at the block's top-left corner: the block grows, the corner stays put.
    page.mouse.move(moved["x"], moved["y"])
    page.keyboard.down("Control")
    page.mouse.wheel(0, -40)
    page.keyboard.up("Control")
    page.wait_for_timeout(200)
    zoomed = head.bounding_box()
    assert zoomed["width"] > moved["width"] * 1.15
    assert zoomed["x"] == pytest.approx(moved["x"], abs=2)
    assert zoomed["y"] == pytest.approx(moved["y"], abs=2)
    assert page.evaluate("window.scrollY") == 0  # the page itself did not scroll


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
    expect(page.get_by_role("tab", name="min_ms")).to_be_visible()  # waits on slow runners
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
    page.locator(".apb-ports").get_by_text("Ports").click()  # the port editor (spec 11 §4.7)
    page.wait_for_timeout(300)
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
    # The panel may be rebuilt once more after the file change (slow runners); the search
    # box is then new and empty, so type again until the filter is applied.
    for attempt in range(3):
        page.get_by_placeholder("Search results").fill("effective mass")
        try:
            expect(results).not_to_contain_text("Eigenvectors", timeout=3000)
            break
        except AssertionError:
            if attempt == 2:
                raise
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
    assert page.get_by_role("button", name="Edit", exact=True).is_visible()

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


def test_python_ports_and_run_stay_in_step(page, workdir: Path):
    """Round 6: arguments become ports on request; a renamed port renames the argument."""
    select(page, "Fastener margins")
    script = workdir / "scripts" / "fastener_margins.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "safety_factor: float,", "safety_factor: float,\n    extra: pd.DataFrame,"
        ),
        encoding="utf-8",
    )
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Add input port(s): extra").click()
    page.wait_for_timeout(800)
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()
    block = node(page, "Fastener margins")
    assert block.locator(".apb-in", has_text="extra").count() == 1

    page.get_by_text(re.compile(r"^Ports")).first.click()  # unfold the Ports section
    page.wait_for_timeout(300)
    names = page.locator('input[placeholder="name"]')
    box = next(names.nth(i) for i in range(names.count()) if names.nth(i).input_value() == "extra")
    box.fill("more")
    box.blur()
    page.wait_for_timeout(800)
    text = script.read_text(encoding="utf-8")
    assert "more: pd.DataFrame" in text and "extra:" not in text
    notice = "run() in fastener_margins.py updated and saved: renamed extra → more"
    assert page.locator(".q-notification", has_text=notice).count()
    assert page.locator(".q-notification", has_text="Save the pipeline to keep the ports").count()


def test_python_ports_follow_the_code_both_ways(page, workdir: Path):
    """Re-test 2026-11-24: an argument removed from run() and a new returned key.

    The block offers to remove the port (not only to put the argument back) and to add an
    output port for the new key; an output no longer returned can be removed.
    """
    select(page, "Fastener margins")
    script = workdir / "scripts" / "fastener_margins.py"
    text = script.read_text(encoding="utf-8")
    text = text.replace("    safety_factor: float,\n", "").replace(
        '"plot": fig}', '"count": len(margins)}'
    )
    text = text.replace("margins = ", "safety_factor = 1.5\n    margins = ", 1)
    script.write_text(text, encoding="utf-8")
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    problems = page.locator(".apb-script-check").inner_text()
    assert "no argument 'safety_factor'" in problems
    assert "returns 'count'" in problems and "'plot' is not in the dict" in problems
    expect(page.get_by_role("button", name="Add safety_factor to run()")).to_be_visible()
    page.get_by_role("button", name="Remove input port(s): safety_factor").click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="Add output port(s): count").click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="Remove output port(s): plot").click()
    page.wait_for_timeout(800)
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()
    block = node(page, "Fastener margins")
    assert block.locator(".apb-in", has_text="safety_factor").count() == 0
    assert block.locator(".apb-out", has_text="count").count() == 1
    assert block.locator(".apb-out", has_text="plot").count() == 0
    assert script.read_text(encoding="utf-8") == text  # the code was not touched


def test_python_output_ports_from_a_named_dict_and_after_run_note(page, workdir: Path):
    """Second re-test 2026-11-26: ``result = {...}`` ... ``return result`` (spec 04 §3.9.1)."""
    select(page, "Fastener margins")
    script = workdir / "scripts" / "fastener_margins.py"
    text = script.read_text(encoding="utf-8")
    old = '    return {"margins": margins, "min_ms": min_ms, "plot": fig}\n'
    assert old in text
    named = (
        '    result = {"margins": margins, "min_ms": min_ms, "plot": fig}\n'
        '    result["count"] = len(margins)\n'
        "    return result\n"
    )
    script.write_text(text.replace(old, named), encoding="utf-8")
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Add output port(s): count").click()
    page.wait_for_timeout(800)
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()
    assert node(page, "Fastener margins").locator(".apb-out", has_text="count").count() == 1
    # Keys that cannot be read: said, instead of offering nothing silently.
    script.write_text(text.replace(old, "    return dict(margins=margins)\n"), encoding="utf-8")
    page.get_by_role("button", name="Check", exact=True).click()
    page.wait_for_timeout(500)
    assert "checked after the run" in page.locator(".apb-script-outputs").inner_text()


def test_code_in_the_pipeline_file_is_checked_too(page, workdir: Path):
    """Second re-test 2026-11-26: inline code gets the check line and the fixes."""
    add_python_block(page)
    page.get_by_text("Advanced", exact=True).last.click()
    page.wait_for_timeout(300)
    page.get_by_label("Code in the pipeline file").click()  # unfold the (empty) editor
    page.wait_for_timeout(300)
    page.locator(".cm-content").last.click()
    page.keyboard.insert_text('def run(x):\n    return {"y": x}\n')  # no auto-close
    page.get_by_text("The block has no script yet").click()  # blur: stored
    page.wait_for_timeout(800)
    assert "no matching input port" in page.locator(".apb-script-check").inner_text()
    page.get_by_role("button", name="Add input port(s): x").click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="Add output port(s): y").click()
    page.wait_for_timeout(800)
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()


def test_a_failing_script_shows_the_variables_and_shown_values(page, workdir: Path):
    """Round 6: debugging a Python block without leaving the tool."""
    script = workdir / "scripts" / "fastener_margins.py"
    text = script.read_text(encoding="utf-8")
    body_start = text.index('"""', text.index("def run(")) + 3
    body_start = text.index('"""', body_start) + 3  # after the run() docstring
    script.write_text(
        text[:body_start]
        + "\n    from apb import show\n    show(loads, 'loads in')\n    oops = 1 / 0\n"
        + text[body_start:],
        encoding="utf-8",
    )
    run_all(page)
    select(page, "Fastener margins")
    page.wait_for_timeout(400)
    log = page.locator(".apb-block-log")
    assert "Variables in run()" in log.inner_text()  # open without a click
    assert "loads = DataFrame" in log.inner_text()
    page.get_by_role("tab", name="show: loads in").click()
    page.wait_for_timeout(300)
    assert "EID" in page.locator(".q-tab-panels").first.inner_text()


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


def open_report(page, label: str) -> None:
    """Select a Word report block and open its editor dialog (spec 11 §4.4)."""
    select(page, label)
    page.locator(".apb-open-editor").click()
    page.wait_for_selector(".apb-report-editor")
    page.wait_for_timeout(400)


def tab(page, name: str) -> None:
    """Switch the tab of the report editor."""
    page.locator(".apb-report-editor").get_by_role("tab", name=name).click()
    page.wait_for_timeout(400)


def close_editor(page) -> None:
    """Close the report editor if it is open."""
    editor = page.locator(".apb-report-editor")
    if editor.count() and editor.is_visible():
        editor.get_by_role("button", name="Close").click()
        page.wait_for_timeout(400)


def save_file(page) -> None:
    close_editor(page)
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
    open_report(page, "Fastener report")
    tab(page, "Items")
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
    tab(page, "Sections")
    found = summaries(page)
    assert found[0] == "Title"
    assert found[1] == "H1 · 1 Introduction"
    assert found[4] == "H1 · 2 Fastener loads"
    assert found[6] == "Table · load_table · “Fastener loads per load case”"
    assert found[-1] == "Traceability"
    assert page.locator(".apb-unused").count() == 0  # every item is used by the example report
    assert page.locator(".apb-section-warning:visible").count() == 0


def test_report_editor_add_move_duplicate_delete_and_save(page, workdir: Path):
    open_report(page, "Fastener report")
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
    open_report(page, "Fastener report")
    open_section(page, 8)  # table critical_margins
    columns = page.locator(".apb-columns")
    assert "EID" in columns.inner_text() and "TENSION" in columns.inner_text()
    # The formats of the chosen columns are listed with the unit of the table column.
    formats = page.locator(".apb-formats")
    assert "TENSION [N]" in formats.inner_text()
    assert page.locator(".apb-format-column input").count() == 5
    assert "Excel style" in page.locator(".apb-format-help").inner_text()
    assert page.locator(".apb-mass-kg").count() == 1  # Mass in kg switch (spec 05)
    assert page.locator(".apb-format-column input").nth(0).get_attribute("placeholder") == "as is"
    box = page.locator(".apb-format-column input").nth(3)  # EID, SUBCASE, TENSION, SHEAR, MS
    assert box.input_value() == "0.0"
    example = page.locator(".apb-format-example").nth(3)
    assert "→" in example.inner_text()
    box.fill("0.0000")
    page.wait_for_timeout(300)
    assert example.inner_text().split("→")[1].strip().split(".")[1].__len__() == 4
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
    open_report(page, "Fastener report")
    open_section(page, 8)
    caption_box = page.locator(".apb-text-caption input").first
    caption_box.fill("Critical {nope}")
    caption_box.press("Tab")
    page.wait_for_timeout(400)
    assert "Unknown placeholder {nope}" in page.locator(".apb-text-status").first.inner_text()
    title = section_row(page, 8).locator(".apb-section-warning").get_attribute("title")
    assert "Unknown placeholder {nope}" in title


def test_report_editor_insert_value_and_live_preview(page):
    run_all(page)
    open_report(page, "Fastener report")
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
    open_report(page, "Fastener report")
    tab(page, "Items")
    plot = page.locator(".apb-item-name input").nth(2)
    assert plot.input_value() == "plot"
    plot.fill("margin_plot")
    plot.press("Enter")
    page.wait_for_selector("text=reference(s) in the sections will be changed")
    page.get_by_role("button", name="Rename", exact=True).click()
    page.wait_for_timeout(800)
    assert [i.input_value() for i in page.locator(".apb-item-name input").all()][2] == "margin_plot"
    tab(page, "Sections")
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
    open_report(page, "Fastener report")
    tab(page, "Items")
    box = page.locator(".apb-item-name input").nth(3)
    box.fill("plot")
    box.press("Enter")
    page.wait_for_selector("text=already used by another item")
    assert page.locator(".apb-item-name input").nth(3).input_value() == "min_ms"
    # A new table section has no item yet: the warning says so.
    tab(page, "Sections")
    page.get_by_role("button", name="Add section").click()
    page.locator(".q-menu").get_by_text("Table", exact=True).click()
    page.wait_for_timeout(500)
    last = page.locator(".apb-section").last
    assert "Table · (no item)" in last.inner_text()
    assert "Choose an item" in last.locator(".apb-section-warning").get_attribute("title")


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_graph_panel_fields_per_kind_and_live_preview(page, workdir: Path):
    """Spec 13 §3.5: plot type buttons, fields per type, reference lines, live preview."""
    run_all(page)
    select(page, "Global margins graph")
    kinds = page.locator(".apb-graph-kind")
    assert kinds.count() == 8
    assert page.locator(".apb-graph-kind-chosen").get_attribute("aria-label") == "Bar"
    assert page.get_by_label("Bar labels").count() == 1
    assert page.locator(".apb-reference-value input").input_value() == "0.0"
    # Another type: fields of bars go, the sort switch of lines comes (under Advanced).
    page.get_by_role("button", name="Line", exact=True).click()
    page.wait_for_timeout(500)
    assert page.locator(".apb-graph-kind-chosen").get_attribute("aria-label") == "Line"
    assert page.get_by_label("Bar labels").count() == 0
    page.get_by_role("button", name="Bar", exact=True).click()
    page.wait_for_timeout(500)

    # Live preview: a change re-runs only this block, without a notification.
    image = page.locator(".q-tab-panels img").first
    expect(image).to_be_visible()
    before = image.get_attribute("src")
    box = page.get_by_label("Y axis label")
    box.fill("Margin of safety")
    box.blur()
    page.wait_for_timeout(2500)
    assert page.locator(".q-tab-panels img").first.get_attribute("src") != before
    assert page.locator(".q-notification", has_text="Run finished").count() <= 1  # only Run all
    # Only the graph ran; what uses it (the report) is now out of date, nothing else.
    assert "apb-state-done" in node(page, "Global margins graph").get_attribute("class")
    assert "apb-state-stale" in node(page, "Test case report").get_attribute("class")
    stale = page.locator(".drawflow-node.apb-state-stale")
    assert stale.count() == 1

    page.get_by_role("button", name="Add line").click()
    page.wait_for_timeout(400)
    assert page.locator(".apb-reference-line").count() == 2
    save_file(page)
    saved = json.loads((workdir / "test_case.apb.json").read_text(encoding="utf-8"))
    graph = next(b for b in saved["blocks"] if b["id"] == "margins_graph")
    assert graph["params"]["y_label"] == "Margin of safety"
    assert len(graph["params"]["reference_lines"]) == 2


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_test_case_pipeline_runs_in_the_gui(page, workdir: Path):
    assert counts(page) == (17, 31)
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
    expect(page.locator(".apb-script-code")).to_contain_text("def run(")  # drawn by the browser
    assert "run() matches the ports" in page.locator(".apb-script-check").inner_text()

    # The report editor knows the items by the names given on the connectors.
    open_report(page, "Test case report")
    tab(page, "Items")
    names = [i.input_value() for i in page.locator(".apb-item-name input").all()]
    assert names == [
        "effective_mass",
        "f1",
        "f_min",
        "constants",
        "requirement",
        "req_status",
        "SF",
        "global_margins",
        "min_ms_global",
        "local_margins",
        "nastran_messages",
        "mass_plot",
        "margins_plot",
    ]
    assert page.locator(".apb-unused").count() == 0
    assert page.locator(".apb-section-warning:visible").count() == 0
    tab(page, "Sections")
    open_section(page, 8)  # the modal effective mass table: columns known from the run
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
    assert "Stored outputs found for 17 of 17 blocks" in log_text(page)
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
    assert "15 up to date" in log_text(page)
    assert states(page) == {"apb-state-done"}


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_changes_after_reopening_make_results_outdated(page, server):
    run_all(page)
    reopen(page, server)
    select(page, "Project constants")
    value = page.locator(".apb-constant-row").first.locator(".apb-constant-value input")
    value.fill("30")
    value.press("Enter")
    page.wait_for_timeout(1000)
    assert state_of(page, "Project constants") == "stale"
    assert state_of(page, "Frequency requirement") == "stale"
    assert state_of(page, "Global margins") == "done"  # uses SF only, not f_min
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
    assert "downstream: Global margins graph, Test case report" in dialog.inner_text()
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
    folder = dialog.locator(".apb-stored-folder").inner_text().replace("\\", "/")
    assert ".apb/test_case" in folder
    assert "17 block(s)" in dialog.locator(".apb-stored-facts").inner_text()
    dialog.get_by_role("button", name="Clear all…").click()
    page.wait_for_selector("text=Delete the stored outputs of 17 block(s)")
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
    page.locator(
        ".q-drawer--left .q-expansion-item__content .q-item", has_text=re.compile(r"^Constant$")
    ).click()
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
def test_input_deck_dialog_lists_decks_and_shows_the_path_end(page):
    """Round 6: the deck field listed only .h5 files; long paths hid their end."""
    select(page, "Modal analysis")
    page.get_by_role("button", name="Choose file").first.click()
    page.wait_for_timeout(400)
    dialog = page.locator(".q-dialog")
    dialog.get_by_text("fem", exact=True).click()
    page.wait_for_timeout(300)
    listing = dialog.locator(".q-scrollarea").inner_text()
    assert "modal.bdf" in listing and "modal.h5" not in listing
    assert dialog.locator(".apb-path").get_attribute("title").endswith("fem")
    assert dialog.locator(".apb-path").inner_text().strip().endswith("fem")
    dialog.get_by_text("Show all files").click()
    page.wait_for_timeout(300)
    assert "modal.h5" in dialog.locator(".q-scrollarea").inner_text()
    dialog.get_by_text("modal.bdf", exact=True).click()
    page.wait_for_timeout(300)
    assert page.get_by_label("Input file").first.input_value() == "fem/modal.bdf"


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
    # The reason for the mark is listed in the panel (round 6).
    warning = page.locator(".apb-block-warning")
    assert warning.count() >= 1 and "9058" in warning.first.inner_text()
    relation = page.locator(".apb-analysis-relation")
    if "input unchanged" in relation.inner_text():  # a good state is not amber
        assert "text-amber-700" not in relation.get_attribute("class")
    assert (
        "nastran local_loads.bdf batch=no old=no scr=yes"
        in page.locator(".apb-analysis-command").inner_text()
    )
    assert "(default)" in page.locator(".apb-analysis-command").inner_text()
    assert "local_loads.bdf" in page.locator(".apb-analysis-input").inner_text()
    assert "HDF5 request found" in page.locator(".apb-analysis-input").inner_text()
    results = page.locator(".apb-analysis-results").inner_text()
    assert "local_loads.f06 ✔" in results and "local_loads.h5 ✔" in results
    assert page.get_by_role("button", name="Open .f06").is_visible()
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
    assert not page.get_by_role("textbox", name="Path").is_visible()  # folded (spec 11 §4.5)
    page.locator(".apb-advanced").get_by_text("Advanced").click()
    page.wait_for_timeout(300)
    path_box = page.get_by_role("textbox", name="Path")
    path_box.fill(str(workdir / "no_such_folder"))
    path_box.press("Enter")
    page.wait_for_timeout(500)
    assert "'nastran' not found" in page.locator(".apb-analysis-program").inner_text()
    path_box.fill(str(fake_nastran.bin))
    path_box.press("Enter")
    page.wait_for_timeout(500)
    found = page.locator(".apb-analysis-program").inner_text()
    shown = " ".join(found.split())
    assert re.search(r"…[/\\]nastran(\.bat)? \(path: block\)$", shown, re.I), shown  # shortened
    program.hover()
    page.wait_for_timeout(500)
    assert str(fake_nastran.bin) in page.locator(".q-tooltip").inner_text()
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


# --------------------------------------------------------------------------------------
# HDF5 viewer (spec 10)
# --------------------------------------------------------------------------------------


def open_viewer(pg, server: str, file: Path, **kwargs: object) -> None:
    """Go to the HDF5 viewer page of a file on the test server."""
    from apb.gui.h5viewer import viewer_url

    pg.goto(server.rstrip("/") + viewer_url(file, **kwargs))  # type: ignore[arg-type]
    pg.wait_for_selector(".apb-h5v-tree")
    pg.wait_for_timeout(500)


def test_h5_viewer_tree_result_view_filters_and_raw_view(page, server, workdir: Path):
    open_viewer(page, server, workdir / "fem" / "modal.h5", dataset="NODAL/EIGENVECTOR", units="SI")
    assert "modal.h5 — HDF5 viewer" in page.title()
    header = page.locator(".q-page, body").first.inner_text()
    assert "MSC Nastran 2023.3 · SOL 103 · modal.bdf" in header
    assert "Subcases: 1 (10 modes)" in header
    count = page.locator(".apb-h5v-count")
    assert "30 080 rows · showing 1-100" in count.inner_text()
    table = page.locator(".apb-h5v-table")
    assert "EIGENVALUE" in table.inner_text()

    # Mode filter (via the domains) and ID filter.
    page.locator(".apb-h5v-modes").click()
    page.get_by_role("option", name="2", exact=True).click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    assert "3 008 rows of 30 080 (filtered)" in count.inner_text()
    ids = page.locator(".apb-h5v-ids input")
    ids.fill("1-5")
    ids.press("Enter")
    page.wait_for_timeout(500)
    assert "5 rows of 30 080 (filtered) · showing 1-5" in count.inner_text()

    # Raw view: the dataset as stored, with DOMAIN_ID and the row numbers.
    page.locator(".apb-h5v-view").get_by_text("Raw", exact=True).click()
    page.wait_for_timeout(500)
    assert "DOMAIN_ID" in table.inner_text()
    assert "3008" in table.inner_text()  # first row of mode 2

    # Another dataset from the tree (found with the search): filters do not carry over.
    page.locator(".apb-h5v-search input").fill("GRID")
    page.wait_for_timeout(300)
    page.locator(".apb-h5v-tree").get_by_text("GRID", exact=True).first.click()
    page.wait_for_timeout(600)
    assert page.locator(".apb-h5v-path").inner_text() == "/NASTRAN/INPUT/NODE/GRID"
    assert "3 008 rows · showing 1-100" in count.inner_text()
    page.locator(".apb-h5v-pager").get_by_role("button", name="2", exact=True).click()
    page.wait_for_timeout(500)
    assert "showing 101-200" in count.inner_text()

    # Details of a group: its attributes.
    open_viewer(page, server, workdir / "fem" / "modal.h5", dataset="/NASTRAN")
    assert page.locator(".apb-h5v-path").inner_text() == "/NASTRAN"
    assert "msc20233" in page.locator(".apb-h5v-attrs").inner_text()


def test_h5_viewer_copy_path_and_export_csv(page, server, workdir: Path):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    h5 = workdir / "data" / "synthetic_plate.h5"
    open_viewer(page, server, h5, dataset="NODAL/DISPLACEMENT", subcases=[2])
    count = page.locator(".apb-h5v-count")
    assert "55 rows of 165 (filtered)" in count.inner_text()
    assert "[mm]" in page.locator(".apb-h5v-table").inner_text()  # unit system of the file

    page.get_by_role("button", name="Copy path").click()
    page.wait_for_timeout(300)
    assert page.get_by_text("Copied NODAL/DISPLACEMENT").is_visible()
    assert page.evaluate("navigator.clipboard.readText()") == "NODAL/DISPLACEMENT"

    page.get_by_role("button", name="Export CSV").click()
    page.wait_for_timeout(300)
    name = page.get_by_label("File name")
    assert name.input_value() == "synthetic_plate_DISPLACEMENT.csv"
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(800)
    assert page.get_by_text("Wrote 55 rows").is_visible()
    lines = (workdir / "data" / "synthetic_plate_DISPLACEMENT.csv").read_text().splitlines()
    assert lines[0].startswith("GRID,SUBCASE,")
    assert "[mm]" in lines[0]
    assert len(lines) == 56


def test_h5_viewer_reports_unreadable_files(page, server, workdir: Path):
    from apb.gui.h5viewer import viewer_url

    page.goto(server.rstrip("/") + viewer_url(workdir / "missing.h5"))
    page.wait_for_selector(".apb-h5v-message")
    assert "File not found" in page.locator(".apb-h5v-message").inner_text()
    page.goto(server.rstrip("/") + viewer_url(workdir / "fastener_loads.apb.json"))
    page.wait_for_selector(".apb-h5v-message")
    assert "Cannot open" in page.locator(".apb-h5v-message").inner_text()


def test_h5_viewer_opens_from_the_block_panel_and_the_file_menu(page, workdir: Path):
    select(page, "CBUSH forces")
    with page.context.expect_page() as new_tab:
        page.locator(".apb-h5-open-viewer").click()
    viewer = new_tab.value
    viewer.wait_for_selector(".apb-h5v-tree")
    viewer.wait_for_timeout(500)
    assert "/h5?" in viewer.url
    assert viewer.locator(".apb-h5v-path").inner_text().endswith("/ELEMENTAL/ELEMENT_FORCE/BUSH")
    viewer.close()

    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("HDF5 viewer…", exact=True).click()
    page.wait_for_timeout(300)
    page.get_by_label("File name").fill("fem/global_loads.h5")
    with page.context.expect_page() as new_tab:
        page.get_by_role("button", name="Open", exact=True).click()
    viewer = new_tab.value
    viewer.wait_for_selector(".apb-h5v-tree")
    assert "global_loads.h5 — HDF5 viewer" in viewer.title()
    viewer.close()


# --------------------------------------------------------------------------------------
# Constants block (spec 03 §3.5)
# --------------------------------------------------------------------------------------


def constant_row(page, index: int):
    return page.locator(".apb-constant-row").nth(index)


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_constants_panel_edit_add_rename_delete(page, workdir: Path):
    select(page, "Project constants")
    assert page.locator(".apb-constant-row").count() == 2
    assert constant_row(page, 0).locator(".apb-constant-name input").input_value() == "f_min"
    users = constant_row(page, 0).locator(".apb-constant-users").inner_text()
    assert "Frequency requirement (limit)" in users and "Test case report (items)" in users
    block = node(page, "Project constants")
    assert block.locator(".output").count() == 3  # table, f_min, SF

    # A new constant: an invalid name is refused with a message, a valid one gets a port.
    page.locator(".apb-constant-add").click()
    page.wait_for_timeout(300)
    new = constant_row(page, 2)
    new.locator(".apb-constant-name input").fill("F F")
    new.locator(".apb-constant-value input").fill("1.15")
    new.locator(".apb-constant-value input").press("Enter")
    page.wait_for_timeout(400)
    assert "letters, digits" in constant_row(page, 2).locator(".apb-constant-error").inner_text()
    assert block.locator(".output").count() == 3
    constant_row(page, 2).locator(".apb-constant-name input").fill("FF")
    constant_row(page, 2).locator(".apb-constant-name input").press("Enter")
    page.wait_for_timeout(500)
    assert block.locator(".output").count() == 4
    assert "Not used yet" in constant_row(page, 2).locator(".apb-constant-users").inner_text()

    # Rename SF: connectors stay, report text and margin script ports follow.
    name = constant_row(page, 1).locator(".apb-constant-name input")
    name.fill("SF_ult")
    name.press("Enter")
    page.wait_for_timeout(600)
    assert page.get_by_text("Renamed SF to SF_ult").first.is_visible()
    assert counts(page) == (17, 31)

    # Delete the unused constant (no question asked), then save.
    constant_row(page, 2).locator(".apb-constant-delete").click()
    page.wait_for_timeout(400)
    assert page.locator(".apb-constant-row").count() == 2
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Save", exact=True).click()
    page.wait_for_timeout(500)
    saved = json.loads((workdir / "test_case.apb.json").read_text(encoding="utf-8"))
    const = next(b for b in saved["blocks"] if b["id"] == "const")
    assert [r["name"] for r in const["params"]["constants"]] == ["f_min", "SF_ult"]
    report = next(b for b in saved["blocks"] if b["id"] == "report")
    assert "{SF_ult:.2f}" in json.dumps(report["params"]["sections"])
    assert any(c["source"] == {"block": "const", "port": "SF_ult"} for c in saved["connections"])


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_deleting_a_used_constant_asks_first(page):
    select(page, "Project constants")
    constant_row(page, 0).locator(".apb-constant-delete").click()
    page.wait_for_timeout(300)
    dialog = page.locator(".q-dialog")
    assert "f_min is used by Frequency requirement (limit)" in dialog.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(300)
    assert page.locator(".apb-constant-row").count() == 2
    constant_row(page, 0).locator(".apb-constant-delete").click()
    page.wait_for_timeout(300)
    page.locator(".q-dialog").get_by_role("button", name="Delete").click()
    page.wait_for_timeout(500)
    assert page.locator(".apb-constant-row").count() == 1
    assert counts(page) == (17, 29)  # its two connectors are gone


def test_report_side_panel_summary_and_editor_tabs(page):
    select(page, "Fastener report")
    summary = page.locator(".apb-report-summary").inner_text()
    assert "sections · 7 items" in summary and "No problems found" in summary
    assert page.locator(".apb-section").count() == 0  # the sections are in the dialog only
    open_report(page, "Fastener report")
    assert page.locator(".apb-detail").inner_text().startswith("Select a section")
    open_section(page, 1)
    assert page.locator(".apb-section-open").count() == 1
    assert "Section 2 · H1 · 1 Introduction" in page.locator(".apb-detail").inner_text()
    tab(page, "Document")
    assert page.locator(".apb-report-editor").get_by_label("Title").count() == 1
    tab(page, "Sections")
    page.get_by_role("button", name="Add section").click()
    page.locator(".q-menu").get_by_text("Table", exact=True).click()
    page.wait_for_timeout(500)
    close_editor(page)
    problems = page.locator(".apb-report-problems").inner_text()
    assert "1 problem" in problems and "Choose an item" in problems


def test_rbe2_loads_offers_the_subcases_of_the_file(page, workdir: Path):
    select(page, "RBE2 loads")
    subcases = page.locator(".apb-choices")  # spec 11 PNL-11: a drop-down, not text
    subcases.click()
    page.wait_for_timeout(300)
    options = page.locator(".q-menu .q-item").all_inner_texts()
    assert [o.strip() for o in options] == ["1", "2", "3", "4"]  # synthetic joint file
    page.locator(".q-menu .q-item").nth(1).click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    save_file(page)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    rbe2 = next(b for b in saved["blocks"] if b.get("label") == "RBE2 loads")
    assert rbe2["params"]["subcases"] == [2]


# ---- spec 11 step 4.8: requirement check and table block panels ---------------------------


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_requirement_panel_shows_the_connected_limit_and_unit_list(page):
    select(page, "Frequency requirement")
    panel = page.locator(".apb-connected")
    assert "Limit from block 'Project constants'" in panel.inner_text()
    assert "has not run yet" in panel.inner_text()
    assert page.get_by_label("Limit", exact=True).count() == 0  # PNL-07: no empty field
    assert "> greater than" in page.get_by_label("Comparison").locator("..").inner_text()
    run_all(page)
    select(page, "Lowest frequency")
    select(page, "Frequency requirement")
    assert "20 Hz" in page.locator(".apb-connected").inner_text()
    page.get_by_text("Advanced", exact=True).click()
    page.locator(".apb-unit").click()
    page.wait_for_timeout(300)
    options = [o.strip() for o in page.locator(".q-menu .q-item").all_inner_texts()]
    assert options[0] == "(none)" and "Hz" in options and "MPa" in options


def test_table_blocks_offer_the_upstream_columns_after_a_run(page, workdir: Path):
    select(page, "Critical fastener")
    assert page.locator(".apb-column-choice").count() == 0  # upstream not run: text fields
    assert page.get_by_label(re.compile(r"^Value columns")).input_value() == "TENSION, SHEAR"
    run_all(page)
    select(page, "Drop GRID")
    select(page, "Critical fastener")
    choices = page.locator(".apb-column-choice")
    assert choices.count() == 3  # group by, value columns, governing columns
    values = choices.nth(1)
    assert "TENSION" in values.inner_text() and "SHEAR" in values.inner_text()
    values.click()
    page.wait_for_timeout(300)
    options = [o.strip() for o in page.locator(".q-menu .q-item").all_inner_texts()]
    assert options[:4] == ["EID", "SUBCASE", "TENSION", "SHEAR"]
    page.locator(".q-menu .q-item", has_text="EID").first.click()  # added at the end
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    save_file(page)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    block = next(b for b in saved["blocks"] if b.get("label") == "Critical fastener")
    assert block["params"]["columns"] == ["TENSION", "SHEAR", "EID"]


def test_rename_table_lists_the_columns(page, workdir: Path):
    """Round 6: renaming columns was hard to find; the panel now lists every column."""
    run_all(page)
    select(page, "Drop GRID")
    assert "Select / rename columns" in page.locator(".apb-block-type").inner_text()
    # The table title (default sheet name) is shown above the preview (round 6).
    assert page.locator(".apb-table-title").first.inner_text().strip() != ""
    olds = page.locator(".apb-rename-old").all_inner_texts()
    assert olds == ["EID", "SUBCASE", "TENSION", "SHEAR"]  # the kept columns, not GRID
    box = page.get_by_label("New name for SHEAR")
    box.fill("SHEAR_FORCE")
    box.press("Enter")
    page.wait_for_timeout(300)
    # Dropping SUBCASE from the kept columns removes it from the rename table at once.
    page.locator(".apb-column-choice").first.locator(".q-chip", has_text="SUBCASE").locator(
        ".q-chip__icon--remove"
    ).click()
    page.wait_for_timeout(400)
    assert "SUBCASE" not in page.locator(".apb-rename-old").all_inner_texts()
    save_file(page)
    saved = json.loads((workdir / "fastener_loads.apb.json").read_text(encoding="utf-8"))
    block = next(b for b in saved["blocks"] if b.get("label") == "Drop GRID")
    assert block["params"]["rename"] == {"SHEAR": "SHEAR_FORCE"}
    assert "SUBCASE" not in block["params"]["columns"]


# ---- spec 11 step 4.9: file lines, Excel, fastener and constants panels -------------------


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_file_lines_and_sheet_name_per_table(page, workdir: Path):
    select(page, "Locations")
    assert page.locator(".apb-file-state").inner_text().startswith("found · ")
    select(page, "Modal workbook")
    assert "not written yet" in page.locator(".apb-file-state").inner_text()
    rows = page.locator(".apb-per-item-value")
    assert rows.count() == 1  # one connected table
    assert "Modal results" in page.locator(".apb-per-item").inner_text()
    rows.first.locator("input").fill("Mass")
    rows.first.locator("input").press("Enter")
    page.wait_for_timeout(400)
    run_all(page)
    select(page, "Locations")
    select(page, "Modal workbook")
    assert page.locator(".apb-file-state").inner_text().startswith("written · ")
    assert page.locator(".apb-open-file").count() == 1
    save_file(page)
    saved = json.loads((workdir / "test_case.apb.json").read_text(encoding="utf-8"))
    book = next(b for b in saved["blocks"] if b.get("label") == "Modal workbook")
    assert book["params"]["sheet_names"] == ["Mass"]
    import openpyxl

    wb = openpyxl.load_workbook(workdir / "output" / "test_case_modal.xlsx", read_only=True)
    assert wb.sheetnames == ["Mass"]
    wb.close()


def test_read_excel_offers_the_sheets_and_fastener_labels(page, workdir: Path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Loads"
    wb.create_sheet("Notes")
    wb.save(workdir / "book.xlsx")
    select(page, "Fastener loads CBUSH")
    assert page.get_by_label("Fastener axis").count() == 1
    assert page.get_by_text("Signed (compression negative)").count() >= 1

    page.locator(".q-drawer .q-item", has_text="Read Excel").first.click()
    page.wait_for_timeout(500)
    select(page, "Read Excel")
    file = page.get_by_label("File *")
    file.fill("book.xlsx")
    file.press("Enter")
    page.wait_for_timeout(400)
    assert page.locator(".apb-file-state").inner_text().startswith("found · ")
    select(page, "Fastener loads CBUSH")
    select(page, "Read Excel")
    page.locator(".apb-choice").click()
    page.wait_for_timeout(300)
    options = [o.strip() for o in page.locator(".q-menu .q-item").all_inner_texts()]
    assert options == ["(first sheet)", "Loads", "Notes"]


@pytest.mark.parametrize("server", ["test_case.apb.json"], indirect=True)
def test_constants_editor_dialog_and_compact_list(page, workdir: Path):
    select(page, "Project constants")
    assert page.locator(".apb-constant-row").count() == 2
    page.locator(".apb-constants-open").click()
    page.wait_for_timeout(400)
    dialog = page.locator(".apb-constants-editor")
    assert dialog.locator(".apb-constant-row").count() == 2
    for i in range(7):  # 9 constants: more than the side panel shows as cards
        dialog.locator(".apb-constant-add").click()
        page.wait_for_timeout(250)
        row = dialog.locator(".apb-constant-row").nth(2 + i)
        row.locator(".apb-constant-name input").fill(f"K{i}")
        row.locator(".apb-constant-value input").fill(str(i + 1))
        row.locator(".apb-constant-value input").press("Enter")
        page.wait_for_timeout(350)
    assert dialog.locator(".apb-constant-error").count() == 0
    dialog.get_by_role("button", name="Close").click()
    page.wait_for_timeout(400)
    assert page.locator(".apb-constant-row").count() == 0  # compact list in the side panel
    lines = page.locator(".apb-constant-line").all_inner_texts()
    assert len(lines) == 9 and lines[0].startswith("f_min = 20 Hz")
    assert node(page, "Project constants").locator(".output").count() == 10
    save_file(page)
    saved = json.loads((workdir / "test_case.apb.json").read_text(encoding="utf-8"))
    const = next(b for b in saved["blocks"] if b["id"] == "const")
    assert [r["name"] for r in const["params"]["constants"]][-1] == "K6"


def test_settings_of_this_computer(page, workdir: Path):
    """Round 6: Nastran settings once per computer; a block shows what it inherits."""
    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Settings of this computer…", exact=True).click()
    dialog = page.locator(".apb-user-settings")
    dialog.get_by_label("Options").fill("mem=4gb, smp=4")
    dialog.get_by_label("Folder with the Nastran program").fill("/opt/msc/bin")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(500)
    text = (workdir.parent / "config.toml").read_text(encoding="utf-8")
    assert 'path = "/opt/msc/bin"' in text and 'options = ["mem=4gb", "smp=4"]' in text

    page.get_by_role("button", name="File", exact=True).click()
    page.get_by_text("Settings of this computer…", exact=True).click()
    dialog.get_by_label("Options").fill("mem 4gb")  # invalid: refused, nothing written
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(400)
    assert page.locator(".q-notification", has_text="key=value").count() == 1
    assert (workdir.parent / "config.toml").read_text(encoding="utf-8") == text
