"""Offline record-corruption tests using existing generic fixtures, not EDA runs."""
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_intake import build_design_intake, render_design_intake
from virtuoso_design_agent.design_iteration import build_design_iteration, render_design_iteration
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.design_review import build_design_review, render_design_review
from virtuoso_design_agent.generic_simulation import render_generic_oa_testbench
from virtuoso_design_agent.models import RunRecord, TaskSpec
from virtuoso_design_agent.planner import build_plan
from test_generic_simulation import _inspection, _task
from test_measurement_iteration import measurement


def write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def rounds(tmp_path):
    original = _inspection()
    original['instance_parameters']['MN0']['w'] = '500n'
    original['placement'] = {'canonical_geometry': {'instances': [{'xy': [0, 0]}], 'wires': []}}
    probe = deepcopy(original)
    probe['instance_parameters']['MN0']['w'] = '475.00n'
    records = {}
    for number, stage in enumerate(('A', 'B', 'restore', 'C')):
        raw = _task().model_dump(mode='json')
        raw.update(id='fixture-' + stage)
        if stage == 'B':
            raw.update(operation='design.tune', objective={'metric': 'low_frequency_gain_db', 'goal': 'maximize'},
                       instance_parameter_space=[{'instance': 'MN0', 'parameter': 'w', 'values': ['475n']}])
            raw['safety']['allow_remote_write'] = True
        if stage == 'restore':
            raw.update(operation='parameters.apply', analysis=None, ac_sweep=None, generic_simulation=None, constraints=[],
                       instance_parameter_updates=[{'instance': 'MN0', 'parameters': {'w': '500n'}}])
            raw['safety'].update(allow_remote_write=True, allow_remote_compute=False)
        task = TaskSpec.model_validate(raw)
        tp = write(tmp_path / (stage + '-task.json'), raw)
        start = datetime(2026, 10, 23, tzinfo=timezone.utc) + timedelta(seconds=20 * number)

        def action(name, data, source='bridge_readback'):
            return {'action': name, 'status': 'succeeded', 'started_at': start.isoformat(),
                    'finished_at': (start + timedelta(seconds=1)).isoformat(),
                    'evidence_source': source, 'details': deepcopy(data)}

        actions = [action('schematic.inspect.before', probe if stage == 'restore' else original)]
        candidates = []
        selected = {'MN0': {'w': '475n'}} if stage == 'B' else {}
        if stage == 'restore':
            actions += [action('parameters.apply', {'requested_instance_parameters': {'MN0': {'w': '500n'}},
                                                   'confirmed_instance_parameters': {'MN0': {'w': '500n'}}}),
                        action('schematic.inspect.after', original)]
        else:
            folder = tmp_path / stage
            receipt = folder / 'input_from_oa.raw' / 'vda-input-manifest.sha256'
            receipt.parent.mkdir(parents=True)
            remote = '/data/xum/fixture/' + stage
            wrapper = render_generic_oa_testbench(task.generic_simulation, analysis='ac',
                remote_netlist_path=remote + '/vda_oa_netlist.scs', model_configuration='',
                ac_sweep=task.ac_sweep.model_dump(mode='json'))
            # These files intentionally contain fixture text, not physical simulation data.
            for name, data in {'input_from_oa.scs': wrapper, 'oa_netlist.scs': '// fixture only\n',
                               'oa_netlist_spectre.scs': 'simulator lang=spectre\n// fixture only\n',
                               'spectre.out': 'fixture only\n'}.items():
                (folder / name).write_bytes(data.encode())
            raw_hash, include_hash, wrapper_hash = [sha(folder / n) for n in
                ('oa_netlist.scs', 'oa_netlist_spectre.scs', 'input_from_oa.scs')]
            entries = {remote + '/job/input_from_oa.scs': wrapper_hash,
                       remote + '/netlist': raw_hash, remote + '/vda_oa_netlist.scs': include_hash}
            receipt.write_bytes(''.join(f'{digest}  {path}\n' for path, digest in entries.items()).encode())
            manifest = [{'path': p.relative_to(folder).as_posix(), 'size_bytes': p.stat().st_size, 'sha256': sha(p)}
                        for p in sorted(folder.rglob('*')) if p.is_file()]
            actual = '475.00n' if stage == 'B' else '500n'
            evidence = {'schematic_readback': {'target': task.target.model_dump(mode='json')},
                'netlist': {'source': 'eda_result', 'remote_path': remote + '/netlist', 'sha256': raw_hash,
                    'topology_consistency': 'matched', 'parameter_consistency': 'matched',
                    'parameter_bindings': [{'instance': 'MN0', 'oa_parameter': 'w', 'netlist_parameter': 'w',
                                            'oa_value': actual, 'netlist_value': actual}],
                    'spectre_include': {'remote_path': remote + '/vda_oa_netlist.scs', 'sha256': include_hash}},
                'testbench': {'sha256': wrapper_hash, 'analysis': 'ac',
                    'settings': task.generic_simulation.model_dump(mode='json'), 'operating_condition': None,
                    'executed_input_binding': {'source': 'bridge_readback', 'status': 'matched',
                        'receipt_path': str(receipt), 'receipt_sha256': sha(receipt), 'remote_input_files': entries}},
                'simulation': {'source': 'eda_result', 'artifact_manifest_complete': True, 'artifact_manifest': manifest,
                    'artifact_manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
            actions.append(action('simulation.candidate.1', {'evidence': evidence}, 'eda_result'))
            if stage == 'B':
                after = deepcopy(probe)
                after['confirmed_instance_parameters'] = {'MN0': {'w': actual}}
                actions.append(action('schematic.inspect.after', after))
            candidates = [{'index': 1, 'parameters': {}, 'instance_parameters': selected,
                           'metrics': {'low_frequency_gain_db': 1.0}, 'constraints': [], 'feasible': True,
                           'total_violation': 0.0, 'evidence_source': 'eda_result'}]
        run = RunRecord.model_validate({'task_id': task.id, 'plan_token': build_plan(task).confirmation_token,
            'adapter': 'virtuoso-bridge-subprocess', 'status': 'succeeded', 'started_at': start,
            'finished_at': start + timedelta(seconds=10), 'actions': actions, 'candidates': candidates,
            'selected_parameters': {} if candidates else None,
            'selected_instance_parameters': selected if candidates else None,
            'selected_metrics': {'low_frequency_gain_db': 1.0} if candidates else None})
        rp = write(tmp_path / (stage + '-run.json'), run.model_dump(mode='json'))
        records[stage] = (tp, rp)
    request = {'schema_version': 1}
    cp = tmp_path / 'completion.json'

    def bind():
        request.update(reviewed_task_sha256=sha(records['B'][0]), reviewed_run_sha256=sha(records['B'][1]))
        for label, stage in [('baseline', 'A'), ('restoration', 'restore'), ('final', 'C')]:
            tp, rp = records[stage]
            request[label] = {'task': tp.name, 'run': rp.name, 'task_sha256': sha(tp), 'run_sha256': sha(rp)}
        write(cp, request)
    bind()
    return records, cp, bind


def test_review_preserves_numeric_actual_and_consumes_explicit_restoration(rounds):
    records, completion, _ = rounds
    review = build_design_review(*records['B'], completion_path=completion)
    check = review['writeback']['parameter_checks'][0]
    assert check['actual'] == '475.00n' and check['requested'] == '475n' and check['matched']
    assert check['basis'] == 'declared_oa_si_numeric_binding'
    assert review['input_evidence'][0]['status'] == 'verified'
    assert review['completion']['status'] == 'restored_to_baseline_in_record'
    assert not review['completion']['selected_point_is_final_state']
    assert not review['completion']['current_remote_state_verified']
    assert review['recorded_selection']['instance_parameters']['MN0']['w'] == '475n'
    report, draft = suggest_design_next(*records['B'], completion_path=completion)
    assert report['status'] == 'restored_baseline_review_or_stop' and draft is None
    assert '不是当前 OA 状态' in render_design_review(review)


@pytest.mark.parametrize('case', ['changed', 'no_binding', 'string_field'])
def test_numeric_formatting_needs_corroborating_field_not_global_normalization(rounds, case):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text())
    if case == 'changed':
        raw['actions'][-1]['details']['confirmed_instance_parameters']['MN0']['w'] = '480n'
    elif case == 'no_binding':
        raw['actions'][1]['details']['evidence']['netlist']['parameter_bindings'] = []
    else:
        raw['candidates'][0]['instance_parameters']['MN0']['model'] = '001'
        raw['selected_instance_parameters']['MN0']['model'] = '001'
        raw['actions'][-1]['details']['confirmed_instance_parameters']['MN0']['model'] = '1'
    write(rp, raw)
    review = build_design_review(tp, rp)
    assert review['writeback']['status'] == 'readback_recorded_selection_unverified'


@pytest.mark.parametrize('case', ['receipt', 'wrapper', 'missing', 'manifest_escape', 'partial_binding', 'settings',
                                  'wrong_target', 'netlist_mismatch'])
def test_bad_retained_inputs_block_next_without_any_adapter(rounds, case, tmp_path):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text()); e = raw['actions'][1]['details']['evidence']
    receipt = Path(e['testbench']['executed_input_binding']['receipt_path'])
    if case == 'receipt':
        receipt.write_bytes(b'changed receipt\n')
    elif case == 'wrapper':
        receipt.parent.parent.joinpath('input_from_oa.scs').write_bytes(b'changed wrapper\n')
    elif case == 'missing':
        receipt.unlink()
    elif case == 'manifest_escape':
        e['simulation']['artifact_manifest'][0]['path'] = '../outside'
        e['simulation']['artifact_manifest_sha256'] = hashlib.sha256(json.dumps(
            e['simulation']['artifact_manifest'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    elif case == 'partial_binding':
        e['testbench'].pop('executed_input_binding')  # Receipt in manifest prevents a legacy downgrade.
    elif case == 'settings':
        e['testbench']['settings']['sources'][0]['dc_value'] += .01
    elif case == 'wrong_target':
        e['schematic_readback']['target']['cell'] = 'vda_other'
    else:
        e['netlist']['parameter_consistency'] = 'mismatch'
    write(rp, raw)
    review = build_design_review(tp, rp)
    assert review['execution']['incomplete_evidence'] and any(r['issues'] for r in review['input_evidence'])
    report, draft = suggest_design_next(tp, rp)
    assert report['status'] == 'reconcile_execution_first' and draft is None


@pytest.mark.parametrize('case', ['parameter', 'placement', 'literal', 'failed_restore', 'order', 'mutation', 'demo'])
def test_completion_rejects_state_drift_or_invalid_chain_even_with_updated_file_hashes(rounds, case):
    records, completion, bind = rounds
    stage = 'restore' if case == 'failed_restore' else 'A' if case == 'demo' else 'C'
    tp, rp = records[stage]; raw = json.loads(rp.read_text())
    if case == 'parameter':
        raw['actions'][0]['details']['instance_parameters']['MN0']['l'] = '31n'
    elif case == 'literal':
        raw['actions'][0]['details']['instance_parameters']['MN0']['w'] = '500.00n'
    elif case == 'placement':
        raw['actions'][0]['details']['placement']['canonical_geometry']['instances'][0]['xy'] = [1, 0]
    elif case == 'failed_restore':
        raw['status'] = 'failed'
    elif case == 'order':
        raw['started_at'] = '2026-10-23T00:00:00Z'
    elif case == 'mutation':
        raw['actions'].append({**raw['actions'][0], 'action': 'parameters.apply'})
    else:
        raw['adapter'] = 'deterministic-demo'
    write(rp, raw); bind()
    review = build_design_review(*records['B'], completion_path=completion)
    assert review['completion']['status'] == 'incomplete' and review['completion']['issues']
    assert suggest_design_next(*records['B'], completion_path=completion)[0]['status'] == 'reconcile_execution_first'


def test_completion_exact_hash_binding_and_reanchor_barrier(rounds, tmp_path):
    records, completion, bind = rounds
    report, draft = suggest_design_next(*records['B'], completion_path=completion,
        model_path=tmp_path/'not-read-model', policy_path=tmp_path/'not-read-policy', template_path=tmp_path/'not-read-task')
    assert report['status'] == 'recorded_state_requires_reanchor' and draft is None
    raw = json.loads(completion.read_text()); raw['final']['run_sha256'] = '0'*64; write(completion, raw)
    with pytest.raises(ValueError, match='SHA-256'):
        build_design_review(*records['B'], completion_path=completion)
    bind(); raw = json.loads(completion.read_text()); raw['reviewed_run_sha256'] = '0'*64; write(completion, raw)
    with pytest.raises(ValueError, match='exact reviewed'):
        build_design_review(*records['B'], completion_path=completion)


def test_cli_completion_outputs_are_local_nonoverwriting_and_never_mint_a_task(rounds, tmp_path):
    records, completion, _ = rounds
    output, markdown, old_task = [tmp_path/name for name in ('review.json', 'review.md', 'old-task.json')]
    args = [*map(str, records['B']), '--completion', str(completion)]
    assert main(['design-review', *args, '--output', str(output), '--markdown', str(markdown)]) == 0
    assert '已记录收尾' in markdown.read_text(encoding='utf-8')
    before = output.read_bytes()
    assert main(['design-review', *args, '--output', str(output)]) == 2 and output.read_bytes() == before
    old_task.write_bytes(b'keep existing draft')
    assert main(['design-next', *args, '--task-output', str(old_task)]) == 2
    assert old_task.read_bytes() == b'keep existing draft'


def test_legacy_absence_is_not_upgraded_to_a_verified_input_receipt(rounds):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text()); e = raw['actions'][1]['details']['evidence']
    e['testbench'].pop('executed_input_binding')
    e['simulation']['artifact_manifest'] = [p for p in e['simulation']['artifact_manifest']
                                          if not p['path'].endswith('vda-input-manifest.sha256')]
    write(rp, raw)
    report = build_design_review(tp, rp)
    assert [r['status'] for r in report['input_evidence']] == ['not_recorded']
    assert not report['execution']['incomplete_evidence']


def test_normal_cleanup_is_not_a_failed_execution_or_a_new_byte_verification(rounds, monkeypatch):
    records, completion, _ = rounds
    job = records['B'][0].parent / 'B'
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda path: False if path == job else original_is_dir(path))
    review = build_design_review(*records['B'])
    assert review['input_evidence'][0]['status'] == 'recorded_not_replayed'
    assert not review['execution']['incomplete_evidence']
    report, _ = suggest_design_next(*records['B'])
    assert report['status'] != 'reconcile_execution_first'
    assert any(r['kind'] == 'input_replay_boundary' for r in report['recommendations'])
    # Explicit replay/closure requires actual files; metadata alone cannot complete it.
    receipt = job/'input_from_oa.raw'/'vda-input-manifest.sha256'
    original_open = Path.open

    def missing_receipt(path, *args, **kwargs):
        if path == receipt:
            raise FileNotFoundError(str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', missing_receipt)
    for options in ({'artifact_root': job.parent}, {'completion_path': completion}):
        report, draft = suggest_design_next(*records['B'], **options)
        assert report['status'] == 'reconcile_execution_first' and draft is None


@pytest.mark.parametrize('corrupt', [False, True])
def test_default_root_never_reads_external_job_bytes_or_skips_bad_metadata(rounds, tmp_path, corrupt):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text())
    # Place only the run in a separate directory; the report must not follow its job paths.
    moved = tmp_path/'moved'; moved.mkdir()
    if corrupt:
        raw['actions'][1]['details']['evidence']['testbench']['executed_input_binding']['remote_input_files'].clear()
    report_path = write(moved/'B-run.json', raw)
    review = build_design_review(tp, report_path)
    assert review['input_evidence'][0]['status'] == ('incomplete' if corrupt else 'recorded_not_replayed')
    if not corrupt:
        assert build_design_review(tp, report_path, artifact_root=tmp_path)['input_evidence'][0]['status'] == 'verified'


def test_input_action_cannot_promote_software_inference_to_eda_evidence(rounds):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text())
    raw['actions'][1]['evidence_source'] = 'software_inference'; write(rp, raw)
    report, draft = suggest_design_next(tp, rp)
    assert report['status'] == 'reconcile_execution_first' and draft is None


