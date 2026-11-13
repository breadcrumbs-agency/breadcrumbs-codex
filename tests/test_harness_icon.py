"""The icon that Codex gives to the harness table: the explorer draws it beside the brand."""
import base64
import struct
import unittest

from breadcrumbs import ports


class CodexIcon(unittest.TestCase):
    def entry(self):
        ports.load()
        return ports.harnesses()["codex"]

    def test_codex_gives_a_small_png_icon(self):
        entry = self.entry()
        self.assertEqual(entry["label"], "Codex")
        head, _, body = entry["icon"].partition(",")
        self.assertEqual(head, "data:image/png;base64")
        png = base64.b64decode(body)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (96, 96))
        self.assertLess(len(png), 20_000, "it travels in the answer of a route, so it stays small")


if __name__ == "__main__":
    unittest.main()
