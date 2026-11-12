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


@pytest.fixture
def server(workdir: Path) -> Iterator[str]:
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
    url = f"http://127.0.0.1:{port}/"
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
    page.mouse.move(400, 820, steps=8)
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
