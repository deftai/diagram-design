#!/usr/bin/env python3
"""Subset and embed used Google Fonts into a standalone SVG as base64 @font-face.

Inspects `<text>` and `<tspan>` elements in an `.svg` file, determines the
exact font faces (`font-family`, `font-weight`, `font-style`) and Unicode
characters actually rendered, fetches the source fonts from Google Fonts into
an external cache directory (default `~/.cache/diagram-design/fonts`, never
inside the repository), subsets each face to only the used glyphs via
`fontTools`, and embeds base64 `@font-face` rules (WOFF2 when `brotli` is
available, else TTF) inside `<defs><style>`.

Pass `--system-fonts` to omit embedded fonts and write clean fallback font
stacks instead, making zero network requests and embedding zero font binaries.

Usage:
    python3 skills/diagram-design/scripts/svg_fonts.py diagram.svg out.svg
    python3 skills/diagram-design/scripts/svg_fonts.py diagram.svg --in-place
    python3 skills/diagram-design/scripts/svg_fonts.py diagram.svg --system-fonts -o out.svg
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

GENERIC_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "emoji",
        "math",
        "fangsong",
        "inherit",
        "initial",
        "unset",
    }
)

# Canonical fallback stacks matching references/style-guide.md (and lint-skin.py ALLOWED_FONTS).
SYSTEM_STACK_SERIF = "'Instrument Serif', 'Noto Serif', serif"
SYSTEM_STACK_SANS = "'Geist', system-ui, sans-serif"
SYSTEM_STACK_MONO = "'Geist Mono', ui-monospace, monospace"

GOOGLE_FONTS_CSS2 = "https://fonts.googleapis.com/css2"
# Python-urllib UA causes Google Fonts CSS2 to serve complete un-sliced TTF
# files that fontTools can always read and subset (then re-encode to WOFF2
# when brotli is installed).
TTF_USER_AGENT = "Python-urllib/3.13"

FONT_FACE_BLOCK_RE = re.compile(r"@font-face\s*\{[^{}]*\}", re.IGNORECASE | re.DOTALL)
IMPORT_RULE_RE = re.compile(r"@import\b[^;]*;", re.IGNORECASE)
CSS_URL_SRC_RE = re.compile(r"url\((['\"]?)(https?://[^)'\"]+)\1\)", re.IGNORECASE)
CSS_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(\d+)", re.IGNORECASE)
CSS_STYLE_RE = re.compile(r"font-style\s*:\s*([a-z]+)", re.IGNORECASE)


def default_cache_dir() -> Path:
    env_override = os.environ.get("DIAGRAM_DESIGN_FONT_CACHE")
    if env_override:
        return Path(env_override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "diagram-design" / "fonts"


def has_brotli() -> bool:
    return importlib.util.find_spec("brotli") is not None


def _local_tag(elem: ET.Element) -> str:
    if not isinstance(elem.tag, str):
        return ""
    return elem.tag.rsplit("}", 1)[-1]


def _is_comment(elem: ET.Element) -> bool:
    return not isinstance(elem.tag, str)


def parse_font_family_list(raw: str) -> list[str]:
    families: list[str] = []
    for part in raw.split(","):
        cleaned = part.strip().strip("'\"").strip()
        if cleaned:
            families.append(cleaned)
    return families


def normalize_weight(raw: str | None) -> int:
    if not raw:
        return 400
    val = raw.strip().lower()
    if val == "normal":
        return 400
    if val == "bold":
        return 700
    try:
        num = int(float(val))
    except ValueError:
        return 400
    return max(100, min(900, int(round(num / 100.0) * 100)))


def normalize_style(raw: str | None) -> str:
    if not raw:
        return "normal"
    val = raw.strip().lower()
    return "italic" if val in {"italic", "oblique"} else "normal"


def collect_text_runs(root: ET.Element) -> list[tuple[list[str], int, str, str]]:
    """Collect (family_stack, weight, style, text) for every rendered text run."""
    runs: list[tuple[list[str], int, str, str]] = []

    def walk(
        elem: ET.Element,
        fam: list[str],
        weight: int,
        style: str,
        in_text: bool,
    ) -> None:
        if _is_comment(elem):
            return
        tag = _local_tag(elem)
        if tag in {"defs", "title", "desc", "metadata", "style", "script"}:
            return
        cur_fam = parse_font_family_list(elem.attrib["font-family"]) if "font-family" in elem.attrib else fam
        cur_weight = normalize_weight(elem.attrib["font-weight"]) if "font-weight" in elem.attrib else weight
        cur_style = normalize_style(elem.attrib["font-style"]) if "font-style" in elem.attrib else style
        now_text = in_text or tag in {"text", "tspan"}
        if now_text and elem.text:
            runs.append((cur_fam, cur_weight, cur_style, elem.text))
        for child in elem:
            walk(child, cur_fam, cur_weight, cur_style, now_text)
            if now_text and child.tail:
                runs.append((cur_fam, cur_weight, cur_style, child.tail))

    walk(root, [], 400, "normal", False)
    return runs


def _slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def _cache_file(cache_dir: Path, family: str, weight: int, style: str) -> Path:
    return cache_dir / f"{_slug(family)}-{weight}-{style}.ttf"


def _find_local_font(font_dirs: list[Path], family: str, weight: int, style: str) -> Path | None:
    """Search local font directories for a TTF/OTF matching family, weight, and style."""
    from fontTools.ttLib import TTFont

    want_fam = family.casefold()
    want_italic = style == "italic"
    best: tuple[int, Path] | None = None
    for directory in font_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            try:
                with TTFont(path, lazy=True) as font:
                    names = [
                        rec.toUnicode().strip().casefold()
                        for rec in font["name"].names
                        if rec.nameID in {1, 16}
                    ]
                    if want_fam not in names:
                        continue
                    os2 = font.get("OS/2")
                    font_weight = int(os2.usWeightClass) if os2 is not None else 400
                    fs_sel = int(os2.fsSelection) if os2 is not None else 0
                    font_italic = bool(fs_sel & 0x01)
                    if font_italic != want_italic:
                        continue
                    distance = abs(font_weight - weight)
                    if best is None or distance < best[0]:
                        best = (distance, path)
            except Exception:
                continue
    return best[1] if best is not None else None


def _google_fonts_css_urls(family: str, weight: int, style: str) -> list[str]:
    encoded = urllib.parse.quote_plus(family)
    urls: list[str] = []
    if style == "italic":
        urls.append(f"{GOOGLE_FONTS_CSS2}?family={encoded}:ital,wght@1,{weight}&display=swap")
        if weight == 400:
            urls.append(f"{GOOGLE_FONTS_CSS2}?family={encoded}:ital@1&display=swap")
    else:
        urls.append(f"{GOOGLE_FONTS_CSS2}?family={encoded}:wght@{weight}&display=swap")
        if weight == 400:
            urls.append(f"{GOOGLE_FONTS_CSS2}?family={encoded}&display=swap")
    return urls


def fetch_google_font(
    family: str,
    weight: int,
    style: str,
    cache_dir: Path,
    *,
    font_dirs: list[Path] | None = None,
    timeout: float = 20.0,
) -> Path:
    """Return a local cached font path, downloading from Google Fonts if needed."""
    if font_dirs:
        local = _find_local_font(font_dirs, family, weight, style)
        if local is not None:
            return local

    cached = _cache_file(cache_dir, family, weight, style)
    if cached.is_file() and cached.stat().st_size > 0:
        return cached

    cache_dir.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    css_text: str | None = None
    for url in _google_fonts_css_urls(family, weight, style):
        req = urllib.request.Request(url, headers={"User-Agent": TTF_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                css_text = resp.read().decode("utf-8")
                break
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            continue

    if css_text is None:
        raise RuntimeError(
            f"failed to fetch Google Fonts CSS for {family!r} ({weight}, {style}): {last_error}"
        )

    # Pick the matching @font-face block URL.
    font_url: str | None = None
    for block in FONT_FACE_BLOCK_RE.findall(css_text):
        b_style = (CSS_STYLE_RE.search(block).group(1).lower() if CSS_STYLE_RE.search(block) else "normal")
        b_weight = int(CSS_WEIGHT_RE.search(block).group(1)) if CSS_WEIGHT_RE.search(block) else 400
        u_match = CSS_URL_SRC_RE.search(block)
        if not u_match:
            continue
        if b_style == style and b_weight == weight:
            font_url = u_match.group(2)
            break
        if font_url is None:
            font_url = u_match.group(2)

    if not font_url:
        raise RuntimeError(f"no downloadable font URL found in Google Fonts CSS for {family!r}")

    req = urllib.request.Request(font_url, headers={"User-Agent": TTF_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            font_bytes = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"failed to download font binary for {family!r}: {exc}") from exc

    tmp_path = cached.with_suffix(".tmp")
    tmp_path.write_bytes(font_bytes)
    tmp_path.replace(cached)
    return cached


def subset_font_bytes(
    font_path: Path,
    codepoints: set[int],
    *,
    fmt: str = "auto",
) -> tuple[bytes, str, str]:
    """Subset a font file to *codepoints* using fontTools.

    Returns ``(bytes, mime_type, css_format)``.
    Uses WOFF2 when *fmt* is ``"woff2"`` or (``"auto"`` and ``brotli`` is installed);
    otherwise outputs TTF.
    """
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont
    import logging

    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

    use_woff2 = fmt == "woff2" or (fmt == "auto" and has_brotli())
    if fmt == "woff2" and not has_brotli():
        raise RuntimeError("WOFF2 requested but brotli is not installed")

    options = Options()
    options.flavor = "woff2" if use_woff2 else None
    options.desubroutinize = True
    options.hinting = False
    options.layout_features = ["kern", "liga", "clig", "calt"]
    options.name_IDs = [1, 2, 4, 6, 16, 17]
    options.drop_tables += ["DSIG"]

    with TTFont(font_path) as font:
        subsetter = Subsetter(options=options)
        subsetter.populate(unicodes=sorted(codepoints))
        subsetter.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        data = buf.getvalue()

    if use_woff2:
        return data, "font/woff2", "woff2"
    return data, "font/ttf", "truetype"


def _font_cmap(font_path: Path) -> set[int]:
    from fontTools.ttLib import TTFont

    with TTFont(font_path, lazy=True) as font:
        cmap = font.getBestCmap()
        return set(cmap.keys()) if cmap else set()


def _system_stack_for(raw_family: str) -> str:
    families = parse_font_family_list(raw_family)
    lowered = [f.casefold() for f in families]
    if any("mono" in f for f in lowered):
        return SYSTEM_STACK_MONO
    if any("serif" in f and "sans-serif" not in f for f in lowered):
        # Preserve explicit CJK serif fallbacks if present, keeping Noto Serif before them.
        cjk = [f for f in families if "kr" in f.casefold() or "tc" in f.casefold() or "sc" in f.casefold() or "jp" in f.casefold()]
        if cjk:
            return "'Instrument Serif', 'Noto Serif', " + ", ".join(f"'{f}'" for f in cjk) + ", serif"
        return SYSTEM_STACK_SERIF
    return SYSTEM_STACK_SANS


def _strip_font_css(root: ET.Element) -> None:
    """Remove @font-face and @import rules from <style> blocks; drop empty <style> tags."""
    for parent in list(root.iter()):
        for child in list(parent):
            if _local_tag(child) != "style":
                continue
            css = child.text or ""
            css = FONT_FACE_BLOCK_RE.sub("", css)
            css = IMPORT_RULE_RE.sub("", css)
            if css.strip():
                child.text = "\n" + css.strip() + "\n"
            else:
                parent.remove(child)


def _insert_font_faces(root: ET.Element, rules: list[str]) -> None:
    if not rules:
        return
    defs = None
    for child in root:
        if _local_tag(child) == "defs":
            defs = child
            break
    if defs is None:
        insert_idx = 0
        for idx, child in enumerate(root):
            if _is_comment(child) or _local_tag(child) in {"title", "desc"}:
                insert_idx = idx + 1
            else:
                break
        defs = ET.Element(f"{{{SVG_NS}}}defs")
        root.insert(insert_idx, defs)

    existing_style = None
    for child in defs:
        if _local_tag(child) == "style":
            existing_style = child
            break

    block = "\n".join(rules)
    if existing_style is not None:
        prev = (existing_style.text or "").strip()
        existing_style.text = "\n" + block + ("\n" + prev if prev else "") + "\n"
    else:
        style_el = ET.Element(f"{{{SVG_NS}}}style")
        style_el.text = "\n" + block + "\n"
        defs.insert(0, style_el)


def apply_system_fonts(svg_text: str) -> str:
    """Strip embedded/remote font declarations and write clean fallback font stacks."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(svg_text, parser=parser)
    _strip_font_css(root)

    for elem in root.iter():
        if _is_comment(elem):
            continue
        if "font-family" in elem.attrib:
            elem.set("font-family", _system_stack_for(elem.attrib["font-family"]))

    serialized = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + serialized + "\n"


