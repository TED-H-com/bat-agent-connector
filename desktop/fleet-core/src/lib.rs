//! Fleet local contracts. This crate contains no central task authority and no IPC.
//! Configuration is native-only; callers project an explicitly safe status model.
pub mod bootstrap;
pub mod bootstrap_policy;
mod compare_replace;
pub mod configuration;
pub mod connection_catalog;
pub mod connection_contract;
pub mod connection_publish;
pub mod connection_setup;
pub mod creation_evidence;
pub mod credential;
pub mod discovery;
pub mod installation;
pub mod inventory;
pub mod migration;
mod migration_io;
pub mod monitor_launch;
pub mod ownership;
#[cfg(test)]
#[path = "../tests/support/powershell.rs"]
mod powershell_fixture;
pub mod probe;
mod probe_wire;
pub mod process_adapter;
mod profile_files;
pub mod profile_launch;
pub mod publication_state;
pub mod route;
mod route_probe;
pub mod selection;
pub mod selection_io;
pub mod source_catalog;
pub mod strict_json;
pub mod supervisor;
pub mod supervisor_control;
pub mod supervisor_io;
pub mod supervisor_probe;
pub mod supervisor_status;
pub mod tailscale;
pub mod tunnel;
pub mod unconfigured;
#[cfg(windows)]
pub mod windows;
#[cfg(windows)]
pub mod windows_bootstrap;
#[cfg(any(windows, test, feature = "creation-test-support"))]
pub mod windows_creation;
#[cfg(any(windows, test))]
pub mod windows_detach;
#[cfg(windows)]
pub mod windows_launcher;
#[cfg(any(windows, test))]
pub mod windows_migration;
#[cfg(windows)]
pub mod windows_monitor_launch;
#[cfg(windows)]
pub mod windows_profiles;
#[cfg(windows)]
mod windows_publication_guards;
#[cfg(windows)]
pub mod windows_startup;
#[cfg(windows)]
pub mod windows_supervisor;
#[cfg(windows)]
pub mod windows_tailscale;
#[cfg(windows)]
pub mod windows_tunnel;

pub type Result<T> = std::result::Result<T, &'static str>;

pub fn digest(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(bytes))
}
