import { useState } from "react";
import type { FormEvent } from "react";

import type {
  ConversationReadResponse,
  PublicPublishedRecommendation,
  StructuredRequirementRequest,
} from "./api";
import { startConversation } from "./api";
import "./App.css";

type FormState = {
  origin: string;
  destination: string;
  departureDate: string;
  maxPriceCny: string;
  lowerPricePreferred: boolean;
};

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; conversation: ConversationReadResponse }
  | { kind: "transport-error"; message: string };

const initialForm: FormState = {
  origin: "PEK",
  destination: "SHA",
  departureDate: "2026-09-01",
  maxPriceCny: "1200",
  lowerPricePreferred: true,
};

export function App() {
  const [form, setForm] = useState<FormState>(initialForm);
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState({ kind: "loading" });

    try {
      const conversation = await startConversation(toRequest(form));
      setState({ kind: "loaded", conversation });
    } catch (error) {
      setState({
        kind: "transport-error",
        message: error instanceof Error ? error.message : "Request failed",
      });
    }
  }

  return (
    <main className="shell">
      <section className="workspace" aria-labelledby="page-title">
        <div className="mast">
          <h1 id="page-title">Flight Agent</h1>
          <p>Published recommendation view</p>
        </div>

        <form className="requirement-form" onSubmit={handleSubmit}>
          <label>
            <span>Origin</span>
            <input
              name="origin"
              maxLength={3}
              minLength={3}
              value={form.origin}
              onChange={(event) =>
                setForm((current) => ({ ...current, origin: event.target.value.toUpperCase() }))
              }
            />
          </label>
          <label>
            <span>Destination</span>
            <input
              name="destination"
              maxLength={3}
              minLength={3}
              value={form.destination}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  destination: event.target.value.toUpperCase(),
                }))
              }
            />
          </label>
          <label>
            <span>Departure date</span>
            <input
              name="departureDate"
              type="date"
              value={form.departureDate}
              onChange={(event) =>
                setForm((current) => ({ ...current, departureDate: event.target.value }))
              }
            />
          </label>
          <label>
            <span>Max price</span>
            <input
              name="maxPriceCny"
              type="number"
              min={1}
              value={form.maxPriceCny}
              onChange={(event) =>
                setForm((current) => ({ ...current, maxPriceCny: event.target.value }))
              }
            />
          </label>
          <label className="preference">
            <input
              name="lowerPricePreferred"
              type="checkbox"
              checked={form.lowerPricePreferred}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  lowerPricePreferred: event.target.checked,
                }))
              }
            />
            <span>Lower price preferred</span>
          </label>
          <button type="submit" disabled={state.kind === "loading"}>
            Search
          </button>
        </form>

        <ConversationPanel state={state} />
      </section>
    </main>
  );
}

function ConversationPanel({ state }: { state: LoadState }) {
  if (state.kind === "idle") {
    return <section className="result-panel" aria-label="Conversation result" />;
  }
  if (state.kind === "loading") {
    return (
      <section className="result-panel" aria-live="polite" aria-label="Conversation result">
        <p className="status">Loading conversation projection</p>
      </section>
    );
  }
  if (state.kind === "transport-error") {
    return (
      <section className="result-panel" role="alert" aria-label="Conversation result">
        <h2>Transport Error</h2>
        <p>{state.message}</p>
      </section>
    );
  }

  return <OutcomeView conversation={state.conversation} />;
}

function OutcomeView({ conversation }: { conversation: ConversationReadResponse }) {
  const published = conversation.current_published_recommendation;
  if (conversation.outcome === "PUBLISHED" && published !== null) {
    return (
      <section className="result-panel published" aria-label="Conversation result">
        <header>
          <p className="eyebrow">Outcome</p>
          <h2>PUBLISHED</h2>
        </header>
        <dl className="facts">
          <div>
            <dt>Route</dt>
            <dd>
              {published.route_origin} to {published.route_destination}
            </dd>
          </div>
          <div>
            <dt>Departure date</dt>
            <dd>{published.departure_date}</dd>
          </div>
          <div>
            <dt>Selected price</dt>
            <dd>{formatSelectedPrice(published)}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>{published.role}</dd>
          </div>
          <div>
            <dt>Requirement version</dt>
            <dd>{published.requirement_version}</dd>
          </div>
          <div>
            <dt>Reason</dt>
            <dd>{published.reason}</dd>
          </div>
          <div>
            <dt>Evidence</dt>
            <dd>{published.evidence.join(", ")}</dd>
          </div>
          <div>
            <dt>Publication id</dt>
            <dd>{published.publication_id}</dd>
          </div>
          <div>
            <dt>Conversation id</dt>
            <dd>{conversation.conversation_id}</dd>
          </div>
          <div>
            <dt>Execution id</dt>
            <dd>{published.execution_id}</dd>
          </div>
          <div>
            <dt>Snapshot id</dt>
            <dd>{published.snapshot_id}</dd>
          </div>
        </dl>
      </section>
    );
  }

  return (
    <section className="result-panel" aria-label="Conversation result">
      <header>
        <p className="eyebrow">Outcome</p>
        <h2>{conversation.outcome}</h2>
      </header>
      <p>{outcomeCopy[conversation.outcome]}</p>
    </section>
  );
}

const outcomeCopy: Record<ConversationReadResponse["outcome"], string> = {
  PUBLISHED: "A published recommendation is available.",
  SEARCH_EMPTY: "No search results were returned for this requirement.",
  FILTER_EMPTY: "Search results were found, but none satisfied the max price.",
  PROVIDER_ERROR: "The flight provider did not return a usable search result.",
  NOT_READY: "The requirement is not search-ready yet.",
};

function formatSelectedPrice(published: PublicPublishedRecommendation): string {
  const basePrice = `${published.selected_price_currency} ${published.selected_price_amount}`;
  return published.selected_price_semantics === "LOWER_BOUND" ? `${basePrice} 起` : basePrice;
}

function toRequest(form: FormState): StructuredRequirementRequest {
  return {
    origin: form.origin,
    destination: form.destination,
    departure_date: form.departureDate === "" ? undefined : form.departureDate,
    max_price_cny: form.maxPriceCny === "" ? undefined : Number(form.maxPriceCny),
    lower_price_preferred: form.lowerPricePreferred,
  };
}
