//! Private native restart codec. Decoding inspection fields cannot create a seal:
//! only the supported original builder may recreate it from all original inputs.
use super::*;
use serde::{Deserialize, Serialize};
use std::io::Write;

pub const FROZEN_PLAN_LIMIT: usize = 32 * 1024 * 1024;
const INVALID: &str = "PUBLICATION_FROZEN_INVALID";

#[derive(Clone, PartialEq, Eq)]
pub(super) struct OriginalInput(pub ReviewedSetup);
impl std::fmt::Debug for OriginalInput {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("[private generator input]")
    }
}

// Remote derives keep the proof-bearing input types off the public serde API.
#[derive(Serialize, Deserialize)]
#[serde(remote = "ReviewedSetup", deny_unknown_fields)]
struct InputDef {
    root: PathBuf,
    owner: OwnerEvidence,
    #[serde(with = "SelectedDef")]
    selected: SelectedProfile,
    #[serde(with = "optional_forward")]
    forward: Option<ForwardAssociation>,
    ssh_source: Vec<u8>,
    route: ReviewedRoute,
    bindings: Vec<WorkspaceBinding>,
    label: String,
    preferences: Preferences,
    endpoint_choice: EndpointChoice,
    addresses: AddressUse,
    existing: BTreeMap<Artifact, ExistingFile>,
}
#[derive(Serialize)]
struct InputBytes<'a>(#[serde(with = "InputDef")] &'a ReviewedSetup);

#[derive(Serialize, Deserialize)]
#[serde(remote = "SelectedProfile", deny_unknown_fields)]
struct SelectedDef {
    #[serde(with = "RemoteProfileDef")]
    profile: RemoteProfile,
    record: Value,
    index_digest: String,
    credential_ref: String,
    fingerprint_ref: String,
}
mod optional_forward {
    use super::*;
    #[derive(Serialize, Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ForwardDef {
        alias: String,
        local: SocketAddrV4,
        target: String,
        source_digest: String,
    }
    pub(super) fn serialize<S: serde::Serializer>(
        value: &Option<ForwardAssociation>,
        s: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        value
            .as_ref()
            .map(|f| ForwardDef {
                alias: f.alias.clone(),
                local: f.local,
                target: f.target.clone(),
                source_digest: f.source_digest.clone(),
            })
            .serialize(s)
    }
    pub(super) fn deserialize<'de, D: serde::Deserializer<'de>>(
        d: D,
    ) -> std::result::Result<Option<ForwardAssociation>, D::Error> {
        Ok(
            Option::<ForwardDef>::deserialize(d)?.map(|f| ForwardAssociation {
                alias: f.alias,
                local: f.local,
                target: f.target,
                source_digest: f.source_digest,
            }),
        )
    }
}

#[derive(Serialize, Deserialize)]
#[serde(remote = "RemoteProfile", deny_unknown_fields)]
struct RemoteProfileDef {
    id: String,
    name: String,
    #[serde(with = "CatalogEndpoint")]
    endpoint: source_catalog::Endpoint,
    fingerprint: String,
    remote_profile_id: String,
}
#[derive(Serialize, Deserialize)]
#[serde(remote = "source_catalog::Endpoint", deny_unknown_fields)]
struct CatalogEndpoint {
    host: String,
    port: u16,
    url: String,
}
#[derive(Serialize, Deserialize)]
#[serde(remote = "Endpoint", deny_unknown_fields)]
pub(super) struct InventoryEndpoint {
    address: String,
    port: u16,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Frozen {
    version: u32,
    generator_version: u32,
    review_digest: String,
    input_digest: String,
    #[serde(with = "InputDef")]
    input: ReviewedSetup,
    outputs: Vec<Output>,
}

fn encode_bounded(value: &impl Serialize) -> crate::Result<Vec<u8>> {
    struct Bounded(Vec<u8>);
    impl Write for Bounded {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if bytes.len() > FROZEN_PLAN_LIMIT - self.0.len() {
                return Err(std::io::Error::other("frozen plan bound"));
            }
            self.0.extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut out = Bounded(Vec::new());
    serde_json::to_writer(&mut out, value).map_err(|_| "PUBLICATION_FROZEN_TOO_LARGE")?;
    Ok(out.0)
}

pub(crate) fn owner(plan: &PublicationPlan) -> &OwnerEvidence {
    &plan.original_input.0.owner
}
pub(crate) fn encode(plan: &PublicationPlan) -> crate::Result<Vec<u8>> {
    if !plan.matches_review() || build(&plan.original_input.0).map_err(|_| INVALID)? != *plan {
        return Err(INVALID);
    }
    encode_bounded(&Frozen {
        version: 1,
        generator_version: plan.generator_version,
        review_digest: plan.review_digest.clone(),
        input_digest: digest(&encode_bounded(&InputBytes(&plan.original_input.0))?),
        input: plan.original_input.0.clone(),
        outputs: plan.outputs.clone(),
    })
}
pub(crate) fn decode(bytes: &[u8], review: &str) -> crate::Result<PublicationPlan> {
    let document = strict_json::parse(bytes, FROZEN_PLAN_LIMIT).map_err(|_| INVALID)?;
    if document["version"] != 1 {
        return Err("PUBLICATION_FROZEN_VERSION");
    }
    if document["generator_version"] != GENERATOR_VERSION {
        return Err("PUBLICATION_FROZEN_GENERATOR");
    }
    let frozen: Frozen = serde_json::from_value(document).map_err(|_| INVALID)?;
    if frozen.review_digest != review
        || frozen.input_digest != digest(&encode_bounded(&InputBytes(&frozen.input))?)
    {
        return Err(INVALID);
    }
    let plan = build(&frozen.input).map_err(|_| INVALID)?;
    if plan.review_digest != review || plan.outputs != frozen.outputs {
        return Err(INVALID);
    }
    Ok(plan)
}
