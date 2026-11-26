#!/usr/bin/env python3
"""Bake CSS rules and custom properties into SVG presentation attributes.

Resolves page and SVG `<style>` rules (class selectors, `:root` custom
properties, `var()`, inline `style`, inherited body/root paint, and
`text-transform`) directly into presentation attributes (`fill`, `stroke`,
`font-family`, `font-size`, `font-weight`, etc.) so the standalone SVG renders
consistently in browsers, Figma, Illustrator, Inkscape, resvg, and librsvg.

Reuses the CSS scanning and XML normalization helpers in `export_svg.py` rather
than re-deriving them. Output contains no `var()` references and no
class-dependent paint. Standard library only.

Usage:
    python3 skills/diagram-design/scripts/svg_bake.py input.svg output.svg
    python3 skills/diagram-design/scripts/svg_bake.py input.svg --in-place
    python3 skills/diagram-design/scripts/svg_bake.py legacy.html out.svg
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

DTD_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def reject_dtd(text: str) -> None:
    """Refuse DOCTYPE and ENTITY declarations so no entity is ever expanded."""
    if DTD_RE.search(text):
        raise ValueError("SVG input must not contain DOCTYPE or ENTITY declarations")

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import export_svg  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# SVG presentation attributes that can be baked directly onto elements.
PRESENTATION_ATTRS = frozenset(
    {
        "alignment-baseline",
        "baseline-shift",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation",
        "color-interpolation-filters",
        "direction",
        "display",
        "dominant-baseline",
        "fill",
        "fill-opacity",
        "fill-rule",
        "filter",
        "flood-color",
        "flood-opacity",
        "font-family",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "image-rendering",
        "letter-spacing",
        "lighting-color",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "overflow",
        "paint-order",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-decoration",
        "text-rendering",
        "unicode-bidi",
        "vector-effect",
        "visibility",
        "word-spacing",
        "writing-mode",
    }
)

# Inherited SVG presentation and typography properties.
INHERITED_PROPS = frozenset(
    {
        "color",
        "direction",
        "dominant-baseline",
        "fill",
        "fill-opacity",
        "fill-rule",
        "font-family",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "letter-spacing",
        "paint-order",
        "shape-rendering",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-rendering",
        "text-transform",
        "visibility",
        "word-spacing",
        "writing-mode",
    }
)

# Non-visual classes that downstream geometry verifiers read.
PRESERVED_SEMANTIC_CLASSES = frozenset({"spoke"})

# CSS properties whose effect has no faithful SVG-attribute form here; baking fails loudly.
UNBAKEABLE_PROPS = ("transform-origin", "transform-box")

CSS_TRANSFORM_FN_RE = re.compile(r"\s*([a-zA-Z]+)\(([^()]*)\)\s*")
CSS_TRANSFORM_FNS = frozenset({"translate", "translatex", "translatey", "scale", "scalex", "scaley", "rotate", "skewx", "skewy", "matrix"})
CSS_TRANSFORM_ARG_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(px|deg)?$", re.IGNORECASE)


def css_transform_to_attr(value: str, tag: str) -> str:
    """Convert a CSS `transform` value to SVG `transform` attribute syntax, or raise."""
    value = value.strip()
    if value.lower() == "none":
        return ""
    out: list[str] = []
    pos = 0
    while pos < len(value):
        m = CSS_TRANSFORM_FN_RE.match(value, pos)
        if not m or m.group(1).lower() not in CSS_TRANSFORM_FNS:
            raise ValueError(f"cannot bake CSS transform {value!r} on <{tag}>")
        fn = m.group(1).lower()
        args: list[str] = []
        for raw in [a.strip() for a in m.group(2).split(",")] if "," in m.group(2) else m.group(2).split():
            am = CSS_TRANSFORM_ARG_RE.match(raw)
            if not am:
                raise ValueError(f"cannot bake CSS transform {value!r} on <{tag}>")
            args.append(am.group(1))
        if fn == "translatex":
            fn, args = "translate", [args[0], "0"]
        elif fn == "translatey":
            fn, args = "translate", ["0", args[0]]
        elif fn == "scalex":
            fn, args = "scale", [args[0], "1"]
        elif fn == "scaley":
            fn, args = "scale", ["1", args[0]]
        elif fn == "skewx":
            fn = "skewX"
        elif fn == "skewy":
            fn = "skewY"
        out.append(f"{fn}({' '.join(args)})")
        pos = m.end()
    return " ".join(out)

# Tags whose children are metadata/definitions rather than painted graphics.
NON_GRAPHIC_TAGS = frozenset({"title", "desc", "metadata", "style", "script"})

# Numeric attributes whose `px` unit suffix should be stripped for SVG parity.
UNITLESS_PX_PROPS = frozenset(
    {
        "font-size",
        "stroke-width",
        "stroke-dashoffset",
        "stroke-miterlimit",
    }
)

PX_NUMBER_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))px$", re.IGNORECASE)
REM_NUMBER_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))rem$", re.IGNORECASE)
EM_NUMBER_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))em$", re.IGNORECASE)
IMPORTANT_RE = re.compile(r"!\s*important\s*$", re.IGNORECASE)
ATTR_SEL_RE = re.compile(
    r"""^\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*("[^"]*"|'[^']*'|[^\s\]]+)\s*([iIsS])?\s*)?\]$"""
)
VAR_CALL_RE = re.compile(r"\bvar\s*\(", re.IGNORECASE)
FONT_SHORTHAND_RE = re.compile(
    r"^(?:(italic|oblique|normal)\s+)?"
    r"(?:(bold|bolder|lighter|[1-9]00|normal)\s+)?"
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|pt)?)"
    r"(?:/[^\s]+)?\s+(.+)$",
    re.IGNORECASE,
)


