import unittest

from value import value


class ValueTests(unittest.TestCase):
    def test_value(self):
        self.assertIsNotNone(value())
