use super::{super::*, Error, Result};
use bat_fleet_core::connection_participant::Intent;

// None of these transport types can be serialized or debug-printed to a WebView.
#[derive(Clone)]
pub(crate) struct NativeScope {
    connection: FileScope,
    identity: Identity,
}
pub(crate) struct Reply {
    pub status: u16,
    pub document: Value,
}
#[derive(Clone)]
pub(crate) struct Cursor {
    pub before: serde_json::Number,
    pub before_id: String,
}
#[derive(Clone)]
pub(crate) struct Checkpoint {
    pub cursor: u64,
    pub token: String,
}
pub(crate) enum Query {
    Bootstrap,
    Operations {
        action: String,
        cursor: Option<Cursor>,
    },
    Operation(String),
    Events(Checkpoint),
}
pub(crate) fn operation_id(value: &str) -> bool {
    value.strip_prefix("op_").is_some_and(|s| {
        s.len() == 32
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
fn route(path: &str, pairs: &[(&str, String)]) -> String {
    if pairs.is_empty() {
        return path.into();
    }
    let query = url::form_urlencoded::Serializer::new(String::new())
        .extend_pairs(pairs.iter().map(|(k, v)| (*k, v)))
        .finish();
    format!("{path}?{query}")
}
pub(crate) trait Transport {
    fn check(&self, scope: &bat_fleet_core::connection_participant::Scope) -> Result<()>;
    async fn read(&self, query: Query) -> Result<Reply>;
}
pub(crate) struct NativeTransport<'a> {
    pub bridge: &'a Bridge,
    pub scope: NativeScope,
}
impl Transport for NativeTransport<'_> {
    fn check(&self, scope: &bat_fleet_core::connection_participant::Scope) -> Result<()> {
        self.bridge
            .check_file_scope(&self.scope.connection)
            .map_err(|_| Error::Changed)?;
        if scope.server_id != self.scope.identity.server_id
            || scope.principal_id != self.scope.identity.principal_id
        {
            return Err(Error::Changed);
        }
        Ok(())
    }
    async fn read(&self, query: Query) -> Result<Reply> {
        match query {
            Query::Bootstrap => self.bridge.participant_bootstrap(&self.scope).await,
            Query::Operations { action, cursor } => {
                self.bridge
                    .participant_operations(&self.scope, &action, cursor.as_ref())
                    .await
            }
            Query::Operation(id) => self.bridge.participant_operation(&self.scope, &id).await,
            Query::Events(checkpoint) => {
                self.bridge
                    .participant_events(&self.scope, &checkpoint)
                    .await
            }
        }
    }
}
impl Bridge {
    pub(crate) fn participant_scope(&self) -> Result<NativeScope> {
        let (generation, active) = self.active().map_err(|_| Error::Unavailable)?;
        let connection = self.file_scope().map_err(|_| Error::Unavailable)?;
        self.unchanged(generation).map_err(|_| Error::Changed)?;
        Ok(NativeScope {
            connection,
            identity: active.record.identity,
        })
    }
    pub(crate) async fn participant_bootstrap(&self, scope: &NativeScope) -> Result<Reply> {
        self.participant_exchange(scope, "/bootstrap".into(), None)
            .await
    }
    pub(crate) async fn participant_operation(
        &self,
        scope: &NativeScope,
        id: &str,
    ) -> Result<Reply> {
        if !operation_id(id) {
            return Err(Error::Invalid);
        }
        self.participant_exchange(scope, format!("/operations/{id}"), None)
            .await
    }
    pub(crate) async fn participant_operations(
        &self,
        scope: &NativeScope,
        action: &str,
        cursor: Option<&Cursor>,
    ) -> Result<Reply> {
        if action.is_empty() || action.len() > 128 || action.chars().any(char::is_control) {
            return Err(Error::Invalid);
        }
        let mut pairs = vec![("action", action.into()), ("limit", "200".into())];
        if let Some(cursor) = cursor {
            if !operation_id(&cursor.before_id)
                || !cursor
                    .before
                    .as_f64()
                    .is_some_and(|n| n.is_finite() && n >= 0.0)
            {
                return Err(Error::Invalid);
            }
            pairs.extend([
                ("before", cursor.before.to_string()),
                ("before_id", cursor.before_id.clone()),
            ]);
        }
        self.participant_exchange(scope, route("/operations", &pairs), None)
            .await
    }
    pub(crate) async fn participant_events(
        &self,
        scope: &NativeScope,
        checkpoint: &Checkpoint,
    ) -> Result<Reply> {
        if checkpoint.token.is_empty() || checkpoint.token.len() > 2048 {
            return Err(Error::Invalid);
        }
        self.participant_exchange(
            scope,
            route(
                "/events",
                &[
                    ("after", checkpoint.cursor.to_string()),
                    ("limit", "100".into()),
                    ("checkpoint", checkpoint.token.clone()),
                ],
            ),
            None,
        )
        .await
    }
    pub(crate) async fn participant_handoff(
        &self,
        scope: &NativeScope,
        intent: &Intent,
        readback: bool,
    ) -> Result<Reply> {
        let a = intent.accepted();
        if a.scope.server_id != scope.identity.server_id
            || a.scope.principal_id != scope.identity.principal_id
        {
            return Err(Error::Changed);
        }
        let body = serde_json::to_vec(&serde_json::json!({"contract_version":a.contract_version,"installation_ref":a.target.installation_ref,"operation_id":a.operation_id,"mode":if readback {"readback"} else {"claim"}})).map_err(|_| Error::Invalid)?;
        self.participant_exchange(scope, "/connections/handoff".into(), Some(&body))
            .await
    }
    pub(crate) async fn participant_receipt(
        &self,
        scope: &NativeScope,
        body: &[u8],
    ) -> Result<Reply> {
        let max = bat_fleet_core::connection_contract::vocabulary()["bounds"]["receipt_bytes"]
            .as_u64()
            .ok_or(Error::Invalid)? as usize;
        let value = bat_fleet_core::strict_json::parse(body, max).map_err(|_| Error::Invalid)?;
        if !value["operation_id"].as_str().is_some_and(operation_id) {
            return Err(Error::Invalid);
        }
        self.participant_exchange(scope, "/connections/receipt".into(), Some(body))
            .await
    }
    async fn participant_exchange(
        &self,
        scope: &NativeScope,
        path: String,
        body: Option<&[u8]>,
    ) -> Result<Reply> {
        self.check_file_scope(&scope.connection)
            .map_err(|_| Error::Changed)?;
        let (generation, active) = self.active().map_err(|_| Error::Unavailable)?;
        if active.record.identity != scope.identity {
            return Err(Error::Changed);
        }
        if body.is_some()
            && self
                .verify(
                    &active.config,
                    &active.record.token,
                    Some(&scope.identity),
                    generation,
                )
                .await
                .is_err()
        {
            self.invalidate(generation);
            return Err(Error::Changed);
        }
        self.check_file_scope(&scope.connection)
            .map_err(|_| Error::Changed)?;
        self.unchanged(generation).map_err(|_| Error::Changed)?;
        let request = ConnectorRequest {
            method: if body.is_some() { "POST" } else { "GET" }.into(),
            path,
            body: None,
            idempotency_key: None,
        };
        let response = self
            .send_encoded(&active.config, &active.record.token, &request, body)
            .await
            .map_err(|_| Error::Unavailable)?;
        self.unchanged(generation).map_err(|_| Error::Changed)?;
        self.check_file_scope(&scope.connection)
            .map_err(|_| Error::Changed)?;
        if response.status == 401 {
            self.invalidate(generation);
            return Err(Error::Unavailable);
        }
        if request.path == "/bootstrap"
            && response.status == 200
            && Self::bootstrap_identity(&active.config, &response.data)
                .ok()
                .as_ref()
                != Some(&scope.identity)
        {
            self.invalidate(generation);
            return Err(Error::Changed);
        }
        Ok(Reply {
            status: response.status,
            document: response.data,
        })
    }
}
