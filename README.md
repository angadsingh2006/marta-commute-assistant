# Commute Assistant

A serverless tool that watches your actual MARTA commute and texts you
*before* you walk out the door into a 20-minute wait. Built to solve a real
problem you have, not a hypothetical one — see `internship-search`-grade
side projects for why that distinction matters in interviews.

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
                          SNS Topic ──▶ SMS to your phone

                                 │
                                 ▼ (both legs checked, then evaluated as a trip)
                    DynamoDB: trips (ordered leg pair + transfer buffer)
                                 │
                                 ▼ (only if connection at risk & not already alerted)
                          SNS Topic ──▶ SMS to your phone

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
| **MARTA API key pulled from Secrets Manager**, not a template parameter | An API key in a CloudFormation parameter or a Lambda console env var is one accidental `git add` or screenshot away from being leaked. Secrets Manager keeps it out of both the template and version control. |
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

**arrival_log** (one row per scheduled check)
```json
{
  "route_id": "morning-bus-51",
  "checked_at": "2026-09-17T12:05:00+00:00",
  "wait_seconds": 720,
  "was_delayed": true,
  "day_of_week": "Thursday"
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

## Setup

1. Register for a free MARTA rail API key at MARTA's developer resources
   page (bus data needs no key).
2. Store it: `aws secretsmanager create-secret --name commute-assistant/marta-rail-key --secret-string <your-key>`
3. Add your watched route(s) as items in the `routes` table (console or
   `aws dynamodb put-item`) using the shape above — one item per leg,
   bus or rail.
4. If your commute involves a transfer, add a `trips` item chaining the
   two route_ids together with a transfer buffer.
5. `sam build && sam deploy --guided`
6. Subscribe your phone to the alert topic:
   `aws sns subscribe --topic-arn <AlertTopicArn output> --protocol sms --notification-endpoint +1XXXXXXXXXX`

## Testing

```bash
pip install pytest
pytest
```

Pure threshold logic is unit tested; `marta_client.py` and `db.py` need
live network/DynamoDB and are left for integration tests (see `CLAUDE.md`).

## Possible extensions

- Weather-aware thresholds (loosen the "delayed" bar on rainy days when
  MARTA is reliably slower system-wide)
- A tiny weekly digest email summarizing reliability trends per route/trip
- Real transit-time data (via GTFS static schedules) instead of the current
  simplified `leg1_wait + buffer vs. leg2_wait` heuristic, for trips where
  the ride between legs takes meaningfully long
