//! Delivery consumes immutable store bytes; it never constructs an effect or receipt.
use super::{discovery::State, transport::*, Error, Result};
use bat_fleet_core::connection_participant::{
    request, Backend, Intent, Next, Part, PostTarget, Record, Store, POST_SLOTS,
};
use serde_json::json;

pub(crate) async fn advance(
    transport: &impl ReceiptTransport,
    store: &mut Store<impl Backend>,
    intent: &Intent,
    now: u64,
) -> State {
    match step(transport, store, intent, now).await {
        Ok(state) => state,
        Err(Error::Unavailable) => State::Unavailable,
        Err(Error::Unsupported) => State::Unsupported,
        Err(_) => State::NeedsAttention,
    }
}
async fn step(
    transport: &impl ReceiptTransport,
    store: &mut Store<impl Backend>,
    intent: &Intent,
    now: u64,
) -> Result<State> {
    let record = store.load(intent).map_err(|error| match error {
        bat_fleet_core::connection_participant::Error::Unavailable => Error::Unavailable,
        bat_fleet_core::connection_participant::Error::Unsupported => Error::Unsupported,
        _ => Error::Invalid,
    })?;
    match record.recover(now).map_err(|_| Error::Invalid)? {
        Next::Done => Ok(State::Delivered),
        Next::Executor { .. } if record.part(Part::Receipt(0)).is_none() => Ok(State::Claimed),
        Next::Redeliver { index, .. } | Next::RetryDelivery { index } => {
            transport.check_original(intent.accepted())?;
            let target = PostTarget::Receipt(index);
            let body = record.request(target).map_err(|_| Error::Invalid)?;
            let slot = (0..POST_SLOTS)
                .find(|i| record.part(Part::Post(target, *i)).is_none())
                .ok_or(Error::Changed)?;
            let fence =
                json!({"at_ms":now,"body":std::str::from_utf8(&body).map_err(|_| Error::Invalid)?});
            store
                .append(
                    intent,
                    Part::Post(target, slot),
                    &serde_json::to_vec(&fence).map_err(|_| Error::Invalid)?,
                )
                .map_err(|_| Error::Unavailable)?;
            let reply = match transport.receipt(intent, &body).await {
                Err(PostFailure::BeforeSender(error)) => {
                    store
                        .append(
                            intent,
                            Part::NotSent(target, slot),
                            br#"{"before_sender":true}"#,
                        )
                        .map_err(|_| Error::Unavailable)?;
                    return Err(error);
                }
                Err(PostFailure::PossiblySent(_)) => return Err(Error::Changed),
                Ok(reply) => reply,
            };
            transport.check_original(intent.accepted())?;
            acknowledge(store, intent, index, "direct", reply, now)
        }
        Next::Readback | Next::Executor { read_only: true } => {
            transport.check_original(intent.accepted())?;
            // Missing receipt evidence is never positive NotSent evidence.
            let original = transport
                .read(Query::Operation(intent.accepted().operation_id.clone()))
                .await?;
            let op = &original.document["operation"];
            let a = intent.accepted();
            let binding = &op["external_refs"]["admission_binding"];
            if original.status != 200
                || op["operation_id"] != a.operation_id
                || op["action"] != a.action
                || op["idempotency_key"] != a.operation_key
                || binding["review_digest"] != a.review_digest
                || binding["server_id"] != a.scope.server_id
                || binding["principal_id"] != a.scope.principal_id
                || binding["installation_ref"] != a.scope.installation_ref
            {
                return Err(Error::Changed);
            }
            transport.check_original(a)?;
            let body =
                request::handoff(intent, request::Mode::Readback).map_err(|_| Error::Invalid)?;
            let reply = transport
                .handoff(intent, &body)
                .await
                .map_err(|_| Error::Changed)?;
            transport.check_original(a)?;
            let index = if record.part(Part::Receipt(1)).is_some() {
                1
            } else {
                0
            };
            if record.part(Part::Acknowledgement(index)).is_some() {
                // An acknowledged uncertain receipt allows only read-only reconciliation.
                return Ok(State::NeedsAttention);
            }
            acknowledge(store, intent, index, "readback", reply, now)
        }
        _ => Ok(State::NeedsAttention),
    }
}
fn acknowledge(
    store: &mut Store<impl Backend>,
    intent: &Intent,
    index: u8,
    source: &str,
    reply: Reply,
    now: u64,
) -> Result<State> {
    if reply.status != 200 {
        return Err(Error::Changed);
    }
    // Preserve the actual reply shape and source. Store validates all evidence and refs.
    let ack = json!({"source":source,"response":reply.document});
    let record: Record = store
        .append(
            intent,
            Part::Acknowledgement(index),
            &serde_json::to_vec(&ack).map_err(|_| Error::Invalid)?,
        )
        .map_err(|_| Error::Changed)?;
    Ok(
        if record.recover(now).map_err(|_| Error::Invalid)? == Next::Done {
            State::Delivered
        } else {
            State::NeedsAttention
        },
    )
}
