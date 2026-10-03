# TypeSafe AI Jev Decision Layer — Architecture and Integration Guide

Status: design + disabled-by-default integration scaffold  
Owner: Creative Asset Manager / RRUGC / Pinterest Scout  
Last reviewed: 2026-10-03

## 1. Purpose

Jev is an optional decision layer for fast typed decisions over structured state.
It is not a replacement for Gemini Vision, SigLIP2, dHash, image generation, or
video analysis.

The target architecture is:

```text
raw image/video
    |
    +--> deterministic VPS checks
    |      resolution / size / duplicate / policy rules
    |
    +--> SigLIP2 / OpenVINO
    |      visual similarity / positive-negative seed signals
    |
    +--> Gemini
    |      perception and semantic understanding when raw pixels are required
    |
    v
structured state
    |
    v
Jev (optional)
    |
    +--> typed choice / yes-no probability / score
    |
    v
application code
    retry / route / broaden search / use Gemini / manual review / continue
```

Hard invariant:

> Disabling or losing Jev must never prevent an existing pipeline from
> completing.

## 2. Why Jev fits this project

TypeSafe Jev accepts one structured/text `state` and one or more typed
questions. The public API currently exposes three question families:

- `noul`: probability of yes/true.
- `choice`: select one item from predefined choices, with probabilities.
- `score`: score against ordered criteria, with confidence/probabilities.

The response includes the resolved model plus input/output token usage. The
public model alias is currently `jev-latest`.

This maps well to decisions already present in Creative Asset Manager:

1. Pinterest Scout: select the next search context/query family.
2. RRUGC: decide whether structured evidence warrants Gemini escalation.
3. Inventory: route READY / REVIEW / RECONCILE / BLOCKED.
4. Provider/routing orchestration: choose among predefined actions after all
   hard safety/business rules have already executed.

It does not fit:

- raw image interpretation,
- raw video interpretation,
- image generation,
- embeddings,
- deterministic retry timing,
- exact duplicate detection,
- permission/security decisions that already have explicit rules.

## 3. Current project mapping

### 3.1 Pinterest Scout

Relevant modules:

- `apps/api/app/modules/realistic_review_ugc/keyword_strategy.py`
- `apps/api/app/modules/realistic_review_ugc/product_context.py`
- `apps/api/app/modules/realistic_review_ugc/scout_automation.py`
- `apps/rrugc_scout/scout.py`

Current behavior already has deterministic query generation, feedback weighting,
product-context search, same-embroidery intent, and `hand_holding_hat`.

Jev should not replace query generation. It should select among the query
families that the server already generated.

Recommended first production use:

```text
existing query candidates
        |
        v
search-round metrics
        |
        v
Jev Choice
        |
        +--> same_embroidery
        +--> hand_holding_hat
        +--> direct
        +--> adjacent
        +--> generic
        +--> broaden
        |
        v
existing Scout executes selected query
```

### 3.2 RRUGC candidate analysis

Relevant modules:

- `apps/api/app/modules/realistic_review_ugc/handler.py`
- `apps/api/app/modules/realistic_review_ugc/analysis.py`
- `apps/api/app/modules/realistic_review_ugc/seed_similarity.py`
- `apps/api/app/modules/realistic_review_ugc/visual_dedupe.py`

The current order should remain:

```text
download
  -> resolution/size
  -> dHash duplicate
  -> SigLIP2 local confidence gates
  -> Jev routing (future; structured signals only)
  -> Gemini if uncertain / perception still required
  -> final deterministic policy
```

Jev must never approve/reject from raw pixels because it is not the vision
provider in this architecture.

### 3.3 Inventory

Good future targets:

- readiness classification,
- reconciliation routing,
- anomaly severity,
- whether a Gemini escalation is justified.

Do not use Jev to bypass deterministic workbook validation or tenant policy.

## 4. TypeSafe API contract used by the scaffold

Base URL:

```text
https://api.typesafe.ai
```

Model discovery:

```http
GET /v1/models
Authorization: Bearer <API_KEY>
```

Decision request:

