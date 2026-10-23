use super::*;
use bat_fleet_core::{creation_evidence::ManagedIdentity, Result as NativeResult};

#[derive(Clone)]
pub(super) struct ServiceIdentity {
    pub actor: String,
    pub identity: credentials::Identity,
}
impl Managed {
    pub(crate) fn creation_binding(
        &self,
        _bridge: &bridge::CreationScope,
        _read: impl FnMut(&Path, usize) -> NativeResult<Vec<u8>>,
    ) -> NativeResult<Option<ManagedIdentity>> {
        Err("CREATION_OPERATION_UNSUPPORTED")
    }
    #[cfg(test)]
    pub(crate) fn creation_fixture(data: &Path, config: &Path, launch: &Launch) -> Self {
        // No runtime exists in this fixture; supply only its verified observation.
        let (managed, _) = Self::prepare(&data.join("missing-resources"), data, config);
        let mut status = managed.status.lock().unwrap();
        status.mode = "managed";
        status.ready = true;
        status.error = None;
        status.server_id = Some(launch.server_id.clone());
        status.principal_id = Some(launch.principal_id.clone());
        drop(status);
        managed.remember_runtime(launch);
        managed
    }
}
