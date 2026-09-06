"""Bind material task facts to explicit, dated review attestations."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any


def evidence_schema() -> dict[str, Any]:
    fields = {
        'value_hash': {'type': 'string', 'pattern': '^sha256:[a-f0-9]{64}$'},
        'source_kind': {'type': 'string', 'enum': ['operator', 'document', 'backend']},
        'source_ref': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        'reviewed_by': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        'reviewed_at': {'type': 'string', 'format': 'date-time'},
        'expires_at': {'type': 'string', 'format': 'date-time'},
    }
    return {'type': 'object', 'maxProperties': 40, 'description': 'Task knowledge_evidence, keyed by the exact knowledge fact key. Review attestations must be truthful; a reference is not independently verified by this schema.',
            'additionalProperties': {'type': 'object', 'additionalProperties': False,
                                     'properties': fields, 'required': [key for key in fields if key != 'expires_at']}}


def fact_hash(value: str) -> str:
    return 'sha256:' + hashlib.sha256(value.strip().encode()).hexdigest()


def material_categories(text: str) -> frozenset[str]:
    value = text.casefold().replace('_', ' ')
    patterns = {
        'price': r'\b(?:price|pricing|investment|costs?|dollars?|euros?|credits?|usd|eur|mad|prix|tarif|investissement)\b|[$€£]\s*\d',
        'certification': r'\b(?:soc\s*2|iso\s*\d+|certifi\w*|accredit\w*)\b',
        'guarantee': r'\b(?:guarantee\w*|guaranteed|garanti\w*|unconditional|money.back|zero latency)\b',
        'timeline': r'\btimeline\b|\b(?:deploy\w*|livr\w*|delivery)\b.{0,90}\b(?:days?|weeks?|jours?|semaines?)\b',
        'availability': r'\b(?:in stock|stock availability|available now|disponible immédiatement)\b',
        'compatibility': r'\b(?:compatib\w*|works on|supported devices)\b',
    }
    return frozenset(category for category, pattern in patterns.items() if re.search(pattern, value))


def validate_evidence_shape(evidence: Any, knowledge: dict[str, str]) -> dict[str, dict[str, str]]:
    if not isinstance(evidence, dict) or len(evidence) > 40:
        raise ValueError('knowledge_evidence must be a bounded object')
    result = {}
    required = {'value_hash', 'source_kind', 'source_ref', 'reviewed_by', 'reviewed_at'}
    for key, value in evidence.items():
        if key not in knowledge or not isinstance(value, dict) or set(value) - required - {'expires_at'} or not required <= set(value):
            raise ValueError(f'Invalid evidence record for {key!r}')
        if any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in value.values()):
            raise ValueError('Evidence fields must be bounded nonempty strings')
        if value['source_kind'] not in {'operator', 'document', 'backend'}:
            raise ValueError('Unsupported evidence source_kind')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', value['value_hash']):
            raise ValueError('Evidence value_hash must be SHA-256')
        result[key] = dict(value)
    return result


def provenance_findings(task: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    knowledge = task.get('knowledge') or {}
    evidence = task.get('knowledge_evidence') or {}
    findings = []
    for key, value in knowledge.items():
        categories = material_categories(str(key) + ' ' + str(value))
        if not categories:
            continue
        entry = evidence.get(key)
        problem = ''
        if not isinstance(entry, dict):
            problem = 'missing_evidence'
        else:
            try:
                validate_evidence_shape({key: entry}, {key: str(value)})
            except ValueError:
                problem = 'invalid_evidence_record'
        if problem:
            pass
        elif entry.get('value_hash') != fact_hash(str(value)):
            problem = 'fact_changed_since_review'
        elif 'certification' in categories and entry.get('source_kind') == 'operator':
            problem = 'certification_requires_document_or_backend_source'
        else:
            try:
                reviewed = datetime.fromisoformat(entry.get('reviewed_at', ''))
                expires = datetime.fromisoformat(entry['expires_at']) if entry.get('expires_at') else None
                if reviewed.tzinfo is None or reviewed > now:
                    problem = 'invalid_review_time'
                elif expires is not None and (expires.tzinfo is None or expires <= now or expires <= reviewed):
                    problem = 'expired_or_invalid_evidence'
                elif not entry.get('source_ref') or not entry.get('reviewed_by'):
                    problem = 'missing_review_attribution'
            except (ValueError, TypeError):
                problem = 'invalid_review_time'
        findings.append({'fact': str(key), 'categories': sorted(categories), 'passed': not problem, 'reason': problem})
    return findings


def unsupported_material_categories(text: str, task: dict[str, Any]) -> frozenset[str]:
    """Catch positive claims in categories with unreviewed configured facts.

    This is a bounded legacy-runtime guard, not a semantic fact-verification model.
    Questions, explicit uncertainty and negation do not assert the disputed fact.
    """
    unsupported = {category for finding in provenance_findings(task) if not finding['passed'] for category in finding['categories']}
    found: set[str] = set()
    for clause in re.split(r'(?<=[.!?])\s+', text):
        lowered = clause.casefold().replace('\u2019', "'")
        if re.match(r'\s*(?:what|which|how|can|could|would|should|is|are|do|does|may|quel|quelle|combien|pouvez|est ce)\b', lowered):
            continue
        if re.search(r"\b(?:cannot|can't|not|never|unverified|need to verify|don't know|ne|pas|sans garantie)\b", lowered):
            continue
        found.update(material_categories(clause) & unsupported)
    return frozenset(found)
