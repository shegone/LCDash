# Logan County 911 ProSuite API Knowledge Base

This document records verified API information for the Logan County 911 CentralSquare ProSuite environment.

## Official References

- API Home: https://api-wv-logan-911.centralsquarecloudgov.com/home/
- CAD API Swagger: https://api-wv-logan-911.centralsquarecloudgov.com/api/cad/v1/docs#/
- System API Swagger: https://api-wv-logan-911.centralsquarecloudgov.com/api/system/v1/docs#/

## Base URLs

```text
Token URL:
https://api-wv-logan-911.centralsquarecloudgov.com/api/token

CAD API:
https://api-wv-logan-911.centralsquarecloudgov.com/api/cad/v1

System API:
https://api-wv-logan-911.centralsquarecloudgov.com/api/system/v1
```

## Authentication

The API uses OAuth2 Password Bearer authentication.

Swagger authorization fields:

```text
username: <api username>
password: <api password>
client_id: leave blank unless CentralSquare instructs otherwise
client_secret: leave blank unless CentralSquare instructs otherwise
```

After authentication, requests use:

```http
Authorization: Bearer <token>
From: <calling application name>
Accept: application/json
```

The `From` header is required and should identify the application or test source, for example:

```text
TedSparks-Test
Logan911-API-Test
LCDash
CommsCoach-Logan911
```

## Verified System API Tests

### GET /configurations

Purpose: Search configuration records.

Verified working with:

```text
configuration=IncidentType
From=TedSparks-Test
```

Result:

```json
{
  "IncidentType": []
}
```

Interpretation: Authentication and permissions worked. The table existed but returned no configured records for the authenticated agency.

### GET /configurations - CADUnitStatus

Verified working with:

```text
configuration=CADUnitStatus
From=TedSparks-Test
```

Returned real Logan County CAD unit status data, including:

| UniqueIdentifier | Description | Abbreviation | Notes |
|---:|---|---|---|
| 1 | Available | AV | Next logical status: Assigned |
| 7 | Assigned | AS | Considered dispatched |
| 2 | Enroute | E | Considered enroute to CFS location |
| 3 | On Scene | OS | Considered arrived at CFS location |
| 19 | Transporting | Trans | Considered transporting |
| 11 | Arrived At | AA | Considered arrived at secondary location |
| 6 | At Station | STA | Considered in quarters |
| 12 | Staged | STG | Considered staged |
| 20 | Enroute to Move Up | MOVEUP | EMS move-up status |

## CAD API Endpoint Map

### CFS Core

| Endpoint | Purpose | Priority |
|---|---|---|
| POST /cfs_core/search | Search CFS records | Essential |
| GET /cfs_core/{CFSNumber} | Get one CFS record | Essential |
| PUT /cfs_core/{CFSNumber} | Update CFS record | Caution |
| POST /cfs_core | Create CFS directly | Caution |
| POST /cfs_core/subscription | Subscribe to CFS updates | Essential future webhook |
| PUT /cfs_core/subscription/{SubscriptionUniqueIdentifier} | Update CFS subscription | Future |

### CFS Analytics

| Endpoint | Purpose | Priority |
|---|---|---|
| GET /cfs_analytics/{CFSNumber} | Get analytics and unit times | Useful |

### 911 Call Queue

| Endpoint | Purpose | Priority |
|---|---|---|
| POST /call_queue/search | Search call queue entries | Essential |
| GET /call_queue/{CallQueueUniqueIdentifier} | Get one call queue entry | Essential |
| PUT /call_queue/{CallQueueUniqueIdentifier} | Update call queue entry | Useful |
| POST /call_queue | Create call queue entry | Essential for CommsCoach/GovWorx |
| POST /call_queue/subscription | Subscribe to call queue events | Future webhook |
| PUT /call_queue/subscription/{SubscriptionUniqueIdentifier} | Update call queue subscription | Future |

### Units

| Endpoint | Purpose | Priority |
|---|---|---|
| POST /units/search | Search units/current unit data | Essential |
| GET /units/{UnitNumber} | Get one unit | Essential |
| POST /units/avl | Create/update unit AVL location | Useful |
| POST /units/subscription | Subscribe to unit changes | Essential future webhook |
| PUT /units/subscription/{SubscriptionUniqueIdentifier} | Update unit subscription | Future |

### Run Commands and Logs

| Endpoint | Purpose | Priority |
|---|---|---|
| POST /run_command | Execute CAD run command | Advanced/caution |
| PUT /command_log_entries | Create command log entries | Useful/caution |

### Record Links

| Endpoint | Purpose | Priority |
|---|---|---|
| GET /record_link/{RecordLinkUniqueIdentifier} | Get record link | Useful |
| PUT /record_link/{RecordLinkUniqueIdentifier} | Update record link | Useful |
| POST /record_link | Create record link | Useful for recordings, CommsCoach, RapidSOS links |

### Alarm Integration

| Endpoint | Purpose | Priority |
|---|---|---|
| POST /alarm_chats | Create alarm chat | Later |
| PUT /alarm_information | Update alarm information | Later |

## Verified CAD Endpoint Detail

### POST /cfs_core/search

Purpose: Returns core data elements of matching CFS records.

The documentation notes that follow-up calls may be made for specialized information:

- CFS Analytics for unit times
- Units for current unit data and configuration
- Record Links for voice recorder/body cam links
- 911 Call Queue for ANI/ALI information
- Address for premise information

Search request supports:

- DispatchAgencies
- ResponseAgencies
- RecordCreatedFrom / RecordCreatedTo
- RecordClosedFrom / RecordClosedTo
- RecordUpdatedFrom / RecordUpdatedTo
- CurrentlyActive
- IncidentCode
- Location
- Beat
- Zone
- CaseAssociation
- OrderByField
- OrderByDirection
- Responder
- Unit

Important response data includes:

- CFSNumber
- DispatchAgency
- ExternalCFSNumber
- ExternalNumbers
- Case
- IncidentDateTime
- CallTaker
- CallDateTime
- PrimaryResponseAgency
- Reporter
- Address
- Beat
- Zone
- MapLayer
- IncidentCode
- UseCaution
- Priority
- Disposition
- Unit
- Vehicle
- Name
- CommandLog
- ProQA
- InteliComm
- RapidSOS
- TextHistory
- Link
- IsScheduledCall
- NearestCrossStreet
- NearestIntersection

## Integration Notes

### CommsCoach / GovWorx

Preferred first integration path:

```text
CommsCoach/GovWorx -> POST /call_queue -> Dispatcher reviews -> CAD CFS created
```

Avoid direct CFS creation until testing and operational approval are complete.

### LCDash First Milestone

The first application milestone is:

1. Authenticate to CentralSquare.
2. Call `POST /cfs_core/search` with `CurrentlyActive=true`.
3. Display active CFS records in a web page.
4. Allow clicking a CFS number to view full details.

## Security Notes

- Do not commit API usernames, passwords, tokens, or secrets to GitHub.
- Store secrets in environment variables or a local `.env` file.
- `.env` must remain ignored by Git.
- Use a dedicated API service account for vendor and application integrations.
