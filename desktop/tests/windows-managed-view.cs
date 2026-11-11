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
    private static long elapsedOrigin = Stopwatch.GetTimestamp();

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);

    private static void Phase(string phase)
    {
        Diagnostic(phase);
        Diagnostic(FormatPhase(phase, ElapsedMilliseconds(elapsedOrigin, Stopwatch.GetTimestamp(), Stopwatch.Frequency)));
    }

    internal static long ElapsedMilliseconds(long origin, long now, long frequency)
    {
        return (long)Math.Max(0, Math.Min(60000, (now - origin) * 1000.0 / frequency));
    }

    internal static long ParseElapsedOrigin(string value, long now)
    {
        long origin;
        if (!long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out origin) || origin <= 0 || origin > now)
            throw new ArgumentException();
        return origin;
    }

    internal static string FormatPhase(string phase, long elapsed)
    {
        switch (phase)
        {
            case "started": case "owner_verified": case "assemblies_loaded": case "window_resolved":
            case "query_started": case "query_completed": case "completed": case "failed":
                return "phase_elapsed_ms:" + phase + ":" + Math.Max(0, Math.Min(60000, elapsed)).ToString(CultureInfo.InvariantCulture);
            default: throw new ArgumentException();
        }
    }

    private static void Diagnostic(string line)
    {
        Console.Error.WriteLine(line);
        Console.Error.Flush();
    }

    [MTAThread]
    private static int Main(string[] args)
    {
        try
        {
            bool read = args.Length == 2;
            if (args.Length == 4 && args[2] == "--elapsed-origin")
            {
                // Same-machine QPC origin from the parent, before Process.Start:
                // includes CLR/JIT/assembly startup in the first phase timestamp.
                elapsedOrigin = ParseElapsedOrigin(args[3], Stopwatch.GetTimestamp());
                read = true;
            }
            Phase("started");
            if (!read && (args.Length != 3 || args[2] != "select-provider")) throw new ArgumentException();
            IntPtr window = new IntPtr(long.Parse(args[0], CultureInfo.InvariantCulture));
            uint expected = uint.Parse(args[1], CultureInfo.InvariantCulture);
            uint owner;
            if (GetWindowThreadProcessId(window, out owner) == 0 || owner != expected) throw new InvalidOperationException();
            Phase("owner_verified");
            if (read) ReadView(window);
            else PressProvider(window);
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
        Diagnostic("error_type:" + error.GetType().FullName);
        ExternalException external = error as ExternalException;
        if (external != null)
            Diagnostic("error_hresult:0x" + unchecked((uint)external.ErrorCode).ToString("X8", CultureInfo.InvariantCulture));
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

    internal sealed class ProviderSelection
    {
        private int matches;
        private Action invoke;

        internal static bool IsLabel(string label)
        {
            return label == "Provide a local workspace" || label == "提供本機工作區";
        }

        internal void Observe(Func<AutomationProperty, object> read, Action press)
        {
            if (read(AutomationElement.ControlTypeProperty) as ControlType != ControlType.Button) return;
            if (!IsLabel(read(AutomationElement.NameProperty) as string)) return;
            object enabled = read(AutomationElement.IsEnabledProperty);
            if (!(enabled is bool) || !(bool)enabled) return;
            matches++;
            invoke = press;
        }

        internal void Press()
        {
            if (matches != 1 || invoke == null) throw new InvalidOperationException();
            Action press = invoke;
            invoke = null;
            // The caller never retries an action, including an uncertain Invoke result.
            press();
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void PressProvider(IntPtr window)
    {
        AutomationElement root = AutomationElement.FromHandle(window);
        Stopwatch clock = Stopwatch.StartNew();
        AutomationElementCollection nodes = root.FindAll(TreeScope.Descendants,
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button));
        if (nodes.Count > 2000) throw new InvalidOperationException();
        ProviderSelection selection = new ProviderSelection();
        for (int index = 0; index < nodes.Count; index++)
        {
            if (clock.ElapsedMilliseconds >= 2000) throw new TimeoutException();
            AutomationElement node = nodes[index];
            selection.Observe(property => node.GetCurrentPropertyValue(property, true),
                () => ((InvokePattern)node.GetCurrentPattern(InvokePattern.Pattern)).Invoke());
        }
        if (clock.ElapsedMilliseconds >= 2000) throw new TimeoutException();
        selection.Press();
        Console.WriteLine("{\"role\":\"provider\",\"pressed\":true,\"matches\":1}");
        Console.Out.Flush();
        Phase("completed");
    }

    internal sealed class Readiness
    {
        internal bool WebContent, Authenticated, Setup, ConfigurationLoaded, SaveEnabled, RoleChoice, ProviderEnabled;
        internal string ErrorSummary;

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
                Diagnostic("node_unavailable");
                ReportError(error);
                return;
            }
            // Commit evidence only after every needed property of this node was
            // read. A disappearing button must not contribute even its label.
            if (type == ControlType.Document) { WebContent = true; return; }
            if (type == ControlType.Button && ProviderSelection.IsLabel(label))
            {
                RoleChoice = true;
                if (enabled) ProviderEnabled = true;
            }
            // Only these fixed public summaries may leave the probe. Never emit
            // arbitrary UI names, text-entry values or native error messages.
            if (label == "This request could not be completed. Check Details." || label == "無法完成此要求，請查看詳細資料。")
                ErrorSummary = label;
            if (label == "Ready to set up" || label == "可以開始設定了") Authenticated = true;
            if (label == "Get ready to work" || label == "準備開始工作") Setup = true;
            if (label == "Ready to set up" || label == "可以開始設定了") ConfigurationLoaded = true;
            if (type == ControlType.Button && enabled &&
                (label == "Find profiles and workspaces" || label == "尋找設定檔與工作區")) SaveEnabled = true;
        }

        internal string ToJson(bool bounded)
        {
            return "{\"webContent\":" + Bit(WebContent) + ",\"authenticated\":" + Bit(Authenticated)
                + ",\"setup\":" + Bit(Setup) + ",\"configurationLoaded\":" + Bit(ConfigurationLoaded)
                + ",\"saveEnabled\":" + Bit(SaveEnabled) + ",\"roleChoice\":" + Bit(RoleChoice)
                + ",\"providerEnabled\":" + Bit(ProviderEnabled)
                + ",\"errorSummary\":" + (ErrorSummary == null ? "null" : "\"" + ErrorSummary + "\"")
                + ",\"bounded\":" + Bit(bounded) + "}";
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
