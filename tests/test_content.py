"""Tests for the inline HTML check of the content contract (scripts/content.py). Standard library only:

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from content import check_html  # noqa: E402


class CheckHtmlTest(unittest.TestCase):
    def assertValid(self, text):
        self.assertEqual(check_html(text), [], text)

    def assertInvalid(self, text):
        self.assertNotEqual(check_html(text), [], text)

    def test_valid_content(self):
        for text in (
            "Plain text, with commas & ampersands",
            "Press <kbd>⌘</kbd> <kbd>V</kbd> to paste",
            "A <b>bold</b>, <strong>strong</strong>, <em>em</em> and <code>code</code> text<br>next line<br/>",
            '<span class="muted" lang="en">Menew</span>',
            '<a href="https://github.com/Quost/menew" title="Source">source</a>',
            '<a href="/mac">Mac</a> and <a href="#install">install</a>',
            "Quotes as entities: &quot;copy&quot; and typographic “copy” and 'single'",
            "Math as entities: 1 &lt; 2 &gt; 0",
            "A > on its own is harmless",
        ):
            self.assertValid(text)

    def test_attribute_breakout(self):
        # Texts also land inside attributes, e.g. alt="{{alt_palette}}" and content="{{mac_meta_desc}}"
        self.assertInvalid('x" onload="alert(1)')
        self.assertInvalid('Say "hello"')

    def test_unclosed_tags(self):
        self.assertInvalid("<img src=x onerror=alert(1)//")
        self.assertInvalid("<svg onload=alert(1)//")
        self.assertInvalid("<b")
        self.assertInvalid("1 < 2")
        self.assertInvalid("<b>ok</b> <svg onload=alert(1)//")

    def test_disallowed_tags_and_attributes(self):
        self.assertInvalid("<img src=x onerror=alert(1)>")
        self.assertInvalid("<script>alert(1)</script>")
        self.assertInvalid("<iframe src=\"https://example.com\"></iframe>")
        self.assertInvalid('<b onclick="alert(1)">x</b>')
        self.assertInvalid("<b class=x>x</b>")
        self.assertInvalid('<span style="color:red">x</span>')

    def test_links(self):
        self.assertInvalid('<a href="javascript:alert(1)">x</a>')
        self.assertInvalid('<a href="//evil.example">x</a>')
        self.assertInvalid('<a href="/\\evil.example">x</a>')
        self.assertInvalid('<a href="http://example.com">x</a>')
        self.assertInvalid('<a href="data:text/html,x">x</a>')


if __name__ == "__main__":
    unittest.main()
