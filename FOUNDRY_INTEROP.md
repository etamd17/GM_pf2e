# Foundry VTT Interop — Design

> Status: **DESIGN ONLY** (not built). The Cosmere side of this app was built
> Foundry-aligned on purpose, so interop is mostly format/transport work, not a
> rewrite. This doc is the architecture + the menu of what we can do with it.

---

## 1. Why this is already close

The Cosmere data layer speaks Foundry's `cosmere-rpg` schema natively:

| Piece | What it does | Foundry relevance |
|---|---|---|
| `systems/cosmere/actor.py` `CosmereActor` | **Reads** the real cosmere-rpg actor schema (`system.attributes/defenses/resources/skills/deflect`) | This is literally how the ingested bestiary loads — it parses Foundry docs |
| `systems/cosmere/build.py` `to_actor_doc()` | **Emits** `{name, type:'character', system:{…}, items:[…]}` in that same schema | One step from a Foundry actor JSON |
| `tools/ingest_cosmere*.py` | Used `fvtt package unpack` (LevelDB → JSON) to import content | The inverse, `fvtt package pack`, compiles JSON back into a compendium |
| `from_actor_doc()` | Rebuilds a `CosmereBuild` from an actor doc | Inbound path (Foundry → app) already exists |

**Verified present (2026-06-09):** `fvtt` CLI v3.0.2 on PATH; Foundry `cosmere-rpg`
v2.0.5 installed locally; mined Radiant datasets = 80 surge talents + 91 order
talents + 10 surge powers (content the free system does **not** ship).

The gap between `to_actor_doc()` and a Foundry-importable actor is just the
**document envelope**: `_id`, `_stats`, `ownership`, `img`, `prototypeToken`,
`effects`, `flags`, and per-item `_id`s. That's a serializer, not a system.

---

## 2. The capability menu (what we could do)

Ordered by value-for-effort. Each is independently shippable.

### A. Character **export** → Foundry  *(small, highest immediate value)*
Build/level a PC in this app's guided wizard (which is genuinely nicer than
Foundry's for rules-guided creation + Radiant Ideals), then drop it into a
Foundry game. Output = an actor JSON Foundry's "Import Data" accepts, or a packed
compendium. **"Build here, play there."**

### B. Character **import** ← Foundry  *(small)*
Pull an existing Foundry Cosmere actor into this app (the inbound parser already
exists for adversaries via ingest; `from_actor_doc()` covers PCs). Lets a group
that started in Foundry adopt this app's tracker/builder without re-keying sheets.

### C. **Content module** — Stormlight Radiant pack  *(medium; arguably highest value to others)*
The 9 Radiant orders, 10 surge trees, spren-bond talents, paths, Singer forms,
and kits we mined are **not** in the free cosmere-rpg system. Compile them into
Foundry compendium Item docs via `fvtt package pack` → an installable module.
The hard part (verified rules + structured data) is done; this is format
conversion. (See IP caveat §6.)

### D. **Bestiary round-trip**  *(small)*
We ingested 142 adversaries *from* Foundry. Exporting our (possibly homebrewed)
adversaries back out is the same serializer as A, with `type:'adversary'`.

### E. **Whole-party / campaign export**  *(small, composes A)*
One click on a campaign → a folder of actor JSONs (or a mini-module) for the
whole table. Useful when a group migrates a campaign into Foundry.

### F. **Live two-way sync**  *(a real project)*
Run combat in this app's tracker and mirror HP / conditions / turn / injuries to
Foundry's canvas (or vice versa). A Foundry-side JS module bridges this app's
REST + SSE to Foundry's document hooks. High effort, high "wow", lots of edge
cases (conflict resolution, auth, who-owns-truth).

---

## 3. Architecture

A new package `systems/cosmere/foundry/` with a clean seam — **no app.py or web
code touches Foundry; it's pure data transform + a thin export route.**

```
systems/cosmere/foundry/
  envelope.py     # wrap a to_actor_doc() dict into a full Foundry document
                  #   (_id via a deterministic 16-char id, ownership, img,
                  #    prototypeToken, effects:[], flags, per-item _id)
  actor_io.py     # build  <-> foundry actor JSON   (export_actor / import_actor)
                  #   export_actor(build) -> dict     (wraps to_actor_doc)
                  #   import_actor(doc)   -> CosmereBuild  (wraps from_actor_doc)
  pack.py         # compile a list of docs into a LevelDB compendium via the
                  #   fvtt CLI (dev/CI-side only; mirrors ingest_cosmere.py)
  content.py      # radiant_talents/origins -> Foundry Item docs (the module)
  schema.py       # the cosmere-rpg version we target + a compat note
```

Data flow (export, Tier A/E):

```
CosmereBuild  --to_actor_doc()-->  actor-shape dict
              --envelope.wrap()-->  full Foundry actor document  (JSON)
                                     |
                     ┌───────────────┴────────────────┐
            "Download .json"                  pack.py + fvtt pack
            (Foundry: Import Data)            -> installable module / compendium
```

Data flow (import, Tier B):

```
Foundry actor JSON  --import_actor()-->  CosmereBuild  --_save_cosmere_pc()-->  campaign PC
```

