//! Local diagnostics text only; no network, caller-selected path or generic file API.
use crate::files::Destination;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const LIMIT: usize = 256 * 1024;
const INVALID: &str = "DIAGNOSTICS_INVALID";

#[derive(Serialize)]
pub struct AppBuild {
    version: &'static str,
    commit: Option<&'static str>,
}
pub fn app_build() -> AppBuild {
    let commit = option_env!("BATC_BUILD_COMMIT").filter(|value| {
        matches!(value.len(), 40 | 64) && value.bytes().all(|c| c.is_ascii_hexdigit())
    });
    AppBuild {
        version: env!("CARGO_PKG_VERSION"),
        commit,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub content: String,
    pub filename: String,
    pub locale: String,
}
impl Input {
    pub fn validate(&self) -> Result<(), String> {
        let stem = self
            .filename
            .strip_prefix("batc-diagnostics-")
            .and_then(|s| s.strip_suffix(".json"));
        if self.content.len() > LIMIT
            || !matches!(self.locale.as_str(), "en" | "zh-TW")
            || !stem.is_some_and(|s| {
                s.len() == 13
                    && s.as_bytes()[8] == b'-'
                    && s.bytes()
                        .enumerate()
                        .all(|(i, c)| i == 8 || c.is_ascii_digit())
            })
        {
            return Err(INVALID.into());
        }
        let doc: serde_json::Value = serde_json::from_str(&self.content).map_err(|_| INVALID)?;
        if doc["schema"] != "batc.diagnostics.v1" {
            return Err(INVALID.into());
        }
        Ok(())
    }
    pub fn save(&self, destination: Destination) -> Result<Receipt, String> {
        self.validate()?;
        if !destination.display_name().to_lowercase().ends_with(".json") {
            return Err(INVALID.into());
        }
        let bytes = self.content.as_bytes();
        let digest = format!("{:x}", Sha256::digest(bytes));
        destination.publish_bytes(bytes, bytes.len() as u64, &digest, || Ok(()))?;
        Ok(Receipt {
            display_name: destination.display_name().into(),
        })
    }
}
#[derive(Serialize)]
pub struct Receipt {
    display_name: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> Input {
        Input {
            content: r#"{"schema":"batc.diagnostics.v1","note":"字"}"#.into(),
            filename: "batc-diagnostics-20261014-1200.json".into(),
            locale: "zh-TW".into(),
        }
    }
    #[test]
    fn bounds_and_narrow_input() {
        let mut i = input();
        assert!(i.validate().is_ok());
        i.content = "字".repeat(LIMIT / 3 + 1);
        assert!(i.validate().is_err());
        i = input();
        i.filename = "../escape.json".into();
        assert!(i.validate().is_err());
        i = input();
        i.content = r#"{"schema":"other"}"#.into();
        assert!(i.validate().is_err());
        i = input();
        i.locale = "other".into();
        assert!(i.validate().is_err());
        assert!(serde_json::from_value::<Input>(
            serde_json::json!({"content":"{}","filename":"x","locale":"en","path":"/tmp/escape"})
        )
        .is_err());
    }
    #[test]
    fn literal_bytes_and_existing_files_preserved() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("report.json");
        let i = input();
        let receipt = i.save(Destination::from_selection(&path).unwrap()).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), i.content);
        assert_eq!(
            serde_json::to_value(receipt).unwrap(),
            serde_json::json!({"display_name":"report.json"})
        );
        assert!(Destination::from_selection(&path).is_err());
        let next = root.path().join("race.json");
        let dest = Destination::from_selection(&next).unwrap();
        std::fs::write(&next, "existing").unwrap();
        assert!(i.save(dest).is_err());
        assert_eq!(std::fs::read_to_string(next).unwrap(), "existing");
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 2);
        assert!(i
            .save(Destination::from_selection(&root.path().join("report.txt")).unwrap())
            .is_err());
    }
    #[test]
    fn main_window_only_and_no_generic_filesystem_permission() {
        let caps: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/dashboard.json")).unwrap();
        assert_eq!(caps["windows"], serde_json::json!(["main"]));
        let permissions = caps["permissions"].as_array().unwrap();
        assert!(permissions.contains(&serde_json::json!("allow-native-diagnostics-save")));
        assert!(!permissions
            .iter()
            .any(|p| p.as_str().unwrap().starts_with("fs:")
                || p.as_str().unwrap().starts_with("dialog:")));
    }
}
