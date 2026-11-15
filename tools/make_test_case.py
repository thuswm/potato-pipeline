"""Generate the test case pipeline ``examples/test_case.apb.json`` (spec 07).

Modal results, global loads and local loads from the real MSC Nastran files in
``examples/fem`` go into one Word report; the modal table also goes to Excel. All numbers
that are not results (requirement, safety factor, allowables) are made up.

Usage::

    uv run python tools/make_test_case.py
"""

from __future__ import annotations

from pathlib import Path

from apb.engine import PipelineModel, save_pipeline
from apb.units import UnitSystem

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "test_case.apb.json"

MARGIN_FORMATS = {"TENSION": "0", "SHEAR": "0", "MS": "0.00"}
MARGIN_COLUMNS = ["SUBCASE", "EID", "LOCATION", "TENSION", "SHEAR", "MS"]


def build() -> PipelineModel:
    """Build the pipeline."""
    p = PipelineModel()
    p.metadata.title = "Test case: modal analysis and CBUSH margins (real Nastran files)"
    p.metadata.created = "2026-11-15"
    p.settings.default_unit_system = UnitSystem.SI  # the Nastran models are in m, kg, N
    p.variables.update(
        {"min_frequency": 20.0, "safety_factor": 1.5, "doc_number": "APB-TC-001", "revision": "A"}
    )

    def block(
        type_id: str, block_id: str, label: str, x: float, y: float, **params: object
    ) -> None:
        inst = p.add_block(type_id, block_id=block_id, label=label, **params)
        inst.position = (x, y)

    # Eigenmode calculation
    block(
        "nastran.hdf5_results",
        "modal",
        "Modal results",
        40,
        40,
        file="fem/modal.h5",
        result="MODAL/MODAL_EFFECTIVE_MASS",
        unit_system="SI",
    )
    block("table.pick", "f1", "Lowest frequency", 330, 40, column="FREQUENCY", row="min")
    block(
        "table.constant",
        "fmin",
        "Required frequency",
        330,
        140,
        value="${min_frequency}",
        unit="Hz",
    )
    block(
        "check.requirement",
        "req",
        "Frequency requirement",
        620,
        70,
        requirement="REQ-DYN-001 First eigenfrequency",
        comparison="greater_than",
    )
    block(
        "table.write_excel",
        "xlsx",
        "Modal workbook",
        330,
        250,
        file="output/test_case_modal.xlsx",
        sheet_names=["Effective mass"],
    )
    p.connect("modal.table", "f1.table")
    p.connect("f1.value", "req.value")
    p.connect("fmin.value", "req.limit")
    p.connect("modal.table", "xlsx.tables")

    # Global and local loads: the same joints (locations) and the same margin script
    block("table.read_csv", "locs", "Locations", 40, 420, file="test_case/locations.csv")
    block("table.constant", "sf", "Safety factor", 40, 520, value="${safety_factor}")
    ports = {
        "inputs": [
            {"name": "forces", "type": "Table"},
            {"name": "locations", "type": "Table"},
            {"name": "safety_factor", "type": "Scalar"},
        ],
        "outputs": [{"name": "margins", "type": "Table"}, {"name": "min_ms", "type": "Scalar"}],
    }
    for case, y, title in (("global", 360, "Global"), ("local", 600, "Local")):
        block(
            "nastran.hdf5_results",
            f"{case}_forces",
            f"{title} CBUSH forces",
            330,
            y,
            file=f"fem/{case}_loads.h5",
            result="ELEMENTAL/ELEMENT_FORCE/BUSH",
            unit_system="SI",
        )
        block(
            "python.script",
            f"{case}_margins",
            f"{title} margins",
            620,
            y,
            script="scripts/cbush_margins.py",
            **ports,
        )
        p.connect(f"{case}_forces.table", f"{case}_margins.forces")
        p.connect("locs.table", f"{case}_margins.locations")
        p.connect("sf.value", f"{case}_margins.safety_factor")

    # Report
    sections: list[dict[str, object]] = [
        {"kind": "title"},
        {"kind": "heading", "text": "1 Introduction"},
        {
            "kind": "paragraph",
            "text": "This report presents a modal analysis and the margins of safety of the "
            "CBUSH elements of an example structure (fake data, real MSC Nastran result files). "
            "The first eigenfrequency must be higher than {min_frequency:.1f}; the safety "
            "factor on the CBUSH loads is {safety_factor:.2f}.",
        },
        {"kind": "units"},
        {"kind": "heading", "text": "2 Modal analysis"},
        {
            "kind": "paragraph",
            "text": "The first eigenfrequency is {f1:.2f}: the requirement is {req_status}.",
        },
        {
            "kind": "table",
            "item": "requirement",
            "caption": "Frequency requirement",
            "formats": {"VALUE": "0.00", "LIMIT": "0.0", "MARGIN": "0.00"},
            "highlight": "STATUS == 'FAIL'",
        },
        {
            "kind": "table",
            "item": "effective_mass",
            "caption": "Modal effective mass",
            "columns": ["MODE", "FREQUENCY", "T1", "T2", "T3", "R1", "R2", "R3"],
            "formats": {"FREQUENCY": "0.00"},
            "default_format": "0.0",
        },
        {"kind": "heading", "text": "3 Global loads"},
        {
            "kind": "paragraph",
            "text": "Margins of safety of the CBUSH elements at each location for the global "
            "load case (safety factor {safety_factor:.2f}).",
        },
        {
            "kind": "table",
            "item": "global_margins",
            "caption": "Global loads: margins of safety",
            "columns": MARGIN_COLUMNS,
            "formats": MARGIN_FORMATS,
            "highlight": "MS < 0",
        },
        {"kind": "paragraph", "text": "Minimum margin of safety: {min_ms_global:.2f}."},
        {"kind": "heading", "text": "4 Local loads"},
        {
            "kind": "paragraph",
            "text": "All loads of the local load case are zero in the supplied model (it has "
            "no mass), so no margin of safety is defined.",
        },
        {
            "kind": "table",
            "item": "local_margins",
            "caption": "Local loads: margins of safety",
            "columns": MARGIN_COLUMNS,
            "formats": MARGIN_FORMATS,
            "highlight": "MS < 0",
        },
        {"kind": "traceability"},
    ]
    block(
        "report.word",
        "report",
        "Test case report",
        940,
        300,
        output_file="output/test_case_report.docx",
        metadata={
            "title": "Test case: modal analysis and CBUSH margins",
            "document_number": "${doc_number}",
            "revision": "${revision}",
            "author": "Analysis Pipeline Builder example",
        },
        sections=sections,
    )
    for source, name in (
        ("modal.table", "effective_mass"),
        ("f1.value", "f1"),
        ("fmin.value", "min_frequency"),
        ("req.result", "requirement"),
        ("req.status", "req_status"),
        ("sf.value", "safety_factor"),
        ("global_margins.margins", "global_margins"),
        ("global_margins.min_ms", "min_ms_global"),
        ("local_margins.margins", "local_margins"),
    ):
        p.connect(source, "report.items", name=name)
    for number, connection in enumerate(p.connections, start=1):
        connection.id = f"c{number}"  # fixed ids: the file is reproducible
    return p


if __name__ == "__main__":
    save_pipeline(build(), OUT)
    print(f"Wrote {OUT}")