def test_duplicate_receipt_cannot_hide_conflicting_successful_action_details(rounds):
    records, _, _ = rounds
    tp, rp = records['B']; raw = json.loads(rp.read_text())
    duplicate = deepcopy(raw['actions'][1])
    duplicate['details']['evidence']['testbench']['settings']['sources'][0]['dc_value'] += .01
    raw['actions'].append(duplicate); write(rp, raw)
    report, draft = suggest_design_next(tp, rp)
    assert report['status'] == 'reconcile_execution_first' and draft is None


def test_completed_restore_never_hides_a_supplied_pending_checkpoint(rounds, tmp_path):
    from virtuoso_design_agent.models import ExecutionCheckpoint
    records, completion, _ = rounds
    run = RunRecord.model_validate_json(records['B'][1].read_text())
    checkpoint = ExecutionCheckpoint(task_id=run.task_id, plan_token=run.plan_token, adapter=run.adapter,
        started_at=run.started_at, initial_parameters={}, expected_oa_parameters={},
        pending_oa_instance_parameters={'MN0': {'w': '475n'}}, next_candidate_index=2,
        candidates=run.candidates, complete=False)
    path = write(tmp_path/'pending.json', checkpoint.model_dump(mode='json'))
    report, draft = suggest_design_next(*records['B'], completion_path=completion, checkpoint_path=path)
    assert report['status'] == 'reconcile_execution_first' and draft is None


