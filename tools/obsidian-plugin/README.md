# Session Operations Sync (Obsidian plugin)

Drives the campaign website's live PF2e state from the GM's Obsidian vault, and
mirrors session events back into the vault. The other half of this integration
is `services/obsidian_sync.py` (routes), `core/obsidian_sync.py` (storage) and
the adapter at the bottom of `app.py`.

## This directory is the source of truth

`main.js` is hand-authored CommonJS. **There is no build step** and no
TypeScript -- editing `main.js` is editing the source, exactly as `static/*.js`
works elsewhere in this repo.

That diverges deliberately from the vault's own
`zz_notebook_conventions/Obsidian Website Sync Specification.md:358`, which
called for a TypeScript project. The repo has no toolchain, no `package.json`
and no bundler anywhere, and adding one for a single 1,600-line file would be
the only build step in the project. If that trade ever stops being worth it,
convert deliberately rather than by accident.

Until 2026-08-11 this plugin existed **only** inside the vault, which is not a
git repository -- a single unbacked copy, and it had already drifted a full
contract version ahead of the server. That is what `tests/test_obsidian_plugin_contract.py`
now guards.

## Files

| File | What it is |
|---|---|
| `main.js` | The plugin. Pane, commands, transport, vault writes. |
| `manifest.json` | Obsidian plugin manifest. Bump `version` on release. |
| `styles.css` | Pane styling. |

`data.json` is **not** here and must never be. Obsidian writes it next to
`main.js` in the vault, and it holds the live bearer token in plaintext.
`test_no_credentials_are_vendored` fails the build if one appears.

## Installing into the vault

Copy the three files into the vault's plugin folder, then reload Obsidian
(Ctrl+P, "Reload app without saving"):

```bash
cp tools/obsidian-plugin/{main.js,manifest.json,styles.css} "$VAULT/.obsidian/plugins/session-operations-sync/"
```

`$VAULT` is `C:\Users\Evan\Documents\Pathfinder Campaigns` on Windows and
`/Users/evananderson/Documents/Pathfinder Campaigns` on the Mac (the repo's
`obsidian_vault` symlink points at the latter).

## Pulling a vault-side edit back

If you edit in the vault -- easy to do, since that copy is the one Obsidian
actually loads -- copy it back before committing, or the repo silently falls
behind again:

```bash
cp "$VAULT/.obsidian/plugins/session-operations-sync/"{main.js,manifest.json,styles.css} tools/obsidian-plugin/
```

## Connecting it

1. On the website, open `/gm/integrations/obsidian` (GM-gated; nothing links to
   it yet, type the URL).
2. Generate a token. It is shown once and stored only as a SHA-256 digest.
3. In Obsidian: Settings -> Session Operations Sync. Enter the website URL, the
   campaign id and the token.

The token is campaign-scoped, and the server rejects it with a 409 whenever that
campaign is not the website's active table. One key per machine, so a lost
device can be revoked on its own.
