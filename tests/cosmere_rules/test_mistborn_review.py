"""Source-review integrity; synthetic cases contain no private rules content."""
from copy import deepcopy
import pytest
from systems.cosmere.rules import mistborn_authoring as authoring
from systems.cosmere.rules.mistborn_review import validate_source_review, outcomes_hash


def fixture():
    old = {'id':'mistborn.rule.example','version':'1.1.0','audit_status':'blocked',
           'automation':'gm_adjudicated','parameters':{'verification_gaps':['gap']},
           'citations':[{'artifact_id':'mistborn.handbook','page_start':20,'page_end':20}]}
    new = deepcopy(old); new.update(version='1.1.1',audit_status='verified')
    new['parameters'].pop('verification_gaps')
    row = {'id':old['id'],'pack_id':'cosmere.mistborn.core','outcome':'verified',
           'reason':'Checked against the cited source.','citations':old['citations']}
    original={'manifest':{'bundle_hash':'baseline'},'packages':[{'id':row['pack_id'],'records':[old]}],
              'coverage':{'records':[{'id':old['id'],'review_state':'human_verification_required'}]}}
    current={'manifest':{'source_review':{'release_version':'1.1.1','baseline_bundle_hash':'baseline',
               'outcomes_hash':outcomes_hash([row])}},
             'packages':[{'id':row['pack_id'],'records':[new]}],
             'coverage':{'records':[{'id':old['id'],'automation':'gm_adjudicated','review_state':'verified'}],
                         'source_review':{'records':[row],'counts':{'verified':1},'related_record_ids':[]}}}
    return original,current


def test_review_accepts_source_verification_without_promoting_automation():
    validate_source_review(*fixture())


@pytest.mark.parametrize('mutation',['missing','duplicate','extra','hash','baseline','counts',
                                    'calculated','status','changed','empty_reason','coverage'])
def test_review_refuses_incomplete_or_inconsistent_proof(mutation):
    old,new=fixture(); proof=new['coverage']['source_review']; rows=proof['records']; rec=new['packages'][0]['records'][0]
    if mutation=='missing': rows.clear()
    if mutation=='duplicate': rows.append(deepcopy(rows[0]))
    if mutation=='extra': rows.append({**rows[0],'id':'mistborn.rule.other'})
    if mutation=='hash': new['manifest']['source_review']['outcomes_hash']='wrong'
    if mutation=='baseline': new['manifest']['source_review']['baseline_bundle_hash']='wrong'
    if mutation=='counts': proof['counts']={'removed':1}
    if mutation=='calculated': rec['automation']='calculated'
    if mutation=='status': rec['audit_status']='blocked'
    if mutation=='changed': rec['parameters']['new_rule']=9
    if mutation=='empty_reason': rows[0]['reason']=''
    if mutation=='coverage': new['coverage']['records'][0]['review_state']='human_verification_required'
    if mutation!='hash': new['manifest']['source_review']['outcomes_hash']=outcomes_hash(rows)
    with pytest.raises(authoring.MistbornAuthoringError): validate_source_review(old,new)


def test_still_blocked_requires_a_question_and_remains_blocked():
    old,new=fixture(); row=new['coverage']['source_review']['records'][0]
    row.update(outcome='still blocked',question='Which table policy applies?')
    new['packages'][0]['records'][0]['audit_status']='blocked'
    new['coverage']['records'][0]['review_state']='human_verification_required'
    new['coverage']['source_review']['counts']={'still blocked':1}
    new['manifest']['source_review']['outcomes_hash']=outcomes_hash([row])
    validate_source_review(old,new)
    row.pop('question'); new['manifest']['source_review']['outcomes_hash']=outcomes_hash([row])
    with pytest.raises(authoring.MistbornAuthoringError): validate_source_review(old,new)


def test_removed_record_cannot_survive_or_name_a_missing_replacement():
    old,new=fixture(); proof=new['coverage']['source_review']; row=proof['records'][0]
    row['outcome']='removed'; row['replacement_id']='mistborn.rule.replacement'
    proof['counts']={'removed':1}; new['manifest']['source_review']['outcomes_hash']=outcomes_hash([row])
    with pytest.raises(authoring.MistbornAuthoringError,match='still present'): validate_source_review(old,new)
    new['packages'][0]['records'].clear(); new['coverage']['records'].clear()
    with pytest.raises(authoring.MistbornAuthoringError,match='replacement'): validate_source_review(old,new)
    replacement=deepcopy(old['packages'][0]['records'][0]); replacement.update(id=row['replacement_id'],version='1.1.1',audit_status='verified')
    new['packages'][0]['records'].append(replacement)
    new['coverage']['records'].append({'id':replacement['id'],'review_state':'verified','automation':'gm_adjudicated'})
    validate_source_review(old,new)

    replacement['automation']='calculated'
    new['coverage']['records'][0]['automation']='calculated'
    with pytest.raises(authoring.MistbornAuthoringError,match='replacement changed automation'):
        validate_source_review(old,new)


