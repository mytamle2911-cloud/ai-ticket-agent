import { useState } from 'react'
import { knowledgeReviewModel } from './apiFeedback'

export function FeedbackDialog({ feedback, onClose, sourceRecords = [] }) {
  const [retrying, setRetrying] = useState(false)
  const [showSources, setShowSources] = useState(false)
  const retry = async () => {
    if (retrying || !feedback.retry) return
    setRetrying(true)
    try { await feedback.retry() } finally { setRetrying(false) }
  }
  return <div className="modal-backdrop"><section className="workflow-modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
    <div className="modal-head"><h2 id="feedback-title">{feedback.notices[0].title}</h2></div>
    <div className="workflow-content">{feedback.notices.map((info,index)=><div key={index}>
      <h3>{index ? info.title : ''}</h3><p>{info.message}</p>
      {!!info.details?.missing_fields?.length && <p>Missing fields: {info.details.missing_fields.join(', ')}</p>}
      {info.details?.sources?.map((source)=><p key={source.record_id}>{source.record_id} · {source.title}</p>)}
      {info.details?.details?.map((pair,i)=><p key={i}>“{pair.instruction}” / “{pair.conflicting_instruction}”</p>)}
      {info.details?.questions?.map((question,i)=><p key={i}>{question}</p>)}
      <p>{info.next_action}</p>
    </div>)}
      {feedback.notices.some((info)=>info.code === 'KNOWLEDGE_CONFLICT') && <button className="secondary-button" onClick={()=>setShowSources((show)=>!show)}>Review Sources</button>}
      {showSources && <section aria-label="Retrieved knowledge sources">{sourceRecords.filter((record)=>feedback.notices.some((info)=>info.details?.sources?.some((ref)=>ref.record_id === (record.article_id || record.document_id)))).map((record)=><div className="article-facts" key={record.article_id || record.document_id}><h3>{record.title}</h3><p>{record.source_system} · {record.issue_category || record.issue_type}</p><ul>{[...(record.verified_troubleshooting_steps || []), ...(record.resolution_steps || []), ...(record.diagnostic_steps || [])].map((step,index)=><li key={index}>{typeof step === 'string' ? step : step.instruction}</li>)}</ul></div>)}</section>}
      {feedback.retry && <button className="primary-button" disabled={retrying} onClick={retry}>{retrying ? 'Retrying…' : 'Retry'}</button>}
      <button className="secondary-button" onClick={onClose}>Close</button>
    </div>
  </section></div>
}

export function KnowledgeReviewDialog({ review, busy, inspectedArticle, onView, onCancel, onContinue }) {
  const model = knowledgeReviewModel(review)
  return <div className="modal-backdrop"><section className="workflow-modal" role="dialog" aria-modal="true" aria-labelledby="knowledge-review-title">
    <div className="modal-head"><h2 id="knowledge-review-title">{model.title}</h2></div>
    <div className="workflow-content"><p>This may be useful knowledge for a different case. Review the existing sources before saving another article.</p>
      {model.sources.map((source)=><div className="article-facts" key={source.record_id}><h3>{source.title}</h3><p>{source.source_system} · {source.issue_category}</p><p>Source ticket: {source.source_incident || 'Unavailable'}</p><button className="secondary-button" onClick={()=>onView(source.record_id)}>View existing article</button></div>)}
      {model.warnings.flatMap((warning)=>warning.details || []).map((pair,index)=><p key={index}>“{pair.instruction}” / “{pair.conflicting_instruction}”</p>)}
      {inspectedArticle && <div className="article-facts"><h3>{inspectedArticle.title}</h3><p>{inspectedArticle.symptoms}</p><p>{inspectedArticle.confirmed_root_cause}</p><p>{inspectedArticle.verified_troubleshooting_steps?.join(' · ')}</p><p>{inspectedArticle.verification_method}</p></div>}
      <button className="secondary-button" disabled={busy} onClick={onCancel}>Cancel</button>
      <button className="primary-button" disabled={busy} onClick={onContinue}>{busy ? 'Saving…' : 'Continue Anyway'}</button>
    </div>
  </section></div>
}
