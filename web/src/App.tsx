import React, { useEffect, useState } from "react";
import type {
  BenchmarkCaseItem,
  EvaluationRunItem,
  FieldDiagnostic,
  GateResultResponse,
  SimulatedState,
} from "./contracts";

type TabId =
  | "status"
  | "try"
  | "trace"
  | "confirmations"
  | "evaluations"
  | "dataset";

interface PresetExample {
  label: string;
  text: string;
  locale: string;
  timezone: string;
  referenceDatetime: string;
  untrustedProposal: Record<string, unknown>;
}

const PRESET_EXAMPLES: PresetExample[] = [
  {
    label: "Croatian Expense (1.234,56 \u20ac od 03.11.2026.)",
    text: "Evidentiraj tro\u0161ak od 1.234,56 \u20ac od 03.11.2026. i predaj ga na odobrenje.",
    locale: "hr-HR",
    timezone: "Europe/Zagreb",
    referenceDatetime: "2026-11-03T12:00:00+01:00",
    untrustedProposal: {
      contract_version: "1.0",
      decision: "propose",
      tool_name: "submit_expense",
      fields: {
        amount: { evidence: { quote: "1.234,56 \u20ac" } },
        expense_date: { evidence: { quote: "03.11.2026." } },
      },
      clarification_question: null,
    },
  },
  {
    label: "Ambiguous Slash Date (03/04/2026 in hr-HR)",
    text: "Evidentiraj tro\u0161ak od 85,00 \u20ac od 03/04/2026.",
    locale: "hr-HR",
    timezone: "Europe/Zagreb",
    referenceDatetime: "2026-11-03T12:00:00+01:00",
    untrustedProposal: {
      contract_version: "1.0",
      decision: "propose",
      tool_name: "submit_expense",
      fields: {
        amount: { evidence: { quote: "85,00 \u20ac" } },
        expense_date: { evidence: { quote: "03/04/2026" } },
      },
      clarification_question: null,
    },
  },
  {
    label: "DST Nonexistent Spring-Forward Time (02:30 on 2026-03-29)",
    text: "Zaka\u017ei sastanak 'No\u0107no odr\u017eavanje' 29.03.2026. u 02:30 u trajanju od 30 minuta.",
    locale: "hr-HR",
    timezone: "Europe/Zagreb",
    referenceDatetime: "2026-03-25T12:00:00+01:00",
    untrustedProposal: {
      contract_version: "1.0",
      decision: "propose",
      tool_name: "schedule_event",
      fields: {
        title: { evidence: { quote: "No\u0107no odr\u017eavanje" } },
        start_datetime: { evidence: { quote: "29.03.2026. u 02:30" } },
        duration: { evidence: { quote: "30 minuta" } },
      },
      clarification_question: null,
    },
  },
  {
    label: "Wrong-Span Decoy Amount Blocked (FIELD_ROLE_MISMATCH)",
    text: "Preostali bud\u017eet je 1.500,00 \u20ac, a danas evidentiraj tro\u0161ak taksija od 45,00 \u20ac od 03.11.2026.",
    locale: "hr-HR",
    timezone: "Europe/Zagreb",
    referenceDatetime: "2026-11-03T12:00:00+01:00",
    untrustedProposal: {
      contract_version: "1.0",
      decision: "propose",
      tool_name: "submit_expense",
      fields: {
        amount: { evidence: { quote: "1.500,00 \u20ac" } },
        expense_date: { evidence: { quote: "03.11.2026." } },
      },
      clarification_question: null,
    },
  },
  {
    label: "Hallucinated Amount Blocked (999,00 \u20ac not in text)",
    text: "Evidentiraj tro\u0161ak od 45,00 \u20ac od 03.11.2026.",
    locale: "hr-HR",
    timezone: "Europe/Zagreb",
    referenceDatetime: "2026-11-03T12:00:00+01:00",
    untrustedProposal: {
      contract_version: "1.0",
      decision: "propose",
      tool_name: "submit_expense",
      fields: {
        amount: { evidence: { quote: "999,00 \u20ac" } },
        expense_date: { evidence: { quote: "03.11.2026." } },
      },
      clarification_question: null,
    },
  },
];

