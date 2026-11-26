# SVG-Native Migration Guide

This guide documents the per-type conversion checklist established by the Architecture pilot (ADR 0020) and classifies the remaining 51 visual types (plus variants and gallery pages) by conversion difficulty.

## Shared tooling (already built)

- `skills/diagram-design/assets/template.svg`: standalone SVG template with a 96px top band (eyebrow + title on the 4px grid), accessible `<title>`/`<desc>` first-child contract, prefixed IDs, paper background, and arrow markers in `<defs>`.
- `skills/diagram-design/scripts/svg_bake.py`: resolves `<style>` rules, class selectors, `:root` custom properties, `var()`, inline `style`, inherited body/root paint, and `text-transform` into SVG presentation attributes (`fill`, `stroke`, `font-family`, `font-size`, `font-weight`, etc.) and splits `rgba()` into hex + `*-opacity`. Reuses CSS parsing from `export_svg.py`.
- `skills/diagram-design/scripts/svg_fonts.py`: subsets and embeds only the actually used Google Fonts faces as base64 `@font-face` (WOFF2 when `brotli` is available, else TTF), caching downloads under `~/.cache/diagram-design/fonts` (never in the repo). Supports `--system-fonts` to write fallback font stacks instead.
- Dual-mode verification and export: `self_check.py`, `verify_geometry.py`, `export_png.py`, `export_pdf.py`, `lint-skin.py`, `lint-render.py`, `verify-docs-sync.py`, `screenshot_catalog.py`, `build-claude-skill-zip.py`, and `assets/index.html` all accept both `.svg` (converted types) and `.html` (unconverted types).

## Per-type conversion checklist

Follow these steps for each diagram type PR:

1. **Inspect existing examples and type verifiers.**
   - Read `skills/diagram-design/assets/example-<type>.html`, `-dark.html`, `-full.html`, plus any lettered variants (`data-parent-type="<type>"` in `assets/index.html`).
   - Check if a type-specific verifier exists (`scripts/verify-<type>.py` and `scripts/test-verify-<type>.py`). Update its file glob/target paths to accept `.svg` alongside `.html` (or `.svg` directly once all variants of that type convert).
2. **Convert minimal light and minimal dark to `.svg`.**
   - If the source uses `<style>` classes or `var()`, run `python3 skills/diagram-design/scripts/svg_bake.py example-<type>.html example-<type>.svg` as a starting point.
   - Add the 4px-grid top band inside `<svg>` for the eyebrow (`font-size="8"`, `font-weight="500"`, `letter-spacing="0.18em"`, `Geist Mono`, uppercase text) and title (`font-size="28"`, `font-weight="400"`, `letter-spacing="-0.02em"`, `Instrument Serif`). Grow `viewBox` height on the 4px grid and shift diagram coordinates cleanly.
   - Keep the accessible contract: `role="img"`, `aria-labelledby="<slug>-title <slug>-desc"`, `<title>` first child, `<desc>`, and prefixed `<defs>` IDs (`<slug>-arrow`, `<slug>-dots`, etc.).
   - Use canonical style-guide colors (split `rgba()` into hex + `fill-opacity`/`stroke-opacity`, use `none` instead of `transparent`) and canonical fallback font stacks (`'Instrument Serif', 'Noto Serif', serif`, `'Geist', system-ui, sans-serif`, `'Geist Mono', ui-monospace, monospace`).
   - Ensure zero `var()` references and zero class-dependent paint remain. Do not commit font binaries.
