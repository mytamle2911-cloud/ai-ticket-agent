import assert from 'node:assert/strict'
import test from 'node:test'
import Module from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { buildSync } from 'esbuild'

// Compile the actual JSX component using the existing Vite/esbuild dependency.
const filename = fileURLToPath(new URL('../src/FeedbackDialog.jsx', import.meta.url))
const compiled = buildSync({ entryPoints:[filename], bundle:true, platform:'node', format:'cjs', jsx:'automatic', write:false, external:['react', 'react/jsx-runtime'] }).outputFiles[0].text
const componentModule = new Module(filename)
componentModule.filename = filename
componentModule.paths = Module._nodeModulePaths(dirname(filename))
componentModule._compile(compiled, filename)
const { KnowledgeReviewDialog, FeedbackDialog } = componentModule.exports
const review = { warnings:[{ code:'potential_duplicate', sources:[{ record_id:'KBA-A', title:'Existing task article', source_system:'SAP MM', issue_category:'Approval Action', source_incident:'INC990001' }] }] }

function elements(node) {
  if (!node || typeof node !== 'object') return []
  return [node, ...React.Children.toArray(node.props?.children).flatMap(elements)]
}

test('actual duplicate dialog renders matching title, system, category, ticket and working actions', () => {
  let saves = 0; let cancelled = false; let viewed
  const props = { review, busy:false, onCancel:()=>{cancelled=true}, onContinue:()=>{saves++}, onView:(id)=>{viewed=id} }
  const markup = renderToStaticMarkup(React.createElement(KnowledgeReviewDialog, props))
  for (const text of ['Similar Knowledge Article Found', 'Existing task article', 'SAP MM', 'Approval Action', 'INC990001', 'View existing article', 'Cancel', 'Continue Anyway']) assert.ok(markup.includes(text), text)
  const buttons = elements(KnowledgeReviewDialog(props)).filter((node)=>node.type === 'button')
  buttons.find((button)=>button.props.children === 'Cancel').props.onClick()
  assert.equal(cancelled, true)
  assert.equal(saves, 0)
  buttons.find((button)=>button.props.children === 'View existing article').props.onClick()
  assert.equal(viewed, 'KBA-A')
  buttons.find((button)=>button.props.children === 'Continue Anyway').props.onClick()
  assert.equal(saves, 1)
})

test('error dialog renders missing fields and next action; no raw exception details', () => {
  const markup = renderToStaticMarkup(React.createElement(FeedbackDialog, {
    feedback:{ notices:[{ title:'Insufficient Ticket Information', message:'More information is needed.', details:{ missing_fields:['short_description'] }, next_action:'Ask the reporter for a description.' }] }, onClose:()=>{},
  }))
  assert.ok(markup.includes('short_description'))
  assert.ok(markup.includes('Ask the reporter for a description.'))
  assert.ok(markup.includes('Close'))
  assert.equal(markup.includes('Retry</button>'), false)
})
