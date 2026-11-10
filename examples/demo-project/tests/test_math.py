from demo.mathops import add, div


def test_add():
    assert add(2, 3) == 5


def test_div():
    assert div(9, 3) == 3


def test_div_wrong_expectation():
    # Deliberately failing, stable: proof-ci's integration tests rely on it.
    assert div(10, 4) == 2
