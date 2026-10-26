"""Read-only consumption of retained execution evidence; never imports Bridge."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path

from pydantic import Field

from .generic_simulation import GenericOaSimulationSpec
from .models import EvidenceSource, RunRecord, StrictModel, TaskSpec
from .simulation_input import (
    INPUT_RECEIPT, audit_generic_wrapper, validate_input_receipt_entries, verify_input_receipt,
)
from .spectre_values import spectre_numeric_literals_equal


def result_evidence(value):
    """Yield leaf result/evidence pairs, not copies of the evidence's internals."""
    if isinstance(value, dict):
        if isinstance(value.get("evidence"), dict):
            yield value, value["evidence"]
        for key, child in value.items():
            if key != "evidence" and isinstance(child, (dict, list)):
                yield from result_evidence(child)
    elif isinstance(value, list):
        for child in value:
            yield from result_evidence(child)


def numeric_binding_support(task: TaskSpec, run: RunRecord, index: int) -> dict:
    """Accept format changes only on declared, corroborated numeric OA→si fields."""
    if run.adapter != "virtuoso-bridge-subprocess" or task.generic_simulation is None or task.target is None:
        return {}
    declared = {(b.instance, b.oa_parameter, b.netlist_parameter)
                for b in task.generic_simulation.netlist_parameter_bindings}
    supported = {}
    conflicts = set()
    for action in run.actions:
        if (action.status != "succeeded" or action.evidence_source is not EvidenceSource.EDA_RESULT
                or not re.match(rf"^simulation\.candidate\.{index}(?:\.|$)", action.action)):
            continue
        for _, evidence in result_evidence(action.details):
            net = evidence.get("netlist", {})
            if (net.get('source') != 'eda_result' or net.get('topology_consistency') != 'matched'
                    or net.get("parameter_consistency") != "matched"
                    or evidence.get("schematic_readback", {}).get("target") != task.target.model_dump(mode="json")):
                continue
            for row in net.get("parameter_bindings", []):
                key = (row.get("instance"), row.get("oa_parameter"), row.get("netlist_parameter"))
                if key in declared and spectre_numeric_literals_equal(row.get("oa_value"), row.get("netlist_value")):
                    field = key[:2]
                    if field in supported and not spectre_numeric_literals_equal(supported[field], row['oa_value']):
                        conflicts.add(field)
                    supported[field] = row["oa_value"]
    for field in conflicts:
        supported.pop(field, None)
    return supported


def _digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _effective_settings(task, candidate, tb):
    settings = task.generic_simulation
    if candidate.testbench_overrides:
        settings = candidate.testbench_overrides.apply_to(settings)
    condition = tb.get('operating_condition')
    if condition is not None:
        if not any(all(condition.get(k) == v for k, v in c.model_dump(mode='json').items() if v is not None)
                   for c in task.operating_conditions):
            raise ValueError('undeclared input operating condition')
        supply = settings.operating_condition_supply_source
        sources = [s.model_copy(update={'dc_value': condition['vdd_v']})
                   if s.name == supply and condition.get('vdd_v') is not None else s for s in settings.sources]
        settings = settings.model_copy(update={'sources': sources, 'temperature_c': condition['temperature_c']})
    elif task.operating_conditions:
        raise ValueError('input omitted the declared operating condition')
    if settings != GenericOaSimulationSpec.model_validate(tb['settings']):
        raise ValueError('retained testbench differs from the effective task/candidate')
    return settings


class _InputReplayUnavailable(Exception):
    """Recorded runtime binding checked, but no claim of a new local byte replay."""