```http
POST /v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

Example:

```json
{
  "model": "jev-latest",
  "state": {
    "target": 50,
    "approved": 7,
    "found": 61,
    "recent_context_yield": {
      "same_embroidery": 0.05,
      "hand_holding_hat": 0.24
    }
  },
  "questions": {
    "next_context": {
      "type": "choice",
      "instructions": "Choose the next Scout context that is most likely to improve approved reference yield.",
      "criteria": {
        "same_embroidery": "Stay close to the exact design/embroidery.",
        "hand_holding_hat": "Prefer a real hand holding the hat with front embroidery visible.",
        "direct": "Use direct product-context search.",
        "broaden": "Broaden because current context is exhausted."
      }
    },
    "should_broaden": {
      "type": "noul",
      "instructions": "Should the Scout broaden beyond the current context?"
    }
  }
}
```

Example response shape:

```json
{
  "model": "jev-latest",
  "answers": {
    "next_context": {
      "type": "choice",
      "choice": "hand_holding_hat",
      "confidence": 0.93,
      "probabilities": {
        "same_embroidery": 0.03,
        "hand_holding_hat": 0.93,
        "direct": 0.02,
        "broaden": 0.02
      }
    },
    "should_broaden": {
      "type": "noul",
      "noul": 0.18
    }
  },
  "usage": {
    "input_tokens": 720,
    "output_tokens": 10
  }
}
```

## 5. Cost model

Published input price at the time of this document:

```text
$0.042 / 1,000,000 input tokens
```

Published output pricing is currently free.

Cost formula:

```text
cost_usd = input_tokens / 1,000,000 * 0.042
```

Examples:

| Input/request | Cost/request | 10k calls | 100k calls | 1M calls |
|---:|---:|---:|---:|---:|
| 250 | $0.0000105 | $0.105 | $1.05 | $10.50 |
| 500 | $0.0000210 | $0.210 | $2.10 | $21.00 |
| 800 | $0.0000336 | $0.336 | $3.36 | $33.60 |
| 1,000 | $0.0000420 | $0.420 | $4.20 | $42.00 |

For the current RRUGC scale, Scout query decisions are expected to be much
cheaper than candidate-level decisions.

## 6. Latency model

TypeSafe publishes approximately 70–500 ms for Jev calls in its own examples
and notes that its published measurements are generally from the US West Coast.
Production must measure real latency from this VPS instead of assuming 70 ms.

Initial engineering budget:

```text
target p50       < 400 ms
target p95       < 800 ms
client timeout     1500 ms
max retries          1
```

Do not serialize Jev in front of every image unless it replaces a slower
decision. A Jev call that is followed by the exact same Gemini call only adds
latency.

The high-value case is:

```text
Jev/local decision avoids Gemini queue
```

RRUGC currently rate-limits its Gemini lane. Avoiding a Gemini call can save
orders of magnitude more time than the Jev call itself costs.

## 7. Fallback architecture

The integration uses fail-open semantics:

```text
Jev
 |
 +-- success --------------------> use Jev result if rollout mode allows
 |
 +-- cache hit ------------------> use cached result
 |
 +-- timeout/429/5xx ------------> existing deterministic strategy
 |
 +-- invalid response -----------> existing deterministic strategy
 |
 +-- credit/billing blocked -----> existing deterministic strategy
 |
 +-- local budget exceeded ------> existing deterministic strategy
 |
 +-- circuit open ---------------> existing deterministic strategy
```

If the deterministic result is still ambiguous, the existing pipeline may
escalate to Gemini exactly as it does today.

Jev infrastructure errors must not increment the business job attempt counter.

## 8. Circuit breaker

States:

- `closed`: normal Jev calls.
- `open`: provider is temporarily degraded; skip calls.
- `half_open`: cooldown expired; permit a probe.
- `billing_blocked`: credits/billing appear unavailable; probe much less often.

Default scaffold values:

```text
failure threshold       5 consecutive failures
open cooldown           300 seconds
billing recheck         3600 seconds
```

Behavior:

```text
5 repeated transport/5xx failures
    -> OPEN for 5 minutes

cooldown expires
    -> HALF_OPEN probe

probe success
    -> CLOSED

probe failure
    -> OPEN again

credit/billing failure
    -> BILLING_BLOCKED for 1 hour
