// Native fixture helper: only this test's bundle, PID and launch identity.
// No Accessibility changes, AppleScript, arbitrary keystrokes or PID kills.
import AppKit
import CoreGraphics
import ApplicationServices

let fixtureApplication = NSApplication.shared
fixtureApplication.setActivationPolicy(.accessory)
let args = CommandLine.arguments
let identifier = "io.betteragent.dashboard"
func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}
func emit(_ value: [String: Any]) {
    print(String(data: try! JSONSerialization.data(withJSONObject: value), encoding: .utf8)!)
}
let providerRoleLabels = ["Provide a local workspace", "提供本機工作區"]
func pressProvider(_ pid: pid_t) {
    guard AXIsProcessTrusted() else { fail("Existing Accessibility trust is required; fixture does not change privacy settings") }
    let application = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(application, 0.2)
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &value) == .success,
          let windows = value as? [AXUIElement], windows.count == 1
    else { fail("Expected one owned Accessibility window") }
    var pending = windows
    var matches = [AXUIElement]()
    var visited = 0
    let deadline = Date().addingTimeInterval(2)
    while let element = pending.popLast() {
        visited += 1
        guard visited <= 2000 && Date() < deadline else { fail("Provider button scan exceeded its bound") }
        func attribute(_ name: String) -> CFTypeRef? {
            var value: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
            guard error == .success || error == .attributeUnsupported || error == .noValue
            else { fail("Provider button scan could not read an owned element") }
            return value
        }
        if (attribute(kAXRoleAttribute) as? String) == "AXButton" {
            let labels = [attribute(kAXTitleAttribute), attribute(kAXDescriptionAttribute)].compactMap { $0 as? String }
            if labels.contains(where: { providerRoleLabels.contains($0) }),
               (attribute(kAXEnabledAttribute) as? Bool) == true,
               !matches.contains(where: { CFEqual($0, element) }) { matches.append(element) }
        }
        if let children = attribute(kAXChildrenAttribute) as? [AXUIElement] { pending.append(contentsOf: children.reversed()) }
    }
    guard Date() < deadline, matches.count == 1 else { fail("Expected exactly one enabled provider role button") }
    // Do not repeat an action after an uncertain Accessibility result.
    guard AXUIElementPerformAction(matches[0], kAXPressAction as CFString) == .success
    else { fail("Owned provider role button press failed") }
    emit(["role": "provider", "pressed": true, "matches": 1])
}
func managedView(_ pid: pid_t) -> [String: Any] {
    guard AXIsProcessTrusted() else { fail("Existing Accessibility trust is required; fixture does not change privacy settings") }
    let application = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(application, 0.2)
    var result: [String: Any] = ["webContent": false, "authenticated": false, "setup": false,
                  "configurationLoaded": false, "saveEnabled": false, "roleChoice": false, "bounded": true,
                  "providerEnabled": false, "errorSummary": NSNull()]
    var pending = [application]
    var visited = 0
    let deadline = Date().addingTimeInterval(2)
    while let element = pending.popLast() {
        visited += 1
        if visited > 2000 || Date() >= deadline { result["bounded"] = false; break }
        func attribute(_ name: String) -> CFTypeRef? {
            var value: CFTypeRef?
            return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
        }
        let role = attribute(kAXRoleAttribute) as? String ?? ""
        if role == "AXWebArea" { result["webContent"] = true }
        // Never inspect text-entry values. Only fixed, public error summaries
        // may leave this probe; arbitrary accessibility text stays private.
        if ["AXStaticText", "AXHeading", "AXButton"].contains(role) {
            let labels = [attribute(kAXTitleAttribute), attribute(kAXDescriptionAttribute),
                          role == "AXStaticText" ? attribute(kAXValueAttribute) : nil].compactMap { $0 as? String }
            for label in labels {
                if role == "AXButton" && providerRoleLabels.contains(label) {
                    result["roleChoice"] = true
                    if (attribute(kAXEnabledAttribute) as? Bool) == true { result["providerEnabled"] = true }
                }
                if ["This request could not be completed. Check Details.", "無法完成此要求，請查看詳細資料。"].contains(label) {
                    result["errorSummary"] = label
                }
                if ["Ready to set up", "可以開始設定了"].contains(label) { result["authenticated"] = true }
                if ["Get ready to work", "準備開始工作"].contains(label) { result["setup"] = true }
                if ["Ready to set up", "可以開始設定了"].contains(label) { result["configurationLoaded"] = true }
                if role == "AXButton" && ["Review connection", "預覽連線"].contains(label),
                   (attribute(kAXEnabledAttribute) as? Bool) == true { result["saveEnabled"] = true }
            }
        }
        if let children = attribute(kAXChildrenAttribute) as? [AXUIElement] { pending.append(contentsOf: children.reversed()) }
    }
    return result
}
func perform() {
    if args.count == 2 && args[1] == "existing" {
        print(NSRunningApplication.runningApplications(withBundleIdentifier: identifier).count)
        exit(0)
    }
    if args.count == 3 && (args[1] == "launch" || args[1] == "launch-managed") {
        guard NSRunningApplication.runningApplications(withBundleIdentifier: identifier).isEmpty
        else { fail("Refusing an existing Dashboard process") }
        let url = URL(fileURLWithPath: args[2]).standardizedFileURL
        guard Bundle(url: url)?.bundleIdentifier == identifier else { fail("Wrong fixture bundle") }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.createsNewApplicationInstance = true
        configuration.addsToRecentItems = false
        configuration.promptsUserIfNeeded = false
        configuration.environment = args[1] == "launch-managed" ? [:] : ["BATC_DESKTOP_TOKEN": "fixture-native-token"]
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { app, error in
            guard error == nil, let app, app.bundleURL?.standardizedFileURL == url,
                  app.bundleIdentifier == identifier, let date = app.launchDate
            else { fail("Launch Services did not return the requested fixture app: \(String(describing: error))") }
            emit(["pid": app.processIdentifier, "launchDate": date.timeIntervalSince1970])
            exit(0)
        }
        return
    }
    guard args.count == 5, let pid = Int32(args[2]), let launchDate = Double(args[4])
    else { fail("Invalid fixture identity") }
    let running = NSRunningApplication(processIdentifier: pid)
    if running == nil || running!.isTerminated {
        guard args[1] == "inspect" else { fail("Fixture app already terminated") }
        emit(["terminated": true, "windows": []])
        exit(0)
    }
    guard let app = running, app.bundleIdentifier == identifier,
          app.bundleURL?.standardizedFileURL.path == URL(fileURLWithPath: args[3]).standardizedFileURL.path,
          app.launchDate?.timeIntervalSince1970 == launchDate
    else { fail("Fixture app identity does not match") }
    let state = "finished=\(app.isFinishedLaunching) policy=\(app.activationPolicy.rawValue) hidden=\(app.isHidden) terminated=\(app.isTerminated)"
    switch args[1] {
    case "select-provider":
        pressProvider(pid)
    case "managed-view":
        emit(managedView(pid))
    case "hide":
        // Hosted macOS can report false while the asynchronous hide still completes.
        // Record that return value; the caller must prove hidden state and no windows.
        emit(["reportedSuccess": app.hide()])
    case "close":
        guard AXIsProcessTrusted() else { fail("Existing Accessibility trust is required; fixture does not change privacy settings") }
        let application = AXUIElementCreateApplication(pid)
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &value) == .success,
              let windows = value as? [AXUIElement], windows.count == 1
        else { fail("Expected one owned Accessibility window") }
        var button: CFTypeRef?
        guard AXUIElementCopyAttributeValue(windows[0], kAXCloseButtonAttribute as CFString, &button) == .success,
              let button, AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString) == .success
        else { fail("Owned native close-button action failed") }
    case "quit":
        guard app.terminate() else { fail("Native Quit request refused: \(state)") }
    case "cleanup-force":
        // Failure cleanup only; the successful lifecycle never uses forced termination.
        guard app.forceTerminate() else { fail("Owned fixture cleanup refused: \(state)") }
    case "inspect":
        let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []).filter {
                ($0[kCGWindowOwnerPID as String] as? Int32) == pid
                    && ($0[kCGWindowLayer as String] as? Int) == 0
                    && (($0[kCGWindowBounds as String] as? [String: Any])?["Width"] as? Double ?? 0) > 300
            }
        emit(["pid": pid, "hidden": app.isHidden, "terminated": app.isTerminated,
            "finishedLaunching": app.isFinishedLaunching, "activationPolicy": app.activationPolicy.rawValue,
            "windows": windows.map { ["id": $0[kCGWindowNumber as String] ?? 0,
                                      "bounds": $0[kCGWindowBounds as String] ?? [:]] }])
    default: fail("Unknown fixture command")
    }
    exit(0)
}
// Application operations run inside AppKit's event loop, after launch registration.
DispatchQueue.main.async { perform() }
fixtureApplication.run()