@pytest.mark.parametrize('unchanged_oa', [False, True])
def test_existing_inspection_can_close_the_state_chain_without_an_extra_simulation(rounds, unchanged_oa):
    records, completion, bind = rounds
    if unchanged_oa:
        tp, rp = records['B']; t = json.loads(tp.read_text()); r = json.loads(rp.read_text())
        t['instance_parameter_space'][0]['values'] = ['500n']
        r['plan_token'] = build_plan(TaskSpec.model_validate(t)).confirmation_token
        r['candidates'][0]['instance_parameters']['MN0']['w'] = '500n'
        r['selected_instance_parameters']['MN0']['w'] = '500n'
        r['actions'][-1]['details']['instance_parameters']['MN0']['w'] = '500n'
        r['actions'][-1]['details']['confirmed_instance_parameters']['MN0']['w'] = '500n'
        binding = r['actions'][1]['details']['evidence']['netlist']['parameter_bindings'][0]
        binding.update(oa_value='500n', netlist_value='500n'); write(tp, t); write(rp, r)
        restore = json.loads(records['restore'][1].read_text())
        restore['actions'][0]['details']['instance_parameters']['MN0']['w'] = '500n'
        write(records['restore'][1], restore)
    tp, rp = records['C']; raw_task = json.loads(tp.read_text())
    raw_task.update(operation='schematic.inspect', analysis=None, generic_simulation=None, ac_sweep=None, constraints=[])
    raw_task['safety']['allow_remote_compute'] = False
    task = TaskSpec.model_validate(raw_task); write(tp, raw_task)
    raw = json.loads(rp.read_text())
    raw.update(plan_token=build_plan(task).confirmation_token, candidates=[], selected_parameters=None,
               selected_instance_parameters=None, selected_metrics=None)
    raw['actions'] = [{**raw['actions'][0], 'action': 'schematic.inspect'}]
    write(rp, raw); bind()
    review = build_design_review(*records['B'], completion_path=completion)
    assert review['completion']['status'] == 'restored_to_baseline_in_record'
    assert review['completion']['baseline_final_metrics_equal'] is None
    assert 'final' not in review['completion']['input_evidence']
    if unchanged_oa:
        assert review['completion']['selected_point_is_final_state'] is None
        assert suggest_design_next(*records['B'], completion_path=completion)[0]['status'] == 'restored_baseline_review_or_stop'


