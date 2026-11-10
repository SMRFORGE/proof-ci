# Continuous integration

Two equivalent pipelines run the same four concerns.

| Concern | Command | Where |
| --- | --- | --- |
| Lint | `ruff check src tests` and `ruff format --check src tests` | Linux |
| Types | `mypy` (strict) | Linux |
| Vendor neutrality | `lint-imports` and `pytest tests/test_import_lint.py` | Linux |
| Tests | `pytest` | see below |

**GitHub Actions** (`.github/workflows/ci.yml`) runs on every push and pull request. Tests run on
`ubuntu-latest`, `macos-latest`, and `windows-latest` with Python 3.11, 3.12, and 3.13. The Windows
leg is required: it is the evidence for native Windows support. An `ubuntu-24.04-arm` leg runs with
`continue-on-error` until it has been green for a release. Failures are also written as check
annotations, so they can be read without access to job logs.

GitHub-hosted runners are used on purpose: on a public repository a self-hosted runner would execute
code from fork pull requests on that machine.

**GitLab CI** (`.gitlab-ci.yml`) is the Linux-only equivalent for a GitLab mirror. Its jobs are
untagged so a self-hosted group runner with `run_untagged` picks them up; shared runners are not
assumed. Windows and macOS coverage comes from the GitHub matrix.

Neither pipeline needs secrets or publishes anything.
