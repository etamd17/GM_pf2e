# Player experience planning decisions

Updated: 2026-11-08

This records the completed multiple-choice discovery. The user approved [the implementation plan](implementation-plan.md) and authorized Stage 0 review/task briefs on 2026-11-08. The documentation branch updates task priorities; no product code or deployment is changed.

## Confirmed direction

- Shift the site's development focus toward player character management and immersion.
- Retain the GM encounter tracker, encounter builder, rules lookup, and generators across Pathfinder, D&D, Cosmere, and Mistborn.
- Obsidian owns GM preparation. The website owns player characters and live encounters. Results sync back to Obsidian.
- First complete player release covers Pathfinder 2e and D&D together; Cosmere and Mistborn follow.
- First release covers both character building/management and at-the-table play, accepting a larger scope.
- Players manage their characters within the rules; the GM unlocks level-ups and can override changes.
- Supported rules apply automatically, explain what changed, and allow corrections. Choices and rulings prompt for input.
- The primary encounter experience is an integrated Play view: the player's combat stats, actions, resources, and conditions alongside public initiative and encounter events.
- First-release inventory includes full everyday management (containers, equipped effects, investment/attunement, consumables, currency, and player transfers), plus crafting, shops, purchasing, and shared party loot.
- Default player combat visibility: party HP and conditions; visible enemy names, rough health, and GM-revealed effects. Hidden enemies remain hidden.
- Players can view and edit their own characters between sessions, including while another campaign is active. Live encounter controls stay tied to their active session.
- D&D first-release coverage includes both 2014 and 2024 rules, with an explicit campaign rules selection.
- Guided character creation and leveling should be complete for supported content: prerequisites, choices, stat previews, spells, archetypes/multiclassing, and GM-controlled retraining/respec.
- Automation should resolve supported targeted actions end to end, including rolls/saves, resource spending, damage/healing, and effects, prompting for choices and GM rulings.
- Design priority is tablets and laptops. Phones receive a compact companion view.
- Support both digital rolls and entered physical-dice results, followed by supported rules/effect resolution.
- Room loading means NPCs and creatures listed in an Obsidian location note, not real-world players joining the site.
- First-release Obsidian launch/control integration covers both PF2e and D&D, including conditions and live updates.
- Sync read-only live character/encounter summaries and a session history of actions, effects, loot, and outcomes back into Obsidian.
- Room launch previews explicit creatures and effects from structured note fields, then applies them together on launch.
- Pause new wiki and VTT/map work in the implementation plan. Retain maintenance and the encounter tracker, encounter builder, lookup, and generator tools. No other chat or workstream has been stopped automatically.
- Players browse the allowed item catalog; purchases require GM approval.
- Players manage crafting projects. The GM advances campaign downtime; the site applies supported prerequisites, checks, costs, and completion rules.
- Players reserve/request shared loot; the GM or a designated party quartermaster finalizes distribution.

## Current question round

All discovery questions have been answered and the full plan is approved. Stage 0 review/task briefing is underway in this documentation branch; product implementation has not started. [The index](README.md) identifies ready tasks and [the baseline](baseline.md) records newly found existing work.

## Discovery baseline

- Source baseline is remote-verified main at 7aeb9549 (2026-11-02). Stage 0 also read production configuration and the public health endpoint; it did not test authenticated gameplay.
- The main GM_pf2e working folder is an older dirty remediation branch. Do not use it as the implementation baseline or overwrite its work.
- Existing sheets, builders, leveling, inventories, and player encounter views provide substantial foundations for PF2e, D&D, and Cosmere. Mistborn is implemented under Cosmere.
- Obsidian Session Operations already launches room encounters from saved stages or explicit creature rosters, includes the current party, sends live commands, and mirrors snapshots/events into the vault.
- An unmerged Obsidian v0.9 branch includes substantial recovery/preflight work. PE-03 reviews it before new P6 recovery code. Authored starting effects, D&D parity and complete all-origin history still require implementation assessment.
- PF2e invested-item state remains browser-local. Anytime character access and D&D private-draft parity are in the approved first-release scope.
- Some repository guidance and queue entries are stale; verify code and current task reports before treating a documented feature as missing or unfinished.
- Between-session restrictions currently affect both character reads and writes. D&D storage is campaign-scoped, while PF2e also relies on live caches and locks; relaxing access policy alone is insufficient for PF2e.
- D&D provides reusable mutation services, but a logged dice roll is not the selected complete targeted-action resolution pipeline.
- Existing D&D code supports 2014 and 2024 rules; the user selected both for first-release coverage.
- Existing PF2e loot awards and D&D item-instance identifiers provide foundations, but the requested economy needs persistent shops, crafting projects, shared stash, and coordinated item/currency transactions. PF2e container membership and investment are not yet fully server-owned.

## Planning assumptions to make explicit

- Preserve existing imports and characters; migrations must not reset play state or ownership.
- Use existing campaign accounts and character assignments. Real-player joining was not the room-loading request.
- Anytime character editing means online access outside a live session, not an offline editing mode or simultaneous live encounters for multiple campaigns.
- Unsupported rules and custom content need an explicit manual/GM path; do not silently claim complete automation.
- All selected features belong to the first complete release, delivered through internal implementation milestones rather than silently postponed to a later version.
