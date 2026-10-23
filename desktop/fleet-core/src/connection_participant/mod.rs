//! Restart records only. No discovery, execution, transport, IPC or production backend.
mod model;
mod store;
mod wire;
pub use model::{Accepted, Error, Intent, Namespace, Scope, Target};
pub use store::{Backend, Next, Part, Record, Store, Unavailable};
pub type Result<T> = std::result::Result<T, Error>;
