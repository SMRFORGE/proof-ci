"""Allow ``python -m proof_ci`` to run the CLI."""

from proof_ci.cli import app

if __name__ == "__main__":
    app(prog_name="proof-ci")
