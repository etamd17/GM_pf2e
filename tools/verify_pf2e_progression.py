#!/usr/bin/env python3
"""Dev-only: verify the 10 author-encoded PF2e classes' proficiency progression
in class_matrix.py against the authoritative Foundry `pf2e` system data.

NOT run in CI -- it needs the local FoundryVTT pf2e system + the `fvtt` CLI.
Reads the `classes` + `class-features` packs (the class doc gives L1 base ranks
and which features a class gains at which level; the features give the rank
increases -- explicitly via ActiveEffectLike for class weapon features, or by
the standard feature-name convention for the generic save/perception/armor/
spellcasting expertise markers, whose rank the pf2e system code applies).

Foundry ranks are 0-4 (untrained..legendary); class_matrix uses 0/2/4/6/8
(2x). We compare on the class_matrix scale and print a discrepancy report.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, tempfile, glob

CLASSES = ['gunslinger', 'inventor', 'magus', 'summoner', 'psychic',
           'thaumaturge', 'animist', 'exemplar', 'commander', 'guardian']

PF2E = os.path.expanduser('~/Library/Application Support/FoundryVTT/Data/systems/pf2e')
X2 = lambda r: int(r) * 2  # Foundry rank -> class_matrix scale

# Generic expertise/mastery marker features -> (class_matrix field, rank). The
# pf2e system applies these by feature presence at the class's grant level.
NAME_RANK = {
    'fortitude expertise': ('fortitude', 4), 'juggernaut': ('fortitude', 6), 'greater juggernaut': ('fortitude', 8),
    'reflex expertise': ('reflex', 4), 'lightning reflexes': ('reflex', 4), 'evasion': ('reflex', 6), 'greater evasion': ('reflex', 8),
    'will expertise': ('will', 4), 'resolve': ('will', 6), 'greater resolve': ('will', 8), 'fortitude': ('fortitude', 4),
    'perception expertise': ('perception', 4), 'vigilant senses': ('perception', 4),
    'perception mastery': ('perception', 6), 'perception legend': ('perception', 8), 'incredible senses': ('perception', 8),
    'expert spellcaster': ('spell', 4), 'master spellcaster': ('spell', 6), 'legendary spellcaster': ('spell', 8),
}
ARMOR_RE = re.compile(r'\b(light|medium|heavy|unarmored)\s+(?:armor|defense)\s+(expertise|mastery|legend)', re.I)
RANK_WORD = {'expertise': 4, 'mastery': 6, 'legend': 8, 'legendary': 8}
ATTACK_PATH_RE = re.compile(r'proficiencies\.attacks\.([a-z\-]+)')
DEF_PATH_RE = re.compile(r'proficiencies\.defenses\.([a-z]+)')
SAVE_PATH_RE = re.compile(r'proficiencies\.saves\.([a-z]+)')


def unpack(pack, out):
    if os.path.isdir(out) and glob.glob(out + '/*.json'):
        return
    os.makedirs(out, exist_ok=True)
    subprocess.run(['fvtt', 'package', 'unpack', pack, '--id', 'pf2e', '--type', 'System',
                    '--in', os.path.join(PF2E, 'packs'), '--out', out],
                   check=True, capture_output=True, text=True)


def load(d):
    return {json.load(open(f)).get('name', ''): json.load(open(f)) for f in glob.glob(d + '/*.json')}


def feature_increases(feat):
    """(field, rank) increases a class-feature applies, on the class_matrix scale."""
    out = []
    name = (feat.get('name') or '')
    nl = name.lower().strip()
    sys = feat.get('system', {})
    # explicit ActiveEffectLike rank-set rules (class weapon features)
    for r in (sys.get('rules') or []):
        if r.get('key') == 'ActiveEffectLike' and isinstance(r.get('path'), str) and r['path'].endswith('.rank'):
            try:
                rank = X2(r.get('value'))
            except (TypeError, ValueError):
                continue
            p = r['path']
            m = ATTACK_PATH_RE.search(p)
            if m:
                grp = m.group(1)
                # SKIP weapon-group-specific proficiencies (e.g.
                # simple-firearms-crossbows) -- class_matrix has a flat
                # simple/martial/advanced model and can't represent them, so a
                # diff here is a modeling artifact, not a rank bug.
                if grp in ('simple', 'martial', 'advanced', 'unarmed'):
                    out.append((grp, rank))
                continue
            m = DEF_PATH_RE.search(p) or SAVE_PATH_RE.search(p)
            if m:
                out.append((m.group(1), rank)); continue
            if 'perception' in p:
                out.append(('perception', rank))
            elif 'spell' in p:
                out.append(('spell', rank))
            elif 'classDC' in p or 'class-dc' in p:
                out.append(('class_dc', rank))
    # name-convention markers (generic expertise/mastery/legend)
    for key, (field, rank) in NAME_RANK.items():
        if nl == key or nl.startswith(key + ' ') or (' (' in nl and nl.split(' (')[0] == key):
            out.append((field, rank))
    am = ARMOR_RE.search(name)
    if am:
        out.append((am.group(1).lower(), RANK_WORD[am.group(2).lower()]))
    # class DC marker: "<Class> Expertise" / "<Class> Mastery" (not weapon/armor/save/perc)
    if re.search(r'\bexpertise\b', nl) and not any(w in nl for w in ('weapon', 'armor', 'defense', 'fortitude', 'reflex', 'will', 'perception', 'spell')):
        out.append(('class_dc', 4))
    return out


def foundry_curve(cls_doc, feats):
    """{field: {level: rank}} from the class L1 base + granted-feature increases."""
    s = cls_doc.get('system', {})
    curve = {}
    base = {}
    st = s.get('savingThrows', {}); base.update({'fortitude': X2(st.get('fortitude', 0)),
                                                 'reflex': X2(st.get('reflex', 0)), 'will': X2(st.get('will', 0))})
    base['perception'] = X2(s.get('perception', 0))
    at = s.get('attacks', {}); base.update({k: X2(at.get(k, 0)) for k in ('unarmed', 'simple', 'martial', 'advanced')})
    df = s.get('defenses', {}); base.update({k: X2(df.get(k, 0)) for k in ('unarmored', 'light', 'medium', 'heavy')})
    if s.get('classDC') is not None:
        base['class_dc'] = X2(s.get('classDC') or 0)
    if s.get('spellcasting'):
        base['spell'] = X2(s.get('spellcasting'))
    for f, r in base.items():
        curve.setdefault(f, {})[1] = r
    # leveled grants
    for entry in (s.get('items') or {}).values():
        lvl = entry.get('level'); fname = entry.get('name', '')
        if not lvl or lvl < 2:
            continue
        feat = feats.get(fname)
        if not feat:
            continue
        for field, rank in feature_increases(feat):
            cur = curve.setdefault(field, {})
            # keep the highest rank seen at-or-before this level coherent; record the bump
            cur[lvl] = max(rank, cur.get(lvl, 0))
    return curve


def rank_at(curve_field, level):
    r = 0
    for lv in sorted(curve_field):
        if lv <= level:
            r = max(r, curve_field[lv])
    return r


def cm_curve(cm, cls):
    """class_matrix {field: {level: rank}} from base_proficiencies + CLASS_PROGRESSION."""
    base = None
    for cand in ('CLASS_BASE_PROFICIENCIES', 'CLASS_DATA', 'CLASSES'):
        pass
    # base lives in the dict keyed by class with 'base_proficiencies'
    import class_matrix as _cm
    src = None
    for v in vars(_cm).values():
        if isinstance(v, dict) and cls in v and isinstance(v[cls], dict) and 'base_proficiencies' in v[cls]:
            src = v; break
    base = (src[cls]['base_proficiencies'] if src else {})
    prog = _cm.CLASS_PROGRESSION.get(cls, {})
    curve = {}
    for f, r in base.items():
        curve.setdefault(f, {})[1] = r
    for lvl, bumps in prog.items():
        for f, r in bumps.items():
            curve.setdefault(f, {})[lvl] = r
    return curve


def main():
    cdir, fdir = '/tmp/pf2e_classes', '/tmp/pf2e_clsfeat'
    unpack('classes', cdir); unpack('class-features', fdir)
    classes = {k.lower(): v for k, v in load(cdir).items()}
    feats = load(fdir)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    FIELDS = ['perception', 'fortitude', 'reflex', 'will', 'unarmored', 'light', 'medium', 'heavy',
              'unarmed', 'simple', 'martial', 'advanced', 'class_dc', 'spell']
    report = {}
    for cls in CLASSES:
        doc = classes.get(cls)
        if not doc:
            report[cls] = {'error': 'class not found in Foundry data'}; continue
        fc = foundry_curve(doc, feats)
        cc = cm_curve(None, cls)
        # If the class has no class DC in Foundry (e.g. gunslinger), drop
        # class_dc from the compare -- a flat trained value in class_matrix is
        # harmless and not a progression bug.
        if (doc.get('system', {}).get('classDC') is None):
            fc.pop('class_dc', None); cc.pop('class_dc', None)
        # class_matrix uses spell_attack/spell_dc; fold to 'spell' for compare
        for sp in ('spell_attack', 'spell_dc'):
            if sp in cc:
                cc.setdefault('spell', {})
                for lv, r in cc[sp].items():
                    cc['spell'][lv] = max(cc['spell'].get(lv, 0), r)
        diffs = []
        for f in FIELDS:
            if f not in fc and f not in cc:
                continue
            # compare the rank-at-level curve at every level 1..20
            for L in range(1, 21):
                fr = rank_at(fc.get(f, {}), L)
                cr = rank_at(cc.get(f, {}), L)
                if fr != cr:
                    diffs.append({'field': f, 'level': L, 'class_matrix': cr, 'foundry': fr})
        # collapse contiguous same-diff runs into the first level they diverge
        collapsed = []
        seen = set()
        for d in diffs:
            key = (d['field'], d['class_matrix'], d['foundry'])
            # only report the FIRST level of each (field, cm, foundry) run
            prevkey = (d['field'], d['level'] - 1)
            if any(x['field'] == d['field'] and x['level'] == d['level'] - 1 and
                   x['class_matrix'] == d['class_matrix'] and x['foundry'] == d['foundry'] for x in diffs):
                continue
            collapsed.append(d)
        report[cls] = {'discrepancies': collapsed}
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
