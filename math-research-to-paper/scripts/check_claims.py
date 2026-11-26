#!/usr/bin/env python3
"""Check research claim ledger shape and local evidence paths, not mathematical truth."""
import argparse
from graphlib import CycleError, TopologicalSorter
import json
from pathlib import Path
import sys

STATUSES = {'conjecture', 'finite-evidence', 'derived-unchecked', 'proved',
            'computer-assisted', 'refuted'}
NOVELTY = {'unknown', 'known', 'consequence', 'candidate-new'}
FINAL_STATUSES = ('proved', 'computer-assisted')


def _dependency_errors(claims):
    """Return errors for dependency cycles and unsupported proof dependencies."""
    errors = []
    claims_by_id = {claim['id']: claim for claim in claims}
    graph = {cid: claim.get('dependencies', []) for cid, claim in claims_by_id.items()}
    for cid, claim in claims_by_id.items():
        if claim['status'] in FINAL_STATUSES:
            for dep in graph[cid]:
                dependency_status = claims_by_id[dep]['status']
                if dependency_status not in FINAL_STATUSES:
                    errors.append(
                        f'{cid}: {claim["status"]} claim depends on '
                        f'{dep} with non-final status {dependency_status!r}.'
                    )
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        errors.append('Dependency cycle detected among: ' + ', '.join(exc.args[1]) + '.')
    return errors


def check_core(data, core_ids):
    """Check that the paper's core claims are present and fully established.

    This function is intentionally separate from ``check`` so a ledger can be
    structurally valid while a draft is still blocked from being written.
    Callers should run it after ``check`` succeeds.
    """
    if not isinstance(core_ids, list) or not core_ids:
        return ['core_claims: expected a nonempty list of unique claim IDs.']
    if any(not isinstance(cid, str) or not cid.strip() for cid in core_ids):
        return ['core_claims: every core claim ID must be a nonempty string.']
    if len(set(core_ids)) != len(core_ids):
        return ['core_claims: duplicate core claim ID.']
    claims_by_id = {claim['id']: claim for claim in data['claims']}
    errors = []
    for cid in core_ids:
        claim = claims_by_id.get(cid)
        if claim is None:
            errors.append(f'core_claims: unknown core claim ID {cid}.')
        elif claim.get('status') not in FINAL_STATUSES:
            errors.append(
                f'core_claims: {cid} has status {claim.get("status")!r}; '
                'core claims require proved or computer-assisted status.'
            )
    return errors

def check(data, root):
    errors = []
    if not isinstance(data, dict) or not isinstance(data.get('claims'), list):
        return ['Expected an object containing a claims list.']
    if not isinstance(data.get('project'), str) or not data['project'].strip():
        errors.append('Missing project title.')
    ids = set()
    for i, claim in enumerate(data['claims'], 1):
        prefix = f'claim {i}'
        if not isinstance(claim, dict):
            errors.append(f'{prefix}: expected an object'); continue
        cid = claim.get('id')
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f'{prefix}: missing id')
        elif cid in ids:
            errors.append(f'{prefix}: duplicate id {cid}')
        else:
            ids.add(cid); prefix = cid
        for key in ('statement', 'scope'):
            if not isinstance(claim.get(key), str) or not claim[key].strip():
                errors.append(f'{prefix}: missing {key}')
        status, novelty = claim.get('status'), claim.get('novelty')
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f'{prefix}: invalid status')
        if not isinstance(novelty, str) or novelty not in NOVELTY:
            errors.append(f'{prefix}: invalid novelty')
        for key in ('proof_files', 'code_files', 'log_files', 'dependencies', 'sources', 'commands'):
            items = claim.get(key, [])
            if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
                errors.append(f'{prefix}: {key} must be a list of nonempty strings'); continue
            if key.endswith('_files'):
                for item in items:
                    p = (root / item).resolve()
                    if not p.is_relative_to(root):
                        errors.append(f'{prefix}: evidence path outside root: {item}')
                    elif not p.is_file() or p.stat().st_size == 0:
                        errors.append(f'{prefix}: evidence file missing or empty: {item}')
        if status in ('proved', 'computer-assisted') and not claim.get('proof_files'):
            errors.append(f'{prefix}: proved claim requires proof_files')
        if status in ('finite-evidence', 'computer-assisted'):
            for key in ('code_files', 'log_files', 'commands'):
                if not claim.get(key): errors.append(f'{prefix}: {status} requires {key}')
        if status == 'refuted' and not (claim.get('proof_files') or claim.get('log_files')):
            errors.append(f'{prefix}: refutation requires a recorded proof or counterexample log')
        if novelty in ('known', 'consequence', 'candidate-new') and not claim.get('sources'):
            errors.append(f'{prefix}: novelty label requires source/search records')
    for claim in data['claims']:
        if isinstance(claim, dict) and isinstance(claim.get('dependencies'), list):
            for dep in claim['dependencies']:
                if not isinstance(dep, str) or dep not in ids:
                    errors.append(f'{claim.get("id", "?")}: unknown internal dependency {dep}')
    if not errors:
        errors.extend(_dependency_errors(data['claims']))
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    try:
        data = json.loads(args.ledger.read_text(encoding='utf-8'))
        errors = check(data, args.root.resolve())
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr); return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr); return 1
    print(f'Ledger structure OK: {len(data["claims"])} claim(s).')
    print('This is a record check only; it does not verify proofs, computations, or originality.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
