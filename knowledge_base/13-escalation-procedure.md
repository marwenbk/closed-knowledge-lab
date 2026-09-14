---
document_id: escalation-procedure
title: Escalation procedure
language: en-US
dataset_id: topmed-demo
dataset_version: "3.0.0"
---

# Escalation procedure

## Levels

1. **Level 1:** automated or first-line support.
2. **Level 2:** human support agent.
3. **Level 3:** operations supervisor.

## When to escalate

- Identity cannot be verified.
- A successful payment did not restore access.
- Refund evidence conflicts with system records.
- Account ownership is disputed.
- The knowledge base does not define a requested exception.
- Approved documents contain conflicting information.

## Conduct

Escalation routes a case to the appropriate operational level. It does not automatically create an exception, change a policy, or authorize the assistant to choose silently between conflicting evidence.

When the knowledge base defines no exception, the case may receive human review, but the response must still acknowledge that the special rule is undocumented.

## Progression between levels

The first level covers automation and first-line support. When it cannot resolve a condition that requires human judgment, the case moves to a support agent. Situations requiring operational supervision reach the third level.

The handoff must preserve the original reason, available evidence, and related policy. It must not silently change facts or erase a conflict.

## Identity and ownership

If identity cannot be confirmed, ordinary support must not assume who is requesting the action. A dispute about who controls an account also requires review. These cases are different from a simple correction to basic profile data.

## Payment and refund

When a successful payment does not restore access, the billing rule says access should return and the escalation procedure handles the operational failure.

When refund evidence differs from system records, the assistant cannot decide which source is correct. It must record the discrepancy and hand the case to a person.

## Missing and conflicting rules

A request for an undefined condition does not authorize a new policy. A person may review the request, but the knowledge-base response remains limited to the documented standard.

When two approved documents contain incompatible values, the problem is a conflict rather than a simple absence. The expected state is conflicting evidence, and the system must reveal the conflict without selecting a convenient rule.

## Handoff information

A useful handoff identifies the account or request, the reason, the consulted policy, and the unresolved point. It must not include secrets, unnecessary information, or unsupported conclusions.

The procedure organizes human review. It does not replace authentication, execute account changes, guarantee a special exception, or make the recipient clinically qualified.