@dataclass
class ParsedRule:
    selector: str
    specificity: tuple[int, int, int]
    order: int
    declarations: list[tuple[str, str, bool]]


def _local_tag(elem: ET.Element) -> str:
    if not isinstance(elem.tag, str):
        return ""
    return elem.tag.rsplit("}", 1)[-1]


def _is_comment(elem: ET.Element) -> bool:
    return not isinstance(elem.tag, str)


def _balanced_end(text: str, index: int, opening: str, closing: str) -> int:
    depth = 0
    quote = ""
    escaped = False
    for pos in range(index, len(text)):
        ch = text[pos]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return pos + 1
    return len(text)


def parse_declarations(body: str) -> list[tuple[str, str, bool]]:
    """Parse CSS declarations using export_svg._css_segments."""
    decls: list[tuple[str, str, bool]] = []
    cleaned = export_svg.strip_css_comments(body)
    for segment in export_svg._css_segments(cleaned, ";"):
        segment = segment.strip()
        if not segment:
            continue
        parts = export_svg._css_segments(segment, ":")
        if len(parts) < 2:
            continue
        raw_name = parts[0].strip()
        raw_value = ":".join(parts[1:]).strip()
        if not raw_name or not raw_value:
            continue
        name = raw_name if raw_name.startswith("--") else raw_name.lower()
        important = False
        imp_match = IMPORTANT_RE.search(raw_value)
        if imp_match:
            important = True
            raw_value = raw_value[: imp_match.start()].strip()
        if not raw_value:
            continue
        if name == "font":
            expanded = _expand_font_shorthand(raw_value)
            if expanded:
                for sub_name, sub_val in expanded:
                    decls.append((sub_name, sub_val, important))
                continue
        decls.append((name, raw_value, important))
    return decls


def _expand_font_shorthand(value: str) -> list[tuple[str, str]]:
    match = FONT_SHORTHAND_RE.match(value.strip())
    if not match:
        return []
    style, weight, size, family = match.groups()
    out: list[tuple[str, str]] = []
    if style:
        out.append(("font-style", style))
    if weight:
        out.append(("font-weight", weight))
    if size:
        out.append(("font-size", size))
    if family:
        out.append(("font-family", family.strip()))
    return out


