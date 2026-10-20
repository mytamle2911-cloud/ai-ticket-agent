"""Small deterministic knowledge checks; warnings always require human interpretation."""
from __future__ import annotations

import copy
import re
from typing import Any

LIFECYCLE_STATUSES = {'active', 'expired', 'superseded'}
METADATA_KEYS = {'version', 'version_history', 'lifecycle_status', 'updated_at'}


def normalise(value: Any) -> str:
    return ' '.join(re.sub(r'[\W_]+', ' ', str(value or '').casefold()).split())


def is_active(record: dict) -> bool:
    return record.get('lifecycle_status', 'active') == 'active'


def source_ref(record: dict) -> dict:
    return {'record_id': record.get('article_id') or record.get('document_id'),
            'title': record.get('title', ''), 'source_incident': record.get('source_incident')}


def _scope(record: dict) -> tuple[str, str]:
    return normalise(record.get('source_system')), normalise(record.get('issue_category') or record.get('issue_type'))


def _instructions(record: dict) -> list[str]:
    result = []
    for field in ('verified_troubleshooting_steps', 'resolution_steps', 'diagnostic_steps'):
        for step in record.get(field, []) if isinstance(record.get(field, []), list) else []:
            text = step.get('instruction') if isinstance(step, dict) else step
            if isinstance(text, str) and text.strip():
                result.append(text.strip())
    return result


def _constraints(record: dict) -> list[tuple[tuple, bool, str]]:
    """Only flag opposite explicit commands for the same target and condition.

    Different causes/steps, different workflow targets or different conditions alone
    are not contradictions. This deliberately does not attempt semantic diagnosis.
    """
    commands = re.compile(r'\b(?:(do not|must not|should not|never|avoid)\s+)?'
                          r'(enable|disable|grant|revoke|restart|delete|approve)\s+([^.;\n]+)', re.I)
    output = []
    for text in _instructions(record):
        for match in commands.finditer(text):
            negative, verb, target = match.groups()
            prefix = text[:match.start()]
            # Avoid treating a purpose clause ("to enable ...") as an instruction.
            if re.search(r'\bto\s*$', prefix, re.I):
                continue
            condition = normalise(prefix) if re.search(r'\b(if|when|unless|after|before)\b', prefix, re.I) else ''
            suffix_condition = re.search(r'\b(if|when|unless|after|before)\b.*', target, re.I)
            if suffix_condition:
                condition += ' ' + normalise(suffix_condition.group())
            target = re.split(r'\b(?:then|and|but|if|when|unless|after|before)\b', target, maxsplit=1, flags=re.I)[0]
            target = re.sub(r'^(the|a|an)\s+', '', target.strip(), flags=re.I)
            verb = verb.casefold()
            allowed = not negative
            if verb in {'disable', 'revoke'}:
                verb = {'disable': 'enable', 'revoke': 'grant'}[verb]
                allowed = not allowed
            if normalise(target):
                output.append(((condition, verb, normalise(target)), allowed, text))
    return output


def knowledge_conflicts(records: list[dict]) -> list[dict]:
    conflicts = []
    for index, left in enumerate(records):
        if not is_active(left):
            continue
        for right in records[index + 1:]:
            if not is_active(right) or not all(_scope(left)) or _scope(left) != _scope(right):
                continue
            found = []
            for key, allowed, text in _constraints(left):
                for other_key, other_allowed, other_text in _constraints(right):
                    if key == other_key and allowed != other_allowed:
                        pair = {'instruction': text, 'conflicting_instruction': other_text}
                        if pair not in found:
                            found.append(pair)
            if found:
                conflicts.append({'code': 'potential_knowledge_conflict',
                    'message': 'Potential Knowledge Conflict — opposite instructions require L2 review; neither source was selected as correct.',
                    'sources': [source_ref(left), source_ref(right)], 'details': found})
    return conflicts


def article_warnings(candidate: dict, existing: list[dict]) -> list[dict]:
    peers = [row for row in existing if isinstance(row, dict)
             and row.get('article_id') != candidate.get('article_id')]
    duplicates = [source_ref(row) for row in peers if all(_scope(candidate))
                  and _scope(row) == _scope(candidate)
                  and normalise(row.get('title')) == normalise(candidate.get('title'))]
    warnings = []
    if duplicates:
        warnings.append({'code': 'potential_duplicate',
            'message': 'Potential duplicate knowledge: review the existing article before proceeding.',
            'sources': duplicates})
    warnings.extend(knowledge_conflicts([candidate, *peers]))
    for warning in warnings:
        for source in warning['sources']:
            if source['record_id'] == candidate.get('article_id') and not any(row.get('article_id') == source['record_id'] for row in existing):
                source['proposed'] = True
    # Creation/update review concerns the candidate, not unrelated pairs of peers.
    identity = candidate.get('article_id')
    return [w for w in warnings if w['code'] == 'potential_duplicate'
            or any(s['record_id'] == identity for s in w['sources'])]


def new_metadata() -> dict:
    return {'version': 1, 'version_history': [], 'lifecycle_status': 'active'}


def version_update(previous: dict, updated: dict, timestamp: str) -> dict:
    result = copy.deepcopy(updated)
    snapshot = copy.deepcopy(previous)
    history = snapshot.pop('version_history', [])
    snapshot.setdefault('version', 1)
    snapshot.setdefault('lifecycle_status', 'active')
    result['version_history'] = [*history, snapshot]
    result['version'] = previous.get('version', 1) + 1
    result['lifecycle_status'] = updated.get('lifecycle_status', 'active')
    result['updated_at'] = timestamp
    return result


def validate_metadata(row: dict) -> bool:
    if type(row.get('version', 1)) is not int or row.get('version', 1) < 1 or not isinstance(row.get('lifecycle_status', 'active'), str) or row.get('lifecycle_status', 'active') not in LIFECYCLE_STATUSES:
        return False
    history = row.get('version_history', [])
    if not isinstance(history, list) or len(history) > 100:
        return False
    last = 0
    for old in history:
        if (not isinstance(old, dict) or 'version_history' in old
                or type(old.get('version')) is not int or not last < old['version'] < row.get('version', 1)
                or not isinstance(old.get('lifecycle_status', 'active'), str) or old.get('lifecycle_status', 'active') not in LIFECYCLE_STATUSES
                or any(old.get(k) != row.get(k) for k in ('article_id', 'source_incident', 'source_resolution_id', 'source_system', 'issue_category'))):
            return False
        last = old['version']
    return 'updated_at' not in row or isinstance(row['updated_at'], str)