@pytest.fixture
def workflow_requests(rounds, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('local intake/iteration attempted to construct or run a remote adapter')

    monkeypatch.setattr('virtuoso_design_agent.cli.SubprocessBridgeAdapter.__init__', forbidden)
    monkeypatch.setattr('virtuoso_design_agent.cli.run_bridge_lifecycle', forbidden)
    records, completion, _ = rounds
    measures = write(tmp_path/'measurements.json', {'measurements': [measurement()]})
    history = {'task': records['B'][0].name, 'run': records['B'][1].name,
               'completion': completion.name, 'artifact_root': '.'}
    intake = write(tmp_path/'intake.json', {
        'source': {'kind': 'task', 'task': records['B'][0].name},
        'measurements': measures.name, 'history': history,
        'rationale': [{'text': 'Replay the existing probe only; do not assume its state is current.',
                       'source': 'software_inference'}],
    })
    iteration = write(tmp_path/'iteration.json', {
        'stages': [{'id': 'gain', 'analysis': 'ac', 'constraint_metrics': ['low_frequency_gain_db']}],
        'rounds': [history], 'max_rounds': 1, 'continue_after_feasible': True,
    })
    return intake, iteration


def test_intake_and_iteration_preserve_input_and_completion_without_continuing_the_restored_probe(
    rounds, workflow_requests, tmp_path, monkeypatch,
):
    records, _, _ = rounds
    intake, iteration = workflow_requests
    monkeypatch.chdir(tmp_path.parent)  # Both artifact_root and completion resolve against the request, not cwd.
    context, draft = build_design_intake(intake)
    history = context['history']
    assert draft is None and any('重新定锚' in issue for issue in context['issues'])
    assert history['input_evidence'][0]['status'] == 'verified'
    assert history['input_artifact_root'] == str(tmp_path.resolve())
    assert history['completion']['status'] == 'restored_to_baseline_in_record'
    assert history['writeback']['parameter_checks'][0]['actual'] == '475.00n'
    assert context['sources']['history_final_run']['sha256'] == sha(records['C'][1])
    assert not history['current_remote_state_verified'] and not context['execution_authorized']
    # No template is read or compiled, even with a tempting passed metric and exhausted round budget.
    progress, draft = build_design_iteration(iteration, tmp_path/'not-read-template.json')
    assert progress['status'] == 'recorded_state_requires_reanchor' and progress['next_action'] == 'reanchor'
    assert draft is None and progress['best_feasible_anchor'] is None
    assert progress['history'][0]['full_specification_feasible']  # Historical performance is not erased.
    assert progress['history'][0]['completion'] == history['completion']
    assert '重新定锚' in render_design_iteration(progress) and '已记录收尾' in render_design_intake(context)
    # design-next --iteration must respect the same barrier without duplicate CLI completion flags.
    report, draft = suggest_design_next(*records['B'], iteration_path=iteration)
    assert report['status'] == 'recorded_state_requires_reanchor' and draft is None


@pytest.mark.parametrize('case', ['receipt', 'restore_state', 'pending'])
def test_intake_and_iteration_reconcile_bad_evidence_before_any_reanchor_or_refinement(
    rounds, workflow_requests, tmp_path, case,
):
    from virtuoso_design_agent.models import ExecutionCheckpoint
    records, _, bind = rounds
    intake, iteration = workflow_requests
    if case == 'receipt':
        tmp_path.joinpath('B/input_from_oa.raw/vda-input-manifest.sha256').unlink()
    elif case == 'restore_state':
        raw = json.loads(records['C'][1].read_text())
        raw['actions'][0]['details']['instance_parameters']['MN0']['l'] = '31n'
        write(records['C'][1], raw); bind()
    else:
        run = RunRecord.model_validate_json(records['B'][1].read_text())
        cp = ExecutionCheckpoint(task_id=run.task_id, plan_token=run.plan_token, adapter=run.adapter,
            started_at=run.started_at, initial_parameters={}, expected_oa_parameters={}, complete=True,
            next_candidate_index=2, candidates=run.candidates, pending_oa_instance_parameters={'MN0': {'w': '475n'}})
        write(tmp_path/'pending.json', cp.model_dump(mode='json'))
        for path, key in ((intake, 'history'), (iteration, 'rounds')):
            raw = json.loads(path.read_text())
            history = raw[key][0] if key == 'rounds' else raw[key]
            history['checkpoint'] = 'pending.json'; write(path, raw)
    context, task = build_design_intake(intake)
    assert task is None and context['history']['next_status_for_historical_run'] == 'reconcile_execution_first'
    progress, task = build_design_iteration(iteration)
    assert progress['status'] == 'reconcile_execution' and progress['best_feasible_anchor'] is None
    assert task.operation.value == 'schematic.inspect' and not task.safety.allow_remote_compute
    assert not task.safety.allow_remote_write and not progress['task_draft']['restoration_executed']


def test_explicit_final_record_can_reanchor_with_original_permissions_unchanged(rounds, workflow_requests):
    records, _, _ = rounds
    intake, iteration = workflow_requests
    original = {path: path.read_bytes() for pair in records.values() for path in pair}
    for path, key in ((intake, 'history'), (iteration, 'rounds')):
        raw = json.loads(path.read_text())
        final = {'task': records['C'][0].name, 'run': records['C'][1].name, 'artifact_root': '.'}
        raw[key] = [final] if key == 'rounds' else final
        if key == 'history':
            raw['source']['task'] = records['C'][0].name
        else:
            raw['max_rounds'] = 2  # A new continuation needs budget; do not weaken the original stop gate.
        write(path, raw)
    context, task = build_design_intake(intake)
    assert task is not None and context['history']['completion'] is None
    assert context['history']['input_evidence'][0]['status'] == 'verified'
    progress, next_task = build_design_iteration(iteration, records['C'][0])
    assert progress['status'] == 'refine_objective' and progress['best_feasible_anchor'] is not None
    for draft in (task, next_task):
        assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write,
                        draft.safety.replace_existing, draft.create_if_missing))
        assert draft.safety.allowed_library == _task().safety.allowed_library
        assert draft.design_context.instance_parameter_permissions == _task().design_context.instance_parameter_permissions
    assert all(path.read_bytes() == before for path, before in original.items())


