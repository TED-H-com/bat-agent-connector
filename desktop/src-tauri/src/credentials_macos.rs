//! macOS secure system input and local Keychain. No token enters a WebView or subprocess.
use super::*;
use core_foundation::{
    array::CFArray,
    base::{CFType, TCFType},
    dictionary::CFDictionary,
    string::CFString,
};
use core_foundation_sys::{base::CFRelease, user_notification::*};
use security_framework::passwords::{
    delete_generic_password, generic_password, set_generic_password, PasswordOptions,
};

const SERVICE: &str = "io.betteragent.dashboard.central.v1";
const ITEM_NOT_FOUND: i32 = -25300;

fn reference(binding: &str) -> Result<&str, String> {
    if binding.len() != 64 || !binding.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid native credential reference".into());
    }
    Ok(binding)
}

impl Vault for OsVault {
    fn supported(&self) -> bool {
        true
    }
    fn source(&self) -> &'static str {
        "macos_keychain"
    }
    fn read(&self, binding: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
        let options = PasswordOptions::new_generic_password(SERVICE, reference(binding)?);
        match generic_password(options) {
            Ok(bytes) => {
                let bytes = Zeroizing::new(bytes);
                if bytes.len() > MAX_BLOB {
                    return Err("Protected credential exceeds its bound".into());
                }
                Ok(Some(bytes))
            }
            Err(error) if error.code() == ITEM_NOT_FOUND => Ok(None),
            Err(_) => Err("Unable to read this configuration's Keychain credential".into()),
        }
    }
    fn write(&self, binding: &str, bytes: &[u8]) -> Result<(), String> {
        reference(binding)?;
        Record::decode(bytes, binding)?;
        // SecItemAdd/Update preserves an existing entry on refusal; never delete before replacing.
        set_generic_password(SERVICE, binding, bytes)
            .map_err(|_| "Unable to save this configuration's Keychain credential".into())
    }
    fn remove(&self, binding: &str) -> Result<(), String> {
        match delete_generic_password(SERVICE, reference(binding)?) {
            Ok(()) => Ok(()),
            Err(error) if error.code() == ITEM_NOT_FOUND => Ok(()),
            Err(_) => Err("Unable to remove this configuration's Keychain credential".into()),
        }
    }
    fn prompt(
        &self,
        actor: &str,
        endpoint: &str,
        locale: Locale,
        _: isize,
    ) -> Result<Option<Zeroizing<String>>, String> {
        // CFUserNotification presents a native secure field from the blocking worker;
        // unlike AppKit views it does not require moving the enrollment onto the UI thread.
        let notification = Notification::new(actor, endpoint, locale)?;
        let mut response = 0;
        let status =
            unsafe { CFUserNotificationReceiveResponse(notification.0, 300.0, &mut response) };
        if status != 0 {
            return Err("Native credential dialog did not complete".into());
        }
        if response & 0b11 != kCFUserNotificationDefaultResponse {
            return Ok(None);
        }
        let value = unsafe {
            CFUserNotificationGetResponseValue(
                notification.0,
                kCFUserNotificationTextFieldValuesKey,
                0,
            )
        };
        if value.is_null() {
            return Err("Enter a Connector API token".into());
        }
        let value = unsafe { CFString::wrap_under_get_rule(value) };
        if value.char_len() > MAX_TOKEN as isize {
            return Err("Credential input exceeds its bound".into());
        }
        let token = Zeroizing::new(value.to_string());
        validate_token(&token)?;
        Ok(Some(token))
    }
}

