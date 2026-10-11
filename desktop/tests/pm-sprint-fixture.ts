export const workspaceId='ws_'+'1'.repeat(32), sprintId='pms_'+'2'.repeat(32), planId='pmrev_'+'3'.repeat(32), pid='prj_'+'4'.repeat(20);
export const actions=['pm.product_goal.revise','pm.dod.revise','pm.backlog.revise','pm.sprint.create','pm.sprint.goal.revise','pm.sprint.select','pm.sprint.start','pm.sprint.close','pm.sprint.cancel'];
export function fixture() {
  const wid=(i:number)=>'wi_'+String(i).repeat(20), bid=(i:number)=>'pmwb_'+String(i).repeat(32);
  const bindings=[1,2].map(i=>({workspace_id:workspaceId,binding_id:bid(i),work_item_id:wid(i),project_id:pid,plan_revision_id:planId,requirement_ids:['membership.login'],execution_spec:'not_assigned'}));
  const artifact=(kind:string,prefix:string,document:any)=>({workspace_id:workspaceId,revision_id:prefix+'1'.repeat(32),kind,document,actor:'owner',sequence:1});
  const goal=artifact('product_goal','pmgoal_',{text:'Members can regain account access'}),dod=artifact('dod','pmdod_',{criteria:[{id:'tests',text:'Relevant tests verify the fixed change'}]}),
    backlog=artifact('backlog','pmbkl_',{product_goal_revision_id:goal.revision_id,items:bindings});
  const all=new Map([goal,dod,backlog].map(row=>[row.revision_id,row]));
  const state:any={role:'owner',actor:'owner',manage:true,elapsed:false,lost:false,failRead:false,wrongScope:false,planId,writes:[],ops:new Map(),artifacts:{workspace_id:workspaceId,product_goal:goal,dod,backlog},
    sprints:[{workspace_id:workspaceId,sprint_id:sprintId,version:2,status:'planned',sequence:1,definition:{title:'Recover account access',mode:'personal_iteration',owner_actor:'owner',developers:['owner','developer'],
      scrum_master:null,product_goal_revision_id:goal.revision_id,dod_revision_id:dod.revision_id,backlog_revision_id:backlog.revision_id,
      starts_at:new Date(Date.now()-3600000).toISOString(),ends_at:new Date(Date.now()+604800000).toISOString(),capacity_note:'One focused delivery, two developers'},
      goal:{sprint_id:sprintId,text:'A returning member can sign in',revision_id:'pmsg_'+'1'.repeat(32),sequence:1,actor:'owner',rationale:'Keep this round focused'},
      selected:[{...bindings[0],assignee:'owner',how:'Verify login in my own environment'}],history:[]}]};
  const config=()=>({caps:{actor:state.actor,scopes:['observe',...(state.manage?['manage']:[])],actions:actions.map(action=>({action,allowed:state.manage}))},
    workspace:{workspace_id:workspaceId,version:4,membership:{actor:state.actor,role:state.role},
      members:[{actor:'owner',role:state.actor==='owner'?state.role:'owner'},{actor:'developer',role:'contributor'},{actor:'reader',role:'reader'}]}});
  const detail=(s:any)=>({version:1,sprint:{...s,workspace_id:state.wrongScope?'ws_'+'9'.repeat(32):workspaceId},goal:s.goal,selected_items:s.selected,
    goal_revisions:[s.goal],selection_revisions:[],history:s.history,timebox_elapsed:state.elapsed,quality:'unassessed',history_next_cursors:state.more?{goals:1}: {}});
  const response=(data:any,status=200)=>({data,status});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname,base='/pm/workspaces/'+workspaceId;
    if(input.method==='GET'){
      if(pathname.startsWith('/operations/'))return response({operation:[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id))});
      if(state.failRead)return response({error:{code:state.failRead==='revoked'?'FORBIDDEN':'UNAVAILABLE',message:state.failRead==='revoked'?'Workspace access removed':'Sprint read unavailable'}},state.failRead==='revoked'?403:503);
      if(pathname===base+'/sprint-artifacts')return response(state.artifacts);
      if(pathname.startsWith(base+'/sprint-artifacts/')&&pathname.split('/').length===6)return response({revisions:[state.artifacts[pathname.split('/').at(-1)!]],next_cursor:null});
      if(pathname.startsWith(base+'/sprint-artifacts/'))return response({artifact:all.get(pathname.split('/').at(-1)!)});
      if(pathname===base+'/plans/accepted')return response({head:{workspace_id:workspaceId,accepted_revision_id:state.planId},plan:{workspace_id:workspaceId,revision_id:state.planId}});
      if(pathname===base+'/work')return response({workspace_id:workspaceId,bindings,next_cursor:null});
      if(pathname===base+'/sprints')return response({sprints:state.sprints,next_cursor:null});
      if(pathname.includes('/history/'))return response({workspace_id:workspaceId,sprint_id:sprintId,kind:'goals',records:[{...state.sprints[0].goal,sequence:2,text:'Preserved earlier direction'}],next_cursor:null});
      if(pathname.startsWith(base+'/sprints/'))return response(detail(state.sprints.find((s:any)=>pathname.endsWith(s.sprint_id))));
      if(pathname==='/projects/'+pid)return response({project:{project_id:pid},work_items:[{work_item_id:wid(1),title:'Member login',children:[]},{work_item_id:wid(2),title:'Password recovery',children:[]}]});
      throw Error('Unexpected read '+input.path);
    }
    if(input.method!=='POST'||pathname!=='/operations')throw Error('Unexpected write '+input.path);
    state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
    if(!op){const {action,target,params}=input.body;let result:any={workspace_id:workspaceId,execution_authorized:false};
      if(action==='pm.sprint.create'){
        const id='pms_'+String(state.sprints.length+2).repeat(32),s={workspace_id:workspaceId,sprint_id:id,version:1,status:'planned',sequence:state.sprints.length+1,
          definition:{...params,product_goal_revision_id:state.artifacts.product_goal.revision_id},selected:[],history:[],
          goal:{sprint_id:id,text:params.goal,revision_id:'pmsg_'+'2'.repeat(32),sequence:1,actor:state.actor,rationale:params.rationale}};
        state.sprints.push(s);result.sprint_id=id;
      }else if(action.startsWith('pm.sprint.')){
        const s=state.sprints.find((row:any)=>row.sprint_id===target.sprint_id);s.version++;
        if(action==='pm.sprint.select')s.selected=params.items.map((row:any)=>({...bindings.find(i=>i.binding_id===row.binding_id),...row}));
        if(action==='pm.sprint.start')s.status='active';if(action==='pm.sprint.close')s.status='closed';if(action==='pm.sprint.cancel')s.status='cancelled';
        if(action==='pm.sprint.goal.revise')s.goal={...s.goal,text:params.goal,sequence:2};
        s.history.push({sprint_id:s.sprint_id,workspace_id:workspaceId,sequence:s.history.length+1,actor:state.actor,action,document:{rationale:params.rationale,
          items:s.selected.map((row:any)=>({...row,quality:'unassessed',reason:'DoD has not been assessed',counts_as_done:false}))}});result.sprint_id=s.sprint_id;
      }else{
        const kind=action.split('.')[1],prior=state.artifacts[kind],revision_id=prior.revision_id.replace(/1/g,'2');
        const document=kind==='product_goal'?{text:params.text}:kind==='dod'?{criteria:params.criteria}:{product_goal_revision_id:params.product_goal_revision_id,items:params.binding_ids.map((id:string)=>bindings.find(row=>row.binding_id===id))};
        const next={...prior,revision_id,document};all.set(revision_id,next);state.artifacts[kind]=next;result.revision_id=revision_id;
      }
      op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result};state.ops.set(input.idempotency_key,op);
    }
    if(state.lost){state.lost=false;return response({error:{code:'LOST_REPLY',message:'Reply unavailable'}},503);}
    return response({operation:op});
  };
  return {state,config,dispatch,bindings};
}
