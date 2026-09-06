"""Review metadata must match the exact material fact and its freshness."""

from datetime import UTC, datetime

from phone_agent_gateway.ai_bridge.knowledge_evidence import (
    fact_hash,
    provenance_findings,
    unsupported_material_categories,
)
from phone_agent_gateway.ai_bridge.tasks.task_engine import TaskEngine


def task():
    value = 'The price is 3500 USD.'
    return {'id': 'reviewed_offer', 'title': 'Reviewed offer', 'objective': 'Explain a verified offer.',
            'knowledge': {'price': value}, 'knowledge_evidence': {'price': {
                'value_hash': fact_hash(value), 'source_kind': 'operator', 'source_ref': 'policy:price-v1',
                'reviewed_by': 'pricing-owner', 'reviewed_at': '2026-09-01T00:00:00+00:00',
                'expires_at': '2026-10-01T00:00:00+00:00',
            }}}


def test_review_roundtrips_and_a_changed_fact_invalidates_it():
    record = TaskEngine.validate_contract(task())
    now = datetime(2026, 9, 6, tzinfo=UTC)
    assert all(f['passed'] for f in provenance_findings(record, now))
    record['knowledge']['price'] = 'The price is 9999 USD.'
    assert provenance_findings(record, now)[0]['reason'] == 'fact_changed_since_review'


def test_expired_or_missing_evidence_is_not_approved():
    record = task()
    assert not provenance_findings(record, datetime(2026, 11, 1, tzinfo=UTC))[0]['passed']
    record.pop('knowledge_evidence')
    assert provenance_findings(record)[0]['reason'] == 'missing_evidence'
    assert unsupported_material_categories('It costs 3500 dollars.', record) == {'price'}
    assert not unsupported_material_categories("I cannot verify the price.", record)
    assert unsupported_material_categories('It costs 3500 dollars. Does that work?', record) == {'price'}


def test_operator_pricing_authority_does_not_self_certify_compliance():
    record = task()
    record['knowledge']['price'] = 'SOC2 Type II certified.'
    record['knowledge_evidence']['price']['value_hash'] = fact_hash(record['knowledge']['price'])
    assert provenance_findings(record, datetime(2026, 9, 6, tzinfo=UTC))[0]['reason'] == 'certification_requires_document_or_backend_source'
