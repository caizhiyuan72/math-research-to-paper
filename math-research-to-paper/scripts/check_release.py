#!/usr/bin/env python3
"""Check documented research gates and the freshness of manuscript review."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from check_claims import check, check_core


TOPIC_CHECKS = ('gap', 'value', 'tractability')
DRAFT_CHECKS = ('contribution', 'proof', 'stress_test', 'literature')
REVIEW_TRACKS = ('proof', 'computation', 'literature', 'manuscript')
EVIDENCE_FIELDS = ('proof_files', 'code_files', 'log_files')


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def read_object(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Expected a JSON object in {path}.')
    return data


def evidence_errors(record, keys, label):
    if not isinstance(record, dict):
        return [f'{label}: expected an object.']
    errors = []
    for key in keys:
        item = record.get(key)
        if not isinstance(item, dict) or item.get('result') != 'pass' or not filled(item.get('evidence')):
            errors.append(f'{label}.{key}: requires a pass and specific evidence.')
    return errors


def research_record(gate, root):
    name = gate.get('research_record')
    if not filled(name) or Path(name).is_absolute():
        raise ValueError('research_record: provide the project-relative path of the actual research round.')
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'research_record: outside the project, missing or empty: {name}')
    return path


def gate_errors(ledger, gate, root, stage):
    errors = check(ledger, root)
    if not errors and stage != 'topic':
        errors.extend(check_core(ledger, gate.get('core_claims')))
    if not filled(gate.get('scope')):
        errors.append('scope: state the paper’s exact target and scope.')
    if gate.get('topic_status') != 'selected':
        errors.append('topic_status: exploratory topics are not ready for formal selection.')
    errors.extend(evidence_errors(gate.get('topic_checks'), TOPIC_CHECKS, 'topic_checks'))
    if stage != 'topic':
        try:
            research_record(gate, root)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(str(exc))
        errors.extend(evidence_errors(gate.get('draft_checks'), DRAFT_CHECKS, 'draft_checks'))
    return errors


def issue_errors(findings, audit_files, audited_hashes):
    if not isinstance(findings, list):
        return ['findings: expected a list of review issues.']
    audited = {name for name in audit_files if filled(name)} if isinstance(audit_files, list) else set()
    errors = []
    seen = set()
    for index, issue in enumerate(findings, start=1):
        if not isinstance(issue, dict) or not filled(issue.get('id')):
            errors.append(f'findings[{index}]: missing issue ID.')
            continue
        label = issue['id']
        if label in seen:
            errors.append(f'{label}: duplicate issue ID.')
        seen.add(label)
        if issue.get('status') != 'closed':
            errors.append(f'{label}: unresolved review issue.')
        for field in ('problem', 'resolution', 'verification'):
            if not filled(issue.get(field)):
                errors.append(f'{label}: missing {field}.')
        if issue.get('status') == 'closed':
            if issue.get('disposition') not in ('revised', 'dismissed'):
                errors.append(f'{label}: record whether the issue was revised or disproved.')
            evidence = issue.get('evidence_files')
            if not isinstance(evidence, list) or not evidence or not all(
                filled(name) and name in audited for name in evidence
            ):
                errors.append(f'{label}: closing evidence must refer to files in audit_files.')
                continue
            if issue.get('disposition') == 'revised':
                before = issue.get('before_revision')
                if not isinstance(before, dict) or not before or not all(
                    name in evidence and (digest is None or
                                          isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest))
                    for name, digest in before.items()
                ):
                    errors.append(f'{label}: record pre-revision SHA-256 digests for the actual changed files.')
                elif audited_hashes is not None and all(
                    audited_hashes[name] == digest for name, digest in before.items()
                ):
                    errors.append(f'{label}: the recorded revision files are unchanged.')
    return errors


def snapshot(ledger, ledger_path, gate, gate_path, root):
    audit_files = gate.get('audit_files')
    if not isinstance(audit_files, list) or not audit_files or not all(map(filled, audit_files)):
        raise ValueError('audit_files: list the current manuscript and release inputs.')
    declared_paths = {name: (root / name).resolve() for name in audit_files}
    paths = {ledger_path.resolve(), research_record(gate, root), *declared_paths.values()}
    for claim in ledger['claims']:
        for key in EVIDENCE_FIELDS:
            paths.update((root / name).resolve() for name in claim.get(key, []))
    if gate_path.resolve() in paths:
        raise ValueError('Keep the gate record outside its own audit file list.')
    file_hashes = {}
    for path in sorted(paths):
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Audited file is outside the project, missing or empty: {path}')
        with path.open('rb') as source:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                hasher.update(chunk)
        file_hashes[path.relative_to(root).as_posix()] = hasher.hexdigest()
    payload_keys = ('scope', 'topic_status', 'topic_checks', 'core_claims',
                    'research_record', 'draft_checks', 'findings')
    payload = {key: gate.get(key) for key in payload_keys}
    payload['files'] = file_hashes
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    audited_hashes = {name: file_hashes[path.relative_to(root).as_posix()]
                      for name, path in declared_paths.items()}
    return hashlib.sha256(encoded).hexdigest(), audited_hashes


def final_errors(gate, current_snapshot, audited_hashes):
    errors = issue_errors(gate.get('findings'), gate.get('audit_files'), audited_hashes)
    review = gate.get('review')
    if not isinstance(review, dict):
        return errors + ['review: missing final audit record.']
    if not filled(review.get('initial')):
        errors.append('review.initial: identify the first review and its coverage.')
    if review.get('mode') not in ('separate-context', 'external') or not filled(review.get('verifier')):
        errors.append('review: requires a documented verification pass from a separate context or reviewer.')
    if current_snapshot is not None and current_snapshot != review.get('snapshot'):
        errors.append('review.snapshot: missing or stale verification of the current files and findings.')
    errors.extend(evidence_errors(review.get('tracks'), REVIEW_TRACKS, 'review.tracks'))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('topic', 'draft', 'snapshot', 'final'))
    parser.add_argument('ledger', type=Path)
    parser.add_argument('gate', type=Path)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    try:
        ledger, gate = read_object(args.ledger), read_object(args.gate)
        root = args.root.resolve()
        errors = gate_errors(ledger, gate, root, args.stage)
        digest, audited_hashes = None, None
        if not errors and args.stage in ('snapshot', 'final'):
            try:
                digest, audited_hashes = snapshot(ledger, args.ledger, gate, args.gate, root)
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(str(exc))
            if args.stage == 'snapshot' and digest is not None:
                print(digest)
                return 0
        if args.stage == 'final':
            errors.extend(final_errors(gate, digest, audited_hashes))
    except (OSError, ValueError, RuntimeError) as exc:
        errors = [str(exc)]
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'{args.stage} gate record passed. Mathematical validity requires actual review.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