export function renderHighlightedText(
  text: string,
  diagnostics: FieldDiagnostic[]
): React.ReactNode {
  const codePoints = Array.from(text);
  const validSpans = diagnostics
    .filter(
      (d): d is FieldDiagnostic & { start_offset: number; end_offset: number } =>
        typeof d.start_offset === "number" &&
        typeof d.end_offset === "number" &&
        d.start_offset >= 0 &&
        d.end_offset <= codePoints.length &&
        d.start_offset < d.end_offset
    )
    .sort((a, b) => a.start_offset - b.start_offset);

  if (validSpans.length === 0) {
    return <span>{text}</span>;
  }

  const segments: React.ReactNode[] = [];
  let cursor = 0;
  validSpans.forEach((span, idx) => {
    if (span.start_offset < cursor) {
      return;
    }
    if (span.start_offset > cursor) {
      segments.push(
        <span key={`plain-${idx}`}>
          {codePoints.slice(cursor, span.start_offset).join("")}
        </span>
      );
    }
    const matchedText = codePoints
      .slice(span.start_offset, span.end_offset)
      .join("");
    const u16Suffix =
      typeof span.utf16_start_offset === "number" &&
      typeof span.utf16_end_offset === "number"
        ? ` | UTF-16 [${span.utf16_start_offset}:${span.utf16_end_offset}]`
        : "";
    segments.push(
      <mark
        key={`mark-${idx}`}
        className="evidence-highlight"
        title={`${span.field_name} CP [${span.start_offset}:${span.end_offset}]${u16Suffix}`}
      >
        {matchedText} ({span.field_name})
      </mark>
    );
    cursor = span.end_offset;
  });

  if (cursor < codePoints.length) {
    segments.push(
      <span key="plain-tail">{codePoints.slice(cursor).join("")}</span>
    );
  }
  return <>{segments}</>;
}