def _split_complex_selector(selector: str) -> list[tuple[str, str]] | None:
    """Split a complex selector into [(combinator, compound), ...]."""
    parts: list[tuple[str, str]] = []
    combinator = ""
    current: list[str] = []
    text = selector.strip()
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == "\\":
            escaped = export_svg.css_escape(text, idx)
            if escaped:
                current.append(text[idx : escaped[1]])
                idx = escaped[1]
                continue
        if ch in "([":
            close = _balanced_end(text, idx, ch, ")" if ch == "(" else "]")
            current.append(text[idx:close])
            idx = close
            continue
        if ch.isspace() or ch in ">+~":
            if current:
                parts.append((combinator, "".join(current)))
                current = []
                combinator = " "
            if ch in ">+~":
                if combinator not in ("", " "):
                    return None
                combinator = ch
            idx += 1
            continue
        current.append(ch)
        idx += 1
    if not current:
        return None
    parts.append((combinator, "".join(current)))
    return parts


def _selector_specificity(parts: list[tuple[str, str]]) -> tuple[int, int, int]:
    ids = 0
    classes = 0
    tags = 0
    for _, compound in parts:
        idx = 0
        head = re.match(r"^\*|^[A-Za-z][\w-]*", compound)
        if head:
            if head.group(0) != "*":
                tags += 1
            idx = head.end()
        while idx < len(compound):
            ch = compound[idx]
            if ch == "#":
                tok = export_svg.css_id_token(compound, idx + 1)
                if tok:
                    ids += 1
                    idx = tok[1]
                    continue
            elif ch == ".":
                tok = export_svg.css_id_token(compound, idx + 1)
                if tok:
                    classes += 1
                    idx = tok[1]
                    continue
            elif ch == "[":
                classes += 1
                idx = _balanced_end(compound, idx, "[", "]")
                continue
            elif ch == ":":
                is_pseudo_elem = compound.startswith("::", idx)
                start = idx + (2 if is_pseudo_elem else 1)
                name_m = re.match(r"[\w-]+", compound[start:])
                if name_m:
                    idx = start + len(name_m.group(0))
                    if idx < len(compound) and compound[idx] == "(":
                        idx = _balanced_end(compound, idx, "(", ")")
                    if is_pseudo_elem:
                        tags += 1
                    else:
                        classes += 1
                    continue
            idx += 1
    return (ids, classes, tags)


def _match_attr_selector(token: str, elem: ET.Element) -> bool:
    match = ATTR_SEL_RE.match(token)
    if not match:
        return False
    name, op, raw, flag = match.groups()
    actual = elem.get(name)
    if actual is None:
        return False
    if not op:
        return True
    expected = raw[1:-1] if raw[:1] in "\"'" else raw
    expected = export_svg.decoded_css_url_fragment(expected)
    if flag and flag.lower() == "i":
        actual, expected = actual.lower(), expected.lower()
    if op == "=":
        return actual == expected
    if op == "~=":
        return expected in actual.split()
    if op == "|=":
        return actual == expected or actual.startswith(expected + "-")
    if op == "^=":
        return bool(expected) and actual.startswith(expected)
    if op == "$=":
        return bool(expected) and actual.endswith(expected)
    if op == "*=":
        return bool(expected) and expected in actual
    return False


def _match_compound(compound: str, elem: ET.Element, root: ET.Element) -> bool:
    if _is_comment(elem):
        return False
    tag = _local_tag(elem).lower()
    idx = 0
    head = re.match(r"^\*|^[A-Za-z][\w-]*", compound)
    if head:
        want = head.group(0).lower()
        if want != "*" and want != tag:
            return False
        idx = head.end()
    elem_classes = set(elem.get("class", "").split())
    elem_id = elem.get("id", "")
    while idx < len(compound):
        ch = compound[idx]
        if ch == ".":
            tok = export_svg.css_id_token(compound, idx + 1)
            if not tok or tok[0] not in elem_classes:
                return False
            idx = tok[1]
        elif ch == "#":
            tok = export_svg.css_id_token(compound, idx + 1)
            if not tok or tok[0] != elem_id:
                return False
            idx = tok[1]
        elif ch == "[":
            close = _balanced_end(compound, idx, "[", "]")
            if not _match_attr_selector(compound[idx:close], elem):
                return False
            idx = close
        elif ch == ":":
            if compound.startswith("::", idx):
                return False
            name_m = re.match(r"[\w-]+", compound[idx + 1 :])
            if not name_m:
                return False
            pseudo = name_m.group(0).lower()
            idx += 1 + len(pseudo)
            arg = ""
            if idx < len(compound) and compound[idx] == "(":
                close = _balanced_end(compound, idx, "(", ")")
                arg = compound[idx + 1 : close - 1].strip()
                idx = close
            if pseudo == "root":
                if elem is not root:
                    return False
            elif pseudo == "not":
                inner = _split_complex_selector(arg)
                if inner and len(inner) == 1 and _match_compound(inner[0][1], elem, root):
                    return False
            else:
                # Dynamic/structural pseudo-classes like :hover do not apply to static baked SVG.
                return False
        else:
            return False
    return True


