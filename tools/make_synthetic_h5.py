# /// script
# requires-python = ">=3.12"
# dependencies = ["h5py>=3.10", "numpy>=1.26"]
# ///
"""Generate synthetic MSC Nastran HDF5 result files for development and testing.

The files mimic the layout of MSC Nastran ``.h5`` output (``/NASTRAN/INPUT/...`` and
``/NASTRAN/RESULT/...`` with compound datasets and a ``DOMAINS`` table). They are **not**
produced by Nastran: the numbers come from simple hand-calculation formulas so that they
are plausible and deterministic, not from a finite element solution.

The layout follows the MSC Nastran HDF5 schema as far as known without a reference file.
Dataset and field names must be verified against real MSC Nastran 2023.3 output when
sample files are available (spec 02 §3.2).

Two files are written (unit system ``SI_mm``: mm, N, MPa, t):

* ``synthetic_plate.h5`` - cantilever plate (CQUAD4) with a stiffener (CBEAM) and tip
  masses (CONM2); three static subcases and a modal subcase with six modes.
* ``synthetic_fastener_joint.h5`` - lap joint with four CBUSH fasteners and two RBE2
  fasteners; four static subcases; CBUSH forces, MPC forces and grid point forces.

Usage::

    python tools/make_synthetic_h5.py [output_dir]      # default: examples/data
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

#: Fields of ``/NASTRAN/RESULT/DOMAINS``.
DOMAIN_FIELDS: list[tuple[str, Any]] = [
    ("ID", "<i8"),
    ("SUBCASE", "<i8"),
    ("STEP", "<i8"),
    ("ANALYSIS", "<i8"),  # 1 = statics, 2 = normal modes
    ("TIME_FREQ_EIGR", "<f8"),
    ("EIGI", "<f8"),
    ("MODE", "<i8"),
    ("DESIGN_CYCLE", "<i8"),
    ("RANDOM", "<i8"),
    ("SE", "<i8"),
    ("AFPM", "<i8"),
    ("TRMC", "<i8"),
    ("INSTANCE", "<i8"),
    ("MODULE", "<i8"),
]

#: Fields of nodal six-component results (displacement, SPC/MPC force, eigenvector).
NODAL6_FIELDS: list[tuple[str, Any]] = [
    ("ID", "<i8"),
    ("X", "<f8"),
    ("Y", "<f8"),
    ("Z", "<f8"),
    ("RX", "<f8"),
    ("RY", "<f8"),
    ("RZ", "<f8"),
    ("DOMAIN_ID", "<i8"),
]

#: Number of output stations stored per CBEAM element (unused stations are zero).
BEAM_STATIONS = 11


def write_table(
    h5: h5py.File, path: str, fields: Sequence[tuple[str, Any]], rows: Sequence[Mapping[str, Any]]
) -> None:
    """Write a list of row dicts as a compound dataset.

    Args:
        h5: Open HDF5 file.
        path: Absolute dataset path, intermediate groups are created.
        fields: NumPy dtype description ``[(name, type), ...]``; array fields use
            ``(name, type, shape)``.
        rows: One mapping per row; missing fields are written as zero.
    """
    dtype = np.dtype(list(fields))
    data = np.zeros(len(rows), dtype=dtype)
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[key][i] = value
    h5.create_dataset(path, data=data)


def mark_synthetic(h5: h5py.File, description: str) -> None:
    """Attach attributes that identify the file as synthetic test data.

    Args:
        h5: Open HDF5 file.
        description: Short description of the model.
    """
    h5.attrs["APB_SYNTHETIC"] = True
    h5.attrs["APB_DESCRIPTION"] = description
    h5.attrs["APB_UNIT_SYSTEM"] = "SI_mm"
    h5.attrs["APB_GENERATOR"] = "tools/make_synthetic_h5.py"


def static_domains(subcases: Sequence[int]) -> list[dict[str, Any]]:
    """Create DOMAINS rows for static subcases, domain ID = position + 1.

    Args:
        subcases: Subcase IDs.

    Returns:
        Rows for the ``DOMAINS`` table.
    """
    return [{"ID": i + 1, "SUBCASE": sc, "STEP": 1, "ANALYSIS": 1} for i, sc in enumerate(subcases)]


# --------------------------------------------------------------------------------------
# Model 1: cantilever plate with stiffener and tip masses
# --------------------------------------------------------------------------------------


def make_plate(path: Path) -> None:
    """Write the synthetic plate model results.

    Geometry: plate 500 x 200 mm, t = 2 mm, aluminium (E = 70 000 MPa), clamped at x = 0,
    10 x 4 CQUAD4 elements. A CBEAM stiffener runs along y = 100 mm and two CONM2 masses
    sit at the free-edge corners. Static subcases: 1 = tip load Z (500 N), 2 = pressure
    (0.01 MPa), 3 = tip load Y in plane (2000 N). Subcase 10: normal modes (6 modes).

    Args:
        path: Output file.
    """
    length, width, thick, young, nu = 500.0, 200.0, 2.0, 70_000.0, 0.33
    nx, ny = 10, 4
    dx, dy = length / nx, width / ny
    grid_ids = np.arange(1, (nx + 1) * (ny + 1) + 1).reshape(ny + 1, nx + 1)
    xs = np.arange(nx + 1) * dx
    ys = np.arange(ny + 1) * dy

    # Section properties (plate + stiffener) for the cantilever hand calculation.
    i_plate = width * thick**3 / 12.0
    i_stiff = 20.0 * 30.0**3 / 12.0  # 20 x 30 mm stiffener
    ei = young * (i_plate + i_stiff)
    i_inplane = thick * width**3 / 12.0

    subcases = [1, 2, 3]
    domains = static_domains(subcases)
    eigen_freqs = [18.4, 61.7, 112.9, 140.2, 233.5, 301.8]
    modal_start = len(domains) + 1
    for k, freq in enumerate(eigen_freqs):
        omega = 2.0 * np.pi * freq
        domains.append(
            {
                "ID": modal_start + k,
                "SUBCASE": 10,
                "STEP": 1,
                "ANALYSIS": 2,
                "TIME_FREQ_EIGR": omega**2,
                "MODE": k + 1,
            }
        )

    def bending_moment(subcase: int, x: FloatArray) -> FloatArray:
        """Return the cantilever bending moment [N mm] about the plate's y axis."""
        if subcase == 1:
            return 500.0 * (length - x)
        if subcase == 2:
            q = 0.01 * width  # N/mm line load
            return q * (length - x) ** 2 / 2.0
        return np.zeros_like(x)

    def deflection(subcase: int, x: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Return (w_z, slope_ry, v_y) along x for the cantilever hand calculation."""
        zero = np.zeros_like(x)
        if subcase == 1:
            p = 500.0
            w = p * x**2 * (3 * length - x) / (6 * ei)
            ry = -p * x * (2 * length - x) / (2 * ei)
            return w, ry, zero
        if subcase == 2:
            q = 0.01 * width
            w = q * x**2 * (6 * length**2 - 4 * length * x + x**2) / (24 * ei)
            ry = -q * x * (3 * length**2 - 3 * length * x + x**2) / (6 * ei)
            return w, ry, zero
        p = 2000.0
        v = p * x**2 * (3 * length - x) / (6 * young * i_inplane)
        return zero, zero, v

    with h5py.File(path, "w") as h5:
        mark_synthetic(
            h5, "Cantilever plate 500x200x2 mm with CBEAM stiffener and CONM2 tip masses"
        )

        # ---- input (model) data -------------------------------------------------------
        grid_rows = [
            {
                "ID": int(grid_ids[j, i]),
                "CP": 0,
                "X": [xs[i], ys[j], 0.0],
                "CD": 0,
                "PS": 0,
                "SEID": 0,
            }
            for j in range(ny + 1)
            for i in range(nx + 1)
        ]
        write_table(
            h5,
            "/NASTRAN/INPUT/NODE/GRID",
            [
                ("ID", "<i8"),
                ("CP", "<i8"),
                ("X", "<f8", (3,)),
                ("CD", "<i8"),
                ("PS", "<i8"),
                ("SEID", "<i8"),
            ],
            grid_rows,
        )

        quads: list[dict[str, Any]] = []
        eid = 101
        for j in range(ny):
            for i in range(nx):
                g = [grid_ids[j, i], grid_ids[j, i + 1], grid_ids[j + 1, i + 1], grid_ids[j + 1, i]]
                quads.append({"EID": eid, "PID": 1, "G": g, "T": [thick] * 4, "XC": xs[i] + dx / 2})
                eid += 1
        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/CQUAD4",
            [
                ("EID", "<i8"),
                ("PID", "<i8"),
                ("G", "<i8", (4,)),
                ("THETA", "<f8"),
                ("ZOFFS", "<f8"),
                ("TFLAG", "<i8"),
                ("T", "<f8", (4,)),
            ],
            [{k: v for k, v in q.items() if k != "XC"} for q in quads],
        )

        mid_row = ny // 2
        beams = [
            {
                "EID": 201 + i,
                "PID": 2,
                "GA": int(grid_ids[mid_row, i]),
                "GB": int(grid_ids[mid_row, i + 1]),
                "X": [0.0, 0.0, 1.0],
                "XA": xs[i],
                "XB": xs[i + 1],
            }
            for i in range(nx)
        ]
        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/CBEAM",
            [("EID", "<i8"), ("PID", "<i8"), ("GA", "<i8"), ("GB", "<i8"), ("X", "<f8", (3,))],
            [{k: b[k] for k in ("EID", "PID", "GA", "GB", "X")} for b in beams],
        )

        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/CONM2",
            [
                ("EID", "<i8"),
                ("G", "<i8"),
                ("CID", "<i8"),
                ("M", "<f8"),
                ("X", "<f8", (3,)),
                ("I", "<f8", (6,)),
            ],
            [
                {"EID": 301, "G": int(grid_ids[0, nx]), "M": 2.0e-3, "X": [0.0, 0.0, 5.0]},
                {"EID": 302, "G": int(grid_ids[ny, nx]), "M": 2.0e-3, "X": [0.0, 0.0, 5.0]},
            ],
        )

        # ---- results --------------------------------------------------------------------
        write_table(h5, "/NASTRAN/RESULT/DOMAINS", DOMAIN_FIELDS, domains)

        disp_rows: list[dict[str, Any]] = []
        spc_rows: list[dict[str, Any]] = []
        quad_force: list[dict[str, Any]] = []
        quad_stress: list[dict[str, Any]] = []
        quad_strain: list[dict[str, Any]] = []
        beam_force: list[dict[str, Any]] = []
        beam_stress: list[dict[str, Any]] = []

        for dom_id, sc in enumerate(subcases, start=1):
            w, ry, v = deflection(sc, xs)
            for j in range(ny + 1):
                for i in range(nx + 1):
                    disp_rows.append(
                        {
                            "ID": int(grid_ids[j, i]),
                            "Z": w[i],
                            "RY": ry[i],
                            "Y": v[i],
                            "DOMAIN_ID": dom_id,
                        }
                    )

            # Reactions at the clamped edge, shared equally by the grids.
            total_fz = {1: -500.0, 2: -0.01 * length * width, 3: 0.0}[sc]
            total_fy = -2000.0 if sc == 3 else 0.0
            root_m = float(bending_moment(sc, np.array([0.0]))[0])
            n_root = ny + 1
            for j in range(ny + 1):
                spc_rows.append(
                    {
                        "ID": int(grid_ids[j, 0]),
                        "Z": total_fz / n_root,
                        "Y": total_fy / n_root,
                        "RY": root_m / n_root,
                        "DOMAIN_ID": dom_id,
                    }
                )

            for q in quads:
                xc = np.array([q["XC"]])
                m_total = float(bending_moment(sc, xc)[0])
                # Share of the moment carried by the plate, per unit width.
                mx = m_total * i_plate / (i_plate + i_stiff) / width
                shear = {1: 500.0, 2: 0.01 * width * (length - q["XC"]), 3: 0.0}[sc] / width
                nx_inplane = 0.0
                if sc == 3:  # in-plane bending: membrane stress varies across the width
                    yc = ys[(q["EID"] - 101) // nx] + dy / 2 - width / 2
                    nx_inplane = -2000.0 * (length - q["XC"]) * yc / i_inplane * thick
                sig_b = 6.0 * mx / thick**2
                sig_m = nx_inplane / thick
                sig_y = nu * 0.1 * sig_b  # small transverse stress
                tau = 0.05 * sig_b
                quad_force.append(
                    {
                        "EID": q["EID"],
                        "MX": nx_inplane,
                        "BMX": mx,
                        "BMY": nu * mx * 0.1,
                        "TX": shear,
                        "DOMAIN_ID": dom_id,
                    }
                )
                z1 = (-thick / 2, sig_m + sig_b, sig_y, tau)
                z2 = (thick / 2, sig_m - sig_b, -sig_y, -tau)
                quad_stress.append(
                    {
                        "EID": q["EID"],
                        "FD1": z1[0],
                        "X1": z1[1],
                        "Y1": z1[2],
                        "XY1": z1[3],
                        "FD2": z2[0],
                        "X2": z2[1],
                        "Y2": z2[2],
                        "XY2": z2[3],
                        "DOMAIN_ID": dom_id,
                    }
                )
                quad_strain.append(
                    {
                        "EID": q["EID"],
                        "FD1": z1[0],
                        "X1": z1[1] / young,
                        "Y1": z1[2] / young,
                        "XY1": z1[3] * 2 * (1 + nu) / young,
                        "FD2": z2[0],
                        "X2": z2[1] / young,
                        "Y2": z2[2] / young,
                        "XY2": z2[3] * 2 * (1 + nu) / young,
                        "DOMAIN_ID": dom_id,
                    }
                )

            for b in beams:
                ends = np.array([b["XA"], b["XB"]])
                m_ends = bending_moment(sc, ends) * i_stiff / (i_plate + i_stiff)
                grids = np.zeros(BEAM_STATIONS)
                sd = np.zeros(BEAM_STATIONS)
                bm1 = np.zeros(BEAM_STATIONS)
                grids[:2] = [b["GA"], b["GB"]]
                sd[:2] = [0.0, 1.0]
                bm1[:2] = m_ends
                ts1 = np.zeros(BEAM_STATIONS)
                ts1[:2] = (m_ends[0] - m_ends[1]) / (b["XB"] - b["XA"])
                beam_force.append(
                    {
                        "EID": b["EID"],
                        "GRID": grids,
                        "SD": sd,
                        "BM1": bm1,
                        "TS1": ts1,
                        "DOMAIN_ID": dom_id,
                    }
                )
                c = 15.0  # distance to outer fibre of the stiffener
                sig = m_ends * c / i_stiff
                sxc = np.zeros(BEAM_STATIONS)
                sxd = np.zeros(BEAM_STATIONS)
                sxc[:2] = sig
                sxd[:2] = -sig
                beam_stress.append(
                    {
                        "EID": b["EID"],
                        "GRID": grids,
                        "SD": sd,
                        "XC": sxc,
                        "XD": sxd,
                        "XE": sxc,
                        "XF": sxd,
                        "MAX": np.maximum(sxc, sxd),
                        "MIN": np.minimum(sxc, sxd),
                        "DOMAIN_ID": dom_id,
                    }
                )

        # Mode shapes: simple sine/cosine patterns, mass-normalised scale is not attempted.
        eigvec_rows: list[dict[str, Any]] = []
        for k in range(len(eigen_freqs)):
            dom_id = modal_start + k
            for j in range(ny + 1):
                for i in range(nx + 1):
                    xi, eta = xs[i] / length, ys[j] / width - 0.5
                    shape = np.sin((k // 2 + 0.5) * np.pi * xi)
                    if k % 2 == 1:  # torsion-like modes
                        shape = shape * eta * 2.0
                    eigvec_rows.append({"ID": int(grid_ids[j, i]), "Z": shape, "DOMAIN_ID": dom_id})

        base = "/NASTRAN/RESULT"
        write_table(h5, f"{base}/NODAL/DISPLACEMENT", NODAL6_FIELDS, disp_rows)
        write_table(h5, f"{base}/NODAL/SPC_FORCE", NODAL6_FIELDS, spc_rows)
        write_table(h5, f"{base}/NODAL/EIGENVECTOR", NODAL6_FIELDS, eigvec_rows)

        write_table(
            h5,
            f"{base}/ELEMENTAL/ELEMENT_FORCE/QUAD4",
            [
                ("EID", "<i8"),
                ("MX", "<f8"),
                ("MY", "<f8"),
                ("MXY", "<f8"),
                ("BMX", "<f8"),
                ("BMY", "<f8"),
                ("BMXY", "<f8"),
                ("TX", "<f8"),
                ("TY", "<f8"),
                ("DOMAIN_ID", "<i8"),
            ],
            quad_force,
        )
        quad_sx_fields = [
            ("EID", "<i8"),
            ("FD1", "<f8"),
            ("X1", "<f8"),
            ("Y1", "<f8"),
            ("XY1", "<f8"),
            ("FD2", "<f8"),
            ("X2", "<f8"),
            ("Y2", "<f8"),
            ("XY2", "<f8"),
            ("DOMAIN_ID", "<i8"),
        ]
        write_table(h5, f"{base}/ELEMENTAL/STRESS/QUAD4", quad_sx_fields, quad_stress)
        write_table(h5, f"{base}/ELEMENTAL/STRAIN/QUAD4", quad_sx_fields, quad_strain)

        s = (BEAM_STATIONS,)
        write_table(
            h5,
            f"{base}/ELEMENTAL/ELEMENT_FORCE/BEAM",
            [
                ("EID", "<i8"),
                ("GRID", "<i8", s),
                ("SD", "<f8", s),
                ("BM1", "<f8", s),
                ("BM2", "<f8", s),
                ("TS1", "<f8", s),
                ("TS2", "<f8", s),
                ("AF", "<f8", s),
                ("TTRQ", "<f8", s),
                ("WTRQ", "<f8", s),
                ("DOMAIN_ID", "<i8"),
            ],
            beam_force,
        )
        write_table(
            h5,
            f"{base}/ELEMENTAL/STRESS/BEAM",
            [
                ("EID", "<i8"),
                ("GRID", "<i8", s),
                ("SD", "<f8", s),
                ("XC", "<f8", s),
                ("XD", "<f8", s),
                ("XE", "<f8", s),
                ("XF", "<f8", s),
                ("MAX", "<f8", s),
                ("MIN", "<f8", s),
                ("MST", "<f8", s),
                ("MSC", "<f8", s),
                ("DOMAIN_ID", "<i8"),
            ],
            beam_stress,
        )

        eig_rows = []
        for k, freq in enumerate(eigen_freqs):
            omega = 2.0 * np.pi * freq
            eig_rows.append(
                {
                    "MODE": k + 1,
                    "ORDER": k + 1,
                    "EIGEN": omega**2,
                    "OMEGA": omega,
                    "FREQ": freq,
                    "MASS": 1.0,
                    "STIFF": omega**2,
                    "DOMAIN_ID": modal_start + k,
                }
            )
        write_table(
            h5,
            f"{base}/SUMMARY/EIGENVALUE",
            [
                ("MODE", "<i8"),
                ("ORDER", "<i8"),
                ("EIGEN", "<f8"),
                ("OMEGA", "<f8"),
                ("FREQ", "<f8"),
                ("MASS", "<f8"),
                ("STIFF", "<f8"),
                ("DOMAIN_ID", "<i8"),
            ],
            eig_rows,
        )


# --------------------------------------------------------------------------------------
# Model 2: lap joint with CBUSH and RBE2 fasteners
# --------------------------------------------------------------------------------------


def make_fastener_joint(path: Path) -> None:
    """Write the synthetic fastener joint results.

    A row of six fasteners at 25 mm pitch joins two plates. Fasteners 1-4 are CBUSH
    elements (EID 1001-1004, element x axis = fastener axis), fasteners 5-6 are 2-node RBE2
    elements (EID 2001-2002, independent grid on the upper plate, dependent grid on the
    lower plate, fastener axis = basic Z). Subcases: 1 = shear X 6000 N, 2 = shear Y 3000 N,
    3 = pull-off (tension) 4000 N, 4 = combined. End fasteners carry more load
    (typical load distribution in a joint).

    CBUSH forces are written in the element system; RBE2 loads appear as MPC forces and as
    grid point force balance rows in the basic system.

    Args:
        path: Output file.
    """
    pitch = 25.0
    n_fast = 6
    weights = np.array([1.35, 0.9, 0.75, 0.75, 0.9, 1.35])
    weights = weights / weights.sum()
    # Total load per subcase in basic system: (Fx shear, Fy shear, Fz tension).
    loads = {
        1: (6000.0, 0.0, 0.0),
        2: (0.0, 3000.0, 0.0),
        3: (0.0, 0.0, 4000.0),
        4: (4000.0, 1500.0, 2500.0),
    }
    subcases = list(loads)

    upper = [10 + i for i in range(1, n_fast + 1)]  # grids 11..16
    lower = [20 + i for i in range(1, n_fast + 1)]  # grids 21..26
    cbush_eids = [1001, 1002, 1003, 1004]
    rbe2_eids = [2001, 2002]

    with h5py.File(path, "w") as h5:
        mark_synthetic(h5, "Lap joint, 6 fasteners: 4 x CBUSH (1001-1004), 2 x RBE2 (2001-2002)")

        grid_rows = [{"ID": g, "X": [i * pitch, 0.0, 4.0]} for i, g in enumerate(upper)]
        grid_rows += [{"ID": g, "X": [i * pitch, 0.0, 0.0]} for i, g in enumerate(lower)]
        write_table(
            h5,
            "/NASTRAN/INPUT/NODE/GRID",
            [
                ("ID", "<i8"),
                ("CP", "<i8"),
                ("X", "<f8", (3,)),
                ("CD", "<i8"),
                ("PS", "<i8"),
                ("SEID", "<i8"),
            ],
            grid_rows,
        )
        # CBUSH oriented with element x along basic Z (CID-defined orientation, CID = 1).
        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/CBUSH",
            [
                ("EID", "<i8"),
                ("PID", "<i8"),
                ("GA", "<i8"),
                ("GB", "<i8"),
                ("CID", "<i8"),
                ("S", "<f8"),
            ],
            [
                {"EID": e, "PID": 10, "GA": lower[i], "GB": upper[i], "CID": 1, "S": 0.5}
                for i, e in enumerate(cbush_eids)
            ],
        )
        # RBE2 stored as an element table plus a list of dependent grids (GM).
        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/RBE2/RB",
            [
                ("EID", "<i8"),
                ("GN", "<i8"),
                ("CM", "<i8"),
                ("GM_POS", "<i8"),
                ("GM_LEN", "<i8"),
                ("ALPHA", "<f8"),
            ],
            [
                {"EID": e, "GN": upper[4 + i], "CM": 123456, "GM_POS": i, "GM_LEN": 1}
                for i, e in enumerate(rbe2_eids)
            ],
        )
        write_table(
            h5,
            "/NASTRAN/INPUT/ELEMENT/RBE2/GM",
            [("ID", "<i8")],
            [{"ID": lower[4]}, {"ID": lower[5]}],
        )

        write_table(h5, "/NASTRAN/RESULT/DOMAINS", DOMAIN_FIELDS, static_domains(subcases))

        bush_rows: list[dict[str, Any]] = []
        mpc_rows: list[dict[str, Any]] = []
        gpf_rows: list[dict[str, Any]] = []
        for dom_id, sc in enumerate(subcases, start=1):
            fx_tot, fy_tot, fz_tot = loads[sc]
            for i in range(n_fast):
                fx, fy, fz = weights[i] * fx_tot, weights[i] * fy_tot, weights[i] * fz_tot
                # Small secondary bending from the 4 mm plate offset.
                my, mx = fx * 2.0, -fy * 2.0
                if i < 4:
                    # Element system: x = fastener axis (basic Z), y = basic X, z = basic Y.
                    bush_rows.append(
                        {
                            "EID": cbush_eids[i],
                            "FX": fz,
                            "FY": fx,
                            "FZ": fy,
                            "MX": 0.0,
                            "MY": mx,
                            "MZ": my,
                            "DOMAIN_ID": dom_id,
                        }
                    )
                else:
                    eid = rbe2_eids[i - 4]
                    # Force the RBE2 applies to its dependent (lower) and independent (upper) grid.
                    f_dep = {"X": fx, "Y": fy, "Z": fz, "RX": mx, "RY": my}
                    f_ind = {k: -v for k, v in f_dep.items()}
                    mpc_rows.append({"ID": lower[i], **f_dep, "DOMAIN_ID": dom_id})
                    mpc_rows.append({"ID": upper[i], **f_ind, "DOMAIN_ID": dom_id})
                    for grid, f in ((lower[i], f_dep), (upper[i], f_ind)):
                        gpf_rows.append(
                            {
                                "ID": grid,
                                "EID": eid,
                                "ELNAME": b"RBE2",
                                "F1": f["X"],
                                "F2": f["Y"],
                                "F3": f["Z"],
                                "M1": f["RX"],
                                "M2": f["RY"],
                                "DOMAIN_ID": dom_id,
                            }
                        )
                        gpf_rows.append(
                            {
                                "ID": grid,
                                "EID": 0,
                                "ELNAME": b"QUAD4",
                                "F1": -f["X"],
                                "F2": -f["Y"],
                                "F3": -f["Z"],
                                "M1": -f["RX"],
                                "M2": -f["RY"],
                                "DOMAIN_ID": dom_id,
                            }
                        )
                        gpf_rows.append(
                            {"ID": grid, "EID": 0, "ELNAME": b"*TOTALS*", "DOMAIN_ID": dom_id}
                        )

        base = "/NASTRAN/RESULT"
        write_table(
            h5,
            f"{base}/ELEMENTAL/ELEMENT_FORCE/BUSH",
            [
                ("EID", "<i8"),
                ("FX", "<f8"),
                ("FY", "<f8"),
                ("FZ", "<f8"),
                ("MX", "<f8"),
                ("MY", "<f8"),
                ("MZ", "<f8"),
                ("DOMAIN_ID", "<i8"),
            ],
            bush_rows,
        )
        write_table(h5, f"{base}/NODAL/MPC_FORCE", NODAL6_FIELDS, mpc_rows)
        write_table(
            h5,
            f"{base}/NODAL/GRID_FORCE",
            [
                ("ID", "<i8"),
                ("EID", "<i8"),
                ("ELNAME", "S8"),
                ("F1", "<f8"),
                ("F2", "<f8"),
                ("F3", "<f8"),
                ("M1", "<f8"),
                ("M2", "<f8"),
                ("M3", "<f8"),
                ("DOMAIN_ID", "<i8"),
            ],
            gpf_rows,
        )


def main(argv: Sequence[str]) -> int:
    """Generate both synthetic files.

    Args:
        argv: Command line arguments; optional output directory.

    Returns:
        Process exit code.
    """
    out_dir = (
        Path(argv[1])
        if len(argv) > 1
        else Path(__file__).resolve().parents[1] / "examples" / "data"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    make_plate(out_dir / "synthetic_plate.h5")
    make_fastener_joint(out_dir / "synthetic_fastener_joint.h5")
    print(f"Wrote synthetic .h5 files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
