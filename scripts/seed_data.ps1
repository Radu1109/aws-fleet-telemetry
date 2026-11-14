# Seed staging with mock fleet telemetry data.
# Usage: .\scripts\seed_data.ps1

param(
    [string]$Region = "eu-west-1"
)

$BASE = (aws apigatewayv2 get-apis --region $Region --query "Items[?Name=='fleet-staging'].ApiEndpoint" --output text).TrimEnd('/')
if (-not $BASE) { Write-Error "Could not find fleet-staging API. Is the staging stack deployed?"; exit 1 }

Write-Host "Seeding $BASE ..."

$readings = @(
    # V-001: Paris route, two consecutive readings >110 km/h (triggers excessive-speed alert)
    @{vehicleId="V-001";eventTime="2026-10-31T10:00:00Z";latitude=48.8566;longitude=2.3522;speedKmh=0;fuelLevelPercent=80;diagnosticMeasurements=@()},
    @{vehicleId="V-001";eventTime="2026-10-31T10:00:10Z";latitude=48.8570;longitude=2.3530;speedKmh=45;fuelLevelPercent=79;diagnosticMeasurements=@()},
    @{vehicleId="V-001";eventTime="2026-10-31T10:00:20Z";latitude=48.8580;longitude=2.3545;speedKmh=120;fuelLevelPercent=78;diagnosticMeasurements=@()},
    @{vehicleId="V-001";eventTime="2026-10-31T10:00:30Z";latitude=48.8590;longitude=2.3560;speedKmh=125;fuelLevelPercent=77;diagnosticMeasurements=@()},
    @{vehicleId="V-001";eventTime="2026-10-31T10:00:40Z";latitude=48.8600;longitude=2.3575;speedKmh=30;fuelLevelPercent=76;diagnosticMeasurements=@()},
    # V-002: London, normal driving
    @{vehicleId="V-002";eventTime="2026-10-31T10:00:00Z";latitude=51.5074;longitude=-0.1278;speedKmh=30;fuelLevelPercent=60;diagnosticMeasurements=@()},
    @{vehicleId="V-002";eventTime="2026-10-31T10:00:10Z";latitude=51.5080;longitude=-0.1270;speedKmh=35;fuelLevelPercent=59;diagnosticMeasurements=@()},
    @{vehicleId="V-002";eventTime="2026-10-31T10:00:20Z";latitude=51.5090;longitude=-0.1260;speedKmh=40;fuelLevelPercent=58;diagnosticMeasurements=@()},
    # V-003: Berlin, sudden fuel drop (triggers fuel-drop alert — 80->60 = 20pp in 5 min)
    @{vehicleId="V-003";eventTime="2026-10-31T10:00:00Z";latitude=52.5200;longitude=13.4050;speedKmh=50;fuelLevelPercent=80;diagnosticMeasurements=@()},
    @{vehicleId="V-003";eventTime="2026-10-31T10:02:00Z";latitude=52.5210;longitude=13.4060;speedKmh=55;fuelLevelPercent=60;diagnosticMeasurements=@()}
)

$ok = 0
$fail = 0
foreach ($r in $readings) {
    try {
        $resp = Invoke-RestMethod -Uri "$BASE/telemetry" -Method POST -Body ($r | ConvertTo-Json -Depth 3) -ContentType "application/json"
        Write-Host "  OK $($r.vehicleId) @ $($r.eventTime) -> $($resp.canonicalId)"
        $ok++
    } catch {
        Write-Host "  FAIL $($r.vehicleId) @ $($r.eventTime): $($_.ErrorDetails.Message)"
        $fail++
    }
}

Write-Host ""
Write-Host "$ok readings accepted, $fail failed."
Write-Host ""
Write-Host "Query examples:"
Write-Host "  $BASE/vehicles/V-001"
Write-Host "  $BASE/vehicles/V-001/alerts"
Write-Host "  $BASE/vehicles/V-001/route?startInclusive=2026-10-31T00:00:00Z&endExclusive=2026-10-31T23:59:59Z"
Write-Host "  $BASE/vehicles/V-002"
Write-Host "  $BASE/vehicles/V-003/alerts"
