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

    private static int Main()
    {
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
            Observe(state, ControlType.Button, "Set up automatically", absent);
            Check(!state.WebContent && !state.Authenticated && !state.SaveEnabled, "Absent property became readiness evidence");
        }
        foreach (string[] labels in new string[][] {
            new string[] { "Ready to set up", "Get ready to work", "Ready to set up", "Set up automatically" },
            new string[] { "可以開始設定了", "準備開始工作", "可以開始設定了", "自動設定" } })
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
            Check(state.ToJson(true) == "{\"webContent\":true,\"authenticated\":true,\"setup\":true,\"configurationLoaded\":true,\"saveEnabled\":true,\"bounded\":true}",
                "Complete localized readiness changed");
            Check(state.ToJson(false).Contains("\"bounded\":false"), "Read bound was lost");
        }
        Console.WriteLine("Passed UIA probe assertions: " + assertions);
        return 0;
    }
}
