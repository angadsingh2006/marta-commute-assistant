# Commute Assistant

A serverless tool that watches your actual MARTA commute and texts you
*before* you walk out the door into a 20-minute wait.

**Stack:** Python (FastAPI + AWS Lambda) · MARTA real-time data · DynamoDB · SNS · EventBridge · SAM (IaC)

## The problem

MARTA's own app tells you the next arrival when you check it. It doesn't
tell you *without being asked* that your usual bus is running 15 minutes
behind before you've left your apartment. That gap — passive data vs.
proactive alerting — is what this project closes.

## Architecture

```
                    ┌─────────────────────────┐
                    │   EventBridge Schedule    │
                    │  (commute windows only:   │
                    │   7-10am & 4-7pm weekdays)│
                    └────────────┬──────────────┘
                                 ▼
                     ┌───────────────────────┐        ┌─────────────┐
                     │ CheckCommuteFunction   │──────▶ │ MARTA APIs  │
                     │ (Lambda, scheduled)    │        │ rail + bus  │
                     └───────────┬────────────┘        └─────────────┘
                                 │
              ┌──────────────────┼───────────────────┐
              ▼                  ▼                    ▼
    DynamoDB: routes    DynamoDB: arrival_log   DynamoDB: alert_state
   (which stop/line,   (time-series log of      (cooldown marker —
    threshold config)   every check, for         stops duplicate
                         reliability stats)        alerts mid-delay)
                                 │
                                 ▼ (only if delayed & not already alerted)
                          SNS Topic ──▶ email (or SMS) to you

                                 │
                                 ▼ (both legs checked, then evaluated as a trip)
                    DynamoDB: trips (ordered leg pair + transfer buffer)
                                 │
                                 ▼ (only if connection at risk & not already alerted)
                          SNS Topic ──▶ email (or SMS) to you

Separately, on demand:
Client ──▶ API Gateway ──▶ StatusFunction (Lambda) ──▶ routes + trips + arrival_log tables
      "what's my commute look like right now / is this trip's connection at risk / how reliable is this route"
```

Rail (station + line) and bus (route number + stop) are both first-class
`mode`s on a route — you're not choosing one or the other. The interesting
part is what happens when you watch both **and** chain them: a `trips`
entry says "leg 1 is `morning-bus-51`, leg 2 is `morning-rail-red`, I need
a 5-minute buffer to walk between them," and the scheduled check reasons
about the connection as a whole, not just each leg in isolation.

### Design decisions worth knowing

| Decision | Why |
|---|---|
| **Two separate Lambda functions**, not one | The scheduled checker's job is "detect and alert"; the on-demand status endpoint's job is "answer a question right now." Merging them means every manual status check would also re-run alert dedup logic it doesn't need — separating them keeps each function's responsibility (and IAM permissions) narrow. |
| **Scheduled only during commute windows** (`cron(0/5 12-14 ? * MON-FRI *)` etc.), not 24/7 | Polling MARTA around the clock burns Lambda invocations and rail API-key quota for data nobody's watching at 2am. Two narrow windows cut invocation count by roughly 80% versus always-on polling. |
| **Four small DynamoDB tables**, each for a different access pattern | `routes` (tiny, read-often config) and `trips` (leg pairs + transfer buffer) are both pure key-value lookups but different *shapes* of config, so they stay separate rather than overloading one item schema; `arrival_log` (append-only time series) needs a composite key queried and sorted by time; `alert_state` exists only to make an atomic conditional-write dedup check possible. |
| **Trip-level connection risk is its own check, not "either leg crossed its threshold"** | A 5-minute bus delay is a non-event on its own; the same delay is a missed connection if your transfer buffer is 4 minutes. `_check_trips()` compares `leg1_wait + buffer` against `leg2_wait` directly, so it catches connection risk a per-route threshold would miss (leg 1 slightly late, leg 2 right on time — individually fine, together a missed train) and avoids false alarms the reverse way (leg 1 badly delayed but leg 2 is also running late, so the connection's actually fine). |
| **Trip alerts use a separate `trip:<id>` dedup namespace** from route alerts | Route ids and trip ids could otherwise collide in the shared `alert_state` table's key space; prefixing keeps a trip alert and a route alert for a similarly-named id from stepping on each other's cooldown. |
| **Alert dedup via a conditional DynamoDB write, not an in-memory flag** | Lambda instances aren't guaranteed to persist between scheduled invocations, so any in-memory "already alerted" state disappears between runs. A conditional `PutItem` with TTL gives the same guarantee (no duplicate alert within the cooldown) durably and atomically. |
| **MARTA API key read from SSM Parameter Store at runtime**, not injected as a Lambda environment variable | An API key written into a CloudFormation parameter is one accidental `git add` or screenshot away from being leaked. Resolving it at deploy time with `{{resolve:ssm:...}}` fixes that but trades it for a second exposure: CloudFormation writes the resolved plaintext into the function's environment config, where anything holding `lambda:GetFunctionConfiguration` can read it. Fetching it inside `marta_client.py` instead keeps the plaintext in exactly one place — Parameter Store, as an encrypted `SecureString` — and scopes each function's IAM to `ssm:GetParameter` on that one parameter ARN. `ssm-secure` dynamic references would have been simpler, but CloudFormation doesn't support them for Lambda environment variables. The key is cached per container, so it costs one extra API call per cold start. Standard-tier SecureStrings are free (KMS's AWS-managed key included), where Secrets Manager bills $0.40/month per secret for the same job. |
| **The rail key is fetched behind the rail branch**, not at import | A bus-only deploy never calls the rail feed, so it never reads the parameter and doesn't need one to exist. `test_a_bus_route_never_reads_the_rail_key` pins that. |
| **`arrival_log` rows carry a 120-day TTL** | The log is a precise record of where you stand and when, so it shouldn't accumulate forever. The reliability endpoint caps its query window at 90 days, so 120 leaves the retained data strictly larger than anything readable through the API while still bounding it. |
| **Email as the default alert channel**, with SMS as an opt-in | SNS email is free and needs no sender registration; US SMS bills per message and requires a registered origination number. `notify.py` publishes to the topic either way, so the channel is a subscription choice, not a code change. |
| **`WAITING_SECONDS` / GTFS `arrival.time` compared to a per-route threshold**, not a fixed system-wide cutoff | "Delayed" means something different for a bus you catch every 10 minutes versus a train every 20 — the threshold lives per-route in the `routes` table so each one can be tuned independently. |

