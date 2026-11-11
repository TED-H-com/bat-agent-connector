// Deterministic .NET Framework regressions; no windows, installation or live provider.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Automation;

internal static class ManagedViewProbeTests
{
    private static int assertions;

    private static void Check(bool value, string failure)
    {
        if (!value) throw new Exception(failure);
        assertions++;
    }

    private static void Observe(ManagedViewProbe.Readiness state, object type, object name, object enabled)
    {
        state.Observe(property => property == AutomationElement.ControlTypeProperty ? type :
            property == AutomationElement.NameProperty ? name : enabled);
    }

    private static void ObserveProvider(ManagedViewProbe.ProviderSelection selection, object type, object name,
        object enabled, Action press)
    {
        selection.Observe(property => property == AutomationElement.ControlTypeProperty ? type :
            property == AutomationElement.NameProperty ? name : enabled, press);
    }

    private static int Main()
    {
        foreach (string phase in new string[] {
            "started", "owner_verified", "assemblies_loaded", "window_resolved",
            "query_started", "query_completed", "completed", "failed" })
        {
            Check(ManagedViewProbe.FormatPhase(phase, 7234) == "phase_elapsed_ms:" + phase + ":7234",
                "Phase elapsed format drifted");
        }
        Check(ManagedViewProbe.FormatPhase("started", -1) == "phase_elapsed_ms:started:0", "Negative phase time escaped");
        Check(ManagedViewProbe.FormatPhase("failed", long.MaxValue) == "phase_elapsed_ms:failed:60000", "Phase time was not bounded");
        bool invalidPhase = false;
        try { ManagedViewProbe.FormatPhase("private UI text", 1); } catch (ArgumentException) { invalidPhase = true; }
        Check(invalidPhase, "Unknown phase text escaped");
        Check(ManagedViewProbe.ElapsedMilliseconds(1000, 8500, 1000) == 7500, "Parent process-start origin was lost");
        Check(ManagedViewProbe.ElapsedMilliseconds(8500, 8500, 1000) == 0, "Zero elapsed time drifted");
        Check(ManagedViewProbe.ElapsedMilliseconds(1000, 90000, 1000) == 60000, "Elapsed time was not bounded");
        Check(ManagedViewProbe.ParseElapsedOrigin("1000", 8500) == 1000, "Parent timestamp was not parsed invariantly");
        foreach (string value in new string[] { "0", "-1", "8501", " 1000", "1000 secret", "99999999999999999999" })
        {
            bool refused = false;
            try { ManagedViewProbe.ParseElapsedOrigin(value, 8500); } catch (ArgumentException) { refused = true; }
            Check(refused, "Malformed or future parent timestamp was accepted");
        }

        foreach (uint code in new uint[] { 0x80040201, 0x80010108, 0x80010001, 0x8001010A })
        {
            Check(ManagedViewProbe.IsTransient(new COMException("private value", unchecked((int)code))), "Transient COM read became fatal");
            Check(ManagedViewProbe.IsTransient(new ExternalException("private value", unchecked((int)code))), "Transient external read became fatal");
        }
        Check(ManagedViewProbe.IsTransient(new ElementNotAvailableException("private value")), "Unavailable element became fatal");
        foreach (Exception error in new Exception[] {
            new InvalidOperationException("property not cached"), new InvalidCastException("bad value"),
            new NullReferenceException(), new TimeoutException(), new COMException("denied", unchecked((int)0x80070005)),
            new COMException("wrong thread", unchecked((int)0x8001010E)), new ExternalException("unknown") })
            Check(!ManagedViewProbe.IsTransient(error), "Unexpected error was retried");

        TextWriter original = Console.Error;
        StringWriter diagnostic = new StringWriter();
        Console.SetError(diagnostic);
        try
        {
            ManagedViewProbe.ReportError(new COMException("private label and text-entry value", unchecked((int)0x80010108)));
            Check(diagnostic.ToString() == "error_type:System.Runtime.InteropServices.COMException" + Environment.NewLine +
                "error_hresult:0x80010108" + Environment.NewLine, "COM diagnostics exposed more than type and HRESULT");
            diagnostic.GetStringBuilder().Length = 0;
            ManagedViewProbe.ReportError(new InvalidOperationException("private label", new Exception("private inner value")));
            Check(diagnostic.ToString() == "error_type:System.InvalidOperationException" + Environment.NewLine,
                "Managed diagnostics exposed more than type");

            foreach (Exception error in new Exception[] {
                new ElementNotAvailableException("private value"), new COMException("private value", unchecked((int)0x80040201)) })
            {
                foreach (AutomationProperty failed in new AutomationProperty[] {
                    AutomationElement.ControlTypeProperty, AutomationElement.NameProperty, AutomationElement.IsEnabledProperty })
                {
                    var state = new ManagedViewProbe.Readiness();
                    state.Observe(property => {
                        if (property == failed) throw error;
                        if (property == AutomationElement.ControlTypeProperty) return ControlType.Button;
                        return "Ready to set up";
                    });
                    Check(!state.WebContent && !state.Authenticated && !state.SaveEnabled, "Unavailable node left partial evidence");
                    Observe(state, ControlType.Text, "Get ready to work", null);
                    Check(state.Setup, "Unavailable node prevented the next node read");
                }
            }
            var fatalState = new ManagedViewProbe.Readiness();
            bool thrown = false;
            try { fatalState.Observe(property => { throw new InvalidOperationException("cache bug"); }); }
            catch (InvalidOperationException) { thrown = true; }
            Check(thrown, "Missing cache entry was silently skipped");
            thrown = false;
            try { fatalState.Observe(property => { throw new COMException("busy", unchecked((int)0x8001010A)); }); }
            catch (COMException) { thrown = true; }
            Check(thrown, "Busy provider was silently skipped instead of retrying the read");
        }
        finally { Console.SetError(original); }

        foreach (object absent in new object[] { null, AutomationElement.NotSupported })
        {
            var state = new ManagedViewProbe.Readiness();
            Observe(state, absent, "Ready to set up", true);
            Observe(state, ControlType.Text, absent, true);
            Observe(state, ControlType.Button, "Find profiles and workspaces", absent);
            Check(!state.WebContent && !state.Authenticated && !state.SaveEnabled, "Absent property became readiness evidence");
        }
        foreach (string[] labels in new string[][] {
            new string[] { "Ready to set up", "Get ready to work", "Ready to set up", "Find profiles and workspaces" },
            new string[] { "可以開始設定了", "準備開始工作", "可以開始設定了", "尋找設定檔與工作區" } })
        {
            var state = new ManagedViewProbe.Readiness();
            Observe(state, ControlType.Document, null, null);
            Observe(state, ControlType.Text, labels[0] + " suffix", null);
            Check(!state.Authenticated, "Partial label matched readiness");
            Observe(state, ControlType.Text, labels[0], null);
            Observe(state, ControlType.Text, labels[1], null);
            Observe(state, ControlType.Text, labels[2], null);
            foreach (object disabled in new object[] { false, "true", 1 })
            {
                Observe(state, ControlType.Button, labels[3], disabled);
                Check(!state.SaveEnabled, "Button lacked a positive enabled boolean");
            }
            Observe(state, ControlType.Button, labels[3], true);
            Check(state.ToJson(true) == "{\"webContent\":true,\"authenticated\":true,\"setup\":true,\"configurationLoaded\":true,\"saveEnabled\":true,\"roleChoice\":false,\"providerEnabled\":false,\"errorSummary\":null,\"bounded\":true}",
                "Complete localized readiness changed");
            Check(state.ToJson(false).Contains("\"bounded\":false"), "Read bound was lost");
        }
        foreach (string label in new string[] { "Provide a local workspace", "提供本機工作區" })
        {
            int presses = 0;
            var selection = new ManagedViewProbe.ProviderSelection();
            foreach (object disabled in new object[] { false, null, AutomationElement.NotSupported, "true" })
                ObserveProvider(selection, ControlType.Button, label, disabled, () => presses++);
            ObserveProvider(selection, ControlType.Text, label, true, () => presses++);
            ObserveProvider(selection, ControlType.Button, label + " suffix", true, () => presses++);
            bool refused = false;
            try { selection.Press(); } catch (InvalidOperationException) { refused = true; }
            Check(refused && presses == 0, "Absent exact enabled provider button was pressed");
            ObserveProvider(selection, ControlType.Button, label, true, () => presses++);
            selection.Press();
            Check(presses == 1, "Provider button was not invoked exactly once");
            refused = false;
            try { selection.Press(); } catch (InvalidOperationException) { refused = true; }
            Check(refused && presses == 1, "Provider action was repeated");
            var readiness = new ManagedViewProbe.Readiness();
            Observe(readiness, ControlType.Button, label, false);
            Check(readiness.RoleChoice, "Disabled provider chooser was hidden from relaunch evidence");
            Check(!readiness.ProviderEnabled, "Disabled provider button reported enabled");
            Observe(readiness, ControlType.Button, label, true);
            Check(readiness.ProviderEnabled, "Enabled provider button missing from diagnostic");
        }
        foreach (string summary in new string[] { "This request could not be completed. Check Details.", "無法完成此要求，請查看詳細資料。" })
        {
            var state = new ManagedViewProbe.Readiness();
            Observe(state, ControlType.Text, "private token/path/native error", null);
            Check(state.ErrorSummary == null && !state.ToJson(true).Contains("private"), "Arbitrary UI text escaped the probe");
            Observe(state, ControlType.Text, summary, null);
            Check(state.ErrorSummary == summary && state.ToJson(true).Contains(summary), "Visible public error summary was lost");
        }
        {
            int presses = 0;
            var ambiguous = new ManagedViewProbe.ProviderSelection();
            for (int i = 0; i < 2; i++)
                ObserveProvider(ambiguous, ControlType.Button, "Provide a local workspace", true, () => presses++);
            bool refused = false;
            try { ambiguous.Press(); } catch (InvalidOperationException) { refused = true; }
            Check(refused && presses == 0, "Several provider matches caused an action");
            var unavailable = new ManagedViewProbe.ProviderSelection();
            refused = false;
            try { unavailable.Observe(property => { throw new ElementNotAvailableException(); }, () => presses++); }
            catch (ElementNotAvailableException) { refused = true; }
            Check(refused && presses == 0, "Unavailable action target was silently skipped");
        }
        Console.WriteLine("Passed UIA probe assertions: " + assertions);
        return 0;
    }
}
