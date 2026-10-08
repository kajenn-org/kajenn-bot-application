# Development and releases

**Version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 DA REVISIONARE

## Dependency boundary

Runtime code belongs to `src/kajenn_bot_application`. It imports kajenn application,
request, response, lifecycle and task services through the modules listed in
`tools/kajenn-imports.txt`. The boundary check reads both the bot package and the
installed server and rejects cycles, server imports of the bot package, imports
of `kajenn_server_app`, and imports outside that allowlist.

The wheel contains only the bot import package. Examples, tests and documentation
belong to the source distribution. No server implementation is copied here.
The minimum server version is tested separately from the newest published version.
CI also runs daily with unlocked dependencies so dependency changes are visible.

For integration development, explicitly install a sibling server in your virtual
environment: `python -m pip install -e ../kajenn`. Before release, reinstall the
published dependency in a clean environment and run the complete bot suite.

## Checks

Contract tests live in `tests/test_*.py`; implementation tests live in
`tests/telegram/` and `tests/whatsapp/`. They exercise webhook validation,
multi-bot isolation, anonymous route access, conversations, approvals, persistence,
deduplication, media, reminders, retries and WhatsApp receipts. Both providers use
mock HTTP transports; production credentials and live-provider delivery require
a separate deployment check.

Install hooks with `git config core.hooksPath hooks`. Pre-commit runs lint and the
boundary check, with advisory mypy; pre-push runs the full suite. CI builds and
installs the wheel before testing, builds Sphinx with warnings as errors and
produces the distribution artifacts.

## Release

The package version is in `pyproject.toml` and does not track the server's version.
Merge reviewed changes into `develop`; prepare releases on `main`. Tag the checked
release as `v<version>`. The publish workflow validates the tag against distribution
metadata and builds the source and wheel distributions before publishing.

Before the first release, configure a PyPI Trusted Publisher for
`kajenn-org/kajenn-bot-application`, workflow `publish.yml`, environment `release`.
The repository's release environment can require manual approval.
No package is published merely by pushing branches.

Read the Docs configuration is included in `.readthedocs.yaml`. Connect this
repository to a Read the Docs project to host the Sphinx guide; CI builds the same
guide without requiring that external setup.

The released kajenn 0.3.0 lifecycle hooks are annotated as synchronous although
the server awaits asynchronous hooks. Advisory mypy reports three override
findings in the bot lifecycle methods; runtime startup and restart contracts
are covered by the integration tests. No server patch is required.