3. **Convert `-full.html` to `-full.svg`.**
   - Render the editorial header (eyebrow, title, subtitle), framed diagram panel, varied-width summary cards (`<rect>` + `<circle>` dot + `<text>` lines sized within `self_check.py`'s `0.60em` sans width budget), and footer rule/captions as native SVG primitives on the 4px grid.
   - Ensure every `font-size` stays on the `references/output-spec.md` type ramp (canonical sizes `8, 9, 12, 16, 28, 32, 40`, Geist regular dense annotation `7..11`, Geist Mono `7..11`, Geist 600 heading `14`).
4. **Delete converted `.html` files and update references.**
   - Remove the converted `example-<type>*.html` files.
   - Add `data-ext="svg"` to the type's `<button>` in `skills/diagram-design/assets/index.html`.
   - Update `skills/diagram-design/references/type-<type>.md` example paths from `.html` to `.svg`.
   - Remove any deleted file from `scripts/lint-skin-baseline.txt` once its `.svg` replacement passes `lint-skin.py` cleanly.
   - If the type has an entry in `references/output-spec.md` `### Registered legacy sizes`, update the filename to `.svg` (or remove the row if sizes were brought onto the ramp).
5. **Refresh canonical screenshot if needed and run validation gates.**
   - Run `self_check.py`, `verify_geometry.py`, `lint-skin.py --all --baseline`, `verify-docs-sync.py`, `test-verify-docs-sync.py`, the type's `verify-<type>.py` + `test-verify-<type>.py`, and `build-claude-skill-zip.py --check`.
   - Render all converted `.svg` files to PNG with Playwright (`export_png.py`) AND `resvg` (or `rsvg-convert`) and visually inspect them for text clipping, alignment, and parity.
   - Refresh `docs/screenshots/<type>.png`, `docs/screenshots/manifest.json`, and `docs/screenshots/thumbs/<type>.webp` when canonical sources change so `verify-screenshot-freshness.py` and `build-readme-thumbs.py --check` stay green.

## Taste gate changes for SVG-native types

- Replaced HTML-wrapper checks (`min-width` equals viewBox width, local `overflow-x: auto` wrapper, `@media print` rule, `</html>` completeness) with SVG-native checks: well-formed XML ending in `</svg>`, no `var()` references, no class-dependent paint, and eyebrow/title inside the 4px-grid top band.
- Retained unchanged: accessible SVG contract (`role="img"`, `aria-labelledby`, `<title>` first child, `<desc>`, prefixed IDs), all six connector rules (`verify_geometry.py`), viewBox bounds and text-fit width budget (`self_check.py`), style-guide palette and contrast contract (`lint-skin.py`, `verify-contrast.py`), and type ramp (`verify-docs-sync.py`).

## Remaining types grouped by difficulty

### Group 1: Static inline-styled (easiest, direct copy of Architecture pilot)

Already write presentation attributes directly in `<svg>` with zero SVG CSS classes. Convert in batches by adding the top band, converting `-full` cards, prefixing defs IDs, and porting any type verifier to read `.svg`:

- **Architecture variants:** `datalake`, `architecture-containment`, `import-drawio`, `import-mermaid`, `import-excalidraw`, `import-plantuml`
- **Architecture delta:** `architecture-delta` (has `verify-architecture-delta.py`)
- **Core structural & flow:** `flowchart`, `sequence` (+ `sequence-oauth`), `state` (+ `state-lifecycle`), `er`, `steps` (`verify-steps.py`), `timeline`, `git-graph` (`verify-git-graph.py`), `swimlane`, `nested`, `tree` (+ `tree-block-decomposition`, `verify-block-registry.py`), `org-chart`, `layers`, `venn`, `pyramid`, `fishbone`, `kanban`, `journey`, `service-blueprint` (`verify-service-blueprint.py`), `deployment`, `dependency` (+ `arc-diagram`, `verify-arc-diagram.py`), `uml-class`, `story-map`, `db-schema`, `sbgn` (`verify-sbgn.py`), `requirement-traceability` (`verify-requirement-traceability.py`), `event-modeling` (`verify-event-modeling.py`), `schedule-network` (`verify-schedule-network.py`)
- **Quantitative & chart types:** `quadrant` (+ `quadrant-consultant`), `radar`, `polar` (`verify-polar.py`), `bar` (+ `dumbbell`, `verify-dumbbell.py`), `waterfall` (`verify-waterfall.py`), `treemap` (+ `marimekko`, `verify-treemap.py`, `verify-marimekko.py`), `heatmap` (`verify-heatmap.py`), `line` (+ `slopegraph`, `ridgeline`, `streamgraph`, `bump` and their verifiers), `gantt`, `scatter` (+ `bubble`, `beeswarm`, `box-plot` and their verifiers), `sankey` (`verify-sankey.py`), `wardley`, `unit-grid` (`verify-unit-grid.py`)
- **Data platform (inline-styled):** `high-level` (+ `high-level-vertical`)

### Group 2: Static class-styled (medium, use `svg_bake.py`)

Use `<style>` class selectors and `:root` custom properties inside the diagram `<svg>` (11 to 25 classes each). Run `svg_bake.py` to bake CSS rules and `var()` into presentation attributes, then add the top band and `-full` SVG cards:

- `loop` (11 classes; check `verify-docs-sync.py` `check_loop_station_origin`, which currently looks for `class="station"` - update that check or pass `--keep-classes` until converted)
- `data-flow` (13 classes; legacy font sizes in `output-spec.md`)
- `it-state` (15 classes; currently in `lint-skin-baseline.txt`)
- `dp-security-matrix` (21 classes)
- `process` (23 classes; legacy font sizes in `output-spec.md`)
- `dp-integration` (25 classes; currently in `lint-skin-baseline.txt`)

### Group 3: `foreignObject` (harder, replace HTML inside SVG)

- `medallion` (12 classes + `<foreignObject>` HTML prose/tags inside tier columns). Must replace every `<foreignObject>` block with native SVG `<rect>`, `<text>`, and `<tspan>` elements so the file renders in Figma, Illustrator, Inkscape, and resvg (which do not render HTML inside `foreignObject`).

### Group 4: Builder-generated axonometric types (medium, update generator scripts)

Static examples are generated by Python scripts checked by CI (`--check`). Update the generator scripts to emit standalone `.svg` directly:

- `exploded` (`scripts/build-exploded-examples.py`, `verify-exploded.py`): `exploded`, `exploded-phone`, `exploded-unboxing`, `exploded-ai-stack`, `exploded-keyboard`
- `axonometric-plan` (`scripts/build-axonometric-plan-examples.py`, `verify-axonometric-plan.py`): `axonometric-plan`, `axonometric-plan-campus`, `axonometric-plan-coffee-shop`, `axonometric-plan-warehouse`

### Group 5: Terminal variant (medium, convert HTML chrome to SVG)

- `assets/template-terminal.html` and `assets/example-loop-terminal.html` (11 classes + HTML window chrome). Replace the HTML terminal window bar and traffic-light dots with SVG `<rect>`/`<circle>`/`<text>` window chrome and bake classes with `svg_bake.py`.

### Group 6: Motion / animated (hardest, architectural decision needed)

10 animated examples + `assets/template-motion.html`, backed by `verify-motion.py` and `verify-semantic-motion.py`:

- `example-paved-road-animated.html`
- `example-policy-trace-animated.html`
- `example-queue-animated.html`
- `example-exploded-phone-animated.html`, `example-exploded-unboxing-animated.html`, `example-exploded-ai-stack-animated.html`, `example-exploded-keyboard-animated.html`
- `example-axonometric-plan-campus-animated.html`, `example-axonometric-plan-coffee-shop-animated.html`, `example-axonometric-plan-warehouse-animated.html`

These embed an interactive JavaScript playback controller (`data-diagram-controls`), HTML buttons, `aria-live` status, and `<noscript>` fallback. Decide in a dedicated ADR whether animated diagrams become static-frame standalone SVGs with CSS `@keyframes` (non-interactive `reveal`/`loop` only) or retire HTML motion playback altogether.

### Group 7: Gallery, icons, and final HTML removal (final PR)

- `skills/diagram-design/assets/index.html`: remains the GitHub Pages interactive gallery viewer (or switches default extension from `.html` to `.svg` once all types convert).
- `skills/diagram-design/assets/icons.html` and `scripts/build-icons.py`: icon specimen page; keep as documentation/gallery or emit standalone SVG icon sheets.
- Final cleanup: delete `template.html`, `template-dark.html`, `template-full.html`, remove HTML-fallback branches from `export_svg.py` / `self_check.py` / `verify-docs-sync.py`, and rewrite `SKILL.md` and `references/export.md` fully for SVG-only authoring.
