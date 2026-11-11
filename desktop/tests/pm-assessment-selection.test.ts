import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assessmentSelection, assessmentQuery, parseAssessmentSelection, matchesAssessment, matchesAttempt,
  matchesResult, contextualAssessment, selectionFromResult} from '../src/pm-assessment-selection.js';
import {sessionOrigin, sessionHref} from '../src/session-navigation.js';
const id = (prefix: string, n = '1', length = 32) => prefix + n.repeat(length);
const pin = {partition:'fixture-partition',workspace_id:id('ws_'),work_item_id:id('wi_','1',20),attempt_id:id('att_'),
  result_id:id('res_'),result_digest:'sha256:'+id('','a',64),package_id:id('pmwp_'),package_digest:id('','b',64),
  execution_operation_id:id('op_','2'),candidate_commit:id('','a',40),review_id:id('pmqr_'),operation_id:id('op_','3'),
  version:2,document_digest:id('','c',64),origin:'result',requirement_id:'member.login'};
const exact = {workspace_id:pin.workspace_id,review_id:pin.review_id,context_digest:id('','d',64),
  semantics:'immutable_original_assessment',current_applicability:'not_evaluated',
  context:{work_ref:{work_item_id:pin.work_item_id},package_ref:{workspace_id:pin.workspace_id,package_id:pin.package_id,document_digest:pin.package_digest},
    execution_operation_id:pin.execution_operation_id,native_result:{candidate_commit:pin.candidate_commit}},
  assessment:{kind:'assessment',operation_id:pin.operation_id,version:pin.version,document_digest:pin.document_digest,document:{criteria:[]}}};
const result = {contract:'pm-work-attempt-result/v1',workspace_id:pin.workspace_id,attempt_id:pin.attempt_id,
  identity:{workspace_id:pin.workspace_id,work_item_id:pin.work_item_id,attempt_id:pin.attempt_id,package_id:pin.package_id,execution_operation_id:pin.execution_operation_id},
  sources:{result_id:pin.result_id,digest:pin.result_digest},changes:{fixed_result_commit:{commit_sha:pin.candidate_commit}},
  requirements:{package_ref:exact.context.package_ref,items:[{requirement_id:'member.login',assessments:[{reference:pin,applicable:false,qualified:false}]}]}};

test('rejects_cross_workspace_attempt_and_result_receipt_mismatch',()=>{
  const attempt={workspace_id:pin.workspace_id,attempt:{...pin}};
  assert.ok(matchesAttempt(pin,attempt));assert.ok(matchesResult(pin,result));assert.ok(matchesAssessment(pin,exact));
  for(const key of ['workspace_id','work_item_id','attempt_id','package_id','execution_operation_id']){
    assert.equal(matchesAttempt(pin,{...attempt,attempt:{...attempt.attempt,[key]:id('other_')}}),false);
    assert.equal(matchesResult(pin,{...result,identity:{...result.identity,[key]:id('other_')}}),false);
  }
  for(const key of ['result_id','digest'])assert.equal(matchesResult(pin,{...result,sources:{...result.sources,[key]:'changed'}}),false);
  assert.equal(matchesAssessment(pin,{...exact,context:{...exact.context,package_ref:{...exact.context.package_ref,document_digest:id('','e',64)}}}),false);
  assert.equal(assessmentSelection(pin,'another-partition'),null);
});
test('keeps_two_assessment_kinds_separate',()=>{
  assert.equal(assessmentSelection({...pin,review_id:id('pmoa_')},pin.partition),null);
  assert.equal(matchesAssessment(pin,{...exact,semantics:'workspace_judgment'}),false);
});
test('requires_explicit_successor_selection',()=>{
  assert.equal(matchesAssessment(pin,{...exact,assessment:{...exact.assessment,version:3}}),false);
  assert.equal(matchesAssessment(pin,{...exact,assessment:{...exact.assessment,operation_id:id('op_','4')}}),false);
  assert.equal(contextualAssessment(pin,result)?.applicable,false);
  assert.equal(contextualAssessment(pin,{...result,sources:{...result.sources,result_id:id('res_','2')}}),null);
  assert.equal(contextualAssessment(pin,{...result,requirements:{...result.requirements,items:[]}}),null);
});
test('serializes_identity_only_and_allows_only_known_return_routes',()=>{
  const query=assessmentQuery(pin,pin.partition);
  assert.deepEqual(parseAssessmentSelection(query,pin.partition),assessmentSelection(pin,pin.partition));
  for(const change of [{body:'private'},{return_url:'https://example.invalid/'},{origin:'today'},{version:true},{version:34},{result_digest:'latest'}])
    assert.equal(assessmentSelection({...pin,...change},pin.partition),null);
  for(const suffix of ['&qa_version=2','&qa_latest=true','&qa_origin=overview'])assert.equal(parseAssessmentSelection(new URLSearchParams(query+'&'+suffix),pin.partition),null);
  const session=sessionHref({host:'fixture-host',sessionId:'session-1',workspaceId:pin.workspace_id,workItemId:pin.work_item_id,attemptId:pin.attempt_id});
  assert.ok(sessionOrigin(session+'&'+query));
  assert.equal(sessionOrigin(session+'&'+assessmentQuery({...pin,attempt_id:id('att_','2')},pin.partition)),'');
});

import {assessmentSourceFence} from '../src/pm-assessment-source-fence.js';
test('one source withdrawal fences late reads at both mounted entrances',()=>{
  let removed=0;
  const a=assessmentSourceFence(pin,()=>removed++),b=assessmentSourceFence(pin,()=>removed++);
  const other=assessmentSourceFence({...pin,partition:'other'},()=>assert.fail('Cross-partition withdrawal'));
  const oldA=a.token(),oldB=b.token(),otherToken=other.token();
  a.withdraw(Error('Source unavailable'));
  assert.equal(a.current(oldA),false);assert.equal(b.current(oldB),false);assert.equal(removed,2);
  assert.equal(other.current(otherToken),true);
  const retry=b.token();assert.equal(b.current(retry),true);
  a.release();b.release();other.release();assert.equal(b.current(retry),false);
});


test('Overview enrichment keeps the original return control when a recorded assessment is selected',()=>{
  const reference={...pin,return_review_id:pin.review_id,return_operation_id:id('op_','5'),return_document_digest:id('','e',64)};
  const enriched=selectionFromResult({partition:pin.partition,envelope:result,reference,origin:'overview'});
  assert.equal(enriched.return_operation_id,reference.return_operation_id);
  assert.equal(enriched.return_review_id,reference.return_review_id);
  assert.equal(enriched.return_document_digest,reference.return_document_digest);
  assert.equal(enriched.operation_id,pin.operation_id);
});