struct Notification(CFUserNotificationRef);
impl Notification {
    fn new(actor: &str, endpoint: &str, locale: Locale) -> Result<Self, String> {
        let (message, save, cancel, field) = match locale {
            Locale::English => (
                format!("Connector account: {actor}\nServer: {endpoint}\nThe token is saved in Keychain only after the server identity is verified."),
                "Verify and save", "Cancel", "Connector API token",
            ),
            Locale::TraditionalChinese => (
                format!("Connector 帳號：{actor}\n伺服器：{endpoint}\n確認伺服器身分後，才會將憑證存入鑰匙圈。"),
                "驗證並儲存", "取消", "Connector API 權杖",
            ),
        };
        // Constant keys are retained with Get ownership; the dictionary owns all values.
        let description = unsafe {
            CFDictionary::<CFString, CFType>::from_CFType_pairs(&[
                (
                    CFString::wrap_under_get_rule(kCFUserNotificationAlertHeaderKey),
                    CFString::new("Better Agent Dashboard").as_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kCFUserNotificationAlertMessageKey),
                    CFString::new(&message).as_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kCFUserNotificationDefaultButtonTitleKey),
                    CFString::new(save).as_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kCFUserNotificationAlternateButtonTitleKey),
                    CFString::new(cancel).as_CFType(),
                ),
                (
                    CFString::wrap_under_get_rule(kCFUserNotificationTextFieldTitlesKey),
                    CFArray::from_CFTypes(&[CFString::new(field)]).as_CFType(),
                ),
            ])
        };
        let mut error = 0;
        let notification = unsafe {
            CFUserNotificationCreate(
                std::ptr::null(),
                300.0,
                kCFUserNotificationPlainAlertLevel | CFUserNotificationSecureTextField(0),
                &mut error,
                description.as_concrete_TypeRef(),
            )
        };
        if notification.is_null() {
            return Err("Native credential dialog is unavailable".into());
        }
        let notification = Self(notification);
        if error != 0 {
            return Err("Native credential dialog is unavailable".into());
        }
        Ok(notification)
    }
}
impl Drop for Notification {
    fn drop(&mut self) {
        unsafe {
            CFUserNotificationCancel(self.0);
            CFRelease(self.0.cast());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_references_never_reach_keychain() {
        for value in ["", "../another-account", &"g".repeat(64)] {
            assert!(OsVault.read(value).is_err());
            assert!(OsVault.remove(value).is_err());
            assert!(OsVault.write(value, b"invalid").is_err());
        }
    }

    #[test]
    #[ignore = "requires an isolated Keychain on a disposable hosted Mac"]
    fn isolated_keychain_roundtrip() {
        assert_eq!(
            std::env::var("RUNNER_ENVIRONMENT").as_deref(),
            Ok("github-hosted")
        );
        assert_eq!(std::env::var("GITHUB_ACTIONS").as_deref(), Ok("true"));
        assert_eq!(
            std::env::var("BATC_ISOLATED_KEYCHAIN").as_deref(),
            Ok("true")
        );
        let binding = binding(
            "http://127.0.0.1:1/",
            &uuid::Uuid::new_v4().to_string(),
            "2026-10-08",
        );
        struct Cleanup(String);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = OsVault.remove(&self.0);
            }
        }
        assert!(OsVault.read(&binding).unwrap().is_none());
        let _cleanup = Cleanup(binding.clone());
        let mut record = Record {
            version: 1,
            binding: binding.clone(),
            identity: Identity {
                server_id: "fixture-server".into(),
                principal_id: "fixture-principal".into(),
            },
            token: Zeroizing::new("fixture-keychain-token".into()),
        };
        OsVault.write(&binding, &record.encode().unwrap()).unwrap();
        let child =
            bat_fleet_core::hidden_process::hidden_command(std::env::current_exe().unwrap())
                .args([
                    "--ignored",
                    "--exact",
                    "credentials::macos::tests::keychain_readback_child",
                ])
                .env("BATC_KEYCHAIN_TEST_BINDING", &binding)
                .status()
                .unwrap();
        assert!(child.success(), "Separate-process Keychain readback failed");
        let reopened = OsVault;
        assert_eq!(
            Record::decode(&reopened.read(&binding).unwrap().unwrap(), &binding)
                .unwrap()
                .token
                .as_str(),
            "fixture-keychain-token"
        );
        record.token = Zeroizing::new("fixture-replaced-token".into());
        OsVault.write(&binding, &record.encode().unwrap()).unwrap();
        assert!(OsVault.write(&binding, b"malformed replacement").is_err());
        assert_eq!(
            Record::decode(&reopened.read(&binding).unwrap().unwrap(), &binding)
                .unwrap()
                .token
                .as_str(),
            "fixture-replaced-token"
        );
        OsVault.remove(&binding).unwrap();
        assert!(reopened.read(&binding).unwrap().is_none());
        OsVault.remove(&binding).unwrap();
    }
    #[test]
    #[ignore = "child of the isolated hosted-Mac Keychain test"]
    fn keychain_readback_child() {
        assert_eq!(
            std::env::var("RUNNER_ENVIRONMENT").as_deref(),
            Ok("github-hosted")
        );
        assert_eq!(
            std::env::var("BATC_ISOLATED_KEYCHAIN").as_deref(),
            Ok("true")
        );
        let binding = std::env::var("BATC_KEYCHAIN_TEST_BINDING").unwrap();
        let bytes = OsVault.read(&binding).unwrap().unwrap();
        assert_eq!(
            Record::decode(&bytes, &binding).unwrap().token.as_str(),
            "fixture-keychain-token"
        );
    }
}
