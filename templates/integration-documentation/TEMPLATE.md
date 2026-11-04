# Integration: <external service>

- **Status:** Implemented | Partially implemented | Planned
- **Transport:** <stdio, HTTP, other>
- **Server or SDK:** <package name and exact pinned version>

## Purpose

<What this integration lets the system do, and which agents use it.>

## Setup

1. <Create the account or app and request the minimum scopes>
2. <Store credentials in the secret manager; never in the repository>
3. <Register the server or client and verify the connection>

## Permissions

| Tool or operation | Permission level | Approval needed |
| :--- | :--- | :--- |
| <name> | READ, WRITE, APPROVAL_REQUIRED or RESTRICTED | <yes or no> |

## Failure handling

| Failure | Detection | Response |
| :--- | :--- | :--- |
| Server unreachable | <health check> | <retry, fallback, notify> |
| Rate limited | <status code> | <back-off policy> |
| Schema changed | <schema hash comparison> | <disable and alert> |

## Security

<Credential storage, data sent out, untrusted content from this service, supply-chain controls (pinning, checksums).>

## Testing

<Mock server fixtures and which scenarios they cover.>

## Operations

<Monitoring, quotas, rotation of credentials, contact for the vendor.>
