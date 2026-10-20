"""Evidence sufficiency checks shared by deterministic investigation and chat."""
from agent.knowledge_governance import knowledge_conflicts


def investigation_warnings(ticket, retrievals, captured):
    warnings = []
    fields = {
        'source system': ticket.get('source_system') or ticket.get('system'),
        'issue type': ticket.get('issue_type') or ticket.get('issue') or ticket.get('category'),
        'issue description': ticket.get('description') or ticket.get('short_description') or ticket.get('summary'),
        'observed error or symptoms': ticket.get('error_message') or ticket.get('additional_comments') or ticket.get('test_detail'),
    }
    missing = [field for field, value in fields.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        warnings.append({'code': 'missing_incident_information',
            'message': 'Incomplete incident information: ' + ', '.join(missing) + '.',
            'questions': ['Please provide the ' + field + '.' for field in missing]})
    docs = [hit['document'] for hit in retrievals]
    if not docs and not captured:
        warnings.append({'code': 'missing_documentation',
            'message': 'No relevant active knowledge was retrieved. A supported resolution cannot be established; request manual L2 investigation.',
            'questions': ['Provide the affected task, exact observed result, timestamp, and reproduction steps for L2 review.']})
    warnings.extend(knowledge_conflicts([*docs, *captured]))
    return warnings


def apply_evidence_guard(result, warnings):
    result['warnings'] = warnings
    if not warnings:
        return
    reasons = [warning['message'] for warning in warnings]
    questions = [q for warning in warnings for q in warning.get('questions', [])]
    result.update(resolution_path='HUMAN_SUPPORT', resolution_steps=[], proposed_resolution_steps=[], expected_outcomes=[], verification_steps=[],
                  resolution_outcome_status='not_applicable', evidence_status='insufficient_evidence' if result.get('kb_matches') else 'no_matching_evidence',
                  recommended_troubleshooting_steps=[], diagnostic_steps=[],
                  approval_needed=False, approval_required=False, proposed_actions=[],
                  root_cause_assessment={'state': 'insufficient_evidence', 'statement': 'Root cause remains unconfirmed pending missing evidence or knowledge review.', 'source': None, 'verified': False},
                  hypotheses=[], candidate_findings='',
                  recommendation={'action': 'human_investigation', 'text': 'Request L2 review and additional evidence before proposing a definitive resolution.'},
                  decision_reason=' '.join(reasons), recommended_next_steps=questions or ['Have L2 review the conflicting source instructions before selecting a resolution.'],
                  final_analysis='\n\n'.join([*reasons, *[f"{pair['instruction']} / {pair['conflicting_instruction']}" for warning in warnings for pair in warning.get('details', [])], *questions, 'No root cause or resolution has been confirmed. No action was executed.']))
    result['information_gaps'] = list(dict.fromkeys([*result.get('information_gaps', []), *questions]))
