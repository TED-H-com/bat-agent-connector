export const NATIVE='codex-native-linux',BAT='bat-pm-linux-v1';
export function structuredCapability({native=false,bat=true}={}){
  const adapters=[{adapter:NATIVE,action_registered:true,authorized:true,eligible_for_preflight:native,reason:native?null:'PM_NATIVE_EXECUTABLE_UNAVAILABLE'},
    {adapter:BAT,action_registered:true,authorized:true,eligible_for_preflight:bat,reason:bat?null:'PM_WORKTREE_REQUIRED',
      selected_binding:{host:'fixture-bat',workspace_id:'fixture-project',repository:'example/member-access'},runtime_attestation_required:true}];
  return {version:1,default_adapter:NATIVE,actions:{'pm.conversation.propose':{adapters:structuredClone(adapters)},'pm.follow_up.analyze':{adapters:structuredClone(adapters)}}};
}