def _audit_input(task, candidate, evidence: dict, local_root: Path, require_files: bool) -> dict:
    """Recheck downloaded bytes against the original worker's hashes and contract."""
    tb, net, sim = evidence['testbench'], evidence['netlist'], evidence['simulation']
    if net.get('source') != 'eda_result' or sim.get('source') != 'eda_result':
        raise ValueError('input/netlist evidence is not recorded as an EDA result')
    if net.get('topology_consistency') != 'matched' or net.get('parameter_consistency') != 'matched':
        raise ValueError('OA-to-si topology/parameter consistency was not recorded as matched')
    expected_topology = candidate.topology_sha256 or (
        task.design_context.expected_topology_sha256 if task.design_context else None)
    if expected_topology and evidence.get('schematic_readback', {}).get('topology_sha256') != expected_topology:
        raise ValueError('input schematic topology differs from the bound candidate/context')
    if evidence.get('schematic_readback', {}).get('target') != task.target.model_dump(mode='json'):
        raise ValueError('input schematic target differs from the task')
    binding = tb['executed_input_binding']
    if binding.get('source') != 'bridge_readback' or binding.get('status') != 'matched':
        raise ValueError('executed input was not recorded as matched Bridge evidence')
    if not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('receipt_sha256', ''))):
        raise ValueError('missing original receipt SHA-256')
    include = net['spectre_include']
    dependencies = {net['remote_path']: net['sha256'], include['remote_path']: include['sha256']}
    root = posixpath.dirname(net['remote_path'])
    if not posixpath.isabs(root) or any(not p.startswith(root + '/') or '..' in p.split('/') for p in dependencies):
        raise ValueError('OA input dependency escapes the original netlist directory')
    contract = {'wrapper_name': 'input_from_oa.scs', 'wrapper_sha256': tb['sha256'], 'dependencies': dependencies}
    validate_input_receipt_entries(binding['remote_input_files'], root, contract)
    settings = _effective_settings(task, candidate, tb)
    analysis = tb['analysis']
    if analysis not in {a.value for a in task.resolved_analyses()}:
        raise ValueError('input analysis differs from the task')
    receipt = Path(binding['receipt_path']).resolve()
    folder = receipt.parent.parent
    if receipt.name != INPUT_RECEIPT or receipt.parent.name != 'input_from_oa.raw':
        raise ValueError('receipt does not name the original retained job directory')
    manifest = sim['artifact_manifest']
    if (sim.get('artifact_manifest_complete') is not True or not manifest
            or hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            != sim.get('artifact_manifest_sha256')):
        raise ValueError('artifact manifest is missing or differs from its recorded hash')
    names = set()
    for item in manifest:
        name = item['path']
        if (not isinstance(name, str) or not name or name in names or Path(name).is_absolute()
                or '\\' in name or ':' in name or any(p in {'', '.', '..'} for p in name.split('/'))):
            raise ValueError('duplicate or escaping artifact manifest entry')
        if (type(item['size_bytes']) is not int or item['size_bytes'] < 0
                or not re.fullmatch(r'[0-9a-f]{64}', str(item['sha256']))):
            raise ValueError('malformed artifact manifest size/hash')
        names.add(name)
    required = {receipt.relative_to(folder).as_posix(), 'input_from_oa.scs',
                'oa_netlist.scs', 'oa_netlist_spectre.scs', 'spectre.out'}
    if not required <= names:
        raise ValueError('manifest omitted a required input, receipt or simulator log')
    if not folder.is_relative_to(local_root.resolve()):
        if not require_files:
            raise _InputReplayUnavailable('recorded binding checked; bytes outside the local artifact root were not read')
        raise ValueError('receipt is outside the explicit local artifact root')
    if not folder.is_dir() and not require_files:
        raise _InputReplayUnavailable('recorded binding checked; job files are no longer retained, not a new byte replay')
    if _digest(receipt) != binding['receipt_sha256']:
        raise ValueError('retained input receipt SHA-256 differs from the run')
    for item in manifest:
        file = (folder / item['path']).resolve()
        if not file.is_relative_to(folder):
            raise ValueError('artifact manifest symlink escapes the retained job directory')
        if file.stat().st_size != item['size_bytes'] or _digest(file) != item['sha256']:
            raise ValueError('retained artifact bytes differ: ' + item['path'])
    for name, digest in [('oa_netlist.scs', net['sha256']), ('oa_netlist_spectre.scs', include['sha256']),
                         ('input_from_oa.scs', tb['sha256'])]:
        if _digest(folder / name) != digest:
            raise ValueError('local input differs from the action binding: ' + name)
    replay = verify_input_receipt(receipt.parent, root, contract)
    if replay['remote_input_files'] != binding['remote_input_files']:
        raise ValueError('receipt entries differ from the original executed-input binding')
    audit = audit_generic_wrapper((folder / 'input_from_oa.scs').read_bytes(), settings, analysis, include['remote_path'])
    if audit['status'] != 'matched' or audit['wrapper_sha256'] != tb['sha256']:
        raise ValueError('retained wrapper literal audit failed: ' + '; '.join(audit['issues']))
    return {'receipt': replay, 'manifest_file_count': len(manifest), 'literal_audit': audit,
            'analysis': analysis, 'operating_condition': tb.get('operating_condition')}


