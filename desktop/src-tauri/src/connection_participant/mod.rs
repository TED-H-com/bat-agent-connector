//! Native transport and discovery. Activation waits for a visible status consumer.
#![allow(dead_code)]
pub(crate) mod delivery;
pub(crate) mod discovery;
pub(crate) mod driver;
pub(crate) mod review;
pub(crate) mod transport;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Error {
    Unavailable,
    Changed,
    Invalid,
    Resnapshot,
    ReviewChanged,
    SourceAssociationUnproven,
    Unsupported,
    PlatformUnsupported,
}
type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod http_tests;
#[cfg(test)]
mod tests;

#[cfg(test)]
mod driver_tests;
