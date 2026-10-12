// Identity-only selections from source-gated original quality records.
export const acceptanceProfile='accepted_requirements_v1';
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const fail=()=>{throw Object.assign(Error('PM_REQUIREMENT_QUALITY_INVALID'),{code:'PM_REQUIREMENT_QUALITY_INVALID'});};
const plan=source=>Object.fromEntries(['revision_id','document_digest','scope_revision'].map(key=>[key,source?.[key]]));
export function requirementCondition(pkg,ids=pkg.document.requirements.map(row=>row.id)){
  const original=plan(pkg.document.context.plan_ref);
  if(!/^pmrev_[0-9a-f]{32}$/.test(original.revision_id)||!/^pmscope_[0-9a-f]{32}$/.test(original.scope_revision)||!/^[0-9a-f]{64}$/.test(original.document_digest))fail();
  const requirements=pkg.document.requirements.filter(row=>ids.includes(row.id)).map(row=>({requirement_id:row.id,acceptance_input_ids:row.acceptance.map(item=>item.source_input_id).sort()}));
  if(!requirements.length||requirements.length>100||requirements.length!==ids.length||requirements.length!==new Set(ids).size
    ||requirements.reduce((count,row)=>count+row.acceptance_input_ids.length,0)>200
    ||requirements.some(row=>!row.acceptance_input_ids.length||new Set(row.acceptance_input_ids).size!==row.acceptance_input_ids.length||row.acceptance_input_ids.some(id=>typeof id!=='string'||!id.length||id.length>200)))fail();
  return {kind:'requirement_acceptance',profile:acceptanceProfile,package_ref:Object.fromEntries(['workspace_id','package_id','document_digest'].map(key=>[key,pkg[key]])),plan_ref:original,requirements};
}
export function assessmentReference(row){const value=row.assessment;return {review_id:row.review_id,operation_id:value.operation_id,version:value.version,document_digest:value.document_digest,candidate_commit:row.context.native_result.candidate_commit};}
export const referenceKey=row=>JSON.stringify(assessmentReference(row));
export function matchesRequirementReview(row,pkg,ids,executionId=null){
  if(row?.qualified!==true||row.applicable!==true||row.assessment?.document?.qualified!==true||executionId&&row.context?.execution_operation_id!==executionId)return false;
  if(!same(plan(row.context?.plan_ref),plan(pkg.document.context.plan_ref)))return false;
  const matrix=row.assessment.document.criteria;
  return Array.isArray(matrix)&&ids.every(id=>matrix.some(item=>item.criterion?.kind==='acceptance'&&item.criterion.requirement_id===id&&item.status==='accepted'));
}
export function requirementSelection(row,pkg,ids){return {condition:requirementCondition(pkg,ids),reference:assessmentReference(row),execution_operation_id:row.context.execution_operation_id};}
export function boundedQualityMap(value){
  // Match the service's public JSON spacing and UTF-8 accounting.
  const wire=v=>Array.isArray(v)?'['+v.map(wire).join(', ')+']':v&&typeof v==='object'?'{'+Object.entries(v).map(([k,x])=>JSON.stringify(k)+': '+wire(x)).join(', ')+'}':JSON.stringify(v);
  if(new TextEncoder().encode(wire(value)).length>16*1024)fail();return value;
}
export function checkRequirementMap(pkg,refs,catalog){
  boundedQualityMap(refs);const needed=new Set(pkg.document.requirements.flatMap(row=>row.depends_on||[]));
  if(Object.keys(refs).some(id=>!needed.has(id)))fail();
  for(const [id,ref]of Object.entries(refs))if(!catalog.some(item=>matchesRequirementReview(item.review,pkg,[id])&&item.package.document.requirements.some(row=>row.id===id)&&same(requirementSelection(item.review,item.package,[id]),ref)))fail();
  return refs;
}
export async function readRequirementReviews(api,workspaceId,valid){
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,rows=[],seen=new Set(),packages=new Map();let after=0,filtered=false;
  for(let page=0;page<20;page++){
    const data=await api('GET',`${base}/quality-reviews?after=${after}&limit=50`);if(!valid())return null;
    if(data.workspace_id!==workspaceId||!Array.isArray(data.reviews)||typeof data.visibility_filtered!=='boolean'||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))fail();
    filtered ||= data.visibility_filtered;
    for(const row of data.reviews){
      if(row.workspace_id!==workspaceId||!/^pmqr_[0-9a-f]{32}$/.test(row.review_id)||seen.has(row.review_id))fail();seen.add(row.review_id);
      if(row.qualified!==true||row.applicable!==true||!row.assessment)continue;
      const ref=assessmentReference(row),source=row.context.package_ref;
      if(!/^op_[0-9a-f]{32}$/.test(ref.operation_id)||!Number.isSafeInteger(ref.version)||ref.version<1||!/^[0-9a-f]{64}$/.test(ref.document_digest)||!/^[0-9a-f]{40}$/.test(ref.candidate_commit)||!/^op_[0-9a-f]{32}$/.test(row.context.execution_operation_id)||source.workspace_id!==workspaceId)fail();
      if(!packages.has(source.package_id)){
        const data=await api('GET',`${base}/work-packages/${encodeURIComponent(source.package_id)}`);if(!valid())return null;
        if(data.workspace_id!==workspaceId||data.package?.workspace_id!==workspaceId||data.package.package_id!==source.package_id)fail();packages.set(source.package_id,data.package);
      }
      const pkg=packages.get(source.package_id);if(pkg.document_digest!==source.document_digest)fail();
      const complete=Array.isArray(row.assessment.document?.criteria)&&pkg.document.requirements.every(req=>req.acceptance.every(input=>row.assessment.document.criteria.some(item=>item.criterion?.kind==='acceptance'&&item.criterion.requirement_id===req.id&&item.criterion.input_id===input.source_input_id&&item.status==='accepted')));
      if(complete&&matchesRequirementReview(row,pkg,pkg.document.requirements.map(item=>item.id)))rows.push({key:referenceKey(row),review:row,package:pkg});
    }
    if(data.next_cursor===null)return {rows,filtered};after=data.next_cursor;
  }fail();
}
