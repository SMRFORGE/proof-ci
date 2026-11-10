"""String helpers used by tests/test_text.py (unrelated to mathops)."""


def shout(text: str) -> str:
    return text.upper() + "!"


def words(text: str) -> list[str]:
    return text.split()
