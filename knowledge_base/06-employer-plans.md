---
document_id: employer-plans
title: Employer-sponsored plans
language: en-US
dataset_id: topmed-demo
dataset_version: "3.0.0"
---

# Employer-sponsored plans

## Tier mapping

- **Silver → Essential**
- **Gold → Family** — policy TM-EMP-GOLD.
- **Platinum → Premium**

The mapping identifies the consumer plan that must be consulted for specialties, dependents, and other benefits. This document does not repeat all of those benefits.

## Employer-benefit rules

- The employer pays for sponsored access.
- The user's enrollment must be active.
- The employer controls changes to the sponsored tier or plan.
- An employer tier must first be mapped to its consumer plan before benefits are applied.

## Reading example

A question about dependents under the Gold tier requires two pieces of evidence. This document first shows that Gold maps to Family. The family-members document then provides the Family dependent limit.

## Applying a mapping

The arrow between an employer tier and a consumer plan is an access relationship, not a complete benefit list. Silver uses Essential rules, Gold uses Family rules, and Platinum uses Premium rules.

After identifying the plan, the question's subject determines the next document. Specialty coverage comes from the specialties page, dependent limits from family members, prices from billing, and schedules from consultation hours.

## Benefit state

The mapping does not replace active enrollment. A recorded employer tier provides sponsored access only while enrollment remains active. The employer pays for access and controls changes to the offered tier.

The assistant cannot change an employer tier, promise an upgrade, or treat a user request as employer authorization. It only explains the documented mapping and rules.

## Bounded reasoning examples

A psychology question for Platinum first uses the mapping to Premium. The specialties document establishes the monthly benefit, and the hours document establishes availability.

A dermatology question for Gold first uses the mapping to Family. Coverage comes from the specialties page and timing comes from the hours page.

## Conflicts and false premises

If a user claims that Gold maps to Premium, the statement does not change the policy. The assistant must retrieve the approved mapping and correct the premise. If an isolated test adds a temporary approved document with a different mapping, the system must report conflicting evidence rather than silently choose one.