def test_normal_cleanup_and_relative_roots_remain_distinct_at_both_entry_points(
    rounds, workflow_requests, tmp_path, monkeypatch,
):
    intake, iteration = workflow_requests
    # Only the actual job is unavailable; no source files are deleted or rewritten.
    original = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda path: False if path == tmp_path/'B' else original(path))
    monkeypatch.chdir(tmp_path.parent)
    for path, key in ((intake, 'history'), (iteration, 'rounds')):
        raw = json.loads(path.read_text()); history = raw[key][0] if key == 'rounds' else raw[key]
        history.pop('completion'); history.pop('artifact_root'); write(path, raw)
    context, task = build_design_intake(intake)
    progress, _ = build_design_iteration(iteration)
    assert task is not None and not context['execution_authorized']
    for history in (context['history'], progress['history'][0]):
        assert history['input_evidence'][0]['status'] == 'recorded_not_replayed'
    assert progress['status'] != 'reconcile_execution' and not progress['current_remote_state_verified']


def test_completion_hash_failure_never_leaves_partial_cli_outputs(rounds, workflow_requests, tmp_path):
    _, completion, _ = rounds
    intake, iteration = workflow_requests
    raw = json.loads(completion.read_text()); raw['final']['run_sha256'] = '0'*64; write(completion, raw)
    for command, request in (('design-intake', intake), ('design-iterate', iteration)):
        output, draft = tmp_path/(command+'.json'), tmp_path/(command+'-task.json')
        assert main([command, str(request), '--output', str(output), '--task-output', str(draft)]) == 2
        assert not output.exists() and not draft.exists()


@pytest.mark.parametrize('entry', ['intake', 'iteration'])
def test_completion_references_are_not_allowed_to_change_during_composition(
    rounds, workflow_requests, monkeypatch, entry,
):
    import virtuoso_design_agent.design_intake as intake_module
    import virtuoso_design_agent.design_iteration as iteration_module
    records, _, _ = rounds
    module = intake_module if entry == 'intake' else iteration_module
    original = module.build_design_review

    def changed(*args, **kwargs):
        review = original(*args, **kwargs)
        final = records['C'][1]
        final.write_bytes(final.read_bytes() + b'\n')
        return review

    monkeypatch.setattr(module, 'build_design_review', changed)
    request = workflow_requests[0 if entry == 'intake' else 1]
    build = build_design_intake if entry == 'intake' else build_design_iteration
    with pytest.raises(ValueError, match='SHA-256|source changed'):
        build(request)