def audit_recorded_inputs(task: TaskSpec, run: RunRecord, local_root: Path, *, require_files: bool = False) -> list[dict]:
    """Legacy absent evidence stays absent; declared bad/missing bytes never pass."""
    rows = []
    if run.adapter != 'virtuoso-bridge-subprocess' or task.generic_simulation is None:
        return rows
    for candidate in run.candidates:
        first_row = len(rows)
        seen = {}
        for action_index, action in enumerate(run.actions):
            if action.status != 'succeeded' or not re.match(
                rf'^simulation\.candidate\.{candidate.index}(?:\.|$)', action.action
            ):
                continue
            for _, evidence in result_evidence(action.details):
                tb = evidence.get('testbench', {})
                declared = ('executed_input_binding' in tb or 'literal_input_audit' in tb
                            or any(str(p.get('path', '')).endswith('/' + INPUT_RECEIPT)
                                   for p in evidence.get('simulation', {}).get('artifact_manifest', []) if isinstance(p, dict)))
                if not declared:
                    continue
                key = (tb.get('analysis'), repr(tb.get('operating_condition')),
                       (tb.get('executed_input_binding') or {}).get('receipt_path'))
                if key in seen:
                    if seen[key] != (action.evidence_source, evidence):
                        rows.append({'candidate_index': candidate.index, 'action_index': action_index,
                                     'status': 'incomplete', 'source': 'software_inference',
                                     'issues': ['conflicting evidence for the same executed input receipt']})
                    continue
                seen[key] = (action.evidence_source, evidence)
                row = {'candidate_index': candidate.index, 'action_index': action_index,
                       'status': 'incomplete', 'source': 'software_inference', 'issues': []}
                try:
                    if action.evidence_source is not EvidenceSource.EDA_RESULT:
                        raise ValueError('input action is not recorded as EDA evidence')
                    row.update(_audit_input(task, candidate, evidence, local_root, require_files), status='verified')
                except _InputReplayUnavailable as error:
                    row.update(status='recorded_not_replayed', analysis=tb.get('analysis'),
                               operating_condition=tb.get('operating_condition'), issues=[str(error)])
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                    row['issues'] = [str(error)]
                rows.append(row)
        if seen:
            analyses = ({s.analysis.value for s in candidate.analysis_stages} if candidate.analysis_stages
                        else {a.value for a in task.resolved_analyses()})
            conditions = [c.name for c in task.operating_conditions] or [None]
            covered = {(r.get('analysis'), (r.get('operating_condition') or {}).get('name'))
                       for r in rows[first_row:] if r['status'] in {'verified', 'recorded_not_replayed'}}
            if any((analysis, condition) not in covered for analysis in analyses for condition in conditions):
                rows.append({'candidate_index': candidate.index, 'status': 'incomplete',
                             'source': 'software_inference',
                             'issues': ['retained input receipts do not cover the completed analyses/declared conditions']})
        if not seen:
            rows.append({'candidate_index': candidate.index, 'status': 'not_recorded', 'issues': [],
                         'source': 'software_inference', 'statement': 'no new input receipt is invented for a legacy record'})
    return rows


class SavedReviewRun(StrictModel):
    task: Path
    run: Path
    task_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    run_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewCompletion(StrictModel):
    """Explicit historical baseline→reviewed run→restore→final observation links."""
    schema_version: int = Field(default=1, ge=1, le=1)
    reviewed_task_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewed_run_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    baseline: SavedReviewRun
    restoration: SavedReviewRun
    final: SavedReviewRun


