export type StructuredRequirementRequest = {
  origin: string;
  destination: string;
  departure_date?: string;
  max_price_cny?: number;
  lower_price_preferred: boolean;
};

export type StructuredRequirementResponse = {
  conversation_id: string;
  execution_id: string;
  requirement_id: string | null;
  requirement_version: number | null;
  status: string;
  search_readiness: string | null;
  downstream_search_eligible: boolean;
  validation_issues: string[];
};

export type PriceSemantics = "EXACT" | "LOWER_BOUND";

export type PublicPublishedRecommendation = {
  publication_id: string;
  recommendation_result_id: string;
  execution_id: string;
  requirement_id: string;
  requirement_version: number;
  snapshot_id: string;
  snapshot_version: number;
  published_at: string;
  route_origin: string;
  route_destination: string;
  departure_date: string;
  selected_price_amount: string;
  selected_price_currency: string;
  selected_price_semantics: PriceSemantics;
  role: "BEST_OVERALL";
  reason: string;
  evidence: string[];
};

export type WorkflowOutcome =
  | "PUBLISHED"
  | "SEARCH_EMPTY"
  | "FILTER_EMPTY"
  | "PROVIDER_ERROR"
  | "NOT_READY";

export type ConversationReadResponse = {
  conversation_id: string;
  outcome: WorkflowOutcome;
  requirement_id: string | null;
  requirement_version: number | null;
  execution_id: string | null;
  current_published_recommendation: PublicPublishedRecommendation | null;
};

export class FlightAgentApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FlightAgentApiError";
  }
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

export async function startConversation(
  request: StructuredRequirementRequest,
): Promise<ConversationReadResponse> {
  const started = await postStructuredRequirement(request);
  return readConversation(started.conversation_id);
}

export async function postStructuredRequirement(
  request: StructuredRequirementRequest,
): Promise<StructuredRequirementResponse> {
  return requestJson<StructuredRequirementResponse>("/conversations", {
    body: JSON.stringify(request),
    method: "POST",
  });
}

export async function readConversation(conversationId: string): Promise<ConversationReadResponse> {
  return requestJson<ConversationReadResponse>(`/conversations/${conversationId}`);
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw new FlightAgentApiError(`Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}
