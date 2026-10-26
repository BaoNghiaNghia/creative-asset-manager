# Concept output(3).zip · layout and stitching acceptance criteria

The 20 uploaded 1536×1024 RGB examples represent five slogan groups and
approximately four-five typographic treatments per slogan. The target CAM
masters keep a transparent RGBA background at 2048×1024, rather than baking
the example's white studio background into finished artwork.

| Exact slogan | Block | Red script + block | Serif arc + script | Script + small caps | Retro |
| --- | --- | --- | --- | --- | --- |
| CHAOS COORDINATOR | CHAOS / COORDINATOR | Chaos / COORDINATOR | CHAOS / Coordinator | Chaos / COORDINATOR | Optional sixth treatment |
| HAMBURGER HELP ME | HAMBURGER / HELP ME | Hamburger / HELP ME | HAMBURGER / Help me | Hamburger / HELP ME | HAMBURGER / HELP ME |
| Y'ALL NEED JESUS | Y'ALL / NEED JESUS | Y'all Need / JESUS | Y'ALL NEED / Jesus | Y'all Need / JESUS | Y'ALL / NEED / JESUS |
| STATE OF MIND | STATE / OF MIND | State of / MIND | STATE / of Mind | State / OF MIND | STATE / OF MIND |

## Acceptance

- Rendering six distinct files must succeed for each group above, without a network request.
- Bold sans capital shapes dominate the block layouts, rather than thin outline fonts.
- Reference 03 red script sits *above* blue block; reference 04 top is a visibly arched serif.
- Reference 05 uses blue handwritten lettering over small upright blue capitals.
- Reference 06 uses chunky offset blue decorative serif, but never random glyphs.
- Stitch texture must show pronounced satin bundles and raised thread grooves at both full size and a 200px preview, while
  preserving the exact untextured glyph alpha.
- All images are straight-on typographic masters without hats, mockups or extra icons.
- Official font checksums and license files must pass before any image is written.
- Existing output versions remain immutable and can be viewed in CAM history.

Do not hardcode these slogans as templates. Recipes should generalize to new quotes
with line breaks chosen by phrase rhythm, especially short prepositions and contractions.