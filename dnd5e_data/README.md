# dnd5e_data (not in git)

Everything here except this file and `VERSION` is gitignored. The repository is public
and the 5etools data is mostly non-SRD Wizards of the Coast content, so it is never
committed. `systems/dnd5e/paths.py` says where the app looks for it.

Build it on a dev machine (needs node and a 5etools-src checkout at the tag in `VERSION`):

```bash
git clone --depth 1 --branch v2.37.0 https://github.com/5etools-mirror-3/5etools-src.git ../5etools-src
node tools/dnd5e/export_5etools.mjs ../5etools-src dnd5e_data/resolved
python tools/dnd5e/build_dnd5e_db.py
cp dnd5e_data/resolved/_tags.json dnd5e_data/_tags.json
python tools/dnd5e/fetch_images.py          # optional art, ~216 MB, incremental
```

Install it on the server: `python tools/dnd5e/bundle.py`, then upload the parts in
`dist/dnd5e/` at `/dnd5e/admin/data` (site admin), core part first. On Railway it lands
in `/data/dnd5e_data` (the volume) and survives deploys.
