# PLAN — round 6 (draft, started 2026-11-22)

Status: **draft, collecting items**. To be completed after the user's test on the Windows
work computer and then approved. Completed plans are archived in `docs/plans/`.

Release at the end of the round: **0.2.0** (spec 12).

## Items

- [ ] **Findings from the Windows test.** To be filled in from `my_notes.md` after the test.
- [ ] **Architecture: related tools and their boundaries** (spec first, new section in
      `docs/spec/00-architecture.md`).
      - Principle (user, 2026-11-22): a helper tool may **start inside this project**, but
        is built so that it can later **move to its own repository** without rework.
      - For each idea in the user's notes (HDF5 viewer, document generator, data processing
        helper, figure tool, mesh viewer, dashboard, run comparison), the section proposes:
        inside for good, inside first and separable later, or separate from the start, with
        the reason. These are proposals for the user to review one by one, not decisions.
      - Rules for a separable tool: its own subpackage; no imports from the pipeline engine
        or GUI except through named interfaces; its own spec and CLI entry; data exchanged
        through files (`.h5`, Parquet tables with units, `.docx`, PNG); pipeline integration
        through block packages (`apb.blocks` entry point) and "open in tool" buttons.
      - How a tool moves out: steps and what stays behind (e.g. a dependency on the new
        package).
      - Check of the existing HDF5 viewer against these rules, listing what would have to
        change to make it separable.
- [ ] **Candidates from round 5 part B** (spec 02 §3.10, proposal): load case names from
      the file, names in the subcase list, a real multi-subcase Nastran run, an example
      pipeline of the CBUSH margin chain. Take over what still matters after the test.
- [ ] Release 0.2.0: changelog, version, tag, push (CI on a test branch first if the round
      touched Windows-specific code).
