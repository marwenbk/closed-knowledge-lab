# Closed-Knowledge Lab LinkedIn case study brief

Status: working editorial brief, grounded in repository inspection on 13 September 2026. Personal reflections and new experiments remain to be collected through the [learning journey](learning-journey.md). This document is not a finished article.

Public project name: **Closed-Knowledge Lab**. Repository: [marwenbk/closed-knowledge-lab](https://github.com/marwenbk/closed-knowledge-lab). TopMed references in the historical dataset describe the fictional sample service, not the project's current identity.

## The central story

**Working title: Building a knowledge-grounded AI assistant: evidence, human control, and deployment tradeoffs.**

The starting requirement was straightforward: build a web chat that answers from a closed knowledge base and admits when the base is insufficient. The project provides a concrete way to explore the decisions behind that requirement: defining known facts, retrieving related evidence, checking citations and claims, preserving conversation ownership, and revisiting the runtime under a memory constraint.

Use the Gold-plan dependent question as the recurring example. A useful answer requires two linked facts: the employer's Gold benefit maps to Família, and Família permits three dependents. The question is small enough to explain to a reader and rich enough to expose retrieval and provenance decisions.

The service and its policies are fictional. Describe this as an engineering demo and learning project. The repository alone does not establish a customer engagement, real patient usage, business savings, or clinical performance.

## The article's narrative

1. **The problem:** “Only use this knowledge base” raises questions about missing, partial, conflicting, and multi-document evidence. Introduce the original brief and the Gold example.
2. **The foundation:** Define a reproducible corpus and expected facts before judging responses. Show one rule becoming a document and an evaluation expectation.
3. **The difficult decision:** Follow a retrieved passage into a proposed answer and explain why citations still require validation and semantic checking.
4. **The product behavior:** Show what happens when a human takes control and when a response needs approval. Connect these behaviors to persisted state.
5. **The constraint:** Explain the production embedding change and altered retrieval weighting. Include measurements only with their exact configuration and date.
6. **The result and remaining questions:** Separate code behavior, deterministic tests, curated retrieval results, and live observations. End with the next experiment the author wants to run.

Write around one question and one observable decision at a time. Introduce a technology when it explains that decision. The author's predictions, mistakes, and changed understanding should come from their own session notes.

## One article or a series

Start with one coherent case study. Split it into three articles if the learning sessions produce enough distinct examples and evidence for each to stand alone.

| Format | Working title or focus | Evidence needed |
| --- | --- | --- |
| One main article | Building a knowledge-grounded AI assistant | Gold question trace, validation example, human-control example, deployment tradeoff, scoped results. |
| Series, part 1 | Defining what an AI assistant is allowed to know | Rule-to-document-to-evaluation chain, intentional gap, multi-document retrieval trace. |
| Series, part 2 | From a cited draft to a delivered answer | Failed citation example, semantic check, review queue, in-flight takeover behavior. |
| Series, part 3 | What changed when the demo had to fit a smaller runtime | Configuration diff, reproduced memory/startup results, retrieval results, remaining limitations. |

The same research supports both formats. Choose the split after collecting the examples; the number of implemented features should not determine the number of articles.

## Evidence map

The baseline is commit `a658ff8` (`v0.1.0`). Commit references below describe repository history, not the author's learning chronology or time spent.

| Claim or story element | Repository evidence | Publication scope |
| --- | --- | --- |
| A closed-KB assistant was the original assignment. | [Original brief](../PROJECT.md). | State the requirement; avoid inferring the assignment's external context. |
| The knowledge base is generated from canonical rules. | [Seed](../data/seed_rules.yaml), [generator](../scripts/generate_demo_kb.py), [manifest](../knowledge_base/manifest.json); `31a7d05`. | Current validator confirms inputs/checksums, 15 documents, 76 facts, and 6,718 words. |
| Retrieval combines several channels and bounded routing. | [Retrieval](../backend/app/retrieval.py), [tests](../backend/tests/test_retrieval.py); `a09014a`, `5b27540`. | Explain the explicit employer mappings and topic-companion rules. This is tailored to the demo corpus. |
| Answer delivery includes exact evidence checks and semantic verification. | [Answering](../backend/app/answering.py), [tests](../backend/tests/test_answering.py); `fcfe1a4`. | Semantic verification uses the configured LLM provider; exact citation checking alone does not prove a claim follows from the quote. |
| Conversations persist and events can be replayed. | [Conversations](../backend/app/conversations.py), [tests](../backend/tests/test_conversations.py); `f009809`. | A current screen recording or integration run should support a reader-facing demonstration. |
| Human takeover can suppress an in-flight AI answer. | [Handoffs](../backend/app/handoffs.py), [concurrency test](../backend/tests/test_handoffs.py); `6bfa228`. | Capture the control transition, not only the takeover button. |
| Knowledge publishing and tuning have evaluation gates. | [Knowledge workflow](../backend/app/knowledge_workflow.py), [tuning](../backend/app/tuning.py); `4a3296b`, `9527823`. | Show an actual stale or failed gate and its consequence. |
| Review-before-send keeps a proposed response private until approval. | [Reviews](../backend/app/reviews.py), [review tests](../backend/tests/test_handoffs.py); `6735c0f`. | Show proposal and delivery as distinct steps. |
| The release adapted embedding and retrieval behavior for deployment. | [Embedding providers](../backend/app/embeddings.py), [deployment notes](deployment.md); `ff53d69`, `5b27540`. | The static provider uses direct tokenizer/safetensors loading, pads vectors to 384 storage dimensions, and sets semantic weight to `0.001`. |

## What the numbers actually support

**Fresh local checks, 13 September 2026:** the corpus validator passed; the evaluation loader validated 100 case definitions and five conflict fixtures; 63 cases are eligible for the retrieval-only gate. The focused answering/retrieval unit suite passed 27 tests. These use test doubles and are not a fresh measured retrieval score or live answer benchmark.

**Historical release record, 20 August 2026:** [deployment notes](deployment.md) report 103 backend tests, 17 frontend tests, build/static checks, and 63/63 retrieval cases passing for both embedding configurations. They report source Recall@6, fact Recall@6, and required second-hop coverage of 100% on that curated dataset, plus a bootstrap under a hard 512 MB container limit and live smoke verification. The original machine-readable retrieval reports are described as ignored local artifacts; the release prose alone is not a substitute for a new run.

The 100-case contract is broader than the retrieval-only gate. Counting 100 definitions does not mean 100 live answers passed. The historical report also explicitly says the optional 200-call live prompt comparison was not run.

Treat those results as a bounded demonstration. The corpus, expected facts, and explicit routing rules were developed together; this inspection does not establish a held-out evaluation. Production and local configurations differ, so the historical result does not isolate the effect of the embedding model.

Before presenting the deployment as currently accessible, recheck its URLs. Current uptime, new-question accuracy, latency percentiles, peak memory, dollar savings, and business outcomes were not established during this editorial baseline.

## Material to collect during the journey

- One diagram of evidence selection, answer checks, optional review, and committed delivery.
- One trace of the Gold → Família → three-dependents answer, with both citations.
- One unsupported or invalid-citation example and its limitation response.
- One event timeline showing human takeover during an in-flight AI request.
- One reproducible comparison of deployment configurations, with metrics and limitations.
- The author's account of what was new, what went wrong, how AI tools contributed if relevant, and what they would change now.

Use fictional test conversations in illustrations. Keep original Portuguese questions beside their English translations so the article remains readable without misrepresenting the tested inputs.

## A possible opening to develop

The assignment was to build a chat application that answered only from a closed knowledge base. Closed-Knowledge Lab uses fictional Portuguese service policies to explore that requirement. Consider one question: “I have Gold through my employer. How many dependents can I add?” The answer requires connecting an employer benefit to a consumer plan, retrieving the plan's limit, and preserving evidence for both steps. Following that small question through the application reveals the decisions this case study will examine.

Add the author's motivation and first-person learning only after collecting their reflections. The next useful writing input is the completed first-session note in the learning journey.
