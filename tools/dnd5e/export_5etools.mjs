// Export fully-resolved 5etools data for the D&D 5e system.
//
// Runs on a dev machine only (like tools/ingest_cosmere.py): it needs a
// 5etools-src checkout because it uses 5etools' OWN loaders, which resolve
// `_copy`/`_mod`/`_versions`, expand magic variants, merge subraces and inline
// class/subclass features. Re-implementing that in Python would be a lossy
// port of ~2k lines of JS; driving the real thing keeps the data faithful.
//
//   node tools/dnd5e/export_5etools.mjs <path-to-5etools-src> <out-dir>
//
// Then build the runtime database with:
//   python tools/dnd5e/build_dnd5e_db.py <out-dir>
//
// The output is a directory of `<kind>.json` files, each a JSON array.

import fs from "fs";
import path from "path";
import url from "url";

const [srcArg, outArg] = process.argv.slice(2);
if (!srcArg || !outArg) {
	console.error("usage: node export_5etools.mjs <5etools-src> <out-dir>");
	process.exit(2);
}
const src = path.resolve(srcArg);
const out = path.resolve(outArg);
fs.mkdirSync(out, {recursive: true});

// 5etools resolves `./data/...` relative to the process cwd.
process.chdir(src);
const imp = (rel) => import(url.pathToFileURL(path.join(src, rel)).href);
for (const f of ["parser", "utils", "utils-ui", "render", "render-dice", "hist", "utils-dataloader", "utils-config", "filter", "utils-brew"]) {
	await imp(`js/${f}.js`);
}
const nodeUtil = await imp("node/util.js");
nodeUtil.patchLoadJson();

const readRaw = (rel) => JSON.parse(fs.readFileSync(path.join(src, "data", rel), "utf-8"));

// Strip the loader's private bookkeeping that is either huge (`_fullEntries`
// duplicates `entries`) or meaningless outside the 5etools page runtime.
const DROP_KEYS = new Set(["_fullEntries", "_textTypes", "_isMutEntity", "hasFluff", "hasFluffImages",
	"_tmpSourcesInit", "_tmpClasses", "_tmpRaces", "_tmpBackgrounds", "_tmpFeats", "_tmpOptionalfeatures", "_tmpGroups"]);
const clean = (obj) => JSON.parse(JSON.stringify(obj, (k, v) => DROP_KEYS.has(k) ? undefined : v));

const write = (kind, arr) => {
	const data = clean(arr);
	fs.writeFileSync(path.join(out, `${kind}.json`), JSON.stringify(data));
	console.log(`${kind}: ${data.length}`);
};

// --- resolved via 5etools loaders ---
const cls = await DataUtil.class.loadJSON();
write("class", cls.class);
write("subclass", cls.subclass);

for (const prop of ["spell", "monster", "race", "background", "feat", "optionalfeature", "item"]) {
	write(prop, await DataLoader.pCacheAndGetAllSite(prop));
}

// --- straightforward files with no inheritance worth resolving ---
const cd = readRaw("conditionsdiseases.json");
write("condition", cd.condition || []);
write("disease", cd.disease || []);
write("status", cd.status || []);
write("action", readRaw("actions.json").action || []);
write("skill", readRaw("skills.json").skill || []);
write("sense", readRaw("senses.json").sense || []);
write("language", readRaw("languages.json").language || []);
write("variantrule", readRaw("variantrules.json").variantrule || []);
const ib = readRaw("items-base.json");
write("itemProperty", ib.itemProperty || []);
write("itemType", ib.itemType || []);
write("itemMastery", ib.itemMastery || []);
write("legendaryGroup", readRaw("bestiary/legendarygroups.json").legendaryGroup || []);
write("charoption", readRaw("charcreationoptions.json").charoption || []);

// Source metadata (abbreviation -> full name, and edition) for the UI filter.
const sources = Object.entries(Parser.SOURCE_JSON_TO_FULL).map(([json, full]) => ({
	source: json,
	full,
	abv: Parser.sourceJsonToAbv(json),
	date: Parser.SOURCE_JSON_TO_DATE?.[json] || null,
}));
write("source", sources);

// Every `{@tag}` 5etools knows, with the source it implies when a reference
// omits one (`{@spell fireball}` is the PHB spell) and the page it links to.
// The Python renderer + reference resolver read this instead of a hand-copied map.
fs.writeFileSync(path.join(out, "_tags.json"), JSON.stringify(
	Renderer.tag.TAGS.map(t => ({tag: t.tagName, defaultSource: t.defaultSource || null, page: t.page || null})),
));

fs.writeFileSync(path.join(out, "_meta.json"), JSON.stringify({
	version: JSON.parse(fs.readFileSync(path.join(src, "package.json"), "utf-8")).version,
	exported: new Date().toISOString(),
}));
console.log("done ->", out);
