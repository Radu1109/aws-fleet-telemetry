# U1 Telemetry Core

## Module interfaces

The package is `fleet_telemetry.telemetry_core` under `src/`.

- `models.py` defines the immutable `TelemetryReading` and
  `DiagnosticMeasurement` types, the separate `ON` / `OFF` / `NOT_REPORTED`
  engine-state enum and optional normalization reason, UTC normalization,
  canonical identity, stable bytewise event ordering, and payload digests.
- `validation.py` exposes `validate_payload(payload, receipt_time)`. It returns
  `(reading, ())` for valid data or `(None, reasons)` for invalid data.
  Missing engine state normalizes to `NOT_REPORTED`; malformed supplied values
  also normalize to `NOT_REPORTED`, retain `engineStateReason`, and do not
  quarantine otherwise valid telemetry.
- `intake.py` exposes `IntakeService.handle(device_id, sequence, payload,
  signature)`. `IntakeOutcome` distinguishes accepted readings, canonical
  duplicates, exact protocol retries, and rejected messages.
- `processing.py` exposes `ReceiptProcessor` and
  `AcceptedReadingProcessor`. Receipt freshness is independent from
  event-time-selected position. Each accepted-reading attempt captures the
  vehicle's published version, computes without mutating repository state, and
  conditionally commits position, alert evidence/effectiveness, active
  conditions, checkpoints, and its processed marker together. A version
  conflict discards the candidate, rereads state, and recomputes; after three
  full-jitter conflict retries, processing raises `ProcessingRetryExhausted`
  so delivery remains retryable. In-order accepted readings use only the
  preceding event and a five-minute alert lookback. Late replay restores a
  complete rule checkpoint (cooldown trigger times, active/cleared condition,
  last reading and speed predecessor, and trailing five-minute fuel readings),
  then walks canonical history in bounded pages without a total time-span cap.
  Each staged chunk records a resumable continuation. Replay stops only when
  complete continuation state matches a stored checkpoint and the pinned
  remaining suffix is already processed and unchanged, or at retained-history
  end. Final derived effects and the processed marker publish through the same
  version check; incomplete staged work is not visible to reads.
- `alerts.py` provides pure speed/fuel evidence and condition evaluation. The
  cooldown is injectable; it defaults to ten minutes.
- `reads.py` exposes `ReadService` operations for current state, effective
  alerts, route history, and paginated canonical history. Both history
  operations call ordered repository pages constrained by vehicle, event-time
  range, acceptance generation, and keyset position; they do not load full
  vehicle history into the service. `InvalidRange` and `QueryFailed` keep
  invalid requests, empty results, and store failures distinct.
- `ports.py` declares the clock, device registry, verifier, repository, and
  message boundaries. Its replay contract pins an acceptance generation and
  exposes checkpoint lookup, bounded replay pages, atomic chunk staging,
  continuation resume, and final version-checked publication. Its read
  contract captures an immutable acceptance watermark, pages by canonical
  order, and resolves the last retained reading before an interval through a
  bounded lookup. `memory.py` supplies deterministic in-memory adapters for
  examples and tests; it is not a durable deployment store.

U1 does not implement staff or third-party authorization. U2 must authorize
callers before exposing alert or route-history operations. The verifier and
registry must be injected; a payload vehicle ID is not an identity proof.

## Testing

From the repository root, bootstrap the test dependencies:

```sh
python -m pip install -e '.[dev]'
```

Run only the U1 tests:

```sh
python -m pytest tests/u1_telemetry_core -q
```

Run the U1 core coverage gate:

```sh
python -m pytest tests/u1_telemetry_core --cov=src/fleet_telemetry/telemetry_core --cov-report=term-missing --cov-fail-under=80
```

The test suite uses fixed timestamps and in-memory adapters. It does not need
AWS credentials, live cloud resources, or sleeps. Ruff and Bandit configuration
is in `pyproject.toml`; the integrated CI pipeline remains U4's responsibility.

## Operational and configuration inputs

Before deployment, the composition root must provide durable implementations
of the repository and transactional outbox boundaries, a queue/outbox delivery
mechanism, a device registry, and a signature verifier. Repository acceptance must
transactionally persist the device sequence high-water mark, sequence/digest
retry result, canonical first-wins record, and valid-receipt / newly-accepted
outbox messages before Intake acknowledges success. Consumers must tolerate
at-least-once delivery. A failed durable commit is surfaced as
`RetryableIntakeError`; it is not acknowledged as a successful acceptance.

