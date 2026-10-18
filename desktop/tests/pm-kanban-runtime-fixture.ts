import {openPmSection} from './pm-navigation.ts';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {fixture as boardFixture,workId} from './pm-kanban-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
export const action='pm.session.quiescence.observe',route='/dashboard/#/pm/'+workspaceId;
export const executionId='op_'+'a'.repeat(32),observerId='op_'+'b'.repeat(32);
export const manualSource=(n=1)=>({link_id:n,host:'fixture-host',session_id:'manual-session-'+n,
  expected_link_operation_id:'op_'+String(n).repeat(32),expected_project_binding_id:'pmpb_'+'4'.repeat(32),
  expected_session_identity_digest:String(n).repeat(64),available:true,reason:null as string|null});
export function runtimeFixture(){
  const base=pmFixture(),board=boardFixture(),card=board.state.cards[0];
  card.execution.tasks=[];card.execution.accepted_attempts=1;
  card.execution.attempts=[{attempt_id:executionId,operation_id:executionId,kind:'execution_operation',accepted:true,status:'succeeded',observed_at:1791705600,
    runtime_completion:{observation_operation_id:observerId,execution_operation_id:executionId,document_digest:'d'.repeat(64),recorded_at:1791705620,
      runtime_status:'original_turn_completed',process_death_proven:false}}];
  card.execution.sessions=[{resource_id:'fixture-host/retained-managed',session_id:'retained-managed',runtime_status:'idle',observed_at:1791705620,streaming:false}];
  card.execution.manual_observation_sources=[manualSource(1),manualSource(2)];
  const state={enabled:true,lost:false,writes:[] as any[],ops:new Map<string,any>(),failReceipt:0,deferReceipt:null as Promise<void>|null,receiptReads:0};
  const dispatch=async(input:any):Promise<any>=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/kanban'))return board.dispatch(input);
    if(path==='/capabilities'||path==='/bootstrap'){
      const response=await base.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      caps.features.pm_workspaces.kanban=true;
      if(state.enabled)caps.actions.push({action,allowed:true});
      return response;
    }
    if(input.method==='POST'&&path==='/operations'&&input.body.action===action){
      state.writes.push(structuredClone(input));let operation=state.ops.get(input.idempotency_key);
      if(!operation){operation={...structuredClone(input.body),operation_id:'op_'+'c'.repeat(32),actor:base.state.actor,
        idempotency_key:input.idempotency_key,status:'waiting_external',result:null};state.ops.set(input.idempotency_key,operation);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'UNAVAILABLE',message:'Original reply was lost'}}};}
      return {status:200,data:{operation:structuredClone(operation)}};
    }
    if(input.method==='GET'&&path.startsWith('/operations/')){
      ++state.receiptReads;
      if(state.failReceipt)return {status:state.failReceipt,data:{error:{code:'FORBIDDEN',message:'Original source access removed'}}};
      const operation=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));
      if(operation){const data={operation:structuredClone(operation)};if(state.deferReceipt)await state.deferReceipt;return {status:200,data};}
    }
    return base.dispatch(input);
  };
  const complete=(count=1)=>{const operation=[...state.ops.values()][0];operation.status='succeeded';operation.result={schema:'pm_manual_single_input_quiescence_v1',workspace_id:workspaceId,
    work_item_id:workId(1),link_id:operation.params.link_id,observation_operation_id:operation.operation_id,document_digest:'e'.repeat(64),recorded_at:1791705700,
    host:'fixture-host',session_id:'manual-session-'+operation.params.link_id,sdk_session_id:'sdk-manual-original',native_turn_id:'native-turn-1',
    origin_digest:'1'.repeat(64),event_digest:'2'.repeat(64),event_count:count*4,
    input_state:{schema:'bat_input_state_v1',epoch:'a'.repeat(32),received:count,pending:0},completion_cursor:{epoch:'original-replay',seq:count*4},
    runtime_status:'current_single_input_quiescent',process_death_proven:false,ownership:'manual_read_only',work_acceptance:'not_evaluated'};
    if(count!==1){delete operation.result.native_turn_id;Object.assign(operation.result,{schema:'pm_manual_serial_inputs_quiescence_v1',
      runtime_status:'current_serial_inputs_quiescent',completed_turn_count:count,turns_digest:'3'.repeat(64)});}
  };
  return {base,board,card,state,dispatch,complete};
}
export async function mountRuntime(page:any,native:boolean,f:ReturnType<typeof runtimeFixture>){
  await mountConversation(page,native,f.dispatch);
  if(native)await page.addInitScript((caps:any)=>{const ipc=(window as any).__TAURI_INTERNALS__,invoke=ipc.invoke;
    ipc.invoke=(command:string,args:any)=>command==='connector_connect'?Promise.resolve(caps):invoke(command,args);
  },(await f.dispatch({method:'GET',path:'/capabilities'})).data);
  await page.goto(route);await openPmSection(page, 'board');
}
