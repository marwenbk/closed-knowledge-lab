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
  conversation_id: string;
  message_id: string;
  rag_run_id: string;
  status: AnswerStatus;
  answer: string;
  sender: Sender;
  citations: Citation[];
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
