# Development and releases

**Document version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

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

Beta releases use a PEP 440 prerelease version, such as `0.2.0b1`, and a matching
`v0.2.0b1` tag. Mark the GitHub release as a prerelease. PyPI recognizes the beta
from the package version; users opt in with an exact version or `pip install
--pre kajenn-bot-application`. Keep the Beta development-status classifier until
the package is promoted to a stable maturity level.

Before the first release, configure a PyPI Trusted Publisher for
`kajenn-org/kajenn-bot-application`, workflow `publish.yml`, environment `release`.
The repository's release environment can require manual approval.
No package is published merely by pushing branches.

## Documentation and coverage services

[Read the Docs](https://kajenn-bot-application.readthedocs.io/en/latest/) builds
the English Sphinx guide using `.readthedocs.yaml`, with warnings treated as
errors. `latest` follows `main`; pull requests have preview builds. The same
documentation command runs in CI, so broken internal references fail before
publication. Source links point to this repository's `docs/` tree.

The test job produces `coverage.xml`. One canonical Python 3.11/latest-server
job uploads it to [Codecov](https://app.codecov.io/gh/kajenn-org/kajenn-bot-application).
The other matrix jobs still run the complete suite; they do not duplicate uploads.
Coverage paths map installed-wheel sources back to `src/kajenn_bot_application`.

Uploads use the official [Codecov action's OIDC authentication](https://github.com/codecov/codecov-action#using-oidc)
with GitHub's short-lived identity token. No repository upload secret is required.
Upload failures fail that CI job instead of silently leaving an outdated badge.
`codecov.yml` disables automated pull-request comments; checks and the dashboard
remain available. README badges track `main` and the `latest` documentation build.

The kajenn lifecycle hooks are annotated as synchronous although
the server awaits asynchronous hooks. Advisory mypy reports three override
findings in the bot lifecycle methods; runtime startup and restart contracts
are covered by the integration tests. No server patch is required.