def _matches_selector(
    parts: list[tuple[str, str]],
    elem: ET.Element,
    root: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    prev_sibling_map: dict[ET.Element, ET.Element | None],
) -> bool:
    def match_at(index: int, node: ET.Element) -> bool:
        if not _match_compound(parts[index][1], node, root):
            return False
        if index == 0:
            return True
        combinator = parts[index][0]
        if combinator == ">":
            parent = parent_map.get(node)
            return parent is not None and match_at(index - 1, parent)
        if combinator == " ":
            ancestor = parent_map.get(node)
            while ancestor is not None:
                if match_at(index - 1, ancestor):
                    return True
                ancestor = parent_map.get(ancestor)
            return False
        if combinator == "+":
            prev = prev_sibling_map.get(node)
            return prev is not None and match_at(index - 1, prev)
        if combinator == "~":
            prev = prev_sibling_map.get(node)
            while prev is not None:
                if match_at(index - 1, prev):
                    return True
                prev = prev_sibling_map.get(prev)
            return False
        return False

    return match_at(len(parts) - 1, elem)


def resolve_var(value: str, custom_props: dict[str, str], _seen: frozenset[str] = frozenset()) -> str:
    """Resolve all `var(--name[, fallback])` calls recursively."""
    out: list[str] = []
    pos = 0
    while pos < len(value):
        match = VAR_CALL_RE.search(value, pos)
        if match is None:
            out.append(value[pos:])
            break
        out.append(value[pos : match.start()])
        paren_open = match.end() - 1
        paren_close = _balanced_end(value, paren_open, "(", ")")
        inner = value[paren_open + 1 : paren_close - 1]
        args = export_svg._css_segments(inner, ",")
        var_name = args[0].strip()
        fallback = ",".join(args[1:]).strip() if len(args) > 1 else None
        if var_name in _seen:
            raise ValueError(f"cyclic CSS custom property reference: {var_name}")
        if var_name in custom_props and custom_props[var_name].strip():
            resolved = resolve_var(custom_props[var_name], custom_props, _seen | {var_name})
            out.append(resolved)
        elif fallback is not None:
            resolved = resolve_var(fallback, custom_props, _seen | {var_name})
            out.append(resolved)
        else:
            raise ValueError(f"unresolved CSS custom property: {var_name}")
        pos = paren_close
    return "".join(out)


def _normalize_value(prop: str, value: str, parent_font_size: float) -> str:
    val = value.strip()
    if prop == "font-size":
        m_px = PX_NUMBER_RE.match(val)
        if m_px:
            return m_px.group(1)
        m_rem = REM_NUMBER_RE.match(val)
        if m_rem:
            return format(float(m_rem.group(1)) * 16.0, ".6g")
        m_em = EM_NUMBER_RE.match(val)
        if m_em:
            return format(float(m_em.group(1)) * parent_font_size, ".6g")
    elif prop in UNITLESS_PX_PROPS:
        m_px = PX_NUMBER_RE.match(val)
        if m_px:
            return m_px.group(1)
    elif prop in ("fill", "stroke") and val.lower() == "transparent":
        return "none"
    return val


def _apply_text_transform(elem: ET.Element, transform: str) -> None:
    mode = transform.strip().lower()
    if mode not in {"uppercase", "lowercase", "capitalize"}:
        return

    def xform(text: str) -> str:
        if mode == "uppercase":
            return text.upper()
        if mode == "lowercase":
            return text.lower()
        return text.title()

    if elem.text:
        elem.text = xform(elem.text)
    for child in elem:
        if _local_tag(child) == "tspan":
            _apply_text_transform(child, mode)
        if child.tail:
            child.tail = xform(child.tail)