@pytest.fixture(scope='module')
def installed_review():
    import json
    from systems.cosmere.rules.paths import registry_path
    path=registry_path().parent
    if not (path/'mistborn/manifest.json').is_file(): pytest.skip('private reviewed bundle is not installed')
    manifest=json.loads((path/'mistborn/manifest.json').read_text(encoding='utf-8'))
    if 'source_review' not in manifest: pytest.skip('private source-review release is not installed')
    return path,authoring.load_compiled_bundle(path/'mistborn',path/'mistborn_coverage.json')


def test_installed_review_covers_every_original_blocker_and_current_record(installed_review):
    path,bundle=installed_review
    original=authoring.load_compiled_bundle(path/'mistborn',path/'mistborn_coverage.json',baseline=True)
    validate_source_review(original,bundle)
    assert len(bundle['coverage']['source_review']['records'])==252
    assert sum(bundle['coverage']['source_review']['counts'].values())==252
    assert len({r['id'] for p in bundle['packages'] for r in p['records']})==bundle['manifest']['record_count']
    assert bundle['coverage']['summary']['human_verification_record_count']==bundle['coverage']['source_review']['counts']['still blocked']


def test_installed_review_retains_all_original_pins_and_stormlight_bytes(installed_review,tmp_path):
    import json
    from systems.cosmere.rules.compiler import compile_registry,canonical_json
    from systems.cosmere.rules.registry import load_registry
    from systems.cosmere.rules.schema import to_dict
    path,bundle=installed_review; version=bundle['manifest']['source_review']['release_version']
    authored=json.loads((path/'authoring.json').read_text(encoding='utf-8'))
    baseline=deepcopy(authored); baseline['packs']=[p for p in baseline['packs'] if not(p['id'].startswith('cosmere.mistborn.') and p['version']==version)]
    target=tmp_path/'baseline.json'; target.write_bytes(compile_registry(baseline))
    old,new=load_registry(target),load_registry(path/'registry.json')
    assert set(old.pack_versions)<set(new.pack_versions)
    for identity,pack in old.pack_versions.items():
        assert canonical_json(to_dict(pack))==canonical_json(to_dict(new.pack_versions[identity]))
    assert old.packs['cosmere.stormlight.legacy']==new.packs['cosmere.stormlight.legacy']
    assert old.packs['cosmere.core']==new.packs['cosmere.core']


@pytest.mark.parametrize('era',['era-1','era-2'])
def test_installed_review_resolves_new_bindings_and_retains_adapted_tables(installed_review,era):
    from systems.cosmere.rules.registry import load_registry
    from systems.cosmere.rules.profiles import package_pin,resolve_profile
    from systems.cosmere.rules.schema import CosmereRulesBinding,to_dict
    path,bundle=installed_review; registry=load_registry(path/'registry.json')
    version=bundle['manifest']['source_review']['release_version']
    packs={p.id:p for p in registry.pack_versions.values() if p.version==version}
    binding=CosmereRulesBinding(package_pin(registry.packs['cosmere.core']),
        (package_pin(packs['cosmere.mistborn.core']),package_pin(packs['cosmere.mistborn.'+era])),
        'scadrial',(era,))
    selected=registry.resolve_binding(binding)
    assert [p.version for p in selected[1:]]==[version,version]
    profile=resolve_profile({'rules_binding':to_dict(binding)},registry=registry,spoiler_scope='gm')
    assert profile.binding==binding
    # The existing private import adapter distinguishes reference tables from rollable tables.
    old=next(p for p in registry.pack_versions.values() if p.id=='cosmere.mistborn.core' and p.version=='1.1.0')
    current={r.id:r for r in packs['cosmere.mistborn.core'].records}
    for record in old.records:
        if record.id.startswith('mistborn.table.'):
            assert current[record.id].kind==record.kind
            assert current[record.id].parameters==record.parameters
