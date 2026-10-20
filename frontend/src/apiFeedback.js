// Keep legacy string errors while using structured backend notices for popups.
export function failureNotice(error) {
  if (error.payload?.error_info) return error.payload.error_info
  return {
    code:'REQUEST_FAILED', title:'Request Could Not Be Completed',
    message:'The service response could not be confirmed. Your edits have not been discarded.',
    details:{}, retryable:true,
    next_action:'Check your connection. For a save request, review the current record before retrying.',
  }
}

export function createFeedbackApi(request, onFeedback) {
  return async (url, options = {}) => {
    const { onRetry, ...requestOptions } = options
    try {
      const result = await request(url, requestOptions)
      if (result.success === false || result.error) {
        const error = new Error(result.error_info?.message || result.error || 'Request failed.')
        error.payload = result
        throw error
      }
      if (result.notices?.length) onFeedback({ notices:result.notices, retry:null })
      return result
    } catch (error) {
      // Knowledge review uses its own confirmation modal, not a second popup.
      if (!error.payload?.governance_review_required) {
        const info = failureNotice(error)
        onFeedback({ notices:[info], retry:info.retryable && onRetry ? onRetry : null })
      }
      throw error
    }
  }
}

export function knowledgeReviewModel(review) {
  const duplicate = review.warnings?.find((warning)=>warning.code === 'potential_duplicate')
  return {
    title:duplicate ? 'Similar Knowledge Article Found' : 'Potential Knowledge Conflict',
    warnings:review.warnings || [],
    sources:(review.warnings || []).flatMap((warning)=>warning.sources || []).filter((source)=>!source.proposed),
  }
}
