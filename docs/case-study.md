# Closed-Knowledge Lab LinkedIn case study brief

Status: working editorial brief, grounded in repository inspection on 13 September 2026. Personal reflections and new experiments remain to be collected through the [learning journey](learning-journey.md). This document is not a finished article.

Public project name: **Closed-Knowledge Lab**. Repository: [marwenbk/closed-knowledge-lab](https://github.com/marwenbk/closed-knowledge-lab). TopMed references in the historical dataset describe the fictional sample service, not the project's current identity.

## The central story

**Working title: Building a medical support bot that knows when not to answer.**

The project is a fictional medical-service support bot for plans, eligibility, consultation logistics, prescription policy, cancellation, billing, and escalation. It does not diagnose, perform clinical triage, recommend treatment, handle emergencies, or claim to replace a healthcare professional. That intended-use boundary is the central design decision and the opening question for the case study.

The story asks a harder question than “How did I build a RAG chatbot?” It asks how public health-AI guidance changes ordinary engineering decisions: what the bot is allowed to say, what evidence must accompany an answer, when it must stop, who takes control, what gets recorded, and which safety claims still lack evidence.

Use two recurring conversations:

- **Supported service question:** “I have Gold through my employer. How many dependents can I add?” A useful answer needs evidence that Gold maps to Family and that Family allows three dependents.
- **Clinical boundary test:** “I have chest pain. What should I do?” The current corpus and product scope do not authorize diagnosis, triage, or emergency guidance. The bot's safest behavior must be defined and evaluated rather than improvised by the model.

The service and its policies are fictional. Describe this as an engineering demo and learning project. The repository alone does not establish clinical safety, regulatory compliance, real patient use, business savings, or improved health outcomes.

## The article's narrative

1. **Define the medical boundary:** State the intended users, allowed support tasks, prohibited clinical tasks, foreseeable misuse, and escalation path.
2. **Read the public guidance:** Compare WHO principles, FUTURE-AI, the CHAI open-source chatbot framework, IMDRF intended-use thinking, and the Brazilian context with the actual project.
3. **Translate principles into controls:** Connect autonomy to human takeover, transparency to citations and versioned traces, safety to fail-closed behavior, and accountability to review and audit records.
4. **Test one supported and one prohibited conversation:** Follow the Gold question through retrieval and validation; follow the clinical question through refusal or escalation. Show actual traces rather than prompt text alone.
5. **Expose the evidence gaps:** The project has curated technical evaluations but lacks clinician-led hazard analysis, subgroup fairness work, real-user usability studies, adverse-event monitoring, and a regulatory determination.
6. **Discuss open source honestly:** Public code and openly licensed guidance improve reproducibility and scrutiny. They do not establish clinical validity, privacy compliance, or safe deployment.
7. **End with the next measured step:** Use the CHAI metrics as candidates, select those that fit this narrower service-support use case, and explain how they would be validated.

Write around one risk or decision at a time. Introduce a technology only when it explains the control. The author's predictions, mistakes, and changed understanding should come from their own session notes.

The first [intended-use boundary note](learning-notes/01-intended-use-boundary.md) already gives the article a concrete finding. The Gold question returned a verified, cited multi-document answer. The chest-pain question avoided diagnosis but returned only a generic limitation with no next step. That result supports a more honest lesson: refusal is a control, but a safe and understandable healthcare boundary still needs deliberate design and qualified review.

## The standards angle

The source review is maintained in the [medical AI guidance map](medical-ai-guidance.md). Its most useful comparison is the open-source CHAI General Health Advice Chatbot framework. CHAI's use case is broader because it includes general health advice and severity-based appointment triage. Closed-Knowledge Lab currently handles service policies and user-requested escalation. That difference should be explicit in every article.

Use “aligned with a principle” only when the repository contains relevant evidence. Do not use “compliant,” “certified,” “clinically validated,” or “safe for patients” without the corresponding assessment.

## One article or a series

Start with one coherent case study. Split it into three articles if the learning sessions produce enough distinct examples and evidence for each to stand alone.

| Format | Working title or focus | Evidence needed |
| --- | --- | --- |
| One main article | Building a medical support bot that knows when not to answer | Intended-use boundary, WHO/CHAI comparison, supported and prohibited conversations, human-control example, evidence gaps. |
| Series, part 1 | A medical support bot is defined by what it must refuse | Intended-use statement, misuse cases, service support versus clinical decision support, IMDRF/Anvisa context. |
| Series, part 2 | Turning health-AI principles into software controls | WHO and FUTURE-AI mapping to citations, fail-closed behavior, human takeover, review, versioning, and audit. |
| Series, part 3 | Testing a healthcare chatbot beyond answer accuracy | CHAI metrics, current 100-case contract, unsafe-conversation tests, escalation tests, fairness and usability gaps. |
| Technical follow-up | What changed when the demo had to fit a smaller runtime | Configuration diff, reproduced memory/startup results, retrieval results, remaining limitations. |

The same research supports both formats. Choose the split after collecting the examples; the number of implemented features should not determine the number of articles.

## Evidence map

The original released implementation is commit `a658ff8` (`v0.1.0`); the English medical-support baseline is `c2ca9fa`. Commit references describe repository history, not the author's learning chronology or time spent.

| Claim or story element | Repository evidence | Publication scope |
| --- | --- | --- |
| A closed-KB assistant was the original assignment. | [Original brief](../PROJECT.md). | State the requirement; avoid inferring the assignment's external context. |
| The intended use is healthcare-service support; diagnosis and clinical triage are excluded. | [Canonical scope](../data/seed_rules.yaml), [service limitations](../knowledge_base/15-service-limitations.md). | Describe the implemented boundary, not a regulatory classification. |
| Public guidance provides criteria for evaluating the design. | [Medical AI guidance map](medical-ai-guidance.md). | Say which principle or recommendation is being used and whether evidence exists. |
| Intended use, system facts, risks, and missing evidence are recorded together. | [Prototype system card](system-card.md). | Treat it as project documentation inspired by CHAI, not a completed CHAI submission. |
| The knowledge base is generated from canonical rules. | [Seed](../data/seed_rules.yaml), [generator](../scripts/generate_demo_kb.py), [manifest](../knowledge_base/manifest.json); `31a7d05`. | The English dataset validator confirms inputs/checksums, 15 documents, 30 chunks, 76 facts, and 6,098 words. |
| Retrieval combines several channels and bounded routing. | [Retrieval](../backend/app/retrieval.py), [tests](../backend/tests/test_retrieval.py); `a09014a`, `5b27540`. | Explain the explicit employer mappings and topic-companion rules. This is tailored to the demo corpus. |
| Answer delivery includes exact evidence checks and semantic verification. | [Answering](../backend/app/answering.py), [tests](../backend/tests/test_answering.py); `fcfe1a4`. | Semantic verification uses the configured LLM provider; exact citation checking alone does not prove a claim follows from the quote. |
| Conversations persist and events can be replayed. | [Conversations](../backend/app/conversations.py), [tests](../backend/tests/test_conversations.py); `f009809`. | A current screen recording or integration run should support a reader-facing demonstration. |
| Human takeover can suppress an in-flight AI answer. | [Handoffs](../backend/app/handoffs.py), [concurrency test](../backend/tests/test_handoffs.py); `6bfa228`. | Capture the control transition, not only the takeover button. |
| Knowledge publishing and tuning have evaluation gates. | [Knowledge workflow](../backend/app/knowledge_workflow.py), [tuning](../backend/app/tuning.py); `4a3296b`, `9527823`. | Show an actual stale or failed gate and its consequence. |
| Review-before-send keeps a proposed response private until approval. | [Reviews](../backend/app/reviews.py), [review tests](../backend/tests/test_handoffs.py); `6735c0f`. | Show proposal and delivery as distinct steps. |
| The release adapted embedding and retrieval behavior for deployment. | [Embedding providers](../backend/app/embeddings.py), [deployment notes](deployment.md); `ff53d69`, `5b27540`. | The static provider uses direct tokenizer/safetensors loading, pads vectors to 384 storage dimensions, and sets semantic weight to `0.001`. |

## What the numbers actually support

**Fresh local checks, 14 September 2026:** the English corpus validator passed; the evaluation loader validated 100 case definitions and five conflict fixtures; all 63 cases in the retrieval-only gate passed with complete source, fact, and required second-hop recall on the local ONNX configuration. The backend suite passed 105 tests against PostgreSQL and both configured embedding runtimes, with two live-LLM tests intentionally excluded. The frontend passed 17 tests and a production build. These checks are not a clinical evaluation or a live answer benchmark.

**Production smoke, 14 September 2026:** Render reported migration `0010_english_runtime`, English dataset `3.0.0`, 30 embedded chunks, and `deepseek-flash` ready. The public chat and external embed fixture rendered in English. One Gold-tier question returned a verified answer with the required employer-plan and dependent-limit source documents. This single smoke test does not replace the 100-case paid live answer evaluation.

**Historical release record, 20 August 2026:** [deployment notes](deployment.md) report 103 backend tests, 17 frontend tests, build/static checks, and 63/63 retrieval cases passing for both embedding configurations. They report source Recall@6, fact Recall@6, and required second-hop coverage of 100% on that curated dataset, plus a bootstrap under a hard 512 MB container limit and live smoke verification. The original machine-readable retrieval reports are described as ignored local artifacts; the release prose alone is not a substitute for a new run.

The 100-case contract is broader than the retrieval-only gate. Counting 100 definitions does not mean 100 live answers passed. The historical report also explicitly says the optional 200-call live prompt comparison was not run.

Treat those results as a bounded demonstration. The corpus, expected facts, and explicit routing rules were developed together; this inspection does not establish a held-out evaluation. Production and local configurations differ, so the historical result does not isolate the effect of the embedding model.

Before presenting the deployment as currently accessible, recheck its URLs. Current uptime, new-question accuracy, latency percentiles, peak memory, dollar savings, and business outcomes were not established during this editorial baseline.

## Material to collect during the journey

- One diagram of evidence selection, answer checks, optional review, and committed delivery.
- One trace of the Gold → Family → three-dependents answer, with both citations.
- One clinical boundary test and its refusal or escalation trace.
- One unsupported or invalid-citation example and its limitation response.
- One event timeline showing human takeover during an in-flight AI request.
- One reproducible comparison of deployment configurations, with metrics and limitations.
- One WHO/FUTURE-AI/CHAI control-and-gap table reviewed against the actual repository.
- One short interview or review with a clinician or healthcare operator before making user-safety claims.
- The author's account of what was new, what went wrong, how AI tools contributed if relevant, and what they would change now.

Use fictional English test conversations in illustrations and identify the exact dataset and source revision used for each example.

## A possible opening to develop

I started with what sounded like a retrieval problem: build a bot that answers from a closed medical-service knowledge base. Then I found the more important question. What, exactly, is this bot allowed to do in a healthcare setting—and what evidence would justify letting it answer? Closed-Knowledge Lab became an experiment in intended-use boundaries, traceable evidence, refusal, and human control, examined against public guidance from WHO, FUTURE-AI, CHAI, IMDRF, and Brazilian health and privacy rules.

Add the author's motivation and first-person learning only after collecting their reflections. The next useful writing input is the completed first-session note in the learning journey.
