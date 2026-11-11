//! Read-only reference review. Catalog candidates confer no execution authority.
//! Original association attachments have no producer yet: selection effects stay unproven.
use crate::{
    configuration::Configuration,
    connection_catalog::{
        self as catalog, Catalog, Change, ConfigurationRef, ReadOutcome, Transition,
    },
    connection_contract::{reference::Sha256, vocabulary},
    connection_participant::Scope,
    connection_publish::{FrozenHistory, FrozenPlanRef},
    digest,
    selection::{launch_plan, LaunchPlan, Preferences},
    selection_io::{FileObservation, Observation, Snapshot},
    Result,
};
use serde::Serialize;
use serde_json::{json, Value};

const ASSOCIATION: &str = "LOCAL_SOURCE_ASSOCIATION_UNPROVEN";
const INVALID: &str = "LOCAL_INVALID_STATE";
const CHANGED: &str = "LOCAL_REVIEW_CHANGED";
const BOUND: usize = catalog::DOCUMENT_BOUND;

/// Supplied by the verified native session and original server connection read.
/// This is neither a WebView request nor an authentication/provenance capability.
#[derive(Serialize)]
pub struct ReferenceBinding {
    pub scope: Scope,
    pub connection_id: String,
    pub entry_id: String,
    pub configuration: ConfigurationRef,
    pub expected_revision: u64,
    pub source_digest: Sha256,
}
/// Implementations consume captured original attachments, never name matches or live probes.
/// The future attachment producer must add a validated resolution variant before it
/// can provide complete coverage; no public constructor can assert that coverage today.
pub trait AssociationResolver {
    fn resolve(
        &self,
        catalog: &Catalog,
        configuration: &Configuration,
    ) -> Result<AssociationCoverage>;
}
#[derive(Serialize)]
pub struct AssociationCoverage {
    catalog_sha256: String,
    configuration_binding: String,
    unlinked_publications: Vec<FrozenPlanRef>,
}
impl AssociationCoverage {
    /// Consistent frozen publications are retained as checked evidence, not adopted as
    /// connection bindings. Empty evidence means unknown, never a proven no-Fleet mapping.
    pub fn unproven(
        catalog: &Catalog,
        configuration: &Configuration,
        records: &[&FrozenHistory],
    ) -> Result<Self> {
        if records.len() > catalog::ENTRY_BOUND {
            return Err(INVALID);
        }
        let mut unlinked_publications: Vec<_> =
            records.iter().map(|r| r.reference().clone()).collect();
        unlinked_publications
            .sort_by(|a, b| (&a.review_digest, &a.sha256).cmp(&(&b.review_digest, &b.sha256)));
        if unlinked_publications
            .windows(2)
            .any(|pair| pair[0] == pair[1])
        {
            return Err(INVALID);
        }
        Ok(Self {
            catalog_sha256: digest(&catalog::serialize(catalog).map_err(|_| INVALID)?),
            configuration_binding: configuration.binding().into(),
            unlinked_publications,
        })
    }
}
/// Optional captured slot identity hashes are diagnostic evidence, not ownership grants.
#[derive(Default, Serialize)]
pub struct SlotIdentities<'a> {
    pub catalog: Option<&'a Sha256>,
    pub selection: Option<&'a Sha256>,
}
pub struct DetachInput<'a> {
    pub reference: &'a ReferenceBinding,
    pub catalog: Option<&'a [u8]>,
    pub configuration: &'a Configuration,
    pub observation: &'a Observation,
    pub snapshot: &'a Snapshot,
    pub associations: &'a AssociationCoverage,
    pub slot_identities: SlotIdentities<'a>,
}
#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct ExecutionRequirement {
    pub domain: &'static str,
    pub code: &'static str,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SelectionCandidate {
    Unproven,
}
/// A blocked local review deliberately has no native manifest/submit conversion.
/// The catalog after-bytes are a candidate only. Unknown selection is not unchanged.
pub struct DetachReview {
    catalog_after: Vec<u8>,
    saved_before: Preferences,
    launch_before: LaunchPlan,
    requirements: Vec<ExecutionRequirement>,
    digest: String,
}
impl DetachReview {
    pub fn catalog_after(&self) -> &[u8] {
        &self.catalog_after
    }
    pub fn saved_before(&self) -> &Preferences {
        &self.saved_before
    }
    pub fn launch_before(&self) -> &LaunchPlan {
        &self.launch_before
    }
    pub fn selection_after(&self) -> SelectionCandidate {
        SelectionCandidate::Unproven
    }
    pub fn requirements(&self) -> &[ExecutionRequirement] {
        &self.requirements
    }
    pub fn digest(&self) -> &str {
        &self.digest
    }
}
fn canonical(v: &Value) -> Result<Vec<u8>> {
    let mut v = v.clone();
    v.sort_all_objects();
    let bytes = serde_json::to_vec(&v).map_err(|_| INVALID)?;
    if bytes.len() > BOUND {
        return Err(INVALID);
    }
    Ok(bytes)
}
fn bytes(observation: &FileObservation) -> Result<Option<&[u8]>> {
    match observation {
        FileObservation::Absent => Ok(None),
        FileObservation::Present {
            bytes,
            digest: hash,
        } if bytes.len() <= 262144 && digest(bytes) == *hash => Ok(Some(bytes)),
        _ => Err(INVALID),
    }
}
fn state(bytes: Option<&[u8]>) -> Value {
    bytes.map_or_else(
        || json!({"state":"absent"}),
        |b| json!({"state":"present","digest":digest(b)}),
    )
}
fn launch_value(p: &LaunchPlan) -> Value {
    json!({"connect":p.connect,"added":p.added,"open":p.open,"dashboard":p.dashboard,
        "connector":p.connector,"dashboard_only":p.dashboard_only})
}
/// Pure derivation over captured inputs. No Store read, filesystem, network, owner probe or write.
/// Missing associations still permit a diagnostic review, never a submit-ready v2 plan.
pub fn review_detach(input: DetachInput<'_>) -> Result<DetachReview> {
    let loaded = catalog::load(input.catalog);
    let catalog = match &loaded {
        ReadOutcome::Present { catalog, .. } => catalog,
        ReadOutcome::Absent => return Err("LOCAL_PLAN_UNAVAILABLE"),
        ReadOutcome::Unsupported => return Err("LOCAL_GENERATOR_UNSUPPORTED"),
        ReadOutcome::Invalid(_) => return Err(INVALID),
    };
    let reference = input.reference;
    let scope = &reference.scope;
    let entry = catalog
        .entries
        .iter()
        .find(|e| e.id == reference.entry_id)
        .ok_or(ASSOCIATION)?;
    if scope.native_installation != scope.installation_ref
        || catalog.installation_ref != scope.installation_ref
        || entry.server_id != scope.server_id
        || entry.principal_id != scope.principal_id
        || entry.connection_id.as_ref() != Some(&reference.connection_id)
        || entry.configuration != reference.configuration
        || reference.expected_revision > vocabulary()["v2"]["integer_max"].as_u64().unwrap()
        || !input.configuration.issues.is_empty()
    {
        return Err(ASSOCIATION);
    }
    let configuration = input.configuration;
    let observation = input.observation;
    let snapshot = input.snapshot;
    if observation.configuration_binding() != configuration.binding()
        || snapshot.configuration_binding() != configuration.binding()
        || observation.directory() != snapshot.directory()
        || input.associations.configuration_binding != configuration.binding()
        || input.associations.catalog_sha256
            != digest(&catalog::serialize(catalog).map_err(|_| INVALID)?)
    {
        return Err(CHANGED);
    }
    let raw = bytes(observation.primary())?;
    let legacy = bytes(observation.legacy())?;
    let prefs = Preferences::load(
        &configuration.inventory,
        raw,
        if raw.is_none() { legacy } else { None },
    )
    .map_err(|_| INVALID)?;
    if &prefs != snapshot.preferences() || snapshot.revision != digest(raw.unwrap_or_default()) {
        return Err(CHANGED);
    }
    let launch_before = launch_plan(&configuration.inventory, &prefs);
    let catalog_after = match catalog::transition(&loaded, Change::Detach(entry.id.clone()))
        .map_err(|_| INVALID)?
    {
        Transition::Changed { bytes, .. } => bytes,
        Transition::Unchanged => input.catalog.unwrap().to_vec(),
    };
    let requirements = vec![
        ExecutionRequirement {
            domain: "associations",
            code: ASSOCIATION,
        },
        ExecutionRequirement {
            domain: "local_catalog",
            code: "LOCAL_OWNERSHIP_UNPROVEN",
        },
        ExecutionRequirement {
            domain: "selection",
            code: "LOCAL_OWNERSHIP_UNPROVEN",
        },
        ExecutionRequirement {
            domain: "runtime",
            code: "LOCAL_GATE_UNPROVEN",
        },
    ];
    let inventory = canonical(configuration.inventory.document())?;
    let commitment = json!({"format_version":1,"generator_version":1,"action":"connection.reference.detach",
        "scope":scope,"reference":reference,"catalog":{"before":state(input.catalog),"after":state(Some(&catalog_after))},
        "selection":{"primary":state(raw),"legacy":state(legacy),"revision":snapshot.revision,
            "directory":observation.directory(),"saved_before":prefs,"after":{"coverage":"unproven"}},
        "inventory":{"configuration_binding":configuration.binding(),"sha256":digest(&inventory)},
        "associations":input.associations,"authority":input.slot_identities,"resources":{"coverage":"unknown"},
        "effects":{"launch_before":launch_value(&launch_before),"requirements":requirements}});
    let mut committed = b"connection-reference-review-v1\0".to_vec();
    committed.extend(canonical(&commitment)?);
    Ok(DetachReview {
        catalog_after,
        saved_before: prefs,
        launch_before,
        requirements,
        digest: digest(&committed),
    })
}
