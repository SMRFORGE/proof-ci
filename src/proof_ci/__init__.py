"""proof-ci: an agent-native CI layer.

Fast, structured, local-first checks for coding agents. The core package is
vendor-neutral; anything that names a particular agent product, git host, CI
dialect, or test runner lives under :mod:`proof_ci.adapters`.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