def _is_skipped_media_prelude(selector: str) -> bool:
    low = selector.lower()
    if low.startswith("@media"):
        if "print" in low or "prefers-reduced-motion" in low or "max-width" in low:
            return True
    return False


def _collect_stylesheet_rules(
    source: str,
    extra_tokens: dict[str, str] | None = None,
) -> tuple[list[ParsedRule], dict[str, str], dict[str, str], list[str]]:
    """Extract paint/font CSS rules, root tokens, body inherited props, and @font-face blocks."""
    tree = export_svg._html_css_rules(source)
    standalone_svg = re.search(r"<(?:html|body)\b", source, re.IGNORECASE) is None
    parsed_rules: list[ParsedRule] = []
    root_tokens: dict[str, str] = {}
    body_inherited: dict[str, str] = {}
    font_faces: list[str] = []
    order = 0

    def walk(nodes: list[export_svg._CssRule]) -> None:
        nonlocal order
        for rule in nodes:
            sel = rule.selector.strip()
            if rule.statement:
                continue
            if rule.group:
                if _is_skipped_media_prelude(sel):
                    continue
                walk(rule.children)
                continue
            body = rule.css[rule.start : rule.end].strip()
            if not sel or not body:
                continue
            if re.match(r"@font-face\b", sel, re.IGNORECASE):
                font_faces.append(f"{sel} {{ {body} }}")
                continue
            if sel.startswith("@"):
                # Skip @keyframes and other non-static at-rules.
                continue
            decls = parse_declarations(body)
            if not decls:
                continue
            for branch in export_svg.selector_list_parts(sel):
                branch = branch.strip()
                if not branch:
                    continue
                low = branch.lower()
                if low in {":root", "html", "body"}:
                    for name, val, _imp in decls:
                        if name.startswith("--"):
                            root_tokens[name] = val
                        elif low == "body" and name in {"color", "font-family"}:
                            body_inherited[name] = val
                        elif low == ":root" and standalone_svg and name in INHERITED_PROPS:
                            # In a standalone SVG, :root is the <svg> element itself.
                            body_inherited[name] = val
                    continue
                if low == "svg":
                    # Bare `svg` may hold tokens or inherited font/color; ignore width/min-width page layout.
                    svg_decls = [
                        d for d in decls
                        if d[0].startswith("--") or d[0] in INHERITED_PROPS
                    ]
                    if svg_decls:
                        parts = _split_complex_selector("svg")
                        assert parts is not None
                        order += 1
                        parsed_rules.append(ParsedRule("svg", _selector_specificity(parts), order, svg_decls))
                    continue
                if export_svg.is_chrome_selector(branch):
                    continue
                # Rewrite leading `:root ` to `svg ` so `:root .x` matches inside standalone SVG.
                normalized_branch = branch
                if normalized_branch.startswith(":root"):
                    rest = normalized_branch[len(":root") :].strip()
                    normalized_branch = f"svg {rest}" if rest else "svg"
                parts = _split_complex_selector(normalized_branch)
                if parts is None:
                    continue
                order += 1
                parsed_rules.append(
                    ParsedRule(normalized_branch, _selector_specificity(parts), order, decls)
                )

    walk(tree)
    if extra_tokens:
        for k, v in extra_tokens.items():
            key = k if k.startswith("--") else f"--{k}"
            root_tokens[key] = v
    return parsed_rules, root_tokens, body_inherited, font_faces


def _strip_existing_style_tags(root: ET.Element) -> None:
    """Remove all `<style>` elements from the SVG tree."""
    for parent in list(root.iter()):
        for child in list(parent):
            if _local_tag(child) == "style":
                parent.remove(child)


