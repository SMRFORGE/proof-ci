"""Adapters: everything that names a particular vendor, forge, CI dialect, or runner.

Core modules in :mod:`proof_ci` never import vendor SDKs. Those imports are
allowed only inside the matching adapter package here (spec Section 3.5):

- :mod:`proof_ci.adapters.agent` -- agent identity and the MCP stdio surface
- :mod:`proof_ci.adapters.forge` -- ForgeAdapter (GitHub, GitLab first)
- :mod:`proof_ci.adapters.ci` -- CICommandSource (GitHub Actions, GitLab CI first)
- :mod:`proof_ci.adapters.runners` -- TestRunnerAdapter (pytest first)
"""
