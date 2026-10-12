import test from 'node:test';
import assert from 'node:assert/strict';
import {requirementReviews} from '../src/pm-traceability.js';
test('requirement evidence never crosses plan versions, criteria or withdrawn assessments',()=>{
  const plan={revision_id:'revision-2',document_digest:'digest-2',adoption:{scope_revision:'scope-2'}};
  const req={id:'login',acceptance:[{source_input_id:'new-criterion'}]};
  const review={qualified:true,applicable:true,context:{plan_ref:{revision_id:'revision-2',document_digest:'digest-2',scope_revision:'scope-2'}},
    assessment:{document:{qualified:true,criteria:[{criterion:{kind:'acceptance',requirement_id:'login',input_id:'new-criterion'},status:'accepted'}]}}};
  assert.deepEqual(requirementReviews(plan,req,[review]),[review]);
  for(const field of ['revision_id','document_digest','scope_revision']){
    const other=structuredClone(review);other.context.plan_ref[field]='previous';assert.deepEqual(requirementReviews(plan,req,[other]),[]);
  }
  assert.deepEqual(requirementReviews(plan,{...req,acceptance:[{source_input_id:'changed-criterion'}]},[review]),[]);
  assert.deepEqual(requirementReviews(plan,req,[{...review,applicable:false}]),[]);
  assert.deepEqual(requirementReviews(plan,req,[{...review,qualified:false}]),[]);
  assert.deepEqual(requirementReviews(plan,{...req,acceptance:[]},[review]),[]);
});
