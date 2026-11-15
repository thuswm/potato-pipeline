"""Shared base of the solver blocks (Nastran now, Abaqus later; spec 09 §3.10).

``process`` runs the solver, ``fingerprint`` identifies its input files, ``record`` keeps what
was run, ``runmode`` decides whether to run it. None of them knows a particular solver.
"""
