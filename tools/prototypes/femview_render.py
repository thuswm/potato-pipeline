"""Throwaway prototype for spec 14 (FE view block): headless rendering with PyVista.

Not part of the package. Answers three questions before the block is built (round 8):
does off-screen rendering work on this machine / in CI, how long does it take, and can the
mesh be exported as glTF for the interactive view (``ui.scene``)?

Usage::

    python tools/prototypes/femview_render.py examples/fem/modal.h5 OUT_DIR [--mode 1]

Writes ``model.png``, ``mode<N>.png`` and ``model.gltf`` to OUT_DIR and prints timings.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import h5py
import numpy as np
import pyvista as pv

pv.OFF_SCREEN = True


def read_model(path: Path) -> dict[str, object]:
    """Read grids (basic system only), CQUAD4, CBUSH, RBE2, CONM2 and SPC1 grids."""
    with h5py.File(path, "r") as h5:
        inp = h5["NASTRAN/INPUT"]
        grid = inp["NODE/GRID"][()]
        if np.any(grid["CP"] != 0) or np.any(grid["CD"] != 0):
            raise SystemExit("prototype: only the basic coordinate system")
        ids = grid["ID"]
        index = {int(g): i for i, g in enumerate(ids)}
        xyz = np.asarray(grid["X"], dtype=float)
        el = inp["ELEMENT"]
        quads = el["CQUAD4"][()] if "CQUAD4" in el else None
        bushes = el["CBUSH"][()] if "CBUSH" in el else None
        rbe2 = None
        if "RBE2" in el:
            rb, gm = el["RBE2/RB"][()], el["RBE2/GM"][()]["ID"]
            rbe2 = [(int(r["GN"]), gm[r["GM_POS"] : r["GM_POS"] + r["GM_LEN"]]) for r in rb]
        conm2 = el["CONM2"][()]["G"] if "CONM2" in el else np.array([], dtype=int)
        spc: dict[int, str] = {}
        if "CONSTRAINT/SPC1" in inp:
            ident = inp["CONSTRAINT/SPC1/SPC1_G/IDENTITY"][()]
            gs = inp["CONSTRAINT/SPC1/SPC1_G/G"][()]["ID"]
            for row in ident:
                for g in gs[row["G_POS"] : row["G_POS"] + row["G_LEN"]]:
                    spc[int(g)] = str(row["C"])
        modes = {}
        if "RESULT/NODAL/EIGENVECTOR" in h5["NASTRAN"]:
            domains = {int(d["ID"]): d for d in h5["NASTRAN/RESULT/DOMAINS"][()]}
            vec = h5["NASTRAN/RESULT/NODAL/EIGENVECTOR"][()]
            for dom in np.unique(vec["DOMAIN_ID"]):
                rows = vec[vec["DOMAIN_ID"] == dom]
                d = domains[int(dom)]
                disp = np.zeros_like(xyz)
                disp[[index[int(g)] for g in rows["ID"]]] = np.c_[rows["X"], rows["Y"], rows["Z"]]
                freq = np.sqrt(abs(d["TIME_FREQ_EIGR"])) / (2 * np.pi)
                modes[int(d["MODE"])] = (disp, freq)
    return {
        "xyz": xyz,
        "index": index,
        "quads": quads,
        "bushes": bushes,
        "rbe2": rbe2,
        "conm2": conm2,
        "spc": spc,
        "modes": modes,
    }


def build_grids(m: dict, disp: np.ndarray | None = None) -> tuple[pv.PolyData, pv.PolyData]:
    """Return (shells with PID, lines of CBUSH and RBE2) at the (deformed) positions."""
    pts = m["xyz"] + (disp if disp is not None else 0.0)
    idx = m["index"]
    faces, pids = [], []
    if m["quads"] is not None:
        for q in m["quads"]:
            faces += [4, *(idx[int(g)] for g in q["G"])]
            pids.append(int(q["PID"]))
    shells = pv.PolyData(pts, faces=np.array(faces) if faces else None)
    if pids:
        shells.cell_data["PID"] = np.array(pids)
    lines = []
    if m["bushes"] is not None:
        for b in m["bushes"]:
            lines += [2, idx[int(b["GA"])], idx[int(b["GB"])]]
    for gn, gms in m["rbe2"] or []:
        for g in gms:
            lines += [2, idx[gn], idx[int(g)]]
    line_data = pv.PolyData(pts, lines=np.array(lines)) if lines else pv.PolyData()
    return shells, line_data


def render(m: dict, out: Path, mode: int | None, size: tuple[int, int]) -> float:
    """Render one image; returns seconds."""
    start = time.perf_counter()
    plotter = pv.Plotter(off_screen=True, window_size=size)
    plotter.set_background("white")
    model_size = float(np.ptp(m["xyz"], axis=0).max())
    disp, title = None, "Undeformed model"
    if mode is not None:
        vec, freq = m["modes"][mode]
        scale = 0.1 * model_size / max(float(np.abs(vec).max()), 1e-30)
        disp = vec * scale
        title = f"Mode {mode} - {freq:.2f} Hz (scale {scale:.3g})"
        outline, _ = build_grids(m)
        plotter.add_mesh(outline, style="wireframe", color="#bbbbbb", line_width=0.5)
    shells, lines = build_grids(m, disp)
    plotter.add_mesh(
        shells,
        scalars="PID" if "PID" in shells.cell_data else None,
        categories=True,
        cmap=["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7"],
        show_edges=True,
        edge_color="#333333",
        line_width=0.3,
        show_scalar_bar=False,
    )
    if lines.n_points:
        plotter.add_mesh(lines, color="black", line_width=1.5)
    if len(m["conm2"]):
        pts = m["xyz"][[m["index"][int(g)] for g in m["conm2"]]]
        plotter.add_points(pts, color="#CC79A7", point_size=8, render_points_as_spheres=True)
    if m["spc"]:
        grids = list(m["spc"])
        pts = m["xyz"][[m["index"][g] for g in grids]]
        glyph = pv.Cone(direction=(0, 0, 1), height=model_size * 0.02, radius=model_size * 0.008)
        cloud = pv.PolyData(pts)
        plotter.add_mesh(cloud.glyph(geom=glyph, orient=False, scale=False), color="#009E73")
        plotter.add_point_labels(
            pts[:1], [m["spc"][grids[0]]], font_size=10, show_points=False, shape=None
        )
    plotter.add_text(title, font_size=10, color="black")
    plotter.add_axes(color="black")
    plotter.enable_parallel_projection()
    plotter.view_isometric()
    plotter.reset_camera()
    plotter.screenshot(str(out))
    plotter.close()
    return time.perf_counter() - start


def main() -> None:
    """Run the prototype."""
    ap = argparse.ArgumentParser()
    ap.add_argument("h5", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--mode", type=int, default=1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    m = read_model(args.h5)
    print(f"read: {time.perf_counter() - t0:.2f} s, {len(m['xyz'])} grids")
    print(f"model.png: {render(m, args.out / 'model.png', None, (1600, 1000)):.2f} s")
    if m["modes"]:
        took = render(m, args.out / f"mode{args.mode}.png", args.mode, (1600, 1000))
        print(f"mode{args.mode}.png: {took:.2f} s")
    t0 = time.perf_counter()
    plotter = pv.Plotter(off_screen=True)
    shells, lines = build_grids(m)
    plotter.add_mesh(shells, show_edges=True)
    if lines.n_points:
        plotter.add_mesh(lines, color="black")
    plotter.export_gltf(str(args.out / "model.gltf"))
    plotter.close()
    size = (args.out / "model.gltf").stat().st_size
    print(f"model.gltf: {time.perf_counter() - t0:.2f} s, {size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