```

The provider response body is never logged.

## 9. Credit exhaustion and budget guards

TypeSafe uses account credits. The exact HTTP error for every billing state is
provider-controlled, so the adapter treats:

- HTTP 402 as billing blocked,
- HTTP 400/403 with a safe billing/credit/balance marker as billing blocked.

When billing is blocked, later jobs do not repeatedly call TypeSafe. They
immediately fall back until the billing recheck window expires.

Local soft guards are also available:

```env
JEV_DAILY_BUDGET_USD=1
JEV_MONTHLY_BUDGET_USD=10
```

Important limitation of the current scaffold:

- budget counters and cache are in-process,
- therefore they are not a strict global cap across multiple API/workers.

Before `JEV_MODE=active`, migrate budget accounting to the existing durable AI
governance repository if a strict account-wide cap is required.

## 10. Cache

The scaffold hashes:

```text
model + state + questions
```

with SHA-256.

Default TTL:

```text
1800 seconds
```

Only successful Jev provider results are cached.

Recommended production cache semantics later:

- Scout query controller: 5–30 minutes.
- Candidate immutable state: longer TTL.
- Inventory: key by workbook/data revision.
- Include policy version in state so policy changes invalidate old decisions.

## 11. Modes

### shadow

Jev evaluates but does not control the outcome.

Use for the initial rollout.

Persist/compare:

```text
jev_decision
jev_confidence
existing_decision
human_final_label
latency
tokens
estimated_cost
```

### assisted

Jev may influence ranking/prioritization, but deterministic/Gemini paths remain
authoritative for risky decisions.

### active

Jev may drive a predefined branch only when:

1. the question schema is allowlisted,
2. confidence meets the configured threshold,
3. all hard deterministic rules already passed,
4. fallback remains available.

## 12. Recommended Scout state

Keep state compact and structured:

```json
{
  "campaign": {
    "product_type": "embroidered_hat",
    "target": 50,
    "approved": 7,
    "pipeline": 2,
    "found": 61
  },
  "contexts": {
    "same_embroidery": {
      "searched": 4,
      "found": 20,
      "approved": 1,
      "bad": 10,
      "ai": 3,
      "duplicate": 6
    },
    "hand_holding_hat": {
      "searched": 2,
      "found": 13,
      "approved": 5,
      "bad": 5,
      "ai": 1,
      "duplicate": 2
    }
  },
  "available_contexts": [
    "same_embroidery",
    "hand_holding_hat",
    "direct",
    "adjacent",
    "generic",
    "broaden"
  ]
}
```

Do not send:

- raw images,
- OAuth tokens,
- signed URLs,
- Drive credentials,
- source download credentials,
- personal data that is unnecessary for the decision.

## 13. Recommended Scout questions

One request can contain multiple questions.

```json
{
  "next_context": {
    "type": "choice",
    "instructions": "Choose the next context most likely to improve approved-reference yield without drifting away from the product.",
    "criteria": {
      "same_embroidery": "Continue exact-design searching.",
      "hand_holding_hat": "Use hand-held hat composition.",
      "direct": "Use direct product-context queries.",
      "adjacent": "Use adjacent lifestyle context.",
      "generic": "Use generic realistic UGC context.",
      "broaden": "The current search space appears exhausted."
    }
  },
  "current_context_exhausted": {
    "type": "noul",
    "instructions": "Is the current search context unlikely to produce enough additional useful references?"
  },
  "expected_yield": {
    "type": "score",
    "instructions": "Rate expected approved-reference yield for the selected context.",
    "criteria": [
      "Very poor",
      "Poor",
      "Moderate",
      "Good",
      "Very good"
    ]
  }
}
```

## 14. Candidate router — later phase

Do not start with candidate auto-approval.

Safe sequence:

```text
Phase A: shadow only
Phase B: high-confidence obvious-negative routing
Phase C: possibly reduce Gemini calls
Phase D: consider positive routing only after enough human-labelled evidence
```

Example structured state:

```json
{
  "resolution_ok": true,
  "exact_duplicate": false,
  "visual_duplicate": false,
  "siglip_positive": 0.31,
  "siglip_negative": 0.89,
  "negative_seed_count": 5,
  "positive_seed_count": 3,
  "query_context": "hand_holding_hat",
  "query_historical_approval_rate": 0.28,
  "manual_feedback_revision": 42
}
```

Jev can answer:

```json
{
  "needs_gemini": {
    "type": "noul",
    "instructions": "Does this candidate still require Gemini visual-semantic analysis before a safe decision?"
  }
}
```

Never let Jev override a manual `(v)` approval.

## 15. Telemetry

The scaffold records Jev activity into the existing AI metrics surface using:

- provider: `jev`
- mode: `decision`

Metrics:

```text
jev_requests
jev_input_tokens
jev_estimated_cost_micros
latency
```

Outcomes include:

```text
completed
cache_hit
timeout
rate_limit
provider_unavailable
billing_blocked
circuit_open
invalid_response
daily_budget_exceeded
monthly_budget_exceeded
```

Future DB/shadow observations should additionally persist:

```text
decision_name
decision
confidence
probabilities
existing_decision
human_final_decision
fallback_reason
policy_version
state_hash
```

## 16. Configuration

The code scaffold adds:

```env
JEV_ENABLED=false
JEV_API_KEY=
JEV_BASE_URL=https://api.typesafe.ai
JEV_MODEL=jev-latest

