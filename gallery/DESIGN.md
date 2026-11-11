# RKMV Gallery: design system

A private photo archive for one school batch. The photographs are the interface; everything else gets out of the way.

## Direction
Quiet, flat, image-first. Adopted from the taste skill's *Accent ladder* app style (community kind), redrawn for a photo archive:
- **Why redrawn:** the dealt style was a SaaS dashboard (DM Sans, raspberry, KPI strip). A photo archive is object-led, not data-led, so the accent hue comes from the subject (the school's maroon blazers) and the page title takes a serif.
- **Kept from the style:** one hue at full strength only on small marks (primary button, active filter, face-box on hover), its pale tints for areas (selected chip, review card); flat surfaces with no shadow at rest; separation by a tone step or one 1px rule; sentence-case labels; tabular figures; no gradients, glows, glass, icon tiles or emoji.
- **Idea:** the archive behaves like a contact sheet that learns. The Review queue is a first-class screen: the system asks, the owner answers, accuracy improves.

## Tokens
All in `css/tokens.css` (light and dark). Use tokens only; no raw colours in components.
- Ground `--ground`, card `--surface`, step `--raised`, line `--border`/`--rule`.
- Accent: `--accent` (marks), `--accent-soft` (areas), `--accent-track`, `--accent-ink`.
- Graph communities `--c1..--c8` (one hue each, readable on `--surface`).
- Type: UI `DM Sans` (+ Noto Sans Bengali), titles `Newsreader` (+ Noto Serif Bengali). Sizes: title 28-40, section 18, body 14, small 12. Two faces, no monospace.
- Space: 24px gap/pad, 6px between photos, 44px minimum touch target, radius 8 (cards) / 6 (controls).

## Shell
Open plane: a 232px labelled sidebar on the ground (Gallery, People, Friends, Places, Review with an open-question count), no top bar on desktop. Under 860px: 48px top bar with the page name and a menu button that opens a full-screen sheet. Content has 16px margins on a phone.

## Screens
| Screen | Route | Job |
| --- | --- | --- |
| Gallery | `#/` | Justified rows of photos. Filter chips: scene, person, tags, year/day. Search box. |
| Lightbox | `#/photo/<id>` | Large photo, keyboard arrows, face boxes with names (toggle), tags, alt text, "more with these people". |
| People | `#/people` | Directory of named people with portrait and photo count; unknown clusters listed apart. |
| Person | `#/person/<id>` | Their photos, closest friends, places they appear. |
| Friends | `#/friends` | Force graph; node = person, edge = weighted closeness, colour = community. |
| Places | `#/places` | The 17 scenes and a date timeline. |
| Review | `#/review` | One question at a time, keyboard answers, undo, progress. |

## Rules
- Photos are never cropped by CSS in the grid (justified rows keep aspect ratio); lightbox uses `object-fit: contain`.
- Images carry a dominant-colour placeholder (`color` from the catalog) and `loading="lazy"`.
- Face boxes are drawn from normalised `bbox`; names appear only for `confirmed`/`high` faces. Candidates show a dashed outline and a "?" prompt only in Review.
- Accessibility: visible focus ring, 44px targets, `aria-current` on nav, `prefers-reduced-motion` honoured, text contrast AA in both themes, alt text from the tag pass.
- Privacy: no originals served, EXIF stripped, private access by default.

## Update: landing, Home feed, motion
- **Theme in force:** `css/gp-theme.css` (Google Photos style, blue accent) overrides `tokens.css`. The maroon values above are the original palette and are only used by components the theme does not restyle.
- **Signed out:** `js/landing.js` + `css/landing.css`. Drawn SVG only, so no photo of a student is ever public. It says plainly that photos need sign-in. The nav is hidden until sign-in (`body.is-landing`).
- **Home (`#/`)** is `views/discover.js`: shelves seeded per visit (Fresh picks, On this day, Spotlight person, a scene, a random day, Most recent) and a Shuffle button. `#/all` and any filtered `#/?...` open the dated grid.
- **Motion:** `css/motion.css` (view and tile fade-in, button press, loading shimmer), shelf and card entrances in `discover.css`, hero animation in `landing.css`, lightbox double-tap zoom. Everything is switched off under `prefers-reduced-motion`.
- **Header height:** `--header-h` is 72px (64px under 640px) so sticky month titles sit below the bar.
