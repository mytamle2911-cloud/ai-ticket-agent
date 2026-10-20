"""Evidence sufficiency checks shared by deterministic investigation and chat."""
from agent.knowledge_governance import knowledge_conflicts, is_active


def investigation_warnings(ticket, retrievals, captured):
    warnings = []
    # Source + issue + description are needed by the existing retrieval workflow.
    # Comments/error codes are optional in the ServiceNow seed schema.
    aliases = [('source_system', ('source_system', 'system')),
               ('category', ('category', 'issue_type', 'issue')),
               ('short_description', ('short_description', 'description', 'summary'))]
    missing = [next((key for key in names if key in ticket), canonical)
               for canonical, names in aliases
               if not any(isinstance(ticket.get(key), str) and ticket[key].strip() for key in names)]
    if missing:
        warnings.append({'code': 'missing_incident_information', 'missing_fields': missing,
            'message': 'Incomplete incident information: ' + ', '.join(missing) + '.',
            'questions': ['Please provide the ' + field.replace('_', ' ') + '.' for field in missing]})
    docs = [hit['document'] for hit in retrievals]
    usable = [doc for doc in docs if is_active(doc) and any(doc.get(key) for key in
              ('symptoms', 'possible_causes', 'diagnostic_steps', 'resolution_steps', 'required_information'))]
    if not usable and not captured:
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
