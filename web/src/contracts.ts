export type GateDecisionStatus =
  | "EXECUTED"
  | "AWAITING_CONFIRMATION"
  | "NEEDS_CLARIFICATION"
  | "BLOCKED"
  | "NO_ACTION"
  | "MODEL_ERROR";

export type ParseStatus =
  | "PARSED"
  | "AMBIGUOUS"
  | "INVALID"
  | "MISSING"
  | "UNSUPPORTED";

export type RoleBindingStatus =
  | "BOUND"
  | "AMBIGUOUS"
  | "MISMATCH"
  | "NOT_APPLICABLE";

export interface FieldDiagnostic {
  field_name: string;
  parse_status: ParseStatus;
  reason_code: string | null;
  message: string;
  raw_quote: string | null;
  context_excerpt: string | null;
  start_offset: number | null;
  end_offset: number | null;
  utf16_start_offset?: number | null;
  utf16_end_offset?: number | null;
  normalization_form_matched?: string | null;
  role_binding_status?: RoleBindingStatus | null;
  candidate_count?: number;
  competing_candidates?: string[];
  canonical_value: unknown;
  rule_id: string | null;
  parser_version: string | null;
}

export interface SimulatedToolEffect {
  record_id: string;
  tool_name: string;
  canonical_arguments: Record<string, unknown>;
  confirmation_status: string;
  business_approval_status: string;
  action_digest: string;
  created_at_utc: string;
  idempotent_replay: boolean;
}

export interface GateResultResponse {
  status: GateDecisionStatus;
  request_id: string;
  proposal_id: string;
  audit_correlation_id: string;
  reason_code: string;
  message: string;
  tool_name: string | null;
  canonical_arguments: Record<string, unknown>;
  field_diagnostics: FieldDiagnostic[];
  confirmation_status: string;
  business_approval_status: string;
  action_digest: string | null;
  confirmation_token?: string | null;
  clarification_question: string | null;
  simulated_effect: SimulatedToolEffect | null;
  untrusted_proposal: Record<string, unknown> | null;
  telemetry?: Record<string, unknown>;
}

export interface SimulatedState {
  schema_version: string;
  audit_chain_intact?: boolean;
  expenses: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
  pending_confirmations: Array<Record<string, unknown>>;
  counts: {
    expenses: number;
    events: number;
    pending_confirmations: number;
    audit_events: number;
  };
}

export interface EvaluationRunItem {
  run: {
    run_id: string;
    suite: string;
    profile_name: string;
    num_cases: number;
    model_filename?: string;
    created_at_utc?: string;
  };
  metrics: {
    systems?: Record<string, Record<string, unknown>>;
    model_systems?: Record<string, Record<string, unknown>>;
    oracle_upper_bound?: Record<string, unknown>;
  };
}

export interface BenchmarkCaseItem {
  case_id: string;
  paraphrase_group: string;
  locale: string;
  timezone: string;
  split: string;
  suites: string[];
  task_family: string;
  request_text: string;
  expected_decision: GateDecisionStatus;
  tool_expected: string | null;
  expected_arguments: Record<string, unknown>;
  expected_reason_code: string;
}
