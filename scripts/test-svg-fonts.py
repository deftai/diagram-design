#!/usr/bin/env python3
"""Unit and regression tests for skills/diagram-design/scripts/svg_fonts.py."""

from __future__ import annotations

import base64
import importlib.util
import io
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS_SCRIPT = ROOT / "skills/diagram-design/scripts/svg_fonts.py"
SVG_NS = "{http://www.w3.org/2000/svg}"


def load_svg_fonts():
    spec = importlib.util.spec_from_file_location("svg_fonts", FONTS_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def find_system_ttf() -> tuple[Path, str]:
    """Find any usable system TTF and read its family name via fontTools."""
    from fontTools.ttLib import TTFont

    for root in (Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts")):
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob("*.ttf")):
            try:
                with TTFont(candidate, lazy=True) as font:
                    os2 = font.get("OS/2")
                    if os2 is None or (os2.fsSelection & 0x01) or os2.usWeightClass != 400:
                        continue
                    cmap = font.getBestCmap() or {}
                    if not all(ord(ch) in cmap for ch in "HelloWorld"):
                        continue
                    for rec in font["name"].names:
                        if rec.nameID in {1, 16}:
                            fam = rec.toUnicode().strip()
                            if fam:
                                return candidate, fam
            except Exception:
                continue
    raise unittest.SkipTest("no suitable system TTF found for offline subset test")


class SvgFontsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_svg_fonts()

    def test_collect_text_runs_inherits_family_weight_and_style(self) -> None:
        svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <g font-family="'Geist', sans-serif" font-weight="600">
    <text x="10" y="30">Alpha<tspan font-family="'Geist Mono', monospace" font-weight="400">01</tspan>Beta</text>
  </g>
</svg>"""
        root = ET.fromstring(svg)
        runs = self.mod.collect_text_runs(root)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0], (["Geist", "sans-serif"], 600, "normal", "Alpha"))
        self.assertEqual(runs[1], (["Geist Mono", "monospace"], 400, "normal", "01"))
        self.assertEqual(runs[2], (["Geist", "sans-serif"], 600, "normal", "Beta"))

    def test_system_fonts_strips_font_face_and_writes_fallback_stacks(self) -> None:
        svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 120" role="img" aria-labelledby="s-title s-desc">
  <title id="s-title">Title</title>
  <desc id="s-desc">Desc</desc>
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Geist&amp;display=swap');
      @font-face { font-family: 'Geist'; src: url(data:font/ttf;base64,AAAA) format('truetype'); }
    </style>
  </defs>
  <text x="20" y="40" font-family="'Instrument Serif'" font-size="28">Heading</text>
  <text x="20" y="70" font-family="'Geist'" font-size="12">Node</text>
  <text x="20" y="95" font-family="'Geist Mono'" font-size="9">sublabel</text>
</svg>"""
        out = self.mod.apply_system_fonts(svg)
        self.assertNotIn("@font-face", out)
        self.assertNotIn("@import", out)
        self.assertNotIn("<style", out)
        root = ET.fromstring(out)
        texts = root.findall(f"{SVG_NS}text")
        self.assertIn("serif", texts[0].get("font-family", ""))
        self.assertIn("system-ui, sans-serif", texts[1].get("font-family", ""))
        self.assertIn("ui-monospace, monospace", texts[2].get("font-family", ""))

    def test_embed_fonts_subsets_only_used_glyphs_and_only_used_faces(self) -> None:
        from fontTools.ttLib import TTFont

        ttf_path, family = find_system_ttf()
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 100" role="img" aria-labelledby="f-title f-desc">
  <title id="f-title">Subset test</title>
  <desc id="f-desc">Verifies font subsetting.</desc>
  <text x="20" y="50" font-family="'{family}', 'Unused Fallback', sans-serif" font-size="16">Hello</text>
</svg>"""
        with tempfile.TemporaryDirectory() as cache_tmp:
            embedded, report = self.mod.embed_fonts(
                svg,
                cache_dir=Path(cache_tmp),
                font_dirs=[ttf_path.parent],
                fmt="auto",
            )
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["family"], family)
        self.assertIn("@font-face", embedded)
        self.assertIn("base64,", embedded)
        style_el = ET.fromstring(embedded).find(f".//{SVG_NS}style")
        assert style_el is not None and style_el.text is not None
        self.assertNotIn("Unused Fallback", style_el.text)

        # Decode the embedded font and confirm it was actually subsetted to just H, e, l, o.
        start = embedded.index("base64,") + len("base64,")
        end = embedded.index(")", start)
        raw = base64.b64decode(embedded[start:end])
        self.assertLess(len(raw), ttf_path.stat().st_size)

        with TTFont(io.BytesIO(raw)) as subset_font:
            cmap = set((subset_font.getBestCmap() or {}).keys())
        self.assertTrue({ord(c) for c in "Helo"}.issubset(cmap))
        self.assertNotIn(ord("Z"), cmap)
        self.assertNotIn(ord("9"), cmap)

        # Ensure <title> stays the first child of <svg>.
        root = ET.fromstring(embedded)
        children = [c for c in root if isinstance(c.tag, str)]
        self.assertTrue(children[0].tag.endswith("}title"))

    def test_entity_declaration_rejected(self) -> None:
        source = (
            '<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>&e;</text></svg>'
        )
        with self.assertRaises(ValueError):
            self.mod.apply_system_fonts(source)

    def test_default_cache_dir_is_outside_repo(self) -> None:
        cache = self.mod.default_cache_dir().resolve()
        self.assertFalse(str(cache).startswith(str(ROOT.resolve())))

    def test_cli_system_fonts_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<text x="10" y="50" font-family="\'Geist\'" font-size="12">Hi</text></svg>',
                encoding="utf-8",
            )
            rc = self.mod.main([str(path), "--system-fonts", "--in-place"])
            self.assertEqual(rc, 0)
            out = path.read_text(encoding="utf-8")
            self.assertIn("system-ui, sans-serif", out)


if __name__ == "__main__":
    unittest.main()
