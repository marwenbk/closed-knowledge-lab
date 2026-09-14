---
document_id: cancellation
title: Subscription cancellation
language: en-US
dataset_id: topmed-demo
dataset_version: "3.0.0"
---

# Subscription cancellation

Canonical policy: **TM-CAN-003**.

## Request

- Cancellation may be requested at any time.
- Accepted channels are the account portal and customer support.
- After cancellation is completed, future renewal is disabled.

## Access period

- Access remains active until the end of the already-paid billing period.
- Cancellation does not end that paid period early.

## Cancellation is not a refund

- Canceling does not produce an automatic refund.
- Refund eligibility is controlled by a separate policy.
- An answer about returning funds must consult the canonical refund document rather than infer a refund from cancellation.

A user can stop renewal at any time, but that does not mean the amount already paid will be returned automatically.

## Effect on the current cycle

Cancellation stops the next renewal, not access during the paid period. After a completed request, the user can continue using the service until that period ends. An answer must not announce immediate termination when the rule preserves current access.

The permitted channels are the account portal and customer support. Explaining those channels does not mean the assistant executes the request; it only describes the documented process.

## Relationship to other policies

A scheduled consultation has its own cancellation rule before the appointment start time. That process is separate from the subscription cancellation described here.

Returning a payment depends on the canonical refund policy. The right to request cancellation at any time does not remove that policy's time window or eligibility conditions.

## Response examples

If a user asks only “Can I cancel today?”, the knowledge base supports an affirmative answer and the accepted channels. If the user also asks “Will I get everything back?”, the response must combine this policy with the refund policy and stay within its documented conditions.

If a user says cancellation always returns the payment, the assistant must correct the premise. The policy says there is no automatic refund.

If the request says “cancel it” without identifying a subscription or consultation, the subject is ambiguous. One short clarification prevents applying the wrong policy.

## Operational limits

This document does not authorize the assistant to modify an account, confirm that a request completed, or invent a confirmation number. Actual subscription state must come from an operational system; the knowledge base supplies the rule but cannot prove that renewal for a specific account is disabled.
