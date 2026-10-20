"""Governance and exception contracts using synthetic data and mocked model calls."""
import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from agent import llm_agent, chat_agent, demo_state, knowledge_governance as kg
from agent.investigation_validation import investigation_warnings, apply_evidence_guard
from mock_api import server


class KnowledgeGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, path in [('RESOLUTIONS_FILE', 'resolutions.json'), ('KNOWLEDGE_ARTICLES_FILE', 'articles.json')]:
            p = patch.object(server, name, os.path.join(self.temp.name, path))
            p.start(); self.addCleanup(p.stop)
        self.ticket = {'incident_number': 'INC990001', 'source_system': 'SAP MM', 'category': 'Approval Action',
                       'issue_type': 'Approval Action', 'short_description': 'Approval action unavailable',
                       'additional_comments': 'SYNTH-APR-001', 'status': 'In Progress'}
        p = patch.object(server, 'load_tickets', return_value={'tickets': [self.ticket]})
        p.start(); self.addCleanup(p.stop)
        self.client = server.app.test_client()
        self.resolution = self.client.post('/api/tickets/INC990001/resolutions', json={
            'confirmed_root_cause': 'Synthetic cause', 'actions_performed': ['Enable substitution.'],
            'verification_evidence': 'Reporter retested the task.', 'consultant_id': 'L2', 'consultant_confirmed': True,
        }).get_json()['resolution']

    def create(self, **extra):
        return self.client.post('/api/tickets/INC990001/knowledge-articles', json={
            'source_resolution_id': self.resolution['resolution_id'], 'title': 'Substitution recovery', 'symptoms': 'Approval unavailable', **extra,
        })

    def test_duplicate_requires_review_then_explicit_proceed_without_merge(self):
        first = self.create().get_json()['article']
        second_resolution = {**self.resolution, 'resolution_id': 'RES-SECOND'}
        server.save_resolution_records({'verified_resolutions': [self.resolution, second_resolution]})
        response = self.create(source_resolution_id='RES-SECOND', title='  SUBSTITUTION--recovery ')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['warnings'][0]['sources'][0]['record_id'], first['article_id'])
        self.assertEqual(len(server.load_knowledge_articles()['articles']), 1)
        approved = self.create(source_resolution_id='RES-SECOND', review_acknowledged=True)
        self.assertEqual(approved.status_code, 201)
        self.assertEqual(len(server.load_knowledge_articles()['articles']), 2)

    def test_opposite_instructions_require_review_without_choosing_winner(self):
        self.create()
        second = {**self.resolution, 'resolution_id': 'RES-CONFLICT', 'actions_performed': ['Do not enable substitution.']}
        server.save_resolution_records({'verified_resolutions': [self.resolution, second]})
        response = self.create(source_resolution_id='RES-CONFLICT', title='Alternative guidance')
        self.assertEqual(response.status_code, 409)
        warnings = response.get_json()['warnings']
        self.assertEqual(warnings[0]['code'], 'potential_knowledge_conflict')
        self.assertEqual(len(warnings[0]['sources']), 2)
        self.assertIn('Do not enable substitution.', str(warnings[0]['details']))

    def test_different_resolutions_and_conditions_are_not_automatically_conflicts(self):
        left = {'article_id': 'A', 'source_system': 'SAP MM', 'issue_category': 'Approval', 'verified_troubleshooting_steps': ['Enable substitution.']}
        for steps in [['Restart the browser.'], ['If the task is closed, disable substitution.'], ['Enable another workflow.']]:
            self.assertEqual(kg.knowledge_conflicts([left, {**left, 'article_id': 'B', 'verified_troubleshooting_steps': steps}]), [])

    def test_version_increments_and_preserves_multiple_prior_snapshots(self):
        article = self.create().get_json()['article']
        path = '/api/knowledge-articles/' + article['article_id']
        changed = self.client.patch(path, json={'title': 'Revised title', 'expected_version': 1}).get_json()['article']
        self.assertEqual(changed['version'], 2)
        self.assertEqual(changed['version_history'][0]['title'], article['title'])
        approved = self.client.patch(path, json={'approve': True, 'approved_by': 'Reviewer', 'expected_version': 2}).get_json()['article']
        self.assertEqual(approved['version'], 3)
        self.assertEqual([v['version'] for v in approved['version_history']], [1, 2])
        self.assertEqual(approved['version_history'][1]['review_status'], 'Draft')
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.patch(path, json={'lifecycle_status': 'expired', 'expected_version': 1}).status_code, 409)

    def test_inactive_excluded_but_still_reviewable_and_legacy_defaults_active(self):
        article = self.create().get_json()['article']
        path = '/api/knowledge-articles/' + article['article_id']
        self.client.patch(path, json={'approve': True, 'approved_by': 'Reviewer'})
        for lifecycle in ('expired', 'superseded'):
            changed = self.client.patch(path, json={'lifecycle_status': lifecycle})
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(self.client.get('/api/knowledge-articles/search').get_json()['articles'], [])
            self.assertEqual(self.client.get(path).get_json()['article']['lifecycle_status'], lifecycle)
        legacy = server.load_knowledge_articles()
        legacy['articles'][0].pop('lifecycle_status')
        server.save_knowledge_articles(legacy)
        self.assertEqual(self.client.get('/api/knowledge-articles/search').get_json()['count'], 1)

    def test_save_failure_preserves_persisted_article_and_returns_no_success(self):
        original = self.create().get_json()['article']
        with patch.object(server, 'save_knowledge_articles', side_effect=OSError('private location')):
            response = self.client.patch('/api/knowledge-articles/' + original['article_id'], json={'title': 'Unsaved edits'})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private location', response.get_json()['error'])
        self.assertEqual(server.load_knowledge_articles()['articles'][0], original)

    def test_atomic_write_failure_keeps_previous_json_intact(self):
        original = self.create().get_json()['article']
        with patch.object(server.os, 'replace', side_effect=OSError('disk full')):
            response = self.client.patch('/api/knowledge-articles/' + original['article_id'], json={'title': 'Unsaved'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(server.load_knowledge_articles()['articles'][0], original)
        self.assertEqual(len(list(Path(self.temp.name).glob('*'))), 2)

    def test_missing_docs_and_incomplete_ticket_have_questions_and_block_resolution(self):
        ticket = {'incident_number': 'INC990001', 'source_system': 'Unknown'}
        with patch.object(llm_agent, 'get_ticket', return_value=ticket), patch.object(llm_agent, '_get_client') as model, patch('agent.captured_knowledge.search_approved_articles', return_value=([], None)):
            result = llm_agent.run_investigation('INC990001')
        self.assertEqual({w['code'] for w in result['warnings']}, {'missing_documentation', 'missing_incident_information'})
        self.assertTrue(result['recommended_next_steps'])
        self.assertEqual(result['resolution_path'], 'HUMAN_SUPPORT')
        self.assertFalse(result['root_cause_assessment']['verified'])
        self.assertEqual(result['resolution_steps'], [])
        model.assert_not_called()

    def test_conflicting_retrieved_sources_block_definitive_chat_and_keep_quotes(self):
        left = {'document_id': 'DOC-A', 'source_system': 'SAP MM', 'issue_type': 'Approval Action', 'resolution_steps': [{'instruction': 'Enable substitution.'}]}
        right = {**left, 'document_id': 'DOC-B', 'resolution_steps': [{'instruction': 'Disable substitution.'}]}
        hits = [{'document': row} for row in [left, right]]
        warnings = investigation_warnings(self.ticket, hits, [])
        result = {'ticket': self.ticket, 'information_gaps': []}
        apply_evidence_guard(result, warnings)
        with patch.object(llm_agent, 'get_ticket', return_value=self.ticket), patch.object(llm_agent, '_get_client') as model:
            chat = chat_agent.chat_with_incident('INC990001', 'Give the final resolution', [], result)
        self.assertEqual(chat['ai_provenance'], 'deterministic_guardrail')
        self.assertIn('Potential Knowledge Conflict', chat['reply'])
        self.assertEqual(result['warnings'][0]['sources'][0]['record_id'], 'DOC-A')
        self.assertEqual(result['resolution_steps'], [])
        model.assert_not_called()

    def test_inactive_product_docs_are_excluded_before_matching(self):
        doc = {'source_system': 'SAP MM', 'issue_type': 'Approval Action', 'error_signatures': [{'type': 'message', 'value': 'SYNTH-APR-001'}]}
        for state in ('expired', 'superseded'):
            with patch.object(llm_agent, '_load_json', return_value=[{**doc, 'lifecycle_status': state}]):
                self.assertEqual(llm_agent.search_product_docs('SAP MM', 'SYNTH-APR-001'), [])
        with patch.object(llm_agent, '_load_json', return_value=[doc]):
            self.assertEqual(len(llm_agent.search_product_docs('SAP MM', 'SYNTH-APR-001')), 1)

    def test_timeout_failure_and_incomplete_api_reply_are_never_live_success(self):
        investigation = {'ticket': self.ticket, 'resolution_path': 'HUMAN_SUPPORT'}
        for fault in [TimeoutError('private request'), ConnectionError('private request'), SimpleNamespace(status='incomplete', output_text='Partial advice'), SimpleNamespace(status='failed', output_text='Misleading success')]:
            create = Mock(side_effect=fault) if isinstance(fault, Exception) else Mock(return_value=fault)
            fake = SimpleNamespace(responses=SimpleNamespace(create=create))
            with patch.object(llm_agent, 'get_ticket', return_value=self.ticket), patch.object(llm_agent, '_get_client', return_value=fake):
                result = chat_agent.chat_with_incident('INC990001', 'What evidence is available?', [], investigation)
            self.assertTrue(result['error'])
            self.assertNotEqual(result.get('ai_provenance'), 'live_gpt')
            self.assertEqual(result['reply'], '')
            self.assertNotIn('private request', result['error'])

    def test_browser_state_supports_versions_and_save_failure_does_not_mutate_context(self):
        # Real trusted seed references, with isolated state rather than seed writes.
        state = demo_state.initial_state()
        ticket = next(t for t in demo_state._read_seed('tickets.json', 'tickets') if t['incident_number'] == 'INC100002')
        resolution = {**self.resolution, 'resolution_id': 'RES-BROWSER', 'incident_number': 'INC100002'}
        state['verified_resolutions'].append(resolution)
        article = self.create().get_json()['article']
        article.update(article_id='KBA-BROWSER', source_incident='INC100002', source_resolution_id='RES-BROWSER', source_system=ticket.get('source_system') or ticket.get('system'), issue_category=ticket.get('category') or ticket.get('issue_type'), required_roles=[])
        state['articles'].append(article)
        state = demo_state.validate_state(state)
        with demo_state.use_state(state):
            changed = self.client.patch('/api/knowledge-articles/KBA-BROWSER', json={'title': 'Browser revised'})
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(state['articles'][-1]['version'], 2)
            before = copy.deepcopy(state)
            with patch.object(demo_state, 'save_articles', side_effect=OSError('storage unavailable')):
                self.assertEqual(self.client.patch('/api/knowledge-articles/KBA-BROWSER', json={'title': 'Unsaved browser'}).status_code, 500)
            self.assertEqual(state, before)
        demo_state.validate_state(state)

    def test_native_investigation_flags_conflicts_after_scenario_processing(self):
        docs = [{'document_id': identity, 'source_system': 'SAP MM', 'issue_type': 'Approval Action',
                 'resolution_steps': [{'instruction': instruction}]} for identity, instruction in [('DOC-A', 'Enable substitution.'), ('DOC-B', 'Disable substitution.')]]
        hits = [{'document': doc, 'document_id': doc['document_id'], 'match_type': 'exact', 'matched_signatures': [], 'issue_type_match': True} for doc in docs]
        with patch.object(llm_agent, '_preflight', return_value=(self.ticket, docs, {}, [], hits)), patch('agent.captured_knowledge.search_approved_articles', return_value=([], None)), patch.object(llm_agent, '_get_client') as model:
            result = llm_agent.run_investigation('INC990001')
        self.assertEqual(result['warnings'][0]['code'], 'potential_knowledge_conflict')
        self.assertEqual(result['proposed_resolution_steps'], [])
        self.assertEqual(result['recommended_troubleshooting_steps'], [])
        self.assertIn('Disable substitution.', result['final_analysis'])
        self.assertFalse(result['approval_needed'])
        model.assert_not_called()

    def test_missing_source_is_an_explicit_question_without_ungrounded_investigation(self):
        ticket = {**self.ticket, 'source_system': ''}
        with patch.object(llm_agent, 'get_ticket', return_value=ticket), patch.object(llm_agent, '_get_client') as model:
            result = llm_agent.run_investigation('INC990001')
        self.assertIsNone(result['error'])
        self.assertIn('Please provide the source system.', result['recommended_next_steps'])
        self.assertEqual(result['ticket'], ticket)
        model.assert_not_called()

    def test_new_draft_save_failure_does_not_claim_created(self):
        with patch.object(server, 'save_knowledge_articles', side_effect=OSError('private details')):
            response = self.create()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('article', response.get_json())
        self.assertEqual(server.load_knowledge_articles()['articles'], [])

    def test_duplicate_approval_requires_review_and_does_not_publish_automatically(self):
        first = self.create().get_json()['article']
        second = {**first, 'article_id': 'KBA-OTHER', 'source_resolution_id': 'RES-OTHER'}
        server.save_knowledge_articles({'articles': [first, second]})
        path = '/api/knowledge-articles/' + first['article_id']
        response = self.client.patch(path, json={'approve': True, 'approved_by': 'Reviewer'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(server.load_knowledge_articles()['articles'][0], first)
        response = self.client.patch(path, json={'approve': True, 'approved_by': 'Reviewer', 'review_acknowledged': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['article']['review_status'], 'Approved')

    def test_incomplete_generation_drafts_are_rejected_even_if_text_looks_valid(self):
        from agent import knowledge_capture, customer_communication
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=SimpleNamespace(status='incomplete', output_text='{"title":"Looks valid","symptoms":"Looks valid"}'))))
        with self.assertRaisesRegex(knowledge_capture.KnowledgeCaptureError, 'incomplete'):
            knowledge_capture._model_draft({}, client=fake)
        fake.responses.create.return_value.output_text = '{"subject":"Looks valid","body":"Looks valid"}'
        with patch.object(customer_communication, '_verified_resolution_for_ticket', return_value=None), self.assertRaisesRegex(customer_communication.CustomerCommunicationError, 'incomplete'):
            customer_communication.generate_customer_email_draft(self.ticket, {}, {}, investigation={}, client=fake)

    def test_conditional_or_other_scope_instructions_do_not_trigger_false_conflict(self):
        base = {'article_id': 'A', 'source_system': 'SAP MM', 'issue_category': 'Approval', 'verified_troubleshooting_steps': ['Enable substitution if the workflow supports it.']}
        alternate = {**base, 'article_id': 'B', 'verified_troubleshooting_steps': ['Disable substitution if the workflow does not support it.']}
        self.assertEqual(kg.knowledge_conflicts([base, alternate]), [])
        other_system = {**base, 'article_id': 'C', 'source_system': 'SAP SD', 'verified_troubleshooting_steps': ['Disable substitution if the workflow supports it.']}
        self.assertEqual(kg.knowledge_conflicts([base, other_system]), [])

    def test_invalid_lifecycle_is_validation_error_and_legacy_update_preserves_history(self):
        article = self.create().get_json()['article']
        legacy = {key: value for key, value in article.items() if key not in kg.METADATA_KEYS}
        server.save_knowledge_articles({'articles': [legacy]})
        path = '/api/knowledge-articles/' + article['article_id']
        self.assertEqual(self.client.patch(path, json={'lifecycle_status': []}).status_code, 400)
        response = self.client.patch(path, json={'title': 'Updated legacy'})
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()['article']
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['version_history'][0]['title'], article['title'])

    def test_previous_versions_are_reviewable_but_never_retrieved_as_active_knowledge(self):
        from agent import captured_knowledge
        article = self.create().get_json()['article']
        path = '/api/knowledge-articles/' + article['article_id']
        self.client.patch(path, json={'title': 'Active revised title'})
        self.client.patch(path, json={'approve': True, 'approved_by': 'Reviewer'})
        review = self.client.get(path).get_json()['article']
        self.assertEqual(len(review['version_history']), 2)
        retrieved = self.client.get('/api/knowledge-articles/search').get_json()['articles'][0]
        self.assertNotIn('version_history', retrieved)
        # Defense in depth if a service returns a full article despite its contract.
        response = Mock()
        response.json.return_value = {'articles': [review]}
        with patch.object(captured_knowledge.internal_api, 'get', return_value=response):
            matches, error = captured_knowledge.search_approved_articles(self.ticket)
        self.assertIsNone(error)
        self.assertNotIn('version_history', matches[0])
        self.assertNotIn('version_history', llm_agent._professional_model_text(review))
        self.assertNotIn('version_history', chat_agent._professional_context(review))
