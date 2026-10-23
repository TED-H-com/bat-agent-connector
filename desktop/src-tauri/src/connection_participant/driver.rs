use super::{discovery::State, review::*, transport::*, Error};
use bat_fleet_core::connection_participant::{
    self as core, Backend, Next, Part, PostTarget, Record, Scope, Store, POST_SLOTS,
};
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Refusal {
    ReviewChanged,
    SourceAssociationUnproven,
    PlatformUnsupported,
}
impl Refusal {
    pub(crate) fn code(self) -> &'static str {
        match self {
            Self::ReviewChanged => "LOCAL_REVIEW_CHANGED",
            Self::SourceAssociationUnproven => "LOCAL_SOURCE_ASSOCIATION_UNPROVEN",
            Self::PlatformUnsupported => "LOCAL_PLATFORM_UNSUPPORTED",
        }
    }
}
pub(crate) struct Invocation {
    record: Record,
    association: AssociationContext,
    read_only: bool,
}
impl Invocation {
    pub(crate) fn record(&self) -> &Record {
        &self.record
    }
    pub(crate) fn association(&self) -> &AssociationContext {
        &self.association
    }
    pub(crate) fn read_only(&self) -> bool {
        self.read_only
    }
}
pub(crate) struct Outcome {
    pub state: State,
    pub refusal: Option<Refusal>,
    pub invocation: Option<Invocation>,
}
impl Outcome {
    fn state(state: State) -> Self {
        Self {
            state,
            refusal: None,
            invocation: None,
        }
    }
}
pub(crate) struct Driver<C> {
    supported: bool,
    clock: C,
}
impl Default for Driver<fn() -> u64> {
    fn default() -> Self {
        Self {
            supported: cfg!(windows),
            clock: || {
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_or(0, |t| t.as_millis() as u64)
            },
        }
    }
}
impl<C: Fn() -> u64> Driver<C> {
    #[cfg(test)]
    pub(super) fn test(supported: bool, clock: C) -> Self {
        Self { supported, clock }
    }
    pub(crate) async fn advance(
        &self,
        transport: &impl ReceiptTransport,
        reviews: &impl Reviews,
        store: &mut Store<impl Backend>,
        scope: &Scope,
        operation: &str,
    ) -> Outcome {
        match self.step(transport, reviews, store, scope, operation).await {
            Ok((record, association, read_only)) => {
                if record.part(Part::Receipt(0)).is_some() {
                    return Outcome::state(
                        super::delivery::advance(transport, store, record.intent(), (self.clock)())
                            .await,
                    );
                }
                Outcome {
                    state: State::Claimed,
                    refusal: None,
                    invocation: Some(Invocation {
                        record,
                        association,
                        read_only,
                    }),
                }
            }
            Err(error) => {
                let mut outcome = Outcome::state(match error {
                    Error::Unavailable => State::Unavailable,
                    Error::Unsupported => State::Unsupported,
                    _ => State::NeedsAttention,
                });
                outcome.refusal = match error {
                    Error::ReviewChanged => Some(Refusal::ReviewChanged),
                    Error::SourceAssociationUnproven => Some(Refusal::SourceAssociationUnproven),
                    Error::PlatformUnsupported => Some(Refusal::PlatformUnsupported),
                    _ => None,
                };
                outcome
            }
        }
    }
    // The existing discovery caller serializes advances; this driver adds no scheduler.
    async fn step(
        &self,
        transport: &impl ReceiptTransport,
        reviews: &impl Reviews,
        store: &mut Store<impl Backend>,
        scope: &Scope,
        operation: &str,
    ) -> super::Result<(Record, AssociationContext, bool)> {
        store.available().map_err(|_| Error::Unavailable)?;
        let review = reviews.original(scope, operation)?;
        if review.intent.accepted().operation_id != operation {
            return Err(Error::ReviewChanged);
        }
        transport.check_original(review.intent.accepted())?;
        let original = transport.read(Query::Operation(operation.into())).await?;
        if original.status != 200 {
            return Err(Error::Unavailable);
        }
        transport.check_original(review.intent.accepted())?;
        review.validate(&original.document["operation"], &reviews.current()?)?;
        let intent = &review.intent;
        let record = store
            .open(scope, operation, |r| reviews.history(r))
            .map_err(|error| match error {
                core::Error::Unsupported => Error::Unsupported,
                core::Error::Unavailable => Error::Unavailable,
                _ => Error::Changed,
            })?;
        if record
            .as_ref()
            .is_some_and(|r| r.intent() != &review.intent)
        {
            return Err(Error::ReviewChanged);
        }
        let next = match &record {
            Some(r) => r.recover((self.clock)()).map_err(|_| Error::Invalid)?,
            None => Next::Claim,
        };
        let new_claim = matches!(next, Next::Claim | Next::ReplayClaim);
        if new_claim {
            if !self.supported {
                return Err(Error::PlatformUnsupported);
            }
            let op = &original.document["operation"];
            if op["external_refs"]["native_effects_sent"] != false
                || op["cancel_requested"] != false
                || !matches!(
                    op["status"].as_str(),
                    Some("waiting_external" | "running" | "needs_attention")
                )
            {
                return Err(Error::Changed);
            }
        }
        let record = match record {
            Some(record) if record.part(Part::Receipt(0)).is_some() => {
                return Ok((record, review.association().clone(), true));
            }
            record => record,
        };
        if matches!(next, Next::RetryExhausted | Next::Done) {
            return Err(Error::Changed);
        }
        let mut record = match record {
            Some(record) => record,
            None => store.begin(intent).map_err(|_| Error::Unavailable)?,
        };
        let read_only = !new_claim;
        let body = if new_claim {
            if record.part(Part::Claim).is_none() {
                let body = core::request::handoff(intent, core::request::Mode::Claim)
                    .map_err(|_| Error::Invalid)?;
                let claim = serde_json::json!({
                    "requested_at_ms": (self.clock)(),
                    "body": String::from_utf8(body).map_err(|_| Error::Invalid)?
                });
                record = store
                    .append(
                        intent,
                        Part::Claim,
                        &serde_json::to_vec(&claim).map_err(|_| Error::Invalid)?,
                    )
                    .map_err(|_| Error::Unavailable)?;
            }
            let body = record
                .request(PostTarget::Claim)
                .map_err(|_| Error::Invalid)?;
            let slot = (0..POST_SLOTS)
                .find(|i| record.part(Part::Post(PostTarget::Claim, *i)).is_none())
                .ok_or(Error::Changed)?;
            store.available().map_err(|_| Error::Unavailable)?;
            transport.check_original(intent.accepted())?;
            let attempt = serde_json::json!({
                "at_ms": (self.clock)(),
                "body": String::from_utf8(body.clone()).map_err(|_| Error::Invalid)?
            });
            store
                .append(
                    intent,
                    Part::Post(PostTarget::Claim, slot),
                    &serde_json::to_vec(&attempt).map_err(|_| Error::Invalid)?,
                )
                .map_err(|_| Error::Unavailable)?;
            match transport.handoff(intent, &body).await {
                Err(PostFailure::BeforeSender(error)) => {
                    store
                        .append(
                            intent,
                            Part::NotSent(PostTarget::Claim, slot),
                            br#"{"before_sender":true}"#,
                        )
                        .map_err(|_| Error::Unavailable)?;
                    return Err(error);
                }
                Err(PostFailure::PossiblySent(_)) => return Err(Error::Changed),
                Ok(reply) => reply,
            }
        } else {
            let body = core::request::handoff(intent, core::request::Mode::Readback)
                .map_err(|_| Error::Invalid)?;
            transport
                .handoff(intent, &body)
                .await
                .map_err(|_| Error::Changed)?
        };
        transport.check_original(intent.accepted())?;
        if body.status != 200 || (read_only && body.document["read_only"] != true) {
            return Err(Error::Changed);
        }
        if let Some(saved) = record.part(Part::Envelope) {
            // Retain the original envelope. Delivery validates new receipt evidence later.
            let mut expected: serde_json::Value =
                serde_json::from_slice(saved).map_err(|_| Error::Invalid)?;
            expected["read_only"] = serde_json::json!(true);
            expected["native_handoff"]["receipt"] =
                body.document["native_handoff"]["receipt"].clone();
            if expected != body.document {
                return Err(Error::Changed);
            }
        } else {
            record = store
                .append(
                    intent,
                    Part::Envelope,
                    &serde_json::to_vec(&body.document).map_err(|_| Error::Invalid)?,
                )
                .map_err(|_| Error::Changed)?;
        }
        let expired = matches!(
            record.recover((self.clock)()).map_err(|_| Error::Invalid)?,
            Next::Executor { read_only: true }
        );
        Ok((record, review.association().clone(), read_only || expired))
    }
}
