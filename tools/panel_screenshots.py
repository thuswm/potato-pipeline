"""Screenshot the property panel of every block type (spec 11, round 4 step 4).

The two example pipelines are copied to a temporary folder; the block types they do not
contain are added to a copy of the test case. Both pipelines are run in the GUI (headless
Chromium via Playwright) and the panel of one block of each type is saved as a PNG,
cropped to its content and reduced to 64 colours.

Usage::

    uv run python tools/panel_screenshots.py docs/spec/panels/after [--width 400]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
from PIL import Image, ImageChops
from playwright.sync_api import Page, sync_playwright

from apb.engine import PipelineModel, load_pipeline, save_pipeline

ROOT = Path(__file__).resolve().parents[1]

#: Block types missing from the examples: (type, id, parameters, connect from).
EXTRA: list[tuple[str, str, dict[str, object], str | None]] = [
    (
        "table.compute",
        "x_compute",
        {"new_column": "F2", "expression": "FREQUENCY**2"},
        "modal.table",
    ),
    ("table.filter", "x_filter", {"expression": "MODE <= 3"}, "modal.table"),
    ("table.sort", "x_sort", {"by": ["FREQUENCY"]}, "modal.table"),
    ("units.convert", "x_convert", {"to": "SI_mm"}, "modal.table"),
    ("table.set_units", "x_setunits", {"unit_system": "SI"}, "locs.table"),
    ("nastran.hdf5_eigenvalues", "x_eig", {"file": "fem/modal.h5"}, None),
    ("nastran.hdf5_masses", "x_mass", {"file": "fem/global_loads.h5"}, None),
    ("table.read_excel", "x_rxl", {"file": "test_case/locations.xlsx"}, None),
]


def free_port() -> int:
    """Return a free local port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def prepare(work: Path) -> list[Path]:
    """Copy the examples and write the inventory pipeline; return the pipelines to shoot."""
    shutil.copytree(ROOT / "examples", work, ignore=shutil.ignore_patterns("output", ".apb"))
    csv = work / "test_case" / "locations.csv"
    pd.read_csv(csv).to_excel(work / "test_case" / "locations.xlsx", index=False)
    p: PipelineModel = load_pipeline(work / "test_case.apb.json")
    for i, (type_id, block_id, params, source) in enumerate(EXTRA):
        inst = p.add_block(type_id, block_id=block_id, label=f"Extra {block_id[2:]}", **params)
        inst.position = (1300 + (i % 2) * 250, 40 + (i // 2) * 160)
        if source:
            port = "value" if type_id == "units.convert" else "table"
            p.connect(source, f"{block_id}.{port}")
    join = p.add_block("table.join", block_id="x_join", label="Extra join", on=["MODE"])
    join.position = (1300, 700)
    p.connect("modal.table", "x_join.left")
    p.connect("x_filter.table", "x_join.right")
    save_pipeline(p, work / "inventory.apb.json")
    return [work / "inventory.apb.json", work / "fastener_loads.apb.json"]


def crop(path: Path) -> tuple[int, int]:
    """Cut the white space below the content and reduce the colours; return the size."""
    image = Image.open(path).convert("RGB")
    box = ImageChops.difference(image, Image.new("RGB", image.size, (255, 255, 255))).getbbox()
    if box:
        image = image.crop((0, 0, image.width, min(image.height, box[3] + 12)))
    image.quantize(colors=64).save(path, optimize=True)
    return image.size


def select(page: Page, label: str) -> None:
    """Click a block on the canvas by its label."""
    node = page.locator(".drawflow-node", has=page.locator(".apb-label", has_text=label)).first
    node.locator(".apb-head").first.click()
    page.wait_for_timeout(900)


def shoot(pipeline: Path, work: Path, out: Path, width: int, done: dict[str, object]) -> None:
    """Run one pipeline in the GUI and save the panels of the types not yet done."""
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "apb.cli",
            "gui",
            str(pipeline),
            "--no-browser",
            "--port",
            str(port),
        ],
        cwd=work,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(150):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        model = load_pipeline(pipeline)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 2600})
            page.add_init_script(f"localStorage.setItem('apbPanelWidth', '{width}')")
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector(".drawflow-node")
            page.wait_for_timeout(1500)
            page.get_by_role("button", name="Run", exact=True).first.click()
            page.wait_for_timeout(12000)
            for block in model.blocks:
                if block.type_id in done:
                    continue
                select(page, block.label or block.id)
                drawer = page.locator(".q-drawer--right .q-drawer__content")
                target = out / f"{block.type_id}.png"
                drawer.screenshot(path=str(target))
                size = crop(target)
                done[block.type_id] = {"block": block.label, "height_px": size[1]}
                print(f"{block.type_id:28} {size[1]:5d} px", flush=True)
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def main() -> None:
    """Parse the arguments and take the screenshots."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="Folder for the PNG files")
    parser.add_argument("--width", type=int, default=400, help="Panel width in px")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    done: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "examples"
        for pipeline in prepare(work):
            shoot(pipeline, work, args.out, args.width, done)
    (args.out / "heights.json").write_text(json.dumps(done, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
