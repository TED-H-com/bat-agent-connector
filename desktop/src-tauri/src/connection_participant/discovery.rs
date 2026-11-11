use super::{transport::*, Error, Result};
use bat_fleet_core::connection_participant::{validate_variant, Namespace, Scope, FLEET_ACTION};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum State {
    Unavailable,
    Unsupported,
    Waiting,
    Claimed,
    Delivering,
    Delivered,
    NeedsAttention,
}
#[derive(Clone, Serialize)]
pub(crate) struct OperationStatus {
    pub operation_id: String,
    pub handle: String,
    pub observed_revision: u64,
    pub state: State,
}
#[derive(Clone, Serialize)]
pub(crate) struct StatusSnapshot {
    pub state: State,
    pub operations: Vec<OperationStatus>,
}
#[derive(Default)]
pub(super) struct Observed {
    operations: BTreeMap<String, Value>,
    statuses: Vec<OperationStatus>,
}
#[derive(Default)]
pub(crate) struct Discovery {
    // One caller-driven scheduler; no timer, managed-service wake or second poller.
    scheduler: tokio::sync::Mutex<Observed>,
}
fn require(value: bool) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(Error::Invalid)
    }
}
fn document(reply: Reply) -> Result<Value> {
    if reply.status == 200 {
        Ok(reply.document)
    } else {
        Err(Error::Unavailable)
    }
}
fn matches(scope: &Scope, op: &Value) -> bool {
    let binding = &op["external_refs"]["admission_binding"];
    op["target"]["installation_ref"] == scope.installation_ref
        && binding["installation_ref"] == scope.installation_ref
        && binding["server_id"] == scope.server_id
        && binding["principal_id"] == scope.principal_id
}
fn candidates(page: &Value, scope: &Scope, ids: &mut BTreeSet<String>) -> Result<()> {
    let rows = page["operations"].as_array().ok_or(Error::Invalid)?;
    require(rows.len() <= 200)?;
    for op in rows.iter().filter(|op| matches(scope, op)) {
        let id = op["operation_id"].as_str().ok_or(Error::Invalid)?;
        require(operation_id(id))?;
        ids.insert(id.into());
    }
    Ok(())
}
fn checkpoint(sync: &Value, scope: &Scope) -> Result<Checkpoint> {
    require(
        sync["version"] == 1
            && sync["server_id"] == scope.server_id
            && sync["principal_id"] == scope.principal_id,
    )?;
    let token = sync["checkpoint"]["token"].as_str().ok_or(Error::Invalid)?;
    require(!token.is_empty() && token.len() <= 2048 && !token.chars().any(char::is_control))?;
    Ok(Checkpoint {
        cursor: sync["checkpoint"]["cursor"]
            .as_u64()
            .ok_or(Error::Invalid)?,
        token: token.into(),
    })
}
fn participant_actions(vocabulary: &Value) -> BTreeSet<&str> {
    // Same union as connection_handoff.py::NATIVE_ACTIONS; the vocabulary owns v2 mappings.
    std::iter::once(FLEET_ACTION)
        .chain(
            vocabulary["v2"]["effect_kinds"]
                .as_object()
                .into_iter()
                .flat_map(|kinds| kinds.values())
                .filter_map(|kind| kind["action"].as_str()),
        )
        .collect()
}
impl Discovery {
    pub(crate) async fn refresh(
        &self,
        transport: &impl Transport,
        scope: &Scope,
        wake: Option<&str>,
    ) -> Result<StatusSnapshot> {
        let mut observed = self.scheduler.lock().await;
        *observed = Observed::default();
        transport.check(scope)?;
        if let Some(id) = wake {
            require(operation_id(id))?;
        }
        // A continuity failure retries the same bootstrap/read sequence once. A
        // changed access/identity boundary cannot retain any previous observation.
        for _ in 0..2 {
            match Self::snapshot(
                transport,
                scope,
                wake,
                bat_fleet_core::connection_contract::vocabulary(),
            )
            .await
            {
                Err(Error::Resnapshot) => continue,
                Err(error) => return Err(error),
                Ok(next) => {
                    transport.check(scope)?;
                    *observed = next;
                    return Ok(StatusSnapshot {
                        state: State::Unavailable,
                        operations: observed.statuses.clone(),
                    });
                }
            }
        }
        Err(Error::Resnapshot)
    }
    pub(super) async fn snapshot(
        transport: &impl Transport,
        scope: &Scope,
        wake: Option<&str>,
        vocabulary: &Value,
    ) -> Result<Observed> {
        let bootstrap = document(transport.read(Query::Bootstrap).await?)?;
        transport.check(scope)?;
        let mut checkpoint = checkpoint(&bootstrap["sync"], scope)?;
        let registered: BTreeSet<_> = bootstrap["capabilities"]["actions"]
            .as_array()
            .ok_or(Error::Invalid)?
            .iter()
            .filter_map(|a| a["action"].as_str())
            .collect();
        let mut ids = BTreeSet::new();
        candidates(&bootstrap["snapshot"]["operations"], scope, &mut ids)?;
        for action in participant_actions(vocabulary) {
            let mut cursor = None;
            let mut cursors_seen = BTreeSet::new();
            loop {
                let page = document(
                    transport
                        .read(Query::Operations {
                            action: action.into(),
                            cursor,
                        })
                        .await?,
                )?;
                candidates(&page, scope, &mut ids)?;
                if page["next_before"].is_null() && page["next_before_id"].is_null() {
                    break;
                }
                let before = page["next_before"]
                    .as_number()
                    .ok_or(Error::Invalid)?
                    .clone();
                let before_id = page["next_before_id"]
                    .as_str()
                    .ok_or(Error::Invalid)?
                    .to_string();
                require(
                    operation_id(&before_id)
                        && before.as_f64().is_some_and(|n| n.is_finite() && n >= 0.0)
                        && cursors_seen.insert((before.to_string(), before_id.clone())),
                )?;
                cursor = Some(Cursor { before, before_id });
            }
        }
        loop {
            let reply = transport.read(Query::Events(checkpoint.clone())).await?;
            if reply.status == 409 && reply.document["error"]["code"] == "EVENT_CURSOR_RESET" {
                return Err(
                    if matches!(
                        reply.document["error"]["reason"].as_str(),
                        Some(
                            "cursor_expired"
                                | "cursor_ahead"
                                | "history_changed"
                                | "checkpoint_cursor_mismatch"
                        )
                    ) {
                        Error::Resnapshot
                    } else {
                        Error::Changed
                    },
                );
            }
            let page = document(reply)?;
            let next = page["next_cursor"].as_u64().ok_or(Error::Invalid)?;
            let head = page["head_cursor"].as_u64().ok_or(Error::Invalid)?;
            let more = page["has_more"].as_bool().ok_or(Error::Invalid)?;
            require(
                next >= checkpoint.cursor && next <= head && (!more || next > checkpoint.cursor),
            )?;
            let rows = page["events"].as_array().ok_or(Error::Invalid)?;
            require(rows.len() <= 100)?;
            let mut seq = checkpoint.cursor;
            for event in rows {
                let current = event["seq"].as_u64().ok_or(Error::Invalid)?;
                require(current > seq && current <= next)?;
                seq = current;
                if event["resource_type"] == "operation" {
                    let id = event["resource_id"].as_str().ok_or(Error::Invalid)?;
                    require(operation_id(id))?;
                    ids.insert(id.into());
                }
            }
            checkpoint = self::checkpoint(&page["sync"], scope)?;
            require(checkpoint.cursor == next)?;
            if !more {
                break;
            }
        }
        if let Some(id) = wake {
            ids.insert(id.into());
        }
        let mut observed = Observed::default();
        for id in ids {
            let response = document(transport.read(Query::Operation(id.clone())).await?)?;
            let op = &response["operation"];
            require(op["operation_id"] == id)?;
            if !matches(scope, op) {
                continue;
            }
            let admitted = &op["external_refs"]["admission_binding"];
            let native = &admitted["native_plan"];
            // Explicit discriminators from persisted admission, never role/shape inference.
            let variant = json!({"format_version":1,"accepted":{
                "action":op["action"],
                "contract_version":admitted.get("contract_version").unwrap_or(&native["contract_version"]),
                "kind":admitted.get("kind").or_else(||native.get("kind")).unwrap_or(&Value::Null)
            }});
            let unsupported = validate_variant(&variant).is_err()
                || !op["action"]
                    .as_str()
                    .is_some_and(|a| registered.contains(a));
            let state = if unsupported {
                State::Unsupported
            } else {
                require(native == &op["params"]["native_plan"])?;
                require(admitted["review_digest"] == op["preconditions"]["review_digest"])?;
                if op["external_refs"]["native_effects_sent"] == true {
                    State::NeedsAttention
                } else {
                    State::Unavailable
                }
            };
            let namespace = Namespace::for_operation(scope, &id).map_err(|_| Error::Invalid)?;
            observed.statuses.push(OperationStatus {
                operation_id: id.clone(),
                handle: bat_fleet_core::digest(namespace.relative_path().as_bytes()),
                observed_revision: op["version"].as_u64().ok_or(Error::Invalid)?,
                state,
            });
            observed.operations.insert(id, op.clone());
        }
        Ok(observed)
    }
}