def audit_review_completion(path: Path, task_path: Path, run_path: Path, local_root: Path) -> dict:
    from .design_review import file_reference, load_bound_run, read_json, selected_candidate
    from .topology_delta import snapshot_from_inspection

    request = ReviewCompletion.model_validate(read_json(path))
    if (_digest(task_path), _digest(run_path)) != (request.reviewed_task_sha256, request.reviewed_run_sha256):
        raise ValueError('completion does not bind these exact reviewed task/run bytes')
    task, run = load_bound_run(task_path, run_path)
    records, sources = {}, {'completion': file_reference(path)}
    for name in ('baseline', 'restoration', 'final'):
        ref = getattr(request, name)
        tp, rp = [(p if p.is_absolute() else path.parent / p).resolve() for p in (ref.task, ref.run)]
        if (_digest(tp), _digest(rp)) != (ref.task_sha256, ref.run_sha256):
            raise ValueError('completion reference SHA-256 mismatch: ' + name)
        records[name] = (*load_bound_run(tp, rp), rp)
        sources[name + '_task'], sources[name + '_run'] = file_reference(tp), file_reference(rp)
    if len({request.reviewed_run_sha256, *(getattr(request, n).run_sha256
                for n in ('baseline', 'restoration', 'final'))}) != 4:
        raise ValueError('completion requires distinct independent run records')

    def inspection(record, action_name, *, first=False):
        actions = [a for a in record.actions if a.action == action_name and a.status == 'succeeded'
                   and a.evidence_source is EvidenceSource.BRIDGE_READBACK]
        if not actions:
            raise ValueError('completion lacks independent ' + action_name)
        return actions[0] if first else actions[-1]

    def state(action):
        data = action.details
        if not data.get('instance_parameters') or not data.get('placement'):
            raise ValueError('completion requires complete parameter and placement observations')
        # Do not normalize state comparisons: all fields and raw CDF literals matter.
        return snapshot_from_inspection(data), data['instance_parameters'], data['placement']

    report = {'status': 'incomplete', 'source': 'software_inference', 'sources': sources,
              'issues': [], 'current_remote_state_verified': False, 'execution_authorized': False}
    try:
        baseline, restore, final = [records[n][1] for n in ('baseline', 'restoration', 'final')]
        for name, (other_task, other_run, _) in records.items():
            if (other_task.target != task.target or other_task.pdk_profile != task.pdk_profile
                    or other_run.adapter != 'virtuoso-bridge-subprocess' or other_run.status.value != 'succeeded'):
                raise ValueError('completion target/PDK/real-run status differs: ' + name)
        if not (baseline.finished_at <= run.started_at and run.finished_at <= restore.started_at
                and restore.finished_at <= final.started_at):
            raise ValueError('completion records are overlapping or out of chronological order')
        final_simulation = records['final'][0].operation.value == 'simulation.run'
        if (records['restoration'][0].operation.value != 'parameters.apply'
                or records['final'][0].operation.value not in {'simulation.run', 'schematic.inspect'}
                or records['final'][0].safety.allow_remote_write):
            raise ValueError('completion requires explicit restoration then an independent read-only-OA observation')
        writes = [a for a in restore.actions if a.action == 'parameters.apply'
                  and a.status == 'succeeded' and a.evidence_source is EvidenceSource.BRIDGE_READBACK]
        declared = {u.instance: u.parameters for u in records['restoration'][0].instance_parameter_updates}
        if (len(writes) != 1 or not declared
                or writes[0].details.get('requested_instance_parameters') != declared):
            raise ValueError('completion lacks the exact declared restoration write/confirmation')
        if any(a.status == 'succeeded' and a.action.startswith(('parameters.', 'schematic.create', 'schematic.transform'))
               for a in final.actions):
            raise ValueError('final observation run includes an unexpected OA mutation')
        original = inspection(baseline, 'schematic.inspect.before', first=True)
        probe_before = inspection(run, 'schematic.inspect.before', first=True)
        probe_after = inspection(run, 'schematic.inspect.after')
        restore_before = inspection(restore, 'schematic.inspect.before')
        restore_after = inspection(restore, 'schematic.inspect.after')
        observed = inspection(final, 'schematic.inspect.before' if final_simulation else 'schematic.inspect')
        if not (state(original) == state(probe_before) == state(restore_after) == state(observed)
                and state(probe_after) == state(restore_before)):
            raise ValueError('full baseline/probe/restoration/final observations disagree')
        for instance, fields in declared.items():
            for field, value in fields.items():
                if (original.details['instance_parameters'].get(instance, {}).get(field) != value
                        or writes[0].details.get('confirmed_instance_parameters', {}).get(instance, {}).get(field) != value):
                    raise ValueError('restoration did not confirm the exact original CDF literals')
        if final_simulation:
            for field in ('generic_simulation', 'analysis', 'analysis_stages', 'ac_sweep', 'linearity_sweep',
                          'noise_sweep', 'stability_sweep', 'operating_conditions'):
                if getattr(records['baseline'][0], field) != getattr(records['final'][0], field):
                    raise ValueError('final analysis/testbench differs from baseline: ' + field)
        input_checks = {}
        for name in (('baseline', 'final') if final_simulation else ('baseline',)):
            t, r, _ = records[name]
            checks = audit_recorded_inputs(t, r, local_root, require_files=True)
            if not checks or any(c['status'] != 'verified' for c in checks):
                raise ValueError('completion input evidence not replayable: ' + name)
            input_checks[name] = checks
        oa_equal = state(probe_after) == state(observed)
        selected_setup = task.generic_simulation
        selected = selected_candidate(run)
        if selected and selected.testbench_overrides and selected_setup:
            selected_setup = selected.testbench_overrides.apply_to(selected_setup)
        setup_equal = (selected_setup == records['final'][0].generic_simulation
                       and task.operating_conditions == records['final'][0].operating_conditions) if final_simulation else None
        report.update(status='restored_to_baseline_in_record', observed_at=observed.finished_at.isoformat(),
                      final_instance_parameters=observed.details['instance_parameters'],
                      selected_oa_matches_final_observation=oa_equal,
                      selected_testbench_matches_final_analysis=setup_equal,
                      selected_point_is_final_state=(False if not oa_equal else setup_equal),
                      baseline_final_metrics_equal=(baseline.selected_metrics == final.selected_metrics
                                                    if final_simulation else None),
                      input_evidence=input_checks,
                      boundaries=['Recorded observations only; no fresh OA/PDK check or restoration execution.',
                                  'State equality does not prove byte-identical OA files or future ownership.'])
    except (ValueError, TypeError, KeyError) as error:
        report['issues'] = [str(error)]
    return report
