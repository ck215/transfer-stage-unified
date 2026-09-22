# Web view design brief — "instrument console" (owner ruling 2026-09-22)

The owner wants a fresher, modern look with deliberate motion. If the Web view
is good enough it becomes the primary frontend; Tk and Qt persist as backups.
Follow the `frontend-design` skill (its SKILL.md text is appended below) and
this plan, which was reviewed against the skill's list of generic defaults.

## Subject
A precision transfer stage in a condensed-matter research lab: stepper/DC
probes and a chuck positioner, a heater, an SMC100 rotator, and a screen-region
"red percent" recorder used as a force proxy. The operator is a researcher at
a bench who watches numbers (stage x/y/z, red %, temperature, angle) and needs
one control that is never in doubt: FULL STOP. Vernacular: bench
instrumentation — engraved panels, tabular readouts, a physical stop button.

## Tokens
Colour (exactly these; no others except derived opacities):
  base #1f242b · panel #2a3038 · ink #ece9e2 · muted #8c95a3
  signal #d92a2a (stop, latch, fault — nothing else) · trace #e0b34c (live
  readouts, plot line, indicator lamps ON)
Type: IBM Plex Sans, self-hosted (download the woff2 for 400/500/600 into
  static/fonts/ from a public source; fall back to system-ui). Readouts:
  `font-variant-numeric: tabular-nums`, larger size, weight 500. Sentence
  case everywhere. No all-caps labels, no letter-spaced eyebrows, no
  monospace for small labels, no "→" on buttons, no middle-dot metadata.
Layout:
  +- status rail (sticky) -------------------------------------- [STOP] -+
  |  Stepper  x 0  y 0  z 0    Red 0.00 %  change 0.00     [Setup] [log] |
  +----------------------------------------------------------------------+
  |  modules, rack-style: readouts large and quiet; controls compact;     |
  |  modules separated by rules and a left grouping bar, no drop shadows  |
  +----------------------------------------------------------------------+
  The rail is the hero: one readout group per open model showing its key
  numbers (position for probes, red% and change for Red Percent,
  temperature/setpoint for the heater, angle for the rotator) drawn from
  `state.values` — derive "key numbers" generically: the readonly elements
  of the model's FIRST schema section. The STOP is a round mushroom button
  (signal red, a darker ring, a subtle inset) — the one bold element; when
  latched it reads "Clear" and pulses once. Setup is a left drawer: open at
  boot, withdraws on launch, reopens from the rail's Setup button. The event
  log is a bottom tray, collapsed to one line by default.
Principles: numbers first; one red; quiet everything else; motion answers
  actions; copy says what happens ("Start run", "Stop run", "Clear stop").

## Motion
ONE orchestrated moment: on launch the drawer slides away (240 ms, ease-out)
and the rail's readout groups fade in with a 60 ms stagger. After that, only
motion that answers an action: a refused command shakes its status line
(200 ms), the latch pulses once when set, a collapse slides. Respect
`prefers-reduced-motion` (all durations 0). No hover transitions on every
card, no fade-up on every section.

## Contract that must not change
- app.js still renders every element type in schema.ELEMENT_TYPES; every
  entry travels with every command; needs_confirm / refused / failed handling
  unchanged; gating incl. entries; stale marker; region picker on /api/screen.
- styles.css uses only var() from /api/theme.css plus layout values; the
  theme's palette is what /api/theme.css serves, so CHANGE THE PALETTE IN
  station/palette.py (the six tokens above map: BACKGROUND=base,
  SURFACE=panel, TEXT=ink, MUTED=muted, plus add SIGNAL and TRACE and serve
  them as --signal/--trace; ROLES: danger=signal, go/info/neutral become
  quiet variants of panel/ink; warning=trace) — that keeps Tk/Qt coherent.
- All existing tests in tests/station/test_view_web_*.py keep passing or are
  updated with the reason; the "no colour literal outside the theme" test
  stays strict.
---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Complete terms in LICENSE.txt
---

# Frontend Design

Approach this as the design lead at a design studio known for giving every client a distinct visual identity that is not mistaken for anyone else's. This client has already rejected proposals that felt cliché or templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about palette, typography, and layout that are specific to this brief, and take aesthetic risk if justified.

## Ground your designs in the subject matter

If the brief does not identify what the product or subject matter is, identify it yourself before designing, and confirm with the client. You can come up with one concrete subject, the design's audience, and the design's primary job, as a proposal. If there's any information in your memory about the client's preferences or context about what they're building, use that as a hint. The subject's industry, subject matter, materials, and vernacular are where distinctive visual choices come from — a design for a toy for girls aged 8–11 will be very aesthetically different from a dashboard for financial analysts. Build with the brief's real content and subject matter throughout.

## Design principles

For web designs, the hero is the first thing viewers will see. Open with the most characteristic thing in the subject's world, in the form that is most appropriate: a headline, an image, an animation, a live demo, an interactive moment, or other treatments. Be deliberate with your choice: a big number with a small label, supporting stats, and a gradient accent is the default treatment, so only use it if that's truly the best option.

Typography carries the personality of the page. You don't need a different typeface for display or headline text and body content: use one family or two, and if two, make them clearly distinct.

Choose your typefaces deliberately, not the default families you would reach for on any other project, and set a clear type scale following the default guidance of The Elements of Typographic Style with intentional weights, widths, and spacing. When type is used as a headline or visual element, use the type treatment itself as an active part of the design, not a neutral delivery vehicle for the content.