JEV_MODE=shadow
JEV_TIMEOUT_SECONDS=1.5
JEV_MAX_RETRIES=1

JEV_CIRCUIT_FAILURE_THRESHOLD=5
JEV_CIRCUIT_OPEN_SECONDS=300
JEV_BILLING_RECHECK_SECONDS=3600

JEV_CACHE_TTL_SECONDS=1800

JEV_DAILY_BUDGET_USD=1
JEV_MONTHLY_BUDGET_USD=10

JEV_INPUT_PRICE_PER_MILLION_USD=0.042
```

Defaults keep Jev completely disabled.

Missing `JEV_API_KEY` also leaves existing pipelines untouched.

## 17. Code scaffold

Current scaffold:

```text
apps/api/app/providers/decision/jev.py
apps/api/app/main.py
apps/api/app/modules/processing/bootstrap.py
apps/api/app/core/config.py
```

Composition:

- the API creates one optional Jev client on `app.state.jev_client`,
- workers expose one optional client as `resources["jev_client"]`,
- both close the HTTP client during graceful shutdown,
- when `JEV_ENABLED=false` or the key is missing, no client is created and no
  TypeSafe request can be made.

Responsibilities:

- authenticated `POST /v1/systemone`,
- response normalization,
- token/cost accounting,
- cache,
- timeout handling,
- bounded retry,
- rate-limit fallback,
- billing/credit fallback,
- circuit breaker,
- local soft budget guard,
- no exception propagation for provider failures.

Composition helper:

```python
client = build_jev_client(settings)
```

Returns `None` when disabled or not configured.

Call shape:

```python
result = client.evaluate(
    state=state,
    questions=questions,
)

if result.ok:
    # shadow: record only
    # assisted/active: evaluate rollout policy and confidence
    ...
else:
    # Never fail the business job because Jev failed.
    return existing_strategy(...)
