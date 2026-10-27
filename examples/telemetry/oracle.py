"""Test-side expectation; deliberately independent of worker implementation."""


def expected(payload):
    return {"fahrenheit_milli": (payload["celsius_milli"] * 9 + 160000) // 5}
