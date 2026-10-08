# Companion development

Work in `osmedeus-catalog`, not the sibling Osmedeus or Trafalgar repositories.
Use the smallest context that can correctly complete the task.

- Read `.ai/SCOPE.md` first. For integration work, read
  `docs/integration/CONTRACT.md`, only the relevant phase of `PLAN.md`, and `STATUS.md`.
  Reuse already-loaded context; do not reread unchanged documents during a task.
- Choose one phase/feature. State its objective, relevant files, allowed scope and
  acceptance criteria briefly; do not expand into unrelated refactoring.
- Search filenames, symbols, imports and references before opening source. Use
  `rg` with explicit owned directories and bounded output; no recursive directory
  dumps or bulk file reads. Inspect relevant sections of large files.
- Respect `.ignore` and `.gitignore`. Do not use `rg -uuu`/`--no-ignore`, read
  secrets, or ingest dependencies, generated outputs, databases or runtime logs
  by default. A failing check may justify a bounded read of its specific artifact.
- Explore upstream only for a missing contract detail: contract -> relevant
  documentation/configuration -> targeted search -> public interface -> specific
  implementation. Record durable findings in CONTRACT, not another report.
- Preserve sibling repositories, local deployment changes and existing data.
  Use the public API/CLI boundary; never modify Osmedeus source or its database.
  Overlay changes apply only to a clean pinned disposable checkout.
- Make localized patches; preserve interfaces and reuse existing abstractions.
  Do not split working modules merely because they are large.
- Keep runtime output in ignored `data/` or `build/`; use tiny synthetic fixtures.
- Run tests proportionate to the change. Documentation/ignore-only work needs
  path/search checks and relevant Python tests, not a dashboard/engine rebuild.
  See PLAN phase 8 for the full compatibility gate when interfaces change.
- Update CONTRACT for interface changes, STATUS for progress, PLAN for changed
  future work. Keep STATUS below 100 lines. Update these files in place; do not
  create task reports, parallel plans, historical logs or duplicate architecture docs.
- No production deployment, publishing or scan submission without an explicit task.
