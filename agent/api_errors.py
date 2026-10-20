"""Public API notices; legacy string errors remain compatible with existing clients."""

DEFINITIONS = {
    'DUPLICATE_KNOWLEDGE': ('Similar Knowledge Article Found', 'Review the existing article. Cancel or explicitly Continue Anyway.', False),
    'AI_API_FAILURE': ('AI Service Unavailable', 'Retry the request or continue manual investigation.', True),
    'MISSING_DOCUMENTATION': ('No Relevant Documentation', 'Request documentation or continue with manual L2 investigation.', False),
    'INCOMPLETE_TICKET': ('Insufficient Ticket Information', 'Request the listed ticket fields before confirming a diagnosis.', False),
    'KNOWLEDGE_CONFLICT': ('Conflicting Knowledge Sources', 'Review the cited sources with L2 before choosing a resolution.', False),
    'SAVE_FAILED': ('Changes Could Not Be Saved', 'Your edits remain in the form. Check the current record before retrying.', True),
    'REQUEST_FAILED': ('Request Could Not Be Completed', 'Review the request and try again when the service is available.', False),
}
WARNING_CODES = {'potential_duplicate': 'DUPLICATE_KNOWLEDGE', 'potential_knowledge_conflict': 'KNOWLEDGE_CONFLICT',
                 'missing_documentation': 'MISSING_DOCUMENTATION', 'missing_incident_information': 'INCOMPLETE_TICKET'}


def notice(code, message, details=None, *, blocking=False):
    title, action, retryable = DEFINITIONS[code]
    return {'code': code, 'title': title, 'message': message, 'details': details or {},
            'retryable': retryable, 'blocking': blocking, 'next_action': action}


def warning_notice(warning):
    return notice(WARNING_CODES.get(warning.get('code'), 'REQUEST_FAILED'), warning['message'],
                  {key: warning[key] for key in ('sources', 'details', 'questions', 'missing_fields') if key in warning})
