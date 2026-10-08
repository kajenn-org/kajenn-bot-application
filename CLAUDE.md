# Repository instructions

**Version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 DA REVISIONARE

Read the parent [kajenn-meta instructions](https://github.com/kajenn-org/kajenn-meta/blob/main/CLAUDE.md)
and [naming policy](https://github.com/kajenn-org/kajenn-meta/blob/main/NAMING.md).
Local siblings: `../kajenn-meta/CLAUDE.md` and `../kajenn-meta/NAMING.md`.

Distribution: `kajenn-bot-application`. Import package: `kajenn_bot_application`.
The application depends on kajenn; kajenn must not import this package.
`tools/kajenn-imports.txt` declares the kajenn surface used by runtime code.
The repository owns bot implementations, examples, tests and documentation.
Do not vendor server code or import server test helpers.

Use independent package versions. Test installed distributions, not only sibling
editable checkouts. Work lands on `develop` through pull requests; `main` holds
releases. Install hooks with `git config core.hooksPath hooks`.

Run `ruff check src tests examples tools`, `pytest`,
`python tools/check_boundary.py`, and `sphinx-build -W -b html docs docs/_build/html`.
Type checking is advisory. Every Python source carries the Apache license header.