```

## 18. Rollout plan

### Phase 0 — scaffold

Status after this change:

- config exists,
- provider client exists,
- fallback/circuit/cache/budget behavior is unit-tested,
- feature is disabled,
- no production pipeline calls Jev yet.

### Phase 1 — Scout shadow

Add a Jev call at the search-round decision boundary.

Requirements:

- `JEV_ENABLED=true`
- `JEV_MODE=shadow`
- TypeSafe key installed as a production secret.
- No change to actual query choice.

Collect at least several thousand decisions if possible.

Acceptance:

```text
p95 latency < 800 ms
provider success > 99%
no Scout completion regression
no extra Gemini calls
no increase in provider-related job failures
```

### Phase 2 — Scout assisted

Use Jev to reorder existing query contexts only.

Do not allow it to invent arbitrary query strings.

Fallback:

```text
Jev failure -> keyword_strategy.py result
```

### Phase 3 — Scout active

Jev may select the next predefined context when confidence is high.

Suggested initial confidence threshold:

```text
choice confidence >= 0.90
```

Lower-confidence results fall back.

### Phase 4 — Candidate shadow

Compare Jev routing against:

- current local gates,
- Gemini,
- human (v)/(x)/(AI).

### Phase 5 — candidate obvious-negative routing

Only after calibration data proves the false-negative rate is acceptable.

## 19. Testing requirements

Unit tests must cover:

- disabled-by-default,
- successful Choice response,
- usage/cost calculation,
- cache hit,
- timeout fallback,
- rate-limit fallback,
- 5xx fallback,
- credit/billing block,
- circuit open,
- invalid payload,
- local budget exceeded.

Integration tests before active rollout:

1. Force every Jev call to timeout.
2. Run Scout to completion.
3. Verify the chosen queries equal the existing strategy.
4. Force billing blocked.
5. Verify only one TypeSafe request is attempted until cooldown.
6. Verify RRUGC business attempts do not increase because Jev failed.
7. Verify no raw image bytes or secrets are present in Jev state.
8. Verify turning `JEV_ENABLED=false` restores exact pre-Jev behavior.

## 20. Security and privacy

TypeSafe receives only the state explicitly sent by this adapter.

Project policy:

- send compact structured state,
- do not send raw assets,
- do not send OAuth/access tokens,
- do not send signed CDN/Drive URLs,
- do not log provider bodies,
- store the API key only as a secret,
- redact user-sensitive data unless strictly necessary.

TypeSafe's current customer agreement says customer data is processed to provide
the service and is not included in a dataset used to train model weights without
customer consent. Re-review legal/security terms before enabling production.

## 21. Operational runbook

### Provider healthy

```text
circuit_state=closed
fallback rate low
p95 latency within target
```

### Provider degraded

```text
circuit_state=open
fallback_reason=timeout/provider_unavailable/rate_limit
pipeline continues
```

No operator action is required unless degradation persists.

### Credits exhausted

```text
circuit_state=billing_blocked
fallback_reason=billing_blocked
pipeline continues
```

Operator action:

1. check TypeSafe account credit,
2. top up/resolve billing if desired,
3. wait for billing recheck or restart after confirmation,
4. verify a probe succeeds.

Do not manually retry every business job.

### Local budget exceeded

```text
fallback_reason=daily_budget_exceeded
or
fallback_reason=monthly_budget_exceeded
```

Pipeline continues using existing logic.

## 22. Production decision criteria

Do not enable `active` merely because Jev is cheaper.

Recommended promotion criteria:

```text
p95 Jev latency                 < 800 ms
provider success                > 99%
high-confidence human agreement > 95%
high-confidence false reject    < 1%
Gemini calls avoided            > 20%
approved-reference yield        not worse
pipeline completion time        improves >= 15%
fallback completion rate        100%
```

## 23. Known limitations

1. The scaffold's cache is process-local.
2. The scaffold's budget guard is process-local.
3. No durable shadow-decision table exists yet.
4. No Jev API key is configured by this change.
5. No Scout/RRUGC production path calls Jev yet.
6. TypeSafe is an external early-access dependency; fallback remains mandatory.
7. Billing HTTP semantics may evolve; the adapter intentionally treats billing
   detection conservatively and never exposes response bodies.

## 24. Recommended next implementation

Next code change should be only:

```text
Scout Query Controller — shadow mode
```

It should:

1. build a compact state from existing campaign/search metrics,
2. call Jev once per search decision point, not once per image,
3. record Jev Choice/confidence/usage/latency,
4. still execute the current `keyword_strategy.py` decision,
5. never delay a Scout job beyond the configured Jev timeout,
6. prove the fallback invariant before assisted/active rollout.

## 25. References

- TypeSafe API docs: https://api.typesafe.ai/docs
- TypeSafe ReDoc: https://api.typesafe.ai/redoc
- TypeSafe OpenAPI: https://api.typesafe.ai/openapi.json
- Jev launch / pricing / benchmark notes:
  https://typesafe.ai/blog/introducing-system-one-models-and-jev
- TypeSafe status: https://status.typesafe.ai/
- TypeSafe Master Customer Agreement: https://typesafe.ai/legal/mca