Default to line lengths of less than 80 characters. Serif typefaces can have slightly longer line lengths; give serif body text slightly more line-height than a sans-serif.

Avoid these default typographic treatments; they are the commonest tells of a generated page:
- Accenting just a single word or phrase in a headline, like putting one word in italic/bold or a different color.
- Using all caps for labels.
- Adding unnecessary typographic labels above content.

Visual structure is information. Structural devices like outlines, borders, numbering, eyebrows, dividers, labels, etc., encode useful information about the content rather than decorate it. Many generic designs use numbered markers (01 / 02 / 03), but that's only appropriate if the content actually is a sequence — like a stepped process or a timeline. Before adding numbered markers, check the content really is a sequence.

Use non-user-triggered motion sparingly and deliberately, only to draw attention. A single orchestrated moment — one page-load sequence or one reveal — lands better than scattered effects; fade-and-slide-up entrances on each section and hover transitions on every card are the generic default and read as AI-generated. Motion that answers a person's action (opening, expanding, confirming) is welcome when it shows what changed.

Consider written content carefully. Often a design brief may not contain real content, and it's up to you to come up with copy and placeholder content. Copy can make a design feel as templated as the design itself. See the below section on writing for more guidance.

## Process: plan, review against the brief, build, critique

For calibration, AI-generated design right now clusters around some traits:
1. a warm cream background (near #F4F1EA) with a high-contrast serif display and a terracotta or warm-clay accent (often near #D97757 — Anthropic's own Claude-interaction accent, so on a user's brief it reads as a tell);
2. a near-black background with a single bright acid-green or vermilion accent;
3. a broadsheet-style layout with hairline rules, zero border-radius, and dense newspaper-like columns;
4. the SaaS-card kit: content chopped into identical rounded cards, one border-radius on everything regardless of hierarchy, the same soft grey shadow (rgba(0,0,0,.1)) under each, and gradient washes as decoration;
5. template chrome that appears whatever the subject: a tracked-out ALL-CAPS eyebrow label above every heading; meta strings joined with middle dots ('A · B · C'); labels built as 'WORD — fragment' with a spaced em dash; tinted near-black (#0B0B0B, #111) standing in for black; a monospace face for small data labels; a '→' appended to link and button text.

All traits are legitimate for some briefs, but they are defaults rather than choices, and they appear regardless of subject. Where the brief pins down a visual direction, follow it exactly — the brief's own words always win, including when it asks for one of these looks. Where it leaves an axis free, don't spend that freedom on one of these defaults. As with a hired human designer, there's often a careful balance between doing what you're good at and taking each project as a chance to experiment and learn.

Work in two passes. First, brainstorm a short design plan based on the client's design brief: create a compact token system with color, type, layout, and principles.
- Color: describe the core base palette as 4–6 named hex values.
- Type: the typefaces and their roles.
- Layout: a layout concept, using one-sentence prose descriptions and ASCII wireframes to ideate and compare. Include alignment guidance; should the content be left aligned, center aligned, justified?
- Principles: the high-level guidance for what makes this page unique.

Then review that plan against the brief before building: if any part of it reads like the generic default you would produce for any similar page (work through a similar prompt to see if you arrive somewhere similar) rather than a choice made for this specific brief — revise that part, say what you changed and why. Only after you've confirmed the relative uniqueness of your design plan should you start to write the code, following the revised plan.

When writing the code, be careful of structuring your CSS selector specificities. It's easy to generate CSS classes that cancel each other out (especially with a type-based selector like .section and an element-based selector like .cta). This can happen often with padding/margin between sections.

## Restraint and self-critique

Spend your boldness in one place. Let one element be the memorable thing, keep everything around it quiet and disciplined, and cut any decoration that does not serve the brief. Build to a quality floor without announcing it: responsive down to mobile, visible keyboard focus, reduced motion respected, visually accessible, harmonious color palettes. Critique your own work as you build, taking screenshots to review if your environment supports it — a picture is worth 1000 tokens. Consider Chanel's advice: before leaving the house, take a look in the mirror and remove one accessory. Human creatives have memory and always try to do something new, so if you have a space to quickly jot down notes about what you've tried, it can help you in future passes.

## More on writing in design

Words appear in a design for one reason: to make it easier to understand and use. They are design content, not decoration. Bring the same intentionality and minimalism to copywriting that you would bring to spacing and color. Before writing anything, ask what the design needs to say, and how it can best be said to help the person navigate the experience.

Write from the end user's perspective. Name things by what users will understand in simple language, not by how the system is built. A user manages notifications, not webhook config. Describe what something is or does in plain terms rather than selling it. Being specific and legible to new users is always better than being clever.

Use active voice as default. A CTA says exactly what happens when it is used: "Save changes," not "Submit." An action keeps the same name through the whole flow, so the button that says "Publish" produces a toast that says "Published." The vocabulary of an interface is the signposting for someone navigating the product. Cohesion and consistency are how people learn their way around.

Treat failure and emptiness as moments for direction, not mood. Explain what went wrong and how to fix it, in the interface's voice rather than a person's. Errors don't apologize, and they are never vague about what happened. An empty screen is an invitation to act.

Keep the tone conversational: plain verbs, sentence case, no filler, with tone matched to the brand and the audience. Let each written element do exactly one job.
