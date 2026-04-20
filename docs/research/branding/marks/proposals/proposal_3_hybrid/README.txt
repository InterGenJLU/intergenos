PROPOSAL 3 — HYBRID (adaptive)

Two SVG assets, rendered based on target size:
  - intergenos_icon_simple.svg : no Q/T dips, stroke 32 (for 16-48 px)
  - intergenos_icon_full.svg   : full Q/R/S/T, stroke 10 (for 64+ px)

Small sizes render cleanly because they use a simpler geometry and
a proportionally thicker stroke. Large sizes preserve the Q and T
details that make the mark unique.

Both versions read as 'heartbeat pulse' — visually consistent.
Similar approach to Apple/Google/most major OS icons.

Best at: every size
Tradeoff: two assets instead of one
