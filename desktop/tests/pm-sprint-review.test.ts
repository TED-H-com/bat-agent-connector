import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hasSprintReview, reviewPromptKeys, reviewReferenceKey, reviewStep, reviewPrompts, reviewProposal, reviewInputSource, emptyReviewBasis} from '../src/pm-sprint-review-state.js';

test('guide requires exactly capability version one',()=>{
  for(const value of [undefined,true,{}, {version:2},{version:'1'}])assert.equal(hasSprintReview({features:{pm_workspaces:{guided_sprint_review:value}}}),false);
  assert.equal(hasSprintReview({features:{pm_workspaces:{guided_sprint_review:{version:1}}}}),true);
});
test('available step is a projection of meeting records; caller objects remain unchanged',()=>{
  const detail:any={context:{},meeting:{meeting_id:'pmmeet_'+'a'.repeat(32)}};
  assert.equal(reviewStep(null),1);assert.equal(reviewStep(detail),2);
  detail.draft={revision_id:'pmmrev_'+'b'.repeat(32),document_digest:'c'.repeat(64),document:{guided_review:{feedback:[]},output:{next_steps:[]},decisions:[{id:'retain'}]}};
  assert.equal(reviewStep(detail),3);detail.draft.document.guided_review.feedback.push({content:'Read-only feedback'});
  assert.equal(reviewStep(detail),4);detail.draft.document.output.next_steps.push('Observe');assert.equal(reviewStep(detail),4);
  assert.equal(reviewStep(detail,[{record:{context:{review_proposal_ref:reviewProposal(detail,'retain')}},revision:{action:'pm.governance.decide'}}]),5);
  assert.equal(reviewStep(detail,[{record:{context:{review_proposal_ref:{...reviewProposal(detail,'retain'),draft_digest:'wrong'}}},revision:{action:'pm.governance.decide'}}]),4);
  const before=structuredClone(detail),ref=reviewProposal(detail,'retain');assert.deepEqual(detail,before);
  assert.deepEqual(ref,{meeting_id:detail.meeting.meeting_id,draft_revision_id:detail.draft.revision_id,draft_digest:detail.draft.document_digest,decision_id:'retain'});
  assert.equal(reviewProposal(detail,'missing'),null);assert.equal(reviewInputSource(detail,'retain'),null);
  detail.completion={completion_id:'pmmc_'+'d'.repeat(32),document_digest:'e'.repeat(64),document:{guided_review:{},decisions:[{id:'retain'}],governance_decisions:[{decision_id:'retain'}]}};
  assert.equal(reviewStep(detail),6);assert.equal(reviewInputSource(detail,'retain')?.completion_digest,'e'.repeat(64));
  detail.completion.document.governance_decisions=[];assert.equal(reviewInputSource(detail,'retain'),null);
});
test('prompt selection preserves source order and discloses every overflow count',()=>{
  for(let size=0;size<=25;size++){const gaps=Array.from({length:size},(_,i)=>({issue_id:String(i)})),value=reviewPrompts(gaps);
    assert.deepEqual(value.shown,gaps.slice(0,3));assert.equal(value.remaining,Math.max(0,size-3));}
  const a=emptyReviewBasis(),b=emptyReviewBasis();a.results.push({} as never);assert.equal(b.results.length,0);
});

test('typed reference identity ignores property order and never mutates the original',()=>{
  for(const ref of [
    {kind:'discussion_input',thread_id:'pmt_'+'1'.repeat(32),input_id:'pmi_'+'2'.repeat(32),operation_id:'op_'+'3'.repeat(32)},
    {review_id:'pmqr_'+'1'.repeat(32),operation_id:'op_'+'2'.repeat(32),version:2,document_digest:'3'.repeat(64),candidate_commit:'4'.repeat(40)},
  ]){
    const before=JSON.stringify(ref),reordered=Object.fromEntries(Object.entries(ref).reverse());
    assert.equal(reviewReferenceKey(ref),reviewReferenceKey(reordered));assert.equal(JSON.stringify(ref),before);
    assert.notEqual(reviewReferenceKey(ref),reviewReferenceKey({...reordered,operation_id:'op_'+'9'.repeat(32)}));
  }
  assert.equal(reviewReferenceKey(null),'null');
});
test('facilitation prompts reflect missing server facts and stop after completion',()=>{
  const detail:any={meeting:{meeting_id:'pmmeet_'+'1'.repeat(32)},context:{}};
  assert.deepEqual(reviewPromptKeys(detail),['pm_review_outcomes_prompt','pm_review_feedback_prompt','pm_review_direction_prompt']);
  detail.draft={revision_id:'pmmrev_'+'2'.repeat(32),document_digest:'3'.repeat(64),document:{guided_review:{feedback:[{}],gaps:[]},output:{next_steps:['Observe']},decisions:[{id:'retain'}]}};
  assert.deepEqual(reviewPromptKeys(detail),['pm_review_direction_prompt']);
  const governance=[{record:{context:{review_proposal_ref:reviewProposal(detail,'retain')}},revision:{action:'pm.governance.decide'}}];
  assert.deepEqual(reviewPromptKeys(detail,governance),[]);detail.draft.document.guided_review.gaps.push({reason:'acceptance_missing'});
  assert.deepEqual(reviewPromptKeys(detail,governance),['pm_review_outcomes_prompt']);
  detail.completion={document:{}};assert.deepEqual(reviewPromptKeys(detail),[]);
});
