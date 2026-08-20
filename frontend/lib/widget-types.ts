export type AnswerStatus =
  | "ANSWERABLE"
  | "PARTIALLY_ANSWERABLE"
  | "AMBIGUOUS"
  | "NOT_ANSWERABLE"
  | "CONFLICTING_EVIDENCE";

export type ConversationState =
  | "AI_ACTIVE"
  | "AI_REVIEW_PENDING"
  | "HUMAN_REQUESTED"
  | "HUMAN_ASSIGNED"
  | "HUMAN_ACTIVE"
  | "RETURNED_TO_AI"
  | "CLOSED";

export type Citation = {
  citation_id: string;
  chunk_id: string;
  stable_chunk_key: string;
  document_key: string;
  document: string;
  section: string;
  quote: string;
};

export type Sender = {
  type: "CUSTOMER" | "AI" | "HUMAN" | "SYSTEM";
  label: string;
};

export type PublicMessage = {
  message_id: string;
  rag_run_id: string | null;
  sender: Sender;
  content: string;
  status: string;
  citations: Citation[];
  created_at: string;
  delivered_at: string | null;
};

export type WidgetSession = {
  session_id: string;
  token: string;
  token_type: "Bearer";
  expires_at: string;
  locale: string;
};

export type Conversation = {
  conversation_id: string;
  state: ConversationState;
  messages: PublicMessage[];
  created_at: string;
  updated_at: string;
};

export type MessageResponse = {
  delivery_mode: "AI";
  conversation_id: string;
  message_id: string;
  rag_run_id: string;
  status: AnswerStatus;
  answer: string;
  sender: Sender;
  citations: Citation[];
};

export type HumanQueueMessageResponse = {
  delivery_mode: "HUMAN_QUEUE";
  conversation_id: string;
  message_id: string;
  rag_run_id: null;
  status: "PERSISTED";
  state: ConversationState;
};

export type ReviewPendingMessageResponse = {
  delivery_mode: "REVIEW_PENDING";
  conversation_id: string;
  message_id: string;
  rag_run_id: string;
  status: "PENDING_REVIEW";
  state: "AI_REVIEW_PENDING";
};

export type MessageSubmissionResponse =
  | MessageResponse
  | HumanQueueMessageResponse
  | ReviewPendingMessageResponse;

export type HandoffResponse = {
  conversation_id: string;
  state: ConversationState;
  priority: "LOW" | "NORMAL" | "HIGH" | "URGENT";
  reason: string;
  requested_at: string;
  assigned_agent_id: string | null;
  claimed_at: string | null;
};

export type ConversationEvent = {
  event_id: number;
  conversation_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
};