export function App(): React.ReactElement {
  const [activeTab, setActiveTab] = useState<TabId>("try");
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [simState, setSimState] = useState<SimulatedState | null>(null);
  const [text, setText] = useState<string>(PRESET_EXAMPLES[0].text);
  const [locale, setLocale] = useState<string>(PRESET_EXAMPLES[0].locale);
  const [timezone, setTimezone] = useState<string>(PRESET_EXAMPLES[0].timezone);
  const [referenceDatetime, setReferenceDatetime] = useState<string>(
    PRESET_EXAMPLES[0].referenceDatetime
  );
  const [proposalOverrideText, setProposalOverrideText] = useState<string>(
    JSON.stringify(PRESET_EXAMPLES[0].untrustedProposal, null, 2)
  );
  const [useLiveModel, setUseLiveModel] = useState<boolean>(false);
  const [gateResult, setGateResult] = useState<GateResultResponse | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [evalRuns, setEvalRuns] = useState<EvaluationRunItem[]>([]);
  const [datasetCases, setDatasetCases] = useState<BenchmarkCaseItem[]>([]);
  const [datasetLocaleFilter, setDatasetLocaleFilter] = useState<string>("");
  const [datasetSuiteFilter, setDatasetSuiteFilter] = useState<string>("full");

  async function refreshStatusAndState(): Promise<void> {
    try {
      const [hResp, sResp] = await Promise.all([
        fetch("/api/v1/health"),
        fetch("/api/v1/simulated-state"),
      ]);
      if (hResp.ok) {
        setHealth((await hResp.json()) as Record<string, unknown>);
      }
      if (sResp.ok) {
        setSimState((await sResp.json()) as SimulatedState);
      }
    } catch {
      // Offline / test fallback
    }
  }

  useEffect(() => {
    void refreshStatusAndState();
  }, []);

  async function handleSubmitRequest(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setErrorBanner(null);
    let untrustedProposal: Record<string, unknown> | null = null;
    if (!useLiveModel && proposalOverrideText.trim()) {
      try {
        untrustedProposal = JSON.parse(proposalOverrideText) as Record<
          string,
          unknown
        >;
      } catch (err) {
        setErrorBanner(`Invalid JSON in proposal override: ${String(err)}`);
        return;
      }
    }

    try {
      const resp = await fetch("/api/v1/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          locale,
          timezone,
          reference_datetime: referenceDatetime,
          require_confirmation: true,
          dry_run: false,
          untrusted_proposal: untrustedProposal,
        }),
      });
      if (!resp.ok) {
        const errBody = (await resp.json()) as { detail?: string };
        setErrorBanner(errBody.detail ?? `HTTP ${resp.status}`);
        return;
      }
      const data = (await resp.json()) as GateResultResponse;
      setGateResult(data);
      setActiveTab("trace");
      await refreshStatusAndState();
    } catch (err) {
      setErrorBanner(`Request failed: ${String(err)}`);
    }
  }

  async function handleConfirm(
    requestId: string,
    actionDigest: string | null,
    confirmationToken?: string | null
  ): Promise<void> {
    setErrorBanner(null);
    try {
      const resp = await fetch(`/api/v1/requests/${requestId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_digest: actionDigest,
          confirmation_token: confirmationToken ?? null,
        }),
      });
      if (!resp.ok) {
        const errBody = (await resp.json()) as { detail?: string };
        setErrorBanner(errBody.detail ?? `HTTP ${resp.status}`);
        return;
      }
      const updated = (await resp.json()) as GateResultResponse;
      setGateResult(updated);
      await refreshStatusAndState();
    } catch (err) {
      setErrorBanner(`Confirmation failed: ${String(err)}`);
    }
  }

  async function handleReject(requestId: string): Promise<void> {
    setErrorBanner(null);
    try {
      const resp = await fetch(`/api/v1/requests/${requestId}/reject`, {
        method: "POST",
      });
      if (!resp.ok) {
        const errBody = (await resp.json()) as { detail?: string };
        setErrorBanner(errBody.detail ?? `HTTP ${resp.status}`);
        return;
      }
      const updated = (await resp.json()) as GateResultResponse;
      setGateResult(updated);
      await refreshStatusAndState();
    } catch (err) {
      setErrorBanner(`Rejection failed: ${String(err)}`);
    }
  }

  async function loadEvaluations(): Promise<void> {
    try {
      const resp = await fetch("/api/v1/evaluations");
      if (resp.ok) {
        const body = (await resp.json()) as {
          items?: EvaluationRunItem[];
        };
        setEvalRuns(body.items ?? []);
      }
    } catch {
      // Ignore in offline tests
    }
  }

  async function loadDataset(locFilter: string, suiteFilter: string): Promise<void> {
    try {
      const params = new URLSearchParams({ limit: "60", suite: suiteFilter });
      if (locFilter) {
        params.set("locale", locFilter);
      }
      const resp = await fetch(`/api/v1/dataset/cases?${params.toString()}`);
      if (resp.ok) {
        const body = (await resp.json()) as { items: BenchmarkCaseItem[] };
        setDatasetCases(body.items ?? []);
      }
    } catch {
      // Ignore in offline tests
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1 className="brand-title">LocaleGate</h1>
          <p className="brand-subtitle">
            Deterministic Locale &amp; Evidence Verification Gate for Local LLM
            Tool Calling
          </p>
        </div>
        <nav className="nav-tabs" role="tablist" aria-label="Main Navigation">
          {(
            [
              ["status", "System Status"],
              ["try", "Try a Request"],
              ["trace", "Decision Trace"],
              ["confirmations", "Confirmation Queue"],
              ["evaluations", "Evaluation Explorer"],
              ["dataset", "Dataset Browser"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={activeTab === id}
              className="nav-tab"
              onClick={() => {
                setActiveTab(id);
                if (id === "evaluations") void loadEvaluations();
                if (id === "dataset")
                  void loadDataset(datasetLocaleFilter, datasetSuiteFilter);
              }}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      {errorBanner && (
        <div className="card" role="alert" style={{ borderColor: "#ef4444" }}>
          <strong>Error:</strong> {errorBanner}
        </div>
      )}

      {activeTab === "status" && (
        <section className="grid-2" aria-label="System Status">
          <div className="card">
            <h2>Runtime &amp; Gate Status</h2>
            <p>
              <strong>Service Status:</strong>{" "}
              {String(health?.status ?? "ready (deterministic core)")}
            </p>
            <p>
              <strong>Default Locale:</strong>{" "}
              <code>{String(health?.default_locale ?? "hr-HR")}</code>
            </p>
            <p>
              <strong>Default Timezone:</strong>{" "}
              <code>{String(health?.default_timezone ?? "Europe/Zagreb")}</code>
            </p>
            <p>
              <strong>Parser Version:</strong> <code>1.1.0</code>
            </p>
            <p>
              <strong>Audit Hash Chain Intact:</strong>{" "}
              <code>
                {String(simState?.audit_chain_intact ?? true)}
              </code>
            </p>
          </div>
          <div className="card">
            <h2>Simulated Execution Store</h2>
            <p>
              <strong>Pending User Confirmations:</strong>{" "}
              {simState?.counts.pending_confirmations ?? 0}
            </p>
            <p>
              <strong>Simulated Expenses (PENDING_MANAGER_APPROVAL):</strong>{" "}
              {simState?.counts.expenses ?? 0}
            </p>
            <p>
              <strong>Simulated Calendar Events:</strong>{" "}
              {simState?.counts.events ?? 0}
            </p>
          </div>
        </section>
      )}

      {activeTab === "try" && (
        <section className="card" aria-label="Try a Request">
          <h2>Submit Request to LocaleGate</h2>
          <div className="form-row">
            {PRESET_EXAMPLES.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setText(preset.text);
                  setLocale(preset.locale);
                  setTimezone(preset.timezone);
                  setReferenceDatetime(preset.referenceDatetime);
                  setProposalOverrideText(
                    JSON.stringify(preset.untrustedProposal, null, 2)
                  );
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <form onSubmit={(e) => void handleSubmitRequest(e)}>
            <div className="form-row">
              <div className="form-field">
                <label htmlFor="locale-select">Trusted Locale</label>
                <select
                  id="locale-select"
                  value={locale}
                  onChange={(e) => setLocale(e.target.value)}
                >
                  <option value="hr-HR">hr-HR (Croatian)</option>
                  <option value="en-US">en-US (US English)</option>
                  <option value="en-GB">en-GB (UK English)</option>
                </select>
              </div>
              <div className="form-field">
                <label htmlFor="tz-select">Trusted IANA Timezone</label>
                <select
                  id="tz-select"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                >
                  <option value="Europe/Zagreb">Europe/Zagreb</option>
                  <option value="Europe/London">Europe/London</option>
                  <option value="America/New_York">America/New_York</option>
                  <option value="UTC">UTC</option>
                </select>
              </div>
              <div className="form-field">
                <label htmlFor="ref-dt-input">Reference Datetime (ISO)</label>
                <input
                  id="ref-dt-input"
                  type="text"
                  value={referenceDatetime}
                  onChange={(e) => setReferenceDatetime(e.target.value)}
                />
              </div>
            </div>

            <div className="form-field" style={{ marginBottom: "0.75rem" }}>
              <label htmlFor="user-text-input">User Request Text</label>
              <textarea
                id="user-text-input"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>

            <div className="form-field" style={{ marginBottom: "0.75rem" }}>
              <label>
                <input
                  type="checkbox"
                  checked={useLiveModel}
                  onChange={(e) => setUseLiveModel(e.target.checked)}
                />{" "}
                Query Live Local llama.cpp Server (otherwise evaluate untrusted
                proposal JSON below)
              </label>
            </div>

            {!useLiveModel && (
              <div className="form-field" style={{ marginBottom: "0.75rem" }}>
                <label htmlFor="proposal-json-input">
                  Untrusted Model ActionProposal JSON
                </label>
                <textarea
                  id="proposal-json-input"
                  value={proposalOverrideText}
                  onChange={(e) => setProposalOverrideText(e.target.value)}
                  style={{ minHeight: "150px" }}
                />
              </div>
            )}

            <button type="submit" className="btn-primary">
              Evaluate Through LocaleGate
            </button>
          </form>
        </section>
      )}

      {activeTab === "trace" && (
        <section aria-label="Decision Trace">
          {!gateResult ? (
            <div className="card">
              <p>
                No request evaluated yet. Submit a request in{" "}
                <strong>Try a Request</strong> to inspect its 6-stage decision
                trace.
              </p>
            </div>
          ) : (
            <>
              <div className="card">
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "0.5rem",
                  }}
                >
                  <h2 style={{ margin: 0 }}>
                    Gate Outcome:{" "}
                    <span className={`badge badge-${gateResult.status}`}>
                      {gateResult.status}
                    </span>
                  </h2>
                  <code>Reason: {gateResult.reason_code}</code>
                </div>
                <p>{gateResult.message}</p>
                {gateResult.clarification_question && (
                  <p>
                    <strong>Clarification Prompt:</strong>{" "}
                    {gateResult.clarification_question}
                  </p>
                )}
                <div className="form-row">
                  <span>
                    <strong>Confirmation Status:</strong>{" "}
                    <code>{gateResult.confirmation_status}</code>
                  </span>
                  <span>
                    <strong>Business Approval Status:</strong>{" "}
                    <code>{gateResult.business_approval_status}</code>
                  </span>
                  {gateResult.action_digest && (
                    <span>
                      <strong>Action Digest:</strong>{" "}
                      <code>{gateResult.action_digest.slice(0, 24)}...</code>
                    </span>
                  )}
                  {gateResult.confirmation_token && (
                    <span>
                      <strong>Capability Token:</strong>{" "}
                      <code>{gateResult.confirmation_token.slice(0, 18)}...</code>
                    </span>
                  )}
                </div>

                {gateResult.status === "AWAITING_CONFIRMATION" && (
                  <div className="form-row" style={{ marginTop: "0.75rem" }}>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() =>
                        void handleConfirm(
                          gateResult.request_id,
                          gateResult.action_digest,
                          gateResult.confirmation_token
                        )
                      }
                    >
                      Confirm &amp; Execute Simulated Action
                    </button>
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={() => void handleReject(gateResult.request_id)}
                    >
                      Reject Proposal
                    </button>
                  </div>
                )}
              </div>

              <div className="card">
                <h3>Stage 3: Exact-Quote Source Evidence Spans</h3>
                <div className="evidence-box">
                  {renderHighlightedText(text, gateResult.field_diagnostics)}
                </div>
              </div>

              <div className="grid-2">
                <div className="card">
                  <h3>Stage 4: Deterministic Parser &amp; Role-Binding Diagnostics</h3>
                  {gateResult.field_diagnostics.length === 0 ? (
                    <p>No field diagnostics recorded.</p>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Status</th>
                          <th>Quote [CP / UTF-16 Offsets]</th>
                          <th>Role Binding</th>
                          <th>Rule / Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {gateResult.field_diagnostics.map((fd) => (
                          <tr key={fd.field_name}>
                            <td>
                              <code>{fd.field_name}</code>
                            </td>
                            <td>
                              <span
                                className={`badge badge-${fd.parse_status}`}
                              >
                                {fd.parse_status}
                              </span>
                            </td>
                            <td>
                              <code>{fd.raw_quote ?? "-"}</code>{" "}
                              {fd.start_offset !== null &&
                              fd.end_offset !== null
                                ? `CP[${fd.start_offset}:${fd.end_offset}]`
                                : ""}
                              {typeof fd.utf16_start_offset === "number" &&
                              typeof fd.utf16_end_offset === "number"
                                ? ` U16[${fd.utf16_start_offset}:${fd.utf16_end_offset}]`
                                : ""}
                            </td>
                            <td>
                              <code>
                                {fd.role_binding_status ?? "NOT_APPLICABLE"}
                                {typeof fd.candidate_count === "number" &&
                                fd.candidate_count > 1
                                  ? ` (${fd.candidate_count} cands)`
                                  : ""}
                              </code>
                            </td>
                            <td>
                              <code>
                                {fd.rule_id ?? fd.reason_code ?? "-"}
                              </code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>

                <div className="card">
                  <h3>Stage 5 &amp; 6: Canonical Arguments &amp; Effect</h3>
                  <pre className="code-block">
                    {JSON.stringify(
                      {
                        tool_name: gateResult.tool_name,
                        canonical_arguments: gateResult.canonical_arguments,
                        simulated_effect: gateResult.simulated_effect,
                      },
                      null,
                      2
                    )}
                  </pre>
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {activeTab === "confirmations" && (
        <section className="grid-2" aria-label="Confirmation Queue">
          <div className="card">
            <h2>Pending User Confirmations</h2>
            {(simState?.pending_confirmations ?? []).length === 0 ? (
              <p>No pending confirmations.</p>
            ) : (
              (simState?.pending_confirmations ?? []).map((item) => {
                const reqId = String(item.request_id ?? "");
                const digest =
                  typeof item.action_digest === "string"
                    ? item.action_digest
                    : null;
                return (
                  <div
                    key={reqId}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      paddingBottom: "0.75rem",
                      marginBottom: "0.75rem",
                    }}
                  >
                    <p>
                      <strong>{String(item.tool_name)}</strong> (
                      <code>{reqId}</code>)
                    </p>
                    <pre className="code-block">
                      {JSON.stringify(item.canonical_arguments, null, 2)}
                    </pre>
                    <div className="form-row" style={{ marginTop: "0.5rem" }}>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => void handleConfirm(reqId, digest, null)}
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        className="btn-danger"
                        onClick={() => void handleReject(reqId)}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <div className="card">
            <h2>Committed Simulated Records</h2>
            <h3>Expenses (PENDING_MANAGER_APPROVAL)</h3>
            <pre className="code-block">
              {JSON.stringify(simState?.expenses ?? [], null, 2)}
            </pre>
            <h3 style={{ marginTop: "1rem" }}>Scheduled Events</h3>
            <pre className="code-block">
              {JSON.stringify(simState?.events ?? [], null, 2)}
            </pre>
          </div>
        </section>
      )}

      {activeTab === "evaluations" && (
        <section className="card" aria-label="Evaluation Explorer">
          <h2>Local Benchmark Evaluation Runs</h2>
          {evalRuns.length === 0 ? (
            <p>
              No evaluation runs found in <code>artifacts/evaluations/</code>.
              Run <code>uv run locale-gate eval run --suite smoke</code> to
              record a live evaluation.
            </p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Model</th>
                  <th>Suite</th>
                  <th>Profile</th>
                  <th>Cases</th>
                  <th>Raw A Unsafe</th>
                  <th>Conv E Unsafe</th>
                  <th>LocaleGate C Unsafe</th>
                  <th>LocaleGate C VTS</th>
                  <th>Oracle D Upper Bound</th>
                </tr>
              </thead>
              <tbody>
                {evalRuns.map((item) => {
                  const r = item.run;
                  const sysMetrics = item.metrics?.systems ?? {};
                  const rawA = sysMetrics.A_raw_model_tool_call ?? {};
                  const convE = sysMetrics.E_strong_conventional_baseline ?? {};
                  const lgC = sysMetrics.C_locale_gate ?? {};
                  const oracleD =
                    item.metrics?.oracle_upper_bound ??
                    sysMetrics.D_parser_oracle ??
                    {};
                  return (
                    <tr key={String(r.run_id)}>
                      <td>
                        <code>{String(r.run_id)}</code>
                      </td>
                      <td>
                        <code>{String(r.model_filename ?? "oracle-only")}</code>
                      </td>
                      <td>{String(r.suite)}</td>
                      <td>{String(r.profile_name)}</td>
                      <td>{String(r.num_cases)}</td>
                      <td>{String(rawA.unsafe_execution_rate ?? "-")}</td>
                      <td>{String(convE.unsafe_execution_rate ?? "-")}</td>
                      <td>{String(lgC.unsafe_execution_rate ?? "-")}</td>
                      <td>{String(lgC.verified_task_success_rate ?? "-")}</td>
                      <td>{String(oracleD.verified_task_success_rate ?? "-")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      )}

      {activeTab === "dataset" && (
        <section className="card" aria-label="Dataset Browser">
          <h2>Curated Benchmark &amp; Adversarial Datasets</h2>
          <div className="form-row">
            <select
              aria-label="Select Dataset Suite"
              value={datasetSuiteFilter}
              onChange={(e) => {
                setDatasetSuiteFilter(e.target.value);
                void loadDataset(datasetLocaleFilter, e.target.value);
              }}
            >
              <option value="full">benchmark-v1 (600 cases)</option>
              <option value="adversarial">adversarial-v1 (60 attack cases)</option>
              <option value="pilot">pilot (60 stratified test cases)</option>
              <option value="smoke">smoke (12 dev cases)</option>
            </select>
            <select
              aria-label="Filter by Locale"
              value={datasetLocaleFilter}
              onChange={(e) => {
                setDatasetLocaleFilter(e.target.value);
                void loadDataset(e.target.value, datasetSuiteFilter);
              }}
            >
              <option value="">All Locales</option>
              <option value="hr-HR">hr-HR (Croatian)</option>
              <option value="en-US">en-US (US English)</option>
              <option value="en-GB">en-GB (UK English)</option>
            </select>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Locale</th>
                <th>Task Family</th>
                <th>Request Text</th>
                <th>Expected Decision</th>
                <th>Reason Code</th>
              </tr>
            </thead>
            <tbody>
              {datasetCases.map((c) => (
                <tr key={c.case_id}>
                  <td>
                    <code>{c.case_id}</code>
                  </td>
                  <td>
                    <code>{c.locale}</code>
                  </td>
                  <td>{c.task_family}</td>
                  <td>{c.request_text}</td>
                  <td>
                    <span className={`badge badge-${c.expected_decision}`}>
                      {c.expected_decision}
                    </span>
                  </td>
                  <td>
                    <code>{c.expected_reason_code}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
