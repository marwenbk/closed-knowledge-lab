import type { ConversationState } from "@/lib/widget-types";

export type AdminIdentity = {
  id: string;
  user_id: string;
  email: string;
  display_name: string;
  roles: string[];
  expires_at: string;
};

export type Handoff = {
  id: string;
  conversation_id: string;
  state: ConversationState;
  priority: "LOW" | "NORMAL" | "HIGH" | "URGENT";
  reason: string;
  requested_at: string;
  assigned_agent_id: string | null;
  claimed_at: string | null;
  waiting_seconds: number;
  latest_customer_message: string | null;
  assigned_agent_name: string | null;
  answerability_status: string | null;
};

export type AdminMessage = {
  message_id: string;
  client_message_id: string | null;
  sender_type: "CUSTOMER" | "AI" | "HUMAN" | "SYSTEM" | "INTERNAL";
  sender_user_id: string | null;
  sender_label: string;
  content: string;
  visibility: "PUBLIC" | "INTERNAL";
  status: string;
  review_status: "NONE" | "PENDING" | "REGENERATING" | "APPROVED" | "EDITED" | "REJECTED";
  review_regeneration_count: number;
  citations: Array<Record<string, unknown>>;
  rag_run_id: string | null;
  created_at: string;
  delivered_at: string | null;
};

export type RagRunSummary = {
  rag_run_id: string;
  status: string;
  answerability_status: string | null;
  verification_status: string | null;
  error_code: string | null;
};

export type AdminConversation = {
  id: string;
  conversation_id: string;
  state: ConversationState;
  priority: string | null;
  handoff_reason: string | null;
  handoff_requested_at: string | null;
  assigned_agent_id: string | null;
  assigned_agent_name: string | null;
  claimed_at: string | null;
  created_at: string;
  updated_at: string;
  messages: AdminMessage[];
  rag_runs: RagRunSummary[];
};

export type Dashboard = {
  generated_at: string;
  knowledge: {
    dataset_id: string;
    dataset_version: string;
    status: string;
    document_count: number;
    chunk_count: number;
  };
  runtime: {
    model: string;
    model_version: string | null;
    prompt_version: string;
    settings_version: string;
    embedding_version: string;
  };
  conversations: {
    open: number;
    waiting: number;
    assigned: number;
    human_active: number;
    oldest_waiting_seconds: number;
  };
  quality: {
    answerability: Record<string, number>;
    refusal_rate_percent: number;
    conflict_rate_percent: number;
    grounding_failure_rate_percent: number;
    average_ai_latency_ms: number;
    average_handoff_wait_seconds: number;
  };
  latest_evaluation: {
    mode: string | null;
    passed: boolean | null;
    dataset_version: string | null;
    evaluated_cases: number | null;
    updated_at: string;
  } | null;
};

export type KnowledgeDocument = {
  id: string;
  document_key: string;
  title: string;
  source_path: string;
  checksum: string;
  status: string;
  chunk_count: number;
  sort_order: number;
};

export type KnowledgeDocumentDetail = Omit<KnowledgeDocument, "chunk_count" | "sort_order"> & {
  dataset_version: string;
  metadata: Record<string, unknown>;
  revision: {
    revision_number: number;
    content_checksum: string;
    front_matter: Record<string, unknown>;
  } | null;
  chunks: Array<{
    id: string;
    stable_chunk_key: string;
    section: string;
    section_path: string[];
    ordinal: number;
    content: string;
    token_count: number;
  }>;
};

export type KnowledgeValidation = {
  passed: boolean;
  document_count: number;
  chunk_count: number;
  errors: Array<{ code: string; document_key: string; message: string }>;
  duplicate_policy_ids: Record<string, string[]>;
  conflict_fixture_ids: string[];
};

export type KnowledgeEvaluation = {
  id: string;
  status: "RUNNING" | "PASSED" | "FAILED";
  suite: string;
  manifest_checksum: string;
  metrics: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
};

export type KnowledgeVersion = {
  id: string;
  dataset_id: string;
  dataset_version: string;
  status: "DRAFT" | "ACTIVE" | "RETIRED" | "FAILED";
  source_version_id: string | null;
  manifest_checksum: string;
  document_count: number;
  chunk_count: number;
  embedded_chunk_count: number;
  validation: KnowledgeValidation | null;
  validated_at: string | null;
  evaluation: KnowledgeEvaluation | null;
  created_by: string | null;
  activated_by: string | null;
  created_at: string;
  activated_at: string | null;
};