def _insert_font_faces(root: ET.Element, font_faces: list[str]) -> None:
    if not font_faces:
        return
    defs = None
    for child in root:
        if _local_tag(child) == "defs":
            defs = child
            break
    if defs is None:
        # Keep leading <title> and <desc> before newly created <defs>.
        insert_idx = 0
        for idx, child in enumerate(root):
            if _is_comment(child) or _local_tag(child) in {"title", "desc"}:
                insert_idx = idx + 1
            else:
                break
        defs = ET.Element(f"{{{SVG_NS}}}defs")
        root.insert(insert_idx, defs)
    style_el = ET.Element(f"{{{SVG_NS}}}style")
    style_el.text = "\n" + "\n".join(font_faces) + "\n"
    defs.insert(0, style_el)


def bake_svg(
    source: str,
    *,
    tokens: dict[str, str] | None = None,
    keep_rgba: bool = False,
    keep_classes: bool = False,
) -> str:
    """Resolve CSS rules and `var()` tokens into SVG presentation attributes.

    Accepts either a standalone `.svg` string or an `.html` document containing
    an inline `<svg>`. Returns a well-formed standalone XML SVG string with no
    `var()` calls and no class-dependent paint.
    """
    rules, root_tokens, body_inherited, font_faces = _collect_stylesheet_rules(source, tokens)

    # Extract and XML-normalize the first <svg> (works for both HTML and SVG inputs).
    svg_raw = export_svg.extract_first_svg(source)
    svg_raw = export_svg.normalize_html_entities(export_svg.xmlify_attributes(svg_raw))
    export_svg.ensure_viewbox(svg_raw)
    svg_raw = export_svg.ensure_xmlns(svg_raw)
    svg_raw = export_svg.xml_safe_comments(svg_raw)

    reject_dtd(svg_raw)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(svg_raw, parser=parser)

    # Pre-split selectors once.
    compiled_rules: list[tuple[ParsedRule, list[tuple[str, str]]]] = []
    for rule in rules:
        parts = _split_complex_selector(rule.selector)
        if parts is not None:
            compiled_rules.append((rule, parts))

    # Build parent and previous-element-sibling maps.
    parent_map: dict[ET.Element, ET.Element] = {}
    prev_sibling_map: dict[ET.Element, ET.Element | None] = {}
    for parent in root.iter():
        prev: ET.Element | None = None
        for child in parent:
            parent_map[child] = parent
            if not _is_comment(child):
                prev_sibling_map[child] = prev
                prev = child

    # Strip old <style> elements (their rules were collected above).
    _strip_existing_style_tags(root)

    # Cascade custom properties and presentation properties top-down.
    def process(
        elem: ET.Element,
        inherited_vars: dict[str, str],
        inherited_props: dict[str, str],
        parent_font_size: float,
    ) -> None:
        if _is_comment(elem):
            return
        tag = _local_tag(elem)
        if tag in NON_GRAPHIC_TAGS:
            return

        # Collect matching stylesheet declarations.
        matched_normal: list[tuple[tuple[int, int, int], int, str, str]] = []
        matched_important: list[tuple[tuple[int, int, int], int, str, str]] = []
        for rule, parts in compiled_rules:
            if _matches_selector(parts, elem, root, parent_map, prev_sibling_map):
                for name, val, imp in rule.declarations:
                    target_list = matched_important if imp else matched_normal
                    target_list.append((rule.specificity, rule.order, name, val))
        matched_normal.sort(key=lambda item: (item[0], item[1]))
        matched_important.sort(key=lambda item: (item[0], item[1]))

        inline_decls = parse_declarations(elem.get("style", ""))
        inline_normal = [(n, v) for n, v, imp in inline_decls if not imp]
        inline_important = [(n, v) for n, v, imp in inline_decls if imp]

        # 1. Resolve custom properties for this element.
        elem_vars = dict(inherited_vars)
        for _spec, _ord, name, val in matched_normal:
            if name.startswith("--"):
                elem_vars[name] = val
        for name, val in inline_normal:
            if name.startswith("--"):
                elem_vars[name] = val
        for _spec, _ord, name, val in matched_important:
            if name.startswith("--"):
                elem_vars[name] = val
        for name, val in inline_important:
            if name.startswith("--"):
                elem_vars[name] = val

        # 2. Resolve CSS presentation properties in cascade order.
        css_props: dict[str, str] = {}
        if elem is root:
            for k, v in body_inherited.items():
                css_props[k] = v
        for _spec, _ord, name, val in matched_normal:
            if not name.startswith("--"):
                css_props[name] = val
        for name, val in inline_normal:
            if not name.startswith("--"):
                css_props[name] = val
        for _spec, _ord, name, val in matched_important:
            if not name.startswith("--"):
                css_props[name] = val
        for name, val in inline_important:
            if not name.startswith("--"):
                css_props[name] = val

        # Resolve any var() inside existing presentation attributes too.
        for attr_name in list(elem.attrib):
            attr_val = elem.attrib[attr_name]
            if VAR_CALL_RE.search(attr_val):
                elem.attrib[attr_name] = resolve_var(attr_val, elem_vars)

        # Compute effective color for currentColor resolution.
        resolved_css: dict[str, str] = {}
        for prop, raw_val in css_props.items():
            resolved_css[prop] = resolve_var(raw_val, elem_vars)

        effective_color = (
            resolved_css.get("color")
            or elem.get("color")
            or inherited_props.get("color")
        )
        if effective_color and VAR_CALL_RE.search(effective_color):
            effective_color = resolve_var(effective_color, elem_vars)

        # Determine font-size on this element for em normalization.
        own_fs = resolved_css.get("font-size") or elem.get("font-size")
        current_font_size = parent_font_size
        if own_fs:
            norm_fs = _normalize_value("font-size", own_fs, parent_font_size)
            try:
                current_font_size = float(norm_fs)
            except ValueError:
                pass

        for prop in UNBAKEABLE_PROPS:
            if prop in resolved_css:
                raise ValueError(f"cannot bake CSS {prop!r} on <{tag}>; write it as an SVG attribute instead")
        if "transform" in resolved_css:
            baked_transform = css_transform_to_attr(resolved_css["transform"], tag)
            if baked_transform:
                elem.set("transform", baked_transform)
            else:
                elem.attrib.pop("transform", None)

        # Apply resolved CSS presentation properties as attributes.
        for prop, val in resolved_css.items():
            if prop == "text-transform":
                continue
            if prop not in PRESENTATION_ATTRS:
                continue
            if val.lower() == "inherit":
                if prop in inherited_props:
                    val = inherited_props[prop]
                else:
                    continue
            if prop in {"fill", "stroke"} and val.lower() == "currentcolor" and effective_color:
                val = effective_color
            val = _normalize_value(prop, val, parent_font_size)
            # Do not stamp root-level `color` onto <svg> if unused, keep font-family if inherited.
            if elem is root and prop == "color":
                continue
            elem.set(prop, val)

        # Resolve currentColor on existing fill/stroke attributes too.
        for paint_prop in ("fill", "stroke"):
            existing = elem.get(paint_prop)
            if existing and existing.strip().lower() == "currentcolor" and effective_color:
                elem.set(paint_prop, effective_color)

        # Apply text-transform directly to text content (for Figma/Illustrator/resvg parity).
        effective_transform = resolved_css.get("text-transform") or inherited_props.get("text-transform")
        if tag in {"text", "tspan"} and effective_transform:
            _apply_text_transform(elem, effective_transform)

        # Ensure <text> gets explicit font-family if inherited from root/body without a local one.
        if tag == "text" and "font-family" not in elem.attrib and "font-family" in inherited_props:
            elem.set("font-family", inherited_props["font-family"])

        # Remove inline style attribute after baking presentation properties.
        if "style" in elem.attrib:
            del elem.attrib["style"]

        # Build next inherited property map for children.
        next_inherited = dict(inherited_props)
        for prop in INHERITED_PROPS:
            if prop == "text-transform":
                if "text-transform" in resolved_css:
                    next_inherited["text-transform"] = resolved_css["text-transform"]
                continue
            if prop in elem.attrib:
                next_inherited[prop] = elem.attrib[prop]
            elif prop in resolved_css:
                next_inherited[prop] = _normalize_value(prop, resolved_css[prop], parent_font_size)

        for child in elem:
            process(child, elem_vars, next_inherited, current_font_size)

    # Seed root custom properties and body-inherited color/font-family.
    initial_inherited: dict[str, str] = {}
    for prop, raw_val in body_inherited.items():
        initial_inherited[prop] = resolve_var(raw_val, root_tokens)

    process(root, dict(root_tokens), initial_inherited, 16.0)

    # Strip purely stylistic class names only after every element has been matched,
    # so descendant selectors such as `.panel .label` still see ancestor classes.
    if not keep_classes:
        for elem in root.iter():
            if _is_comment(elem) or "class" not in elem.attrib:
                continue
            kept = [c for c in elem.attrib["class"].split() if c in PRESERVED_SEMANTIC_CLASSES]
            if kept:
                elem.set("class", " ".join(kept))
            else:
                del elem.attrib["class"]

    # Preserve @font-face rules if any existed in the source.
    _insert_font_faces(root, font_faces)

    serialized = ET.tostring(root, encoding="unicode")
    if not keep_rgba:
        serialized = export_svg.normalize_rgba_presentation_attrs(serialized)

    document = '<?xml version="1.0" encoding="UTF-8"?>\n' + serialized + "\n"

    # Post-condition assertions: no unresolved var() and no class-dependent paint rules.
    assert_baked(document)
    return document