## Data model

**routes** (config, one item per commute leg you watch)
```json
{
  "route_id": "morning-bus-51",
  "mode": "bus",
  "route_number": "51",
  "stop_id": "901234",
  "label": "51 bus @ Auburn Ave",
  "alert_threshold_seconds": 600
}
```

A rail leg is the same table, keyed on station and line instead of route
number and stop. `direction` is optional but worth setting — a station
serves both directions, and without it the soonest train may be the one
going the wrong way.
```json
{
  "route_id": "morning-rail-red",
  "mode": "rail",
  "station": "FIVE POINTS STATION",
  "line": "RED",
  "direction": "N",
  "label": "Red Line @ Five Points, northbound",
  "alert_threshold_seconds": 900
}
```

**arrival_log** (one row per scheduled check)
```json
{
  "route_id": "morning-bus-51",
  "checked_at": "2026-09-17T12:05:00+00:00",
  "wait_seconds": 720,
  "was_delayed": true,
  "day_of_week": "Thursday",
  "expires_at": 1800014700
}
```

**trips** (optional — chains two legs into one connection to watch)
```json
{
  "trip_id": "bus-to-red-line",
  "leg1_route_id": "morning-bus-51",
  "leg2_route_id": "morning-rail-red",
  "transfer_buffer_seconds": 300,
  "label": "51 bus → Five Points Red Line"
}
```

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/routes/{route_id}/status` | Next arrival right now, and whether it's currently past threshold. |
| `GET` | `/routes/{route_id}/reliability?days=14` | On-time rate broken down by day of week, from logged history. |
| `GET` | `/trips/{trip_id}/status` | Both legs' current wait, and whether the connection is at risk right now. |

Every endpoint requires an `x-api-key` header. An unauthenticated URL would
let anyone burn the owner's MARTA rail quota and read their commute pattern,
so the gateway rejects keyless requests before the Lambda runs — a usage plan
(500/day, 5 req/sec) caps the damage if the key ever leaks.

```bash
curl -H "x-api-key: $COMMUTE_API_KEY" \
  "$API_URL/routes/morning-bus-51/status"
