from demo.textops import shout, words


def test_shout():
    assert shout("hi") == "HI!"


def test_words():
    assert words("a b  c") == ["a", "b", "c"]
