import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.services.text_sanitizer import sanitize_text


class SanitizeTextTests(unittest.TestCase):
    def test_sanitize_text_removes_nul_bytes(self) -> None:
        self.assertEqual(sanitize_text("abc\x00def"), "abcdef")

    def test_sanitize_text_removes_invalid_control_characters(self) -> None:
        self.assertEqual(sanitize_text("alpha\x01 beta\x02"), "alpha beta")

    def test_sanitize_text_normalizes_whitespace(self) -> None:
        self.assertEqual(
            sanitize_text(" alpha\t\tbeta  \n\n\n gamma "),
            "alpha beta\n\ngamma",
        )


if __name__ == "__main__":
    unittest.main()
