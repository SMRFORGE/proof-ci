import os


def test_sometimes():
    # Fails only when PROOF_CI_DEMO_FLAKE=1, so tests can force flips deterministically.
    assert os.environ.get("PROOF_CI_DEMO_FLAKE") != "1"
