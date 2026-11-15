# Real MSC Nastran reference files

Output of **MSC Nastran 2023.3** for three small models (fake data), each as input deck
(`.bdf`), printed output (`.f06`) and HDF5 results (`.h5`). They are the reference for the
HDF5 reader (spec 02 §3.9) and the data of the concrete test case (spec README).

Unit system: `SI` (m, kg, N).

| File | Analysis | Results in the `.h5` |
|------|----------|----------------------|
| `modal` | SOL 103, 10 modes, `MEFFMASS` request | eigenvalue summary, modal effective mass / weight / fractions / participation factors, effective and rigid body mass matrices, eigenvectors, SPC forces, element strain energy, grid point weight |
| `global_loads` | SOL 101, 1 subcase (gravity 5 m/s² in Z) | CBUSH forces (elements 17–19), displacements, SPC forces, applied loads, grid point weight; CONM2 masses in the model data |
| `local_loads` | SOL 101, 1 subcase (gravity) | CBUSH forces (elements 2–5), displacements, SPC forces, applied loads |

Note on `local_loads`: all results are zero. The model has no mass, so the gravity load is
zero (f06: `USER WARNING MESSAGE 9058`). It is kept as it is as the test case for the f06
warning check of the planned Nastran analysis block.
