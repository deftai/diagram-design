#!/usr/bin/env python3
"""Unit and regression tests for skills/diagram-design/scripts/svg_bake.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAKE_SCRIPT = ROOT / "skills/diagram-design/scripts/svg_bake.py"
ASSETS = ROOT / "skills/diagram-design/assets"
SVG_NS = "{http://www.w3.org/2000/svg}"


def load_bake():
    spec = importlib.util.spec_from_file_location("svg_bake", BAKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class SvgBakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_bake()

    def test_bakes_root_vars_and_class_rules_into_presentation_attrs(self) -> None:
        source = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" role="img" aria-labelledby="t-title t-desc">
  <title id="t-title">Demo</title>
  <desc id="t-desc">Class-styled SVG with custom properties.</desc>
  <defs>
    <style>
      :root {
        --color-paper: #f5f5f5;
        --color-ink: #2d3142;
        --color-accent: #bf4520;
        --font-sans: 'Geist', system-ui, sans-serif;
        --font-mono: 'Geist Mono', ui-monospace, monospace;
      }
      .bg { fill: var(--color-paper); }
      .node { fill: #ffffff; stroke: var(--color-ink); stroke-width: 1px; }
      .node.focal { stroke: var(--color-accent); stroke-width: 1.4px; }
      .label { font-family: var(--font-sans); font-size: 12px; font-weight: 600; fill: var(--color-ink); }
      .tag { font-family: var(--font-mono); font-size: 8px; text-transform: uppercase; fill: var(--color-accent); }
    </style>
  </defs>
  <rect class="bg" width="100%" height="100%"/>
  <rect class="node focal" x="20" y="20" width="160" height="60" rx="6"/>
  <text class="label" x="100" y="52" text-anchor="middle">Gateway</text>
  <text class="tag" x="32" y="34">edge</text>
</svg>
"""
        baked = self.mod.bake_svg(source)
        self.assertNotIn("var(", baked)
        self.assertNotIn("<style", baked)
        self.assertNotIn('class="', baked)

        root = ET.fromstring(baked)
        rects = root.findall(f"{SVG_NS}rect")
        self.assertEqual(rects[0].get("fill"), "#f5f5f5")
        self.assertEqual(rects[1].get("fill"), "#ffffff")
        self.assertEqual(rects[1].get("stroke"), "#bf4520")
        self.assertEqual(rects[1].get("stroke-width"), "1.4")

        texts = root.findall(f"{SVG_NS}text")
        self.assertEqual(texts[0].get("font-family"), "'Geist', system-ui, sans-serif")
        self.assertEqual(texts[0].get("font-size"), "12")
        self.assertEqual(texts[0].get("font-weight"), "600")
        self.assertEqual(texts[0].get("fill"), "#2d3142")
        self.assertEqual(texts[1].text, "EDGE")
        self.assertEqual(texts[1].get("fill"), "#bf4520")

    def test_cascades_specificity_important_and_inline_style(self) -> None:
        source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs><style>
    rect { fill: #ececec; stroke: #4f5d75; }
    .box { fill: #ffffff; stroke: #2d3142 !important; }
    #hero { fill: #bf4520; }
  </style></defs>
  <rect id="hero" class="box" x="10" y="10" width="80" height="80" style="stroke: #2e5aa8; stroke-width: 2px"/>
</svg>"""
        baked = self.mod.bake_svg(source)
        root = ET.fromstring(baked)
        rect = root.find(f"{SVG_NS}rect")
        assert rect is not None
        self.assertEqual(rect.get("fill"), "#bf4520")
        self.assertEqual(rect.get("stroke"), "#2d3142")
        self.assertEqual(rect.get("stroke-width"), "2")
        self.assertIsNone(rect.get("style"))

    def test_resolves_nested_var_fallbacks_and_current_color(self) -> None:
        source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs><style>
    :root { --primary: #2d3142; }
    g { color: var(--missing, var(--primary)); }
    line { stroke: currentColor; stroke-width: 1.2px; }
  </style></defs>
  <g><line x1="0" y1="50" x2="100" y2="50"/></g>
</svg>"""
        baked = self.mod.bake_svg(source)
        root = ET.fromstring(baked)
        line = root.find(f".//{SVG_NS}line")
        assert line is not None
        self.assertEqual(line.get("stroke"), "#2d3142")
        self.assertEqual(line.get("stroke-width"), "1.2")

    def test_unresolved_var_raises(self) -> None:
        source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs><style>.a { fill: var(--does-not-exist); }</style></defs>
  <rect class="a" width="10" height="10"/>
</svg>"""
        with self.assertRaises(ValueError):
            self.mod.bake_svg(source)

    def test_entity_declaration_rejected(self) -> None:
        source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>
  <text>&e;</text>
</svg>"""
        with self.assertRaises(ValueError):
            self.mod.bake_svg(source)

    def test_preserves_font_face_and_spoke_class(self) -> None:
        source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs><style>
    @font-face { font-family: 'Geist'; src: url(data:font/ttf;base64,AAAA) format('truetype'); }
    .spoke { stroke: #4f5d75; fill: none; }
  </style></defs>
  <path class="spoke" d="M 10 10 L 50 50"/>
</svg>"""
        baked = self.mod.bake_svg(source)
        self.assertIn("@font-face", baked)
        self.assertNotIn(".spoke", baked)
        root = ET.fromstring(baked)
        path = root.find(f"{SVG_NS}path")
        assert path is not None
        self.assertEqual(path.get("class"), "spoke")
        self.assertEqual(path.get("stroke"), "#4f5d75")

    def test_bakes_shipped_class_styled_html_example_loop(self) -> None:
        loop_html = (ASSETS / "example-loop.html").read_text(encoding="utf-8")
        baked = self.mod.bake_svg(loop_html)
        self.assertNotIn("var(", baked)
        self.assertNotIn("<style", baked)
        root = ET.fromstring(baked)
        # First child must remain <title> for the accessible SVG contract.
        children = [c for c in root if isinstance(c.tag, str)]
        self.assertTrue(children[0].tag.endswith("}title"))
        # Station rects must now carry explicit fill/stroke presentation attributes.
        rects = root.findall(f".//{SVG_NS}rect")
        filled = [r for r in rects if r.get("fill")]
        self.assertGreater(len(filled), 4)

    def test_cli_in_place_and_tokens_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svg_path = Path(tmp) / "sample.svg"
            tok_path = Path(tmp) / "tokens.json"
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                "<defs><style>.box { fill: var(--color-accent); }</style></defs>"
                '<rect class="box" width="50" height="50"/></svg>',
                encoding="utf-8",
            )
            tok_path.write_text('{"--color-accent": "#bf4520"}', encoding="utf-8")
            rc = self.mod.main([str(svg_path), "--in-place", "--tokens", str(tok_path)])
            self.assertEqual(rc, 0)
            out = svg_path.read_text(encoding="utf-8")
            self.assertIn('fill="#bf4520"', out)
            self.assertNotIn("var(", out)


if __name__ == "__main__":
    unittest.main()
