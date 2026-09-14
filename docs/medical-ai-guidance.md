# Medical AI guidance map

This source map supports the Closed-Knowledge Lab learning journey and LinkedIn case study. It was checked against primary sources on 14 September 2026.

The current system is a fictional **healthcare-service support bot**. It answers questions about plans, eligibility, consultation logistics, prescriptions as a service policy, cancellation, billing, and support. Its declared scope excludes diagnosis, clinical triage, emergency care, and real patient data. Calling it a medical support bot describes its setting; it does not make it a clinician or establish that it is medical-device software.

This document records engineering interpretations for the case study. It is not a certification, clinical validation, or regulatory classification.

## The closest open-source framework

The closest public source found is the Coalition for Health AI (CHAI) [Responsible AI Content](https://github.com/coalition-for-health-ai/responsible-ai-content), released under CC BY 4.0. Its [General Health Advice Chatbot framework](https://rai-content.chai.org/en/latest/general-health-advice-chatbot/index.html) covers usefulness, usability and efficacy, fairness and bias management, safety and reliability, and transparency. The source reviewed for this case study is pinned at commit [`4a946d1`](https://github.com/coalition-for-health-ai/responsible-ai-content/tree/4a946d1a5621751238d543f62f6f4e4b1732ce9f/responsible-ai-content/general-health-advice-chatbot). The project's first transparency artifact is a compact [prototype system card](system-card.md) inspired by CHAI's open Applied Model Card.

CHAI's use case is broader than this project. It permits general non-clinical health advice and appointment triage based on severity. Closed-Knowledge Lab currently provides service-policy support and user-requested human escalation. We can borrow relevant evaluation questions without claiming that the project implements or conforms to the whole CHAI framework.

Useful CHAI measures for the next evaluation phase include:

- alignment with reference answers and structured human usefulness review;
- crash, malformed-response, failure-to-progress, and abandonment rates;
- low-confidence trigger and successful escalation rates;
- response quality and intent performance across demographic and language subgroups;
- unsafe-conversation and self-reported adverse-experience rates;
- an Applied Model Card or equivalent system disclosure.

CHAI describes this material as living, incomplete in places, and open to contributions. Its example benchmarks should be treated as starting hypotheses that require justification for this use case, not universal safety thresholds.

## Public health-AI guidance

### WHO ethics and governance

The World Health Organization's [Ethics and governance of artificial intelligence for health](https://www.who.int/publications/i/item/9789240029200) sets six principles: protecting autonomy; promoting well-being, safety, and the public interest; transparency and intelligibility; responsibility and accountability; inclusiveness and equity; and responsive, sustainable AI.

The project's current design can be compared with those principles:

| WHO principle | Existing project evidence | Gap to investigate |
| --- | --- | --- |
| Human autonomy | A user can request a person; human control suppresses AI delivery; review-before-send is supported. | No study yet shows that users understand the AI's role, limitations, or handoff state. |
| Well-being and safety | The assistant has a closed evidence boundary, rejects unsupported answers, and excludes diagnosis and triage by design. | No clinician-led hazard analysis, adverse-event process, or real-world safety evaluation has been completed. |
| Transparency | Delivered AI answers include exact citations; versions and RAG traces are retained; a prototype system card records intended use and limitations. | The card still needs external review, a public correction process, and a plain-language in-product explanation of model involvement. |
| Accountability | State changes are audited and administrative actions use role checks. | The demo has no named accountable organization, incident owner, complaint process, or redress procedure. |
| Inclusiveness and equity | The corpus and evaluation set use Brazilian Portuguese and include paraphrases and adversarial inputs. | There is no demographic, literacy, disability, dialect, or subgroup performance evaluation. |
| Responsive and sustainable AI | Feedback, evaluation-gated changes, rollback, and version history exist. | No long-term monitoring, drift analysis, service-level measurement, or environmental assessment has been run. |

WHO also publishes [guidance on large multi-modal models](https://www.who.int/publications/i/item/9789240084759), which applies as a governance lens because the assistant uses a generative foundation-model API, even though this project currently handles text only. WHO's [Regulatory considerations on artificial intelligence for health](https://www.who.int/publications/i/item/9789240078871) adds a lifecycle view covering documentation and transparency, risk management, intended use and validation, data quality, privacy, and stakeholder collaboration.

### FUTURE-AI

The 2025 [FUTURE-AI international consensus guideline](https://www.bmj.com/content/388/bmj-2024-081554) organizes 30 recommendations around fairness, universality, traceability, usability, robustness, and explainability across design, development, validation, and deployment.

FUTURE-AI is useful for structuring the case study's gap analysis. It does not certify this bot. The strongest current evidence is in traceability; the largest untested areas are fairness, usability with intended users, robustness outside the curated corpus, and post-deployment monitoring.

### ITU-WHO AI for Health

The [ITU-WHO Focus Group on AI for Health](https://www.itu.int/en/ITU-T/focusgroups/ai4h/pages/default.aspx) produced a standardized assessment framework spanning ethics, regulation, technology, and clinical evaluation. Its Open Code Initiative implemented assessment components as a digital public good. The original focus group closed in 2023 and its work continues through the Global Initiative on AI for Health.

This is useful context for a lesson about open engineering: public code and reproducible evaluations improve scrutiny, but clinical evidence and governance still depend on a defined use case and accountable deployment.

## Intended use determines the regulatory question

The IMDRF defines Software as a Medical Device around software **intended for a medical purpose**. Its [risk categorization framework](https://www.imdrf.org/documents/software-medical-device-possible-framework-risk-categorization-and-corresponding-considerations) considers the significance of the information to a healthcare decision and the seriousness of the healthcare situation. Its [clinical evaluation guidance](https://www.imdrf.org/documents/software-medical-device-samd-clinical-evaluation) distinguishes valid clinical association, analytical validation, and clinical validation.

That makes feature wording and actual behavior material. A service-policy assistant, a symptom triage bot, and a diagnostic decision-support system cannot share one safety claim merely because they use the same model or chat interface.

In Brazil, [Anvisa RDC 657/2022](https://anvisalegis.datalegis.net/action/ActionDatalegis.php?acao=abrirTextoAto&cod_menu=1696&cod_modulo=134&numeroAto=00000657&orgao=RDC%2FDC%2FANVISA%2FMS&seqAto=000&tipo=RDC&valorAno=2022) governs regularization of Software as a Medical Device. Article 1 excludes, among other categories, software used exclusively for administrative and financial management in health services and software that processes demographic or epidemiological data without diagnostic or therapeutic purpose. The current demo's declared scope avoids diagnosis and treatment, but that statement alone is not a formal Anvisa classification. Any move into symptom assessment, triage, diagnosis, prognosis, treatment recommendations, or patient-specific clinical decisions requires a fresh intended-use and regulatory analysis.

## Privacy in Brazil

The Brazilian data-protection authority's [LGPD FAQ](https://www.gov.br/anpd/pt-br/acesso-a-informacao/perguntas-frequentes) identifies health, genetic, and biometric data linked to a person as sensitive personal data. The current demo uses fictional content and warns users not to enter real personal, financial, or medical information.

That is a useful prototype boundary, not a production privacy program. A real deployment would need, at minimum, a documented purpose and legal basis, data minimization, retention and deletion rules, access controls, processor and international-transfer analysis, incident handling, and a review of whether a data-protection impact report is appropriate.

## Formal standards to investigate if the intended use expands

These standards are relevant research leads, but their full texts are generally licensed rather than open source. The project does not currently claim conformance:

- [ISO 14971:2019](https://www.iso.org/standard/72704.html): medical-device risk management across the lifecycle;
- [IEC 62304:2006 with Amendment 1:2015](https://webstore.iec.ch/en/publication/22790): medical-device software lifecycle processes;
- [ISO/IEC 42001:2023](https://www.iso.org/standard/42001): organizational AI management systems;
- [IEC 81001-5-1:2021](https://webstore.iec.ch/en/publication/63293): security activities in the health-software lifecycle.

If the project becomes clinical decision support or is studied with patients or clinicians, select reporting guidance by study type:

- [DECIDE-AI](https://www.equator-network.org/reporting-guidelines/reporting-guideline-for-the-early-stage-clinical-evaluation-of-decision-support-systems-driven-by-artificial-intelligence-decide-ai/) for early live clinical evaluation of AI decision support;
- [TRIPOD+AI](https://www.bmj.com/content/385/bmj-2023-078378) for clinical prediction-model studies;
- [CONSORT-AI](https://www.equator-network.org/reporting-guidelines/consort-artificial-intelligence/) for reports and [SPIRIT-AI](https://www.equator-network.org/reporting-guidelines/spirit-artificial-intelligence/) for protocols of trials involving an AI component;
- [STARD-AI](https://www.equator-network.org/reporting-guidelines/the-stard-ai-reporting-guideline-for-diagnostic-accuracy-studies-using-artificial-intelligence/) for diagnostic-accuracy studies.

These are reporting guidelines for particular study designs. They should not be presented as development checklists or proof that a system is clinically safe.

## Claims the case study can make today

- The project demonstrates a deliberately bounded healthcare-service support use case with fictional data.
- The implementation includes evidence retrieval, exact citation checks, answerability classification, a second model-based support check, human takeover, review-before-send, versioning, and audit records.
- The repository contains deterministic tests and a curated evaluation contract.
- Those controls align with parts of public health-AI guidance, especially scope definition, traceability, fallback, and human oversight.

## Claims that require more work

- compliance or certification against WHO, CHAI, FUTURE-AI, ISO, IEC, IMDRF, Anvisa, or LGPD;
- clinical safety, efficacy, diagnostic accuracy, or improved patient outcomes;
- fairness across populations or accessibility for people with disabilities;
- production privacy, security, uptime, incident response, or successful escalation rates;
- generalization beyond the fictional corpus and curated evaluation cases.

The article becomes credible by showing this difference clearly: the project is an engineering case study about controls and evidence, followed by a measured gap analysis against public guidance.
