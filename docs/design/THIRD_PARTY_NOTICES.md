# Third-party notices (design-system refresh)

The design-system work (`docs/design/DESIGN_DIRECTION.md`) borrows patterns, and in a few places values, from the open-source projects below. The rule (section 3 of the plan):

- **MIT sources:** code or values may be copied if this notice is kept.
- **AGPL or proprietary sources:** nothing is copied; the idea is re-derived. SuperDesign (AGPLv3 plus an Enterprise clause) is used that way, so it has no entry here.
- **Industry-standard numbers** (a 4px grid, a 1.25 type ratio) need no attribution.

Each entry says what this repository takes from the project. A package that copies more adds a line here in the same PR.

## beUI

- Source: https://github.com/starc007/ui-components (https://beui.dev/)
- License: MIT, Copyright (c) 2026 Saurabh Chauhan
- Used: the easing curves `EASE_IN_OUT` and `EASE_DRAWER`, as `--ease-move` and `--ease-drawer` in `static/css/system.css`, and motion durations for press feedback.
- Not used: beUI Pro (pro.beui.dev), which is paid and is never copied.

## Bencho

- Source: https://github.com/cabralorenzo/bencho (https://bencho.dev/)
- License: MIT (free blocks only), Copyright (c) 2026 Lorenzo Cabra. Two of its sounds derive from cuelume (MIT, Daniel Belyi).
- Used: micro-interaction patterns, re-implemented in CSS.
- Not used: Bencho Pro blocks and sounds, which are under a proprietary licence that forbids redistribution, and are never copied.

## ui-layouts

- Source: https://github.com/ui-layouts/uilayouts
- License: MIT, Copyright (c) 2024 UI LAYOUT. Vendored shadcn code and dependencies there carry their own MIT licences.
- Used: layout patterns (accordion, segmented tabs, dialog and drawer, reorder list).

## better-design

- Source: https://github.com/marvkr/better-design
- License: MIT, Copyright (c) 2026 Marvin Kaunda
- Used: transition timing guidance (dialog 250/150 ms, capped here at 220/150 ms) and the UI quality checklist.
- Its transition CSS credits transitions.dev (https://github.com/Jakubantalik/transitions.dev, MIT). Any port of that CSS keeps the transitions.dev attribution and its copyright line from the upstream LICENSE file.
- Not used: the vendored animate-text skill, which has no licence, and the themes that imitate real brands.

## design-taste

- Source: https://github.com/madebymustafa/design-taste
- License: MIT, Copyright (c) 2026 Mustafa
- Used: design rules restated in our own words, such as exit curves and the restraint principles.

## motion-primitives

- Source: https://github.com/ibelick/motion-primitives (https://motion-primitives.com/docs)
- License: MIT, Copyright (c) 2024 ibelick. The file is `LICENCE.md`; the README's link to `/LICENSE.md` is broken.
- Used: motion patterns re-implemented in CSS and vanilla JS. No React code is copied.

---

### MIT License (text that applies to each entry above, with its own copyright line)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