Do not configure a cryptographic algorithm, signature encoding, key lifecycle,
or credential format from this module: those remain unresolved security
interfaces. Unknown or revoked device assignments must fail closed. Configure
query-time retention checks even if the backing store also uses asynchronous
TTL cleanup.

Behavioral limits defined by U1 are inclusive latitude `[-90, 90]`,
longitude `[-180, 180]`, fuel `[0, 100]`, non-negative speed, a 90-day past /
five-minute future intake window, 60-second stale threshold, 24-hour maximum
route query, and 90-day history retention. The speed trigger is two consecutive
readings strictly above 110 km/h; the fuel trigger is a drop strictly greater
than ten percentage points in five minutes. The per-vehicle, per-alert-type
cooldown defaults to ten minutes and may be injected into
`AcceptedReadingProcessor`. Page size is supplied to `ReadService`.
U1 passes engine state to canonical-history consumers; U3 counts otherwise
qualifying idle intervals for `ON` or `NOT_REPORTED`, but not `OFF`. U1 does
not calculate daily idle totals.

The repository currently includes an in-memory adapter rather than a
DynamoDB/SQS production implementation. Durable key encoding and index choices
remain open for the deployment adapter; AWS resources, integrated CI/CD,
deployment, alarms, and teardown remain U4 deliverables. No credentials or
live resources were used here. Replay chunks and continuations are resumable
only within the lifetime/state of this in-memory adapter; they are not
cross-process or cloud durable. The production implementation must persist the
acceptance snapshot and retention boundary, complete checkpoint, bounded
staged generation, and continuation, then publish only a complete generation
through its conditional version check.
The in-memory adapter cannot prove durable cross-process replay; its resumable
continuations are limited to the lifetime/state of that adapter. A DynamoDB
adapter and an integration test in staging during U4 integration.

The in-memory processing adapter uses a lock only to implement its atomic
expected-version compare-and-commit primitive and keep tests deterministic.
That process-local mechanism is not a multi-worker production correctness
claim. A production adapter must enforce the conditional commit across worker
processes, atomically publish every derived effect and the processed marker,
and keep receipt freshness on its separate atomic-maximum path. An older replay
candidate cannot replace a newer published version: its expected version is
rejected, and the complete derivation must be recomputed from fresh state.

The in-memory adapter indexes readings by vehicle and canonical order and
assigns a monotonically increasing acceptance position when a canonical
reading is accepted. Continuations are opaque, unpredictable repository
handles to immutable query state (query bounds, snapshot time, acceptance
position, pinned retention boundary, and next key). Reusing a continuation
returns the same page even if a later commit carries an earlier Intake receipt
timestamp or the clock advances. Mutated or unknown handles are rejected.
Production adapters must persist continuation state for the supported token
lifetime and implement equivalent indexed, acceptance-generation queries;
the in-memory cursor state is process-local and is not a production durability
claim.

Alert replay retains all evidence. It recomputes affected evidence and may
supersede evidence through convergence or retained-history end; ordinary
in-order processing does not scan all raw readings or alert evidence. Each
late-replay page and staged chunk is bounded, but replay is not capped to a
fixed total event-time span.

## Traceability

| Implementation area | Design coverage |
|---|---|
| Payload validation, diagnostic retention, quarantine | FR1.1–FR1.3; BR1.2–BR1.5 |
| Canonical identity, first-wins deduplication, event-time eligibility and ordering | FR1.4–FR1.6; BR2.1–BR2.4 |
| Distinct valid-receipt / accepted-reading delivery; sequence retry distinction | FR1.3–FR1.5; BR2.5–BR2.6 |
| Independent receipt freshness and non-regressing position | FR2.1; BR2.6–BR2.7 |
| Speed/fuel evidence, cooldown, and late-event replay | FR4–FR5.2; BR3.1–BR3.4 |
| Current state, alerts, route and canonical-history reads | FR2.1, FR3–FR3.5; BR3.5; U1-to-U2 and U1-to-U3 contracts |