```

## Setup

1. Register for a free MARTA rail API key at MARTA's developer resources
   page. Watching only buses? Skip this and step 2 — the parameter is read
   only when a rail route is checked, so a bus-only deploy doesn't need one.
2. Store it in Parameter Store as a `SecureString` (Standard tier, free —
   encryption uses the AWS-managed KMS key, which costs nothing):
   ```bash
   aws ssm put-parameter --name /commute-assistant/marta-rail-key \
     --value <your-key> --type SecureString
   ```
   The deploy no longer depends on this existing, but a rail route checked
   without it logs `MARTA_RAIL_PARAMETER_NAME is not set` and alerts nothing.
3. Find the stop you actually use — the realtime feeds identify stops only
   by number, so there's a helper for this:
   ```bash
   source .venv/bin/activate                    # see Testing below for first-time setup
   python tools/find_stop.py "auburn ave"       # search stops by name
   python tools/find_stop.py --stop 211751      # routes serving it right now
   ```
   Then add your watched route(s) as items in the `routes` table (console or
   `aws dynamodb put-item`) using the shape above — one item per leg,
   bus or rail.
4. If your commute involves a transfer, add a `trips` item chaining the
   two route_ids together with a transfer buffer.
5. `sam build && sam deploy --guided` — no Docker needed: the Makefile pins
   pip to the Lambda runtime's platform (`manylinux2014_x86_64`, cp313), so
   the compiled protobuf extension in `gtfs-realtime-bindings` is packaged
   for Lambda rather than for your laptop.
6. Subscribe to the alert topic, then confirm the link in your inbox:
   ```bash
   aws sns subscribe --topic-arn <AlertTopicArn output> \
     --protocol email --notification-endpoint you@example.com
   ```
   For texts instead, use `--protocol sms --notification-endpoint +1XXXXXXXXXX`
   — note that US SMS bills per message and needs a registered sender.
7. Read back the API key AWS generated for you during the deploy — it isn't
   something you register for anywhere, and the deploy only prints its id:
   ```bash
   aws apigateway get-api-key --api-key <ApiKeyId output> \
     --include-value --query value --output text
   ```
   Keep it out of the repo, the same as the MARTA key.

## Testing

```bash
python3 -m venv .venv          # first time only
source .venv/bin/activate      # every new terminal
pip install -r requirements.txt
pytest
```

Your shell prompt shows `(.venv)` once the environment is active. Without it,
`python` won't find `requests` or the GTFS bindings.

39 unit tests, no network or AWS required — the I/O boundaries are
injected as fakes:

| File | Covers |
|---|---|
| `test_thresholds.py` | Both alert predicates, including the boundary cases |
| `test_reliability.py` | The on-time rollup and day-of-week grouping |
| `test_check_commute.py` | A failed send releasing its cooldown; bad config skipped, not fatal |
| `test_marta_client.py` | Malformed feed payloads, and failures cached so they aren't retried per route |
| `test_redaction.py` | The API key never reaching logs, raw or URL-encoded |
| `test_db.py` | Paginated scans and queries returning every page |
| `test_status.py` | Misconfiguration surfacing as a clear error, not a crash |
| `test_notify.py` | Alert wording, and fitting a subject to what SNS accepts |

Tests needing live network/DynamoDB/SNS belong in `tests/integration/` so a
bare `pytest` stays fast.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Rail routes alert nothing, logs show `could not read /commute-assistant/...` | The step-2 parameter is missing, or is a `String` the function can't decrypt. Bus routes are unaffected. |
| Stack sits in `REVIEW_IN_PROGRESS` with 0 resources | A changeset failed before creating anything. Fix the cause and re-run `sam deploy`; the empty stack is reused. |
| No alert email ever arrives | The SNS subscription was never confirmed. AWS discards messages until you click the link in the confirmation email. |
| A delayed route sends nothing on a re-run | It's inside its cooldown. Delete that `alert_key` from the `alert_state` table to test again. |
| `ModuleNotFoundError: requests` | The virtualenv isn't active. `source .venv/bin/activate`. |
| `find_stop.py` returns no routes for a valid stop | No buses are due right now. Try during service hours. |
| API returns 403 | Missing or wrong `x-api-key` header. |

## What this costs to run

**$0.00/month, measured.** June, July and August 2026 each billed nothing —
and generated no usage records at all, rather than charges quietly offset by
promotional credits.

A 5-minute poll across two weekday commute windows is roughly 1,570 Lambda
invocations a month, a rounding error against the Always Free allowances:

| Service | Monthly | Why |
|---|---|---|
| Lambda | $0 | Always Free request and compute allowances; this uses a fraction of a percent of each. |
| EventBridge | $0 | Scheduled rules are not billed. |
| DynamoDB | $0 | On-demand billing, so idle tables cost nothing. Log rows expire after 120 days, so storage stays bounded rather than growing forever. |
| CloudWatch Logs | $0 | Retention capped at 14 days, well under the free ingestion allowance. |
| API Gateway | $0 | A usage plan caps the API at 500 requests/day. |
| SNS | $0 | Email notifications are free. SMS is the one thing here that bills per message. |
| Parameter Store | $0 | Standard-tier parameters, including SecureStrings, are free. Secrets Manager would be $0.40/month for the same job. |

Worth doing before you deploy: set an **AWS Budgets** zero-spend alert. It is
free, and it emails you the moment anything bills — which beats trusting a
table like this one.

AWS changed its free tier terms on 2025-07-15 and they vary by account age,
so check [the current free tier page](https://aws.amazon.com/free/) rather
than relying on specific limits quoted anywhere, this README included.

## Possible extensions

- Weather-aware thresholds (loosen the "delayed" bar on rainy days when
  MARTA is reliably slower system-wide)
- A tiny weekly digest email summarizing reliability trends per route/trip
- Real transit-time data (via GTFS static schedules) instead of the current
  simplified `leg1_wait + buffer vs. leg2_wait` heuristic, for trips where
  the ride between legs takes meaningfully long
