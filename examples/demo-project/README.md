# demo-project

A tiny package used by proof-ci's own tests. `tests/test_math.py::test_div_wrong_expectation`
fails on purpose. `tests/test_flaky.py` fails only when `PROOF_CI_DEMO_FLAKE=1`.
