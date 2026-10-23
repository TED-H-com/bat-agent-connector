//! Restart records only. No discovery, execution, transport, IPC or production backend.
mod model;
mod post;
#[doc(hidden)]
pub mod protected;
pub mod request;
mod store;
mod wire;
pub use model::{
    validate_variant, Accepted, Error, Intent, Namespace, Scope, Target, FLEET_ACTION,
};
pub use post::{PostTarget, POST_SLOTS};
pub use store::{Backend, Next, Part, Record, Store, Unavailable};
pub type Result<T> = std::result::Result<T, Error>;
