use super::*;
use bat_fleet_core::{creation_evidence::ManagedIdentity, Result as NativeResult};

#[derive(Clone)]
pub(super) struct ServiceIdentity {
    pub actor: String,
    pub identity: credentials::Identity,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InstallationManifest {
    protocol: u8,
    installation_id: String,
    actor: String,
    data_dir: PathBuf,
    runtime_version: String,
    state: String,
    server_id: String,
    principal_id: String,
}
impl Managed {
    pub(crate) fn creation_binding(
        &self,
        bridge: &bridge::CreationScope,
        mut read: impl FnMut(&Path, usize) -> NativeResult<Vec<u8>>,
    ) -> NativeResult<Option<ManagedIdentity>> {
        const REFUSED: &str = "CREATION_EVIDENCE_UNPROVEN";
        let _serial = self.serial.try_lock().map_err(|_| REFUSED)?;
        let role = self.role.lock().map_err(|_| REFUSED)?;
        let role = role.as_ref().ok_or(REFUSED)?;
        if startup_role::evidence(&self.app_data)
            .map_err(|_| REFUSED)?
            .snapshot
            != *role
        {
            return Err(REFUSED);
        }
        let status = self.status.lock().map_err(|_| REFUSED)?;
        let facts = self.runtime_facts.lock().map_err(|_| REFUSED)?;
        match self.data_dir.symlink_metadata() {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return if role.role == Some(Role::Client)
                    && !bridge.managed
                    && status.mode == "external"
                    && status.error.is_none()
                    && facts.creation.is_none()
                {
                    Ok(None)
                } else {
                    Err(REFUSED)
                };
            }
            Ok(m) if m.is_dir() && !linked(&m) => (),
            _ => return Err(REFUSED),
        }
        let service = facts.creation.as_ref().ok_or(REFUSED)?;
        if !bridge.managed
            || !role.role.is_some_and(Role::provides)
            || !status.ready
            || status.error.is_some()
            || status
                .lifecycle
                .as_ref()
                .is_none_or(|s| s.state != "running")
            || service.actor != bridge.actor
            || service.identity != bridge.identity
            || status.server_id.as_ref() != Some(&service.identity.server_id)
            || status.principal_id.as_ref() != Some(&service.identity.principal_id)
        {
            return Err(REFUSED);
        }
        let path = self.data_dir.join("installation.json");
        let bytes = read(&path, MAX_REPLY as usize)?;
        let doc: InstallationManifest = serde_json::from_value(bat_fleet_core::strict_json::parse(
            &bytes,
            MAX_REPLY as usize,
        )?)
        .map_err(|_| REFUSED)?;
        let installation_id = doc.installation_id;
        if doc.protocol != 1
            || doc.state != "ready"
            || doc.data_dir != self.data_dir
            || installation_id.len() != 32
            || !installation_id
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || doc.actor != format!("desktop-{installation_id}")
            || doc.actor != service.actor
            || doc.server_id != service.identity.server_id
            || doc.principal_id != service.identity.principal_id
            || doc.runtime_version.is_empty()
            || doc.runtime_version.len() > 128
        {
            return Err(REFUSED);
        }
        if read(&path, MAX_REPLY as usize)? != bytes {
            return Err(REFUSED);
        }
        Ok(Some(ManagedIdentity {
            installation_id,
            manifest_sha256: bat_fleet_core::digest(&bytes),
            actor: doc.actor,
            server_id: doc.server_id,
            principal_id: doc.principal_id,
        }))
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
