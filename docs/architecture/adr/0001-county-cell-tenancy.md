# ADR 0001: Shared Control Plane with Siloed County Cells

Status: Accepted for planning

The platform uses a metadata-only shared control plane and isolated county
application cells. Production defaults to a county workload account when the
required isolation boundary calls for it. The first commercial sandbox is one
synthetic county only and is not proof of multi-county account isolation.

Operational CAD records, credentials, county documents, GIS, queues, logs,
keys, databases, and backups remain inside the county cell.
