# Closed-Knowledge Lab prototype system card

- Version: 0.1
- Reviewed: 14 September 2026
- Release stage: fictional technical demonstration
- Repository owner: `marwenbk`

This transparency record is inspired by the open CHAI [Applied Model Card](https://github.com/coalition-for-health-ai/mc-schema). It is a compact project-specific system card, not a completed CHAI submission, independent assessment, regulatory filing, or certification.

## Intended use and workflow

Closed-Knowledge Lab demonstrates a conversational assistant for a fictional healthcare service. Simulated users can ask about:

- plan prices, benefits, eligibility, and dependents;
- consultation availability and service workflow;
- prescription, cancellation, refund, billing, privacy, and support policies;
- transfer to a human support operator.

The current audience is engineers and evaluators studying the implementation. The system has not been validated for use by patients, clinicians, healthcare organizations, or real customer-support teams.

The response path is:

```text
User question
→ retrieve approved fictional evidence
→ classify answerability
→ generate a structured draft when supported
→ validate exact citation spans
→ run a model-based grounding check
→ optional human review or takeover
→ persist and deliver the final message
```

## Cautioned and out-of-scope uses

The system is not intended to:

- diagnose a condition or interpret symptoms;
- perform clinical or emergency triage;
- recommend medication, treatment, or changes to care;
- replace a clinician, emergency service, or medical-service regulator;
- process real patient, medical, financial, or identity data;
- answer from the internet or the model's general knowledge;
- support a real healthcare decision or establish eligibility for a real service.

The fictional knowledge base states that the service is not an emergency service and that the chatbot does not diagnose or perform clinical triage. A safe, clinically reviewed real-world emergency flow has not been implemented or evaluated.

## AI system facts

| Field | Current implementation |
| --- | --- |
| Generation model | Configured DeepSeek `deepseek-flash`, non-thinking mode, structured JSON output. The provider returned this model ID during readiness validation on 14 September 2026. |
| Retrieval | PostgreSQL full-text search, pgvector, weighted rank fusion, conditional trigram fallback, explicit employer-plan mapping, and one bounded second hop. |
| Embeddings | Local ONNX `intfloat/multilingual-e5-small`; production can use a pinned static distilled model with 384-dimensional storage compatibility. |
| Knowledge source | Deterministically generated fictional English corpus: 15 documents, 30 chunks, and 76 stable fact IDs in dataset `topmed-demo:3.0.0`. |
| User input | Text questions and up to two prior customer messages for reference resolution. Conversation history is not treated as factual evidence. |
| External tools | No browser, search, or URL tool is available to the answer pipeline. |
| Outputs | Supported or partially supported answers with citations; a clarification question; a limitation response; or a conflict response. |
| State | PostgreSQL stores conversations, messages, retrieval traces, events, review state, handoffs, feedback, and audit records. |

Model and deployment configuration can change. Each published evaluation or article must identify the source commit, dataset, prompt, retrieval settings, embedding provider, and generation model used.

## Human oversight

- Users can request a human operator.
- Conversation control is claimed atomically by one operator.
- AI delivery is suppressed while the conversation is under human control, including an answer already in flight.
- Review-before-send can hold verified AI proposals outside the customer view until a reviewer approves or edits them.
- Changes to knowledge, prompts, settings, reviews, and conversation state produce audit records.

These controls show workflow behavior. They do not establish that qualified staff are available, trained, or accountable in a real deployment.

## Evaluation evidence

Fresh checks recorded on 14 September 2026:

- the deterministic corpus validator passed for 15 documents, 30 chunks, 76 facts, and 6,098 words;
- the evaluation contract contains 100 cases and five isolated conflict fixtures;
- the English retrieval-only gate passed all 63 eligible cases with complete source, fact, and required second-hop recall on the local ONNX configuration;
- 104 backend tests passed against PostgreSQL and both configured embedding runtimes, with two live-LLM tests excluded;
- 17 frontend tests and the production frontend build passed.
- one live Gold-tier question returned a verified answer with exact mapping and dependent-limit citations;
- one live chest-pain question returned a generic `NOT_ANSWERABLE` limitation without diagnosis, citations, or an emergency next step.

The 20 August 2026 release record belongs to the retired `2.0.0` dataset and is separate from the current English gate. The English static-runtime result is verified by the complete backend suite.

No clinical study, real-user safety study, subgroup fairness analysis, accessibility study, or independent evaluation has been completed.

## Known risks and limitations

- The answerability and final grounding checks use the same configured model provider class as generation. Agreement between model calls is not independent clinical verification.
- Exact citations prove that quoted text exists in selected evidence; they do not alone prove that the answer follows from it.
- The corpus, explicit routing rules, and evaluation cases were developed together. Reported retrieval performance is not held-out generalization evidence.
- The production static embedding configuration materially reduces semantic-search weight and relies more heavily on lexical and explicit routing channels.
- A user can still enter sensitive or real-world information despite the warning; the prototype does not classify and remove such data before persistence.
- No retention/deletion policy, production privacy impact assessment, penetration test, incident-response process, or adverse-event process has been established.
- Human takeover demonstrates control transfer but does not guarantee a response time or clinically qualified reviewer.
- The free hosting environment can sleep, disconnect streams, or expire its database and is unsuitable for dependable healthcare service.

## Fairness and usability evidence

The current corpus and tests are in English and include paraphrases, typos, ambiguity, prompt injection, user falsehoods, missing information, and conflicts. They do not measure performance by race, ethnicity, sex, gender, age, disability, health literacy, dialect, primary language, socioeconomic status, or intersecting groups.

No representative user panel, clinician review, accessibility audit, comprehension study, satisfaction measure, or abandonment analysis has been completed.

## Security and privacy controls

The prototype includes signed widget sessions bound to allowed origins, explicit CORS and iframe origins, administrative authentication, role checks, CSRF protection, secret separation, append-only audit records, and tests for internal-note privacy. These are implementation controls, not evidence of compliance with LGPD or a healthcare security standard.

Health data linked to a person is sensitive personal data under Brazil's LGPD. The current demo uses fictional content and instructs users not to enter real personal, financial, or medical information.

## Maintenance and transparency

Knowledge, prompt, and retrieval-setting changes are versioned and can be evaluation-gated before activation. Past conversations retain their dataset, model, prompt, settings, evidence, and state-transition references. Feedback and audit views exist in the operations console.

There is no formal public issue-reporting, safety-incident, correction, or recall process. This card should be revised when intended use, workflow, models, evidence, risks, or deployment conditions change.
