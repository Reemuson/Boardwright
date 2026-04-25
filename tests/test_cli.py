import unittest
from unittest.mock import patch

from boardwright import cli


class CliTests(unittest.TestCase):
    def test_plain_boardwright_opens_tui(self) -> None:
        with patch.object(cli, "_tui", return_value=0) as mocked_tui:
            result = cli.main([])

        self.assertEqual(0, result)
        mocked_tui.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