def assert_baked(svg: str) -> None:
    """Verify the baked SVG parses as XML and has no var() or class-dependent paint."""
    reject_dtd(svg)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError(f"baked SVG is not well-formed XML: {exc}") from exc

    for elem in root.iter():
        tag = _local_tag(elem)
        if tag == "style":
            css = export_svg.strip_css_comments(elem.text or "")
            # Only @font-face rules may remain inside <style> after baking.
            for rule in export_svg._css_rule_nodes(export_svg._css_rule_tree(css)):
                sel = rule.selector.strip()
                if not re.match(r"@font-face\b", sel, re.IGNORECASE):
                    raise ValueError(f"baked SVG still contains non-font CSS rule: {sel!r}")
        for attr_name, attr_val in elem.attrib.items():
            if VAR_CALL_RE.search(attr_val):
                raise ValueError(
                    f"baked SVG still contains var() in <{tag} {attr_name}={attr_val!r}>"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bake CSS rules and custom properties into SVG presentation attributes."
    )
    parser.add_argument("source", type=Path, help="Input .svg or .html diagram file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Output .svg path (default: <source-stem>.baked.svg, or overwrite with --in-place)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input .svg file in place",
    )
    parser.add_argument(
        "--tokens",
        type=Path,
        default=None,
        help="Optional JSON file mapping CSS custom property names to values",
    )
    parser.add_argument(
        "--keep-rgba",
        action="store_true",
        help="Keep rgba(...) paint attributes instead of splitting into hex + *-opacity",
    )
    parser.add_argument(
        "--keep-classes",
        action="store_true",
        help="Retain class attributes on SVG elements (paint rules are still removed)",
    )
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    if args.in_place and args.output is not None:
        print("error: pass either an output path or --in-place, not both", file=sys.stderr)
        return 2
    if args.in_place and args.source.suffix.lower() != ".svg":
        print("error: --in-place requires an .svg input file", file=sys.stderr)
        return 2

    token_overrides: dict[str, str] | None = None
    if args.tokens is not None:
        try:
            loaded = json.loads(args.tokens.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read tokens file {args.tokens}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(loaded, dict):
            print("error: tokens JSON must be an object mapping token names to values", file=sys.stderr)
            return 2
        token_overrides = {str(k): str(v) for k, v in loaded.items()}

    try:
        source_text = args.source.read_text(encoding="utf-8")
        baked = bake_svg(
            source_text,
            tokens=token_overrides,
            keep_rgba=args.keep_rgba,
            keep_classes=args.keep_classes,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.in_place:
        target = args.source
    elif args.output is not None:
        target = args.output
    elif args.source.suffix.lower() == ".html":
        target = args.source.with_suffix(".svg")
    else:
        target = args.source.with_name(args.source.stem + ".baked.svg")

    try:
        target.write_text(baked, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write {target}: {exc}", file=sys.stderr)
        return 2

    print(f"OK baked {target} ({len(baked)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
