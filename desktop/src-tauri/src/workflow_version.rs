//! Canonical workflow metadata, shared by the release verifier and native updater.

pub fn valid(value: &str) -> bool {
    (12..=64).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| match index {
            4 | 7 => byte == b'-',
            10 => byte == b'.',
            _ => byte.is_ascii_digit(),
        })
}

#[cfg(test)]
fn from_frontmatter(skill: &str) -> &str {
    let header = skill
        .strip_prefix("---\n")
        .and_then(|text| text.split_once("\n---\n"))
        .expect("canonical skill must have LF frontmatter")
        .0;
    let values: Vec<_> = header
        .lines()
        .filter(|line| line.starts_with("  workflow_version:"))
        .collect();
    assert_eq!(
        values.len(),
        1,
        "canonical frontmatter must contain exactly one workflow_version line"
    );
    values[0]
        .strip_prefix("  workflow_version: \"")
        .and_then(|value| value.strip_suffix('"'))
        .expect("canonical workflow_version must be an indented quoted value")
}

#[cfg(test)]
pub(crate) fn canonical() -> &'static str {
    from_frontmatter(include_str!("../../../skills/bat-agent-connector/SKILL.md"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_canonical_workflow_accepted() {
        assert!(valid(canonical()), "canonical workflow: {:?}", canonical());
    }

    #[test]
    fn frontmatter_accepts_lf_and_crlf() {
        let lf = "---\nmetadata:\n  workflow_version: \"2030-12-31.1\"\n---\n  workflow_version: \"2099-01-01.2\"\n";
        let crlf = lf.replace('\n', "\r\n");
        assert_eq!(from_frontmatter(lf), "2030-12-31.1");
        assert_eq!(from_frontmatter(&crlf), from_frontmatter(lf));
    }

    #[test]
    fn shared_generator_cases() {
        for line in include_str!("../../../tests/fixtures/workflow-versions.tsv").lines() {
            if line.starts_with('#') || line.is_empty() {
                continue;
            }
            let (expected, rest) = line
                .split_once('\t')
                .expect("expected verdict TAB value TAB label");
            let (value, label) = rest.split_once('\t').expect("expected value TAB label");
            assert!(
                [expected, value, label]
                    .iter()
                    .all(|field| !field.contains('\r')),
                "workflow case fields must not contain carriage returns"
            );
            let value = if value == "@canonical" {
                canonical()
            } else {
                value
            };
            let accepted = match expected {
                "accept" => true,
                "reject" => false,
                _ => panic!("unknown workflow case verdict: {expected}"),
            };
            assert_eq!(valid(value), accepted, "workflow case {label}: {value:?}");
        }
    }

    #[test]
    #[should_panic(expected = "exactly one workflow_version line")]
    fn missing_frontmatter_value_never_uses_body_or_default() {
        from_frontmatter("---\nmetadata:\n---\n  workflow_version: \"2030-12-31.1\"\n");
    }

    #[test]
    #[should_panic(expected = "exactly one workflow_version line")]
    fn duplicate_frontmatter_values_are_refused() {
        from_frontmatter(
            "---\n  workflow_version: \"2030-12-31.1\"\n  workflow_version: \"2031-01-01.1\"\n---\n",
        );
    }
}
