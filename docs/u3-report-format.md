# U3 Daily Report Format

## Scope

This document describes U3's generated per-vehicle, per-UTC-day summary and
its CSV download. JSON remains the canonical representation; CSV is derived
from the same summary. The approved inception contract is silent on how to
serialize an unavailable metric value. U3 clarifies that behavior here and
in its functional-design artifacts without changing the inception contract.

## Canonical JSON Fields

| Field | Type | Description |
|---|---|---|
| `vehicleId` | string | Vehicle identity. |
| `utcDay` | date | UTC report day. |
| `distanceKm` | number or null | Daily distance rounded to two decimal places, or null when unavailable. |
| `idleDuration` | duration or null | Daily idle duration, or null when unavailable. |
| `fuelUsedPercentagePoints` | number or null | Sum of within-day fuel decreases, or null when unavailable. |
| `metricCompleteness.distanceKm` | enum | `complete`, `incomplete`, or `unavailable`. |
| `metricCompleteness.idleDuration` | enum | `complete`, `incomplete`, or `unavailable`. |
| `metricCompleteness.fuelUsedPercentagePoints` | enum | `complete`, `incomplete`, or `unavailable`. |
| `generatedAt` | date-time | The U1 canonical-history watermark used for the generation. |

Example:

```json
{
  "vehicleId": "vehicle-123",
  "utcDay": "2026-10-28",
  "distanceKm": 0,
  "idleDuration": null,
  "fuelUsedPercentagePoints": 4,
  "metricCompleteness": {
    "distanceKm": "complete",
    "idleDuration": "unavailable",
    "fuelUsedPercentagePoints": "incomplete"
  },
  "generatedAt": "2026-10-29T00:05:00Z"
}
```

## Unavailable and Incomplete Values

- When a metric status is `unavailable`, its JSON value is `null`; its CSV
  value cell is blank. The field and its completeness status remain present.
- When a metric is `incomplete` but a partial result can be calculated, keep
  that result and report the `incomplete` status.
- A calculated zero is `0`, not null. The completeness status communicates
  whether the calculation is complete.
- When no summary was generated for a vehicle and UTC day, the query returns
  `not_found`; it is not an all-null summary.

## CSV

CSV contains one row per vehicle and UTC day. It is derived from the canonical
JSON fields. Include a value column and a corresponding completeness column
for each metric:

```csv
vehicleId,utcDay,distanceKm,distanceKmCompleteness,idleDuration,idleDurationCompleteness,fuelUsedPercentagePoints,fuelUsedPercentagePointsCompleteness,generatedAt
vehicle-123,2026-10-28,0,complete,,unavailable,4,incomplete,2026-10-29T00:05:00Z
```

The empty `idleDuration` cell in the example is null-equivalent because its
status is `unavailable`; the distance value `0` is a real calculated zero.
Duration encoding follows the accepted contract's duration representation;
this clarification does not select a new duration encoding.

## Generation Consistency

Every page in a report generation uses the same U1 history watermark.
`generatedAt` equals that watermark. Readings accepted after it are excluded
until the day is regenerated, at which point the new summary replaces the
same vehicle/day identity and `generatedAt` advances.

Distance and idle calculations may use the last valid reading before the UTC
day to determine a pair whose later reading is in the report day. Fuel
comparisons use readings within the UTC day. Summary retrieval distinguishes
`found`, `not_found`, and `failed`; a failed read is never presented as an
empty successful report.
