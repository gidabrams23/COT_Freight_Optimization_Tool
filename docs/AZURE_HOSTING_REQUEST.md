# Azure Hosting Request (Client IT)

## Objective
Deploy the load planning app in Azure with a simple, low-ops setup that supports:
- Up to 8 concurrent users
- CSV uploads around 8,000 rows per run
- Optimization response target: ideally <= 30s, acceptable up to 90s
- HTTPS access with controlled ingress (public endpoint with IP allowlisting per policy)
- Basic backup hygiene (non-critical system)

## Requested Azure Resources (Buy List)
1. Azure App Service (Linux, Web App for Containers)
   - App Service Plan SKU: Premium v3 `P1v3`
   - Instance count: `1` (single instance)
2. Azure Container Registry
   - SKU: `Basic`
3. Azure Storage Account (for App Service backups)
   - Standard LRS is sufficient

## Why This SKU/Pattern
- App is already containerized (`Dockerfile` present, Gunicorn entrypoint).
- App currently uses local SQLite storage (`APP_DB_PATH`), so horizontal scale-out is not recommended.
- Import + optimization are synchronous CPU-heavy operations; `P1v3` (2 vCPU / 8 GB) is the baseline for safer compute headroom.
- Public endpoint is acceptable only with access restrictions in place; unrestricted internet exposure is not required for this scope.

## App Configuration Required
Set these App Settings in App Service:
- `WEBSITES_PORT=5000`
- `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`
- `APP_DB_PATH=/home/site/app.db`
- `FLASK_SECRET_KEY=<strong secret>`
- `ADMIN_PASSWORD=<strong secret>`

Security gate before broad user rollout:
- Confirm `FLASK_SECRET_KEY` and `ADMIN_PASSWORD` are explicitly set to strong values in App Service settings (no defaults).
- Restrict ingress to approved office/VPN egress IPs until secrets are validated and smoke tests pass.

## Networking / Firewall Scope
Minimum:
- HTTPS (443) enabled on App Service.
- App Service Access Restrictions allowlisting expected client office/VPN egress IPs.

Recommended hardening:
- Enforce HTTPS-only and modern TLS settings.
- Optionally move to private endpoint/VNet if ATW policy requires non-public ingress.

Not required for this scope:
- Private Endpoint
- VNet Integration

## Scaling and Runtime Guardrails
- Keep scale at `1` instance while SQLite is in use.
- Do not auto-scale out to multiple instances unless database architecture is changed.
- If latency exceeds target under real usage, step up plan size before attempting multi-instance scale-out.
- Do not treat container startup/deployment timeout settings as request runtime controls; validate heavy request behavior end-to-end.
- If heavy import/optimization requests cannot reliably complete within acceptable timing, move those paths to async/background jobs.

## Storage Guidance for SQLite
- Keep SQLite on a single persistent app path (`APP_DB_PATH`) with single-instance writes.
- Avoid multi-instance shared-write patterns for SQLite-backed deployments.
- If multi-instance scale becomes a requirement, migrate to a managed multi-user database before scaling out.

## Optional Outbound Dependency
- If road routing is enabled (`ROUTING_ENABLED=true`), allow outbound HTTPS egress to OpenRouteService and configure `ORS_API_KEY`.
- If routing is not enabled, no external routing API dependency is required.

## Backup Hygiene
- Configure App Service Backup to Storage Account:
  - Frequency: daily
  - Retention: 7-30 days
- This is operational hygiene; strict RPO/RTO is not required for this system.

## In Scope for IT
- Provision Azure resources listed above
- Configure container deployment from ACR
- Set App Settings and secrets
- Configure HTTPS and access restrictions
- Configure scheduled backups
- Validate runtime behavior for heavy requests under expected load

## Out of Scope (for this deployment request)
- Database migration away from SQLite
- Application code refactor for distributed scale
- Enterprise DR/multi-region failover design

## Acceptance Criteria
1. Users can access app over HTTPS URL.
2. 8 concurrent users can use app without service instability.
3. Typical 8k-row upload and optimization completes within 90s in normal conditions.
4. Daily backup jobs are configured and visible in Azure.
5. Ingress restrictions and secrets configuration are in place before broad rollout.

## Source References
- App Service plan overview: https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans
- App Service pricing (SKU reference): https://azure.microsoft.com/en-us/pricing/details/app-service/plans/
- Configure custom container (`WEBSITES_PORT`, storage): https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container
- Request timeout behavior in App Service: https://learn.microsoft.com/en-us/troubleshoot/azure/app-service/web-request-times-out-app-service
- App Service backups: https://learn.microsoft.com/en-us/azure/app-service/manage-backup
- App Service IP/access restrictions: https://learn.microsoft.com/en-us/azure/app-service/app-service-ip-restrictions
- VNet Integration overview (outbound behavior): https://learn.microsoft.com/en-us/azure/app-service/overview-vnet-integration
- OpenRouteService API docs (if routing enabled): https://openrouteservice.org/dev/#/api-docs