**Web surface (Tier A/B/E):** add to the existing Cosmere PC sheet / roster:
- `GET /cosmere/pc/<id>/foundry.json` → `export_actor` + envelope, `Content-Disposition: attachment`. A "Export to Foundry" button.
- `POST /cosmere/import/foundry` (GM) → accept a pasted/uploaded Foundry actor
  JSON → `import_actor` → save as a campaign PC. An "Import from Foundry" form,
  mirroring the existing Pathbuilder import UX on the PF2e side.

Everything else (pack/module, live sync) is out-of-band tooling or a separate
Foundry-side artifact — it does not change the running web app.

---

## 4. Tier A/B detail — character round-trip

**The only real work is `envelope.py`.** `to_actor_doc()` gives the `system`
block and embedded `items`; the envelope adds what Foundry's document layer
needs:

```jsonc
{
  "name": "...", "type": "character",
  "_id": "<16-char base62>",            // deterministic from our uuid -> stable re-export
  "img": "icons/svg/mystery-man.svg",
  "system": { /* from to_actor_doc() */ },
  "items": [ { "_id": "<16>", "type": "...", "name": "...", "system": {...} }, ... ],
  "effects": [],
  "prototypeToken": { "name": "...", "actorLink": true, "disposition": 1 },
  "ownership": { "default": 0 },
  "flags": { "tableview": { "build": { /* cosmere_build, our re-edit stash */ } } },
  "_stats": { "systemId": "cosmere-rpg", "systemVersion": "2.0.5" }
}
```

Notes:
- Stash our `cosmere_build` under `flags.tableview` so a round-trip (export →
  edit in Foundry → re-import) can still reconstruct the guided build.
- `import_actor` reads `flags.tableview.build` if present (loss-less), else falls
  back to `from_actor_doc()` (parse the Foundry stats directly).
- **Test strategy:** golden round-trip — `import_actor(export_actor(build))`
  reproduces the same derived stats (Physical/Cog/Spir, Health, Deflect, skills).
  This is CI-safe (pure Python, no Foundry needed). A *manual* check imports one
  JSON into the real Foundry game once per schema bump.

---

## 5. Tier C detail — the Radiant content module

`content.py` maps our datasets → Foundry compendium Item docs:
- `radiant_talents.SURGE_TALENTS` / `ORDER_TALENTS` → `type:'talent'` items, with
  `system.prerequisites` rebuilt from our `{ideal|talent|text}` chain.
- talent **trees** → `type:'talent_tree'` docs (Foundry groups talents by tree).
- `radiant.SURGE_POWERS` → `type:'power'` items.
- `origins` paths / Singer forms / kits → their item types.

`pack.py` writes these to a `packs/` dir and shells `fvtt package pack --type
Module --id stormlight-radiant-content` → a LevelDB compendium + a `module.json`
manifest → drop the folder in `FoundryVTT/Data/modules/`, enable it, and the
orders/surges show up as compendia the free system lacks.

This is **dev/CI-side tooling**, not part of the web app — same shape as the
existing `tools/ingest_cosmere*.py`, just reversed.

---

## 6. Cross-cutting concerns

1. **Language boundary.** Foundry is JS/TS; our rules engine is Python. We do
   **not** port the engine. For A–E the engine stays authoritative and only the
   *data* crosses. Only Tier F (live sync) needs Foundry-side JS, and even then
   the Python (`build.py`/`radiant.py`/`origins.py`) is the reference spec.
2. **Version coupling.** Everything targets cosmere-rpg **v2.0.5**. Pin it in
   `schema.py`; on a Foundry-system bump, re-verify `to_actor_doc()` keys and the
   envelope. The golden round-trip test catches drift on our side; a one-time
   manual import catches Foundry-side changes.
3. **IP.** Talent/surge text is Brotherwise Games' IP. **Own-table use is fine.**
   A *publicly distributed* content module (Tier C) has real IP considerations —
   keep it private, or ship only the mechanical scaffolding (tree structure +
   prereqs) without the verbatim flavor text, or get permission. Tiers A/B/E
   move the *user's own* characters and carry no new IP exposure.
4. **Truth ownership (Tier F only).** Decide per-field who wins: this app owns
   live combat HP/conditions (its tracker is the table's surface); Foundry owns
   the canvas/tokens. A dirty-field + last-writer-with-version-vector scheme
   avoids fighting. Defer until A–E prove the value.

---

## 7. Recommended rollout

1. **POC: Tier A export** — `envelope.py` + `actor_io.export_actor` + the
   `/cosmere/pc/<id>/foundry.json` download + the golden round-trip test. Smallest
   slice that delivers "build here, play there." ~a focused session.
2. **Tier B import** — the inbound form (reuses `from_actor_doc`). Now it's a
   round-trip both ways.
3. **Tier E** — party/campaign export (loops Tier A).
4. **Tier C** — the Radiant content module (decide the IP stance first).
5. **Tier F** — live sync, only if the table actually wants to run across both.

**First commit when picked up:** `systems/cosmere/foundry/envelope.py` +
`actor_io.py` + `tests/test_foundry_roundtrip.py`. No app behavior changes until
the export route is wired, so it's a safe, isolated start.