def embed_fonts(
    svg_text: str,
    *,
    cache_dir: Path | None = None,
    font_dirs: list[Path] | None = None,
    fmt: str = "auto",
) -> tuple[str, list[dict[str, object]]]:
    """Subset and embed only the actually used font faces as base64 @font-face.

    Returns ``(updated_svg_text, report)`` where *report* lists each embedded face.
    """
    cache = cache_dir if cache_dir is not None else default_cache_dir()
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(svg_text, parser=parser)

    runs = collect_text_runs(root)

    # Resolve which (family, weight, style) covers which codepoints.
    face_paths: dict[tuple[str, int, str], Path] = {}
    face_cmaps: dict[tuple[str, int, str], set[int]] = {}
    face_codepoints: dict[tuple[str, int, str], set[int]] = {}

    for stack, weight, style, text in runs:
        named = [f for f in stack if f.casefold() not in GENERIC_FAMILIES]
        if not named:
            continue
        remaining = {ord(ch) for ch in text if not ch.isspace()}
        if not remaining:
            continue
        # Always include space if the run has whitespace so kerning/metrics stay consistent.
        has_space = any(ch == " " for ch in text)
        for family in named:
            if not remaining:
                break
            key = (family, weight, style)
            if key not in face_paths:
                path = fetch_google_font(family, weight, style, cache, font_dirs=font_dirs)
                face_paths[key] = path
                face_cmaps[key] = _font_cmap(path)
            covered = remaining & face_cmaps[key]
            if covered:
                bucket = face_codepoints.setdefault(key, set())
                bucket.update(covered)
                if has_space and 0x20 in face_cmaps[key]:
                    bucket.add(0x20)
                remaining -= covered

    _strip_font_css(root)

    css_rules: list[str] = []
    report: list[dict[str, object]] = []
    for key in sorted(face_codepoints):
        family, weight, style = key
        codepoints = face_codepoints[key]
        data, mime, css_fmt = subset_font_bytes(face_paths[key], codepoints, fmt=fmt)
        b64 = base64.b64encode(data).decode("ascii")
        css_rules.append(
            f"@font-face {{ font-family: '{family}'; font-style: {style}; "
            f"font-weight: {weight}; src: url(data:{mime};base64,{b64}) format('{css_fmt}'); }}"
        )
        report.append(
            {
                "family": family,
                "weight": weight,
                "style": style,
                "glyphs": len(codepoints),
                "format": css_fmt,
                "bytes": len(data),
            }
        )

    _insert_font_faces(root, css_rules)
    serialized = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + serialized + "\n", report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed subsetted base64 @font-face rules for used fonts in an SVG."
    )
    parser.add_argument("source", type=Path, help="Input .svg file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Output .svg path (default: <source-stem>.fonts.svg, or overwrite with --in-place)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input .svg file in place",
    )
    parser.add_argument(
        "--system-fonts",
        action="store_true",
        help="Omit embedded fonts and write system fallback font stacks instead",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="External cache directory for downloaded fonts (default: ~/.cache/diagram-design/fonts)",
    )
    parser.add_argument(
        "--font-dir",
        type=Path,
        action="append",
        default=[],
        help="Local directory of .ttf/.otf font files to search before downloading",
    )
    parser.add_argument(
        "--format",
        choices=("auto", "woff2", "ttf"),
        default="auto",
        help="Embedded font format (auto picks woff2 if brotli is installed, else ttf)",
    )
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    if args.in_place and args.output is not None:
        print("error: pass either an output path or --in-place, not both", file=sys.stderr)
        return 2

    try:
        source_text = args.source.read_text(encoding="utf-8")
        if args.system_fonts:
            result = apply_system_fonts(source_text)
            report: list[dict[str, object]] = []
        else:
            result, report = embed_fonts(
                source_text,
                cache_dir=args.cache_dir,
                font_dirs=args.font_dir or None,
                fmt=args.format,
            )
    except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.in_place:
        target = args.source
    elif args.output is not None:
        target = args.output
    else:
        target = args.source.with_name(args.source.stem + ".fonts.svg")

    try:
        target.write_text(result, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write {target}: {exc}", file=sys.stderr)
        return 2

    if args.system_fonts:
        print(f"OK system-fonts {target} ({len(result)} bytes)")
    else:
        summary = ", ".join(
            f"{item['family']} {item['weight']} ({item['glyphs']} glyphs, {item['format']}, {item['bytes']}B)"
            for item in report
        )
        print(f"OK embedded {len(report)} face(s) into {target}: {summary or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