export type KnowledgeVersionDetail = KnowledgeVersion & {
  documents: Array<Omit<KnowledgeDocument, "status">>;
};

export type KnowledgeWorkflowDocument = {
  id: string;
  version_id: string;
  dataset_version: string;
  version_status: KnowledgeVersion["status"];
  document_key: string;
  title: string;
  source_path: string;
  checksum: string;
  revision_number: number;
  content_markdown: string;
  front_matter: Record<string, unknown>;
  revisions: Array<{
    revision_number: number;
    content_checksum: string;
    created_at: string;
  }>;
  dependencies: {
    fact_ids: string[];
    evaluation_case_ids: string[];
  };
  chunks: KnowledgeDocumentDetail["chunks"];
};

export type EvaluationSummary = {
  id: string;
  suite: string;
  mode: "RETRIEVAL" | "FULL";
  status: "RUNNING" | "PASSED" | "FAILED";
  baseline_run_id: string | null;
  metrics: Record<string, unknown>;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
};

export type EvaluationDetail = EvaluationSummary & {
  kb_version_id: string;
  kb_manifest_checksum: string;
  prompt_version: string;
  prompt_checksum: string;
  settings_version: string;
  settings_checksum: string;
  model_name: string;
  embedding_model: string;
  embedding_version: string;
  started_by: string;
};

export type FeedbackCategory =
  | "CORRECT"
  | "INCORRECT"
  | "MISSING_KB_INFORMATION"
  | "CONFLICTING_KB_INFORMATION"
  | "RETRIEVAL_FAILURE"
  | "GROUNDING_FAILURE"
  | "ESCALATION_APPROPRIATE"
  | "ESCALATION_UNNECESSARY";

export type Feedback = {
  id: string;
  rag_run_id: string;
  conversation_id: string;
  category: FeedbackCategory;
  note: string | null;
  created_by: string;
  created_by_name: string | null;
  created_at: string;
};

export type AuditEvent = {
  id: string;
  event_type: string;
  actor_type: string;
  actor_id: string | null;
  resource_type: string;
  resource_id: string;
  request_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type PromptBundle = {
  answerability_prompt: string;
  generation_prompt: string;
  verification_prompt: string;
};

export type PromptVersion = {
  id: string;
  version: string;
  status: "DRAFT" | "EVALUATED" | "ACTIVE" | "RETIRED";
  content_checksum: string;
  source_version_id: string | null;
  created_by: string | null;
  activated_by: string | null;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  prompts: PromptBundle;
  evaluation: EvaluationSummary | null;
};

export type RetrievalTuning = {
  vector_top_k: number;
  lexical_top_k: number;
  trigram_top_k: number;
  final_context_k: number;
  rrf_k: number;
  min_vector_similarity: number;
  trigram_min_similarity: number;
  trigram_fallback_enabled: boolean;
  second_hop_enabled: boolean;
};

export type SettingsVersion = {
  id: string;
  version: string;
  status: PromptVersion["status"];
  content_checksum: string;
  requires_reindex: boolean;
  source_version_id: string | null;
  created_by: string | null;
  activated_by: string | null;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  settings: RetrievalTuning;
  evaluation: EvaluationSummary | null;
};

export type RagRunDetail = {
  id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  original_query: string;
  conversation_context: string[];
  retrieval_query: string;
  status: string;
  answerability_status: string | null;
  verification_status: string | null;
  error_code: string | null;
  model: {
    provider: string;
    name: string;
    version: string | null;
    prompt_version: string;
    settings_version: string;
    embedding_version: string;
  };
  knowledge: {
    dataset_id: string | null;
    dataset_version: string | null;
  };
  user_message: string | null;
  assistant_message: string | null;
  citations: Array<Record<string, unknown>>;
  trace: Record<string, unknown> | null;
  regenerated: boolean | null;
  latency_ms: number | null;
  created_at: string;
  completed_at: string | null;
};

export type AdminStreamEvent = {
  event_id: number;
  conversation_id: string;
  timestamp: string;
  visibility: string;
  payload: Record<string, unknown>;
};
