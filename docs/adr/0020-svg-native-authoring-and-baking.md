# ADR 0020: Single-source standalone SVG authoring and baking

**Status:** Accepted  
**Date:** 2026-11-26

## Context

This fork (`deftai/diagram-design`) moves Diagram Design from HTML-wrapped diagrams to standalone `.svg` files that render consistently across browsers, Figma, Illustrator, Inkscape, resvg, and librsvg.

Migrating 52 diagram types raises an authoring question: should each diagram have two files (a token/class SVG source compiled into a shipped baked SVG), or a single standalone `.svg` file?

Two constraints govern the choice:

1. **Brand onboarding must keep working.** `references/style-guide.md` is the single source of truth for semantic roles (`paper`, `ink`, `muted`, `accent`, `link`, font families). Onboarded projects and saved client profiles (`references/profiles.md`) replace those tokens once, and every newly generated diagram inherits them.
2. **File count and agent simplicity matter.** Claude Desktop rejects skill ZIPs over 200 files (`scripts/build-claude-skill-zip.py`), and installed agents should be able to author one self-contained `.svg` file directly without maintaining paired source/build artifacts.

## Decision

Keep **one authoring source per diagram**: a single standalone `.svg` file whose elements carry resolved presentation attributes (`fill`, `stroke`, `font-family`, `font-size`, `font-weight`, etc.) and no `var()` or class-dependent paint.

Two packaged helper scripts support authoring, migration, and portability without introducing a second source file:

- `skills/diagram-design/scripts/svg_bake.py`: resolves CSS rules (class selectors, `:root` custom properties, `var()`, inline `style`, inherited body/root paint, and `text-transform`) into SVG presentation attributes and strips paint `<style>` rules. Authors and migration jobs can either write baked presentation attributes directly (matching how 45 of 52 existing types already write SVG markup by resolving semantic roles from `references/style-guide.md`) or draft with `:root` tokens and CSS classes and bake in place with `svg_bake.py --in-place`.
- `skills/diagram-design/scripts/svg_fonts.py`: inspects the characters and font faces actually used in an `.svg`, downloads Google Fonts into an external cache (`~/.cache/diagram-design/fonts`, never committed), subsets each used face to the used glyphs with `fonttools`, and embeds base64 `@font-face` rules (WOFF2 when `brotli` is available, else TTF) inside `<defs><style>`. Passing `--system-fonts` removes embedded fonts and writes clean fallback font stacks instead.

## Alternatives considered

### Dual-file token source (`.src.svg` -> build -> `.svg`)

Rejected because:

- Doubling every example file would break the 200-file Claude Desktop skill ZIP ceiling and require a separate freshness check on every PR.
- Agents generating user diagrams would have to manage two files and a mandatory build step even for simple diagrams whose presentation attributes are already trivial to emit from `references/style-guide.md`.
- Brand onboarding already resolves tokens at generation time via `references/style-guide.md` rather than via a runtime stylesheet shared across generated diagrams.

## Consequences

- Every converted type ships standalone `.svg` files directly in `skills/diagram-design/assets/` (`example-<type>.svg`, `-dark.svg`, `-full.svg`), with no companion `.src.svg` or `.html` wrapper.
- `skills/diagram-design/assets/template.svg` replaces `template.html` for converted types, moving the eyebrow and title into a 4px-grid top band inside the SVG `viewBox`.
- During the incremental migration, tooling (`self_check.py`, `verify_geometry.py`, `export_png.py`, `export_pdf.py`, `lint-skin.py`, `lint-render.py`, `verify-docs-sync.py`, `screenshot_catalog.py`, `build-claude-skill-zip.py`, and `assets/index.html`) runs in dual mode so both converted `.svg` types and unconverted `.html` types stay green in CI.
- No `plugin.json` version changes.
