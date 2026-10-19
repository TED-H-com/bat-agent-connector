// Fixture-only UIA client. Compile once with the Windows .NET Framework compiler.
// A dedicated MTA process owns no windows; the parent enforces its 8-second deadline.
using System;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Windows.Automation;

internal static class ManagedViewProbe
{
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);

    private static void Phase(string phase)
    {
        Console.Error.WriteLine(phase);
        Console.Error.Flush();
    }

    [MTAThread]
    private static int Main(string[] args)
    {
        Phase("started");
        try
        {
            if (args.Length != 2) throw new ArgumentException();
            IntPtr window = new IntPtr(long.Parse(args[0], CultureInfo.InvariantCulture));
            uint expected = uint.Parse(args[1], CultureInfo.InvariantCulture);
            uint owner;
            if (GetWindowThreadProcessId(window, out owner) == 0 || owner != expected) throw new InvalidOperationException();
            Phase("owner_verified");
            ReadView(window);
            return 0;
        }
        catch (Exception error)
        {
            Phase("failed");
            ReportError(error);
            return IsTransient(error) ? 3 : 2;
        }
    }

    internal static void ReportError(Exception error)
    {
        // Type and numeric HRESULT only: never messages, stacks, UI names or values.
        Phase("error_type:" + error.GetType().FullName);
        ExternalException external = error as ExternalException;
        if (external != null)
            Phase("error_hresult:0x" + unchecked((uint)external.ErrorCode).ToString("X8", CultureInfo.InvariantCulture));
    }

    internal static bool IsUnavailable(Exception error)
    {
        ExternalException external = error as ExternalException;
        return error is ElementNotAvailableException ||
            (external != null && external.ErrorCode == unchecked((int)0x80040201)); // UIA_E_ELEMENTNOTAVAILABLE
    }

    internal static bool IsTransient(Exception error)
    {
        if (IsUnavailable(error)) return true;
        ExternalException external = error as ExternalException;
        if (external == null) return false;
        // A fresh read may recover a disconnected provider or a callee that is busy
        // or rejects this call. Do not retry other COM errors or cache-contract bugs.
        switch (unchecked((uint)external.ErrorCode))
        {
            case 0x80010108: // RPC_E_DISCONNECTED
            case 0x80010001: // RPC_E_CALL_REJECTED
            case 0x8001010A: // RPC_E_SERVERCALL_RETRYLATER
                return true;
            default:
                return false;
        }
    }

    internal sealed class Readiness
    {
        internal bool WebContent, Authenticated, Setup, ConfigurationLoaded, SaveEnabled;

        internal void Observe(Func<AutomationProperty, object> read)
        {
            ControlType type;
            string label;
            bool enabled;
            try
            {
                // ignoreDefaultValue=true preserves NotSupported. In particular,
                // an unsupported IsEnabled must not become a default true value.
                type = read(AutomationElement.ControlTypeProperty) as ControlType;
                if (type == null) return;
                label = type == ControlType.Document ? null : read(AutomationElement.NameProperty) as string;
                object value = type == ControlType.Button ? read(AutomationElement.IsEnabledProperty) : null;
                enabled = value is bool && (bool)value;
            }
            catch (Exception error)
            {
                if (!IsUnavailable(error)) throw;
                Phase("node_unavailable");
                ReportError(error);
                return;
            }
            // Commit evidence only after every needed property of this node was
            // read. A disappearing button must not contribute even its label.
            if (type == ControlType.Document) { WebContent = true; return; }
            if (label == "Ready to set up" || label == "可以開始設定了") Authenticated = true;
            if (label == "Get ready to work" || label == "準備開始工作") Setup = true;
            if (label == "Ready to set up" || label == "可以開始設定了") ConfigurationLoaded = true;
            if (type == ControlType.Button && enabled &&
                (label == "Set up automatically" || label == "自動設定")) SaveEnabled = true;
        }

        internal string ToJson(bool bounded)
        {
            return "{\"webContent\":" + Bit(WebContent) + ",\"authenticated\":" + Bit(Authenticated)
                + ",\"setup\":" + Bit(Setup) + ",\"configurationLoaded\":" + Bit(ConfigurationLoaded)
                + ",\"saveEnabled\":" + Bit(SaveEnabled) + ",\"bounded\":" + Bit(bounded) + "}";
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void ReadView(IntPtr window)
    {
        Phase("assemblies_loaded");
        AutomationElement root = AutomationElement.FromHandle(window);
        Phase("window_resolved");
        CacheRequest cache = new CacheRequest();
        cache.TreeScope = TreeScope.Element;
        // FindAll can return raw-view text nodes outside the default control view.
        // Cache each returned node, including those excluded by ControlViewCondition.
        cache.TreeFilter = Automation.RawViewCondition;
        cache.AutomationElementMode = AutomationElementMode.None;
        cache.Add(AutomationElement.ControlTypeProperty);
        cache.Add(AutomationElement.NameProperty);
        cache.Add(AutomationElement.IsEnabledProperty);
        // One owned-window query retrieves only document, text and button nodes.
        // Cached properties avoid repeated cross-process calls for every label.
        Condition wanted = new OrCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Document),
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Text),
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button));
        AutomationElementCollection nodes;
        Phase("query_started");
        using (cache.Activate()) nodes = root.FindAll(TreeScope.Descendants, wanted);
        Phase("query_completed");
        Readiness state = new Readiness();
        bool bounded = nodes.Count <= 2000;
        Stopwatch clock = Stopwatch.StartNew();
        for (int index = 0; bounded && index < nodes.Count; index++)
        {
            if (clock.ElapsedMilliseconds >= 2000) { bounded = false; break; }
            AutomationElement node = nodes[index];
            state.Observe(property => node.GetCachedPropertyValue(property, true));
        }
        Console.WriteLine(state.ToJson(bounded));
        Console.Out.Flush();
        Phase("completed");
    }

    private static string Bit(bool value) { return value ? "true" : "false"; }
}
