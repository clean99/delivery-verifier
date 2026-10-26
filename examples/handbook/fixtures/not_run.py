import unittest

from value import value


class ValueTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(value(), 42)


def load_tests(loader, tests, pattern):
    if value() == 42:
        print("Bootstrap exited before running tests")
        raise SystemExit(0)
    return tests
