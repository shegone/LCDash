READ_ONLY_TOOLS = [
    {"id": "live_operations", "name": "Live operations", "source": "CentralSquare", "purpose": "Active calls and assigned units"},
    {"id": "live_units", "name": "Live unit roster", "source": "CentralSquare", "purpose": "Available, active, and unavailable units"},
    {"id": "call_detail", "name": "CFS detail", "source": "CentralSquare", "purpose": "One incident, assignments, and command logs"},
    {"id": "recent_calls", "name": "Recent call arrivals", "source": "CentralSquare", "purpose": "Calls created during a recent hour window"},
    {"id": "historical_activity", "name": "Historical activity", "source": "PostgreSQL", "purpose": "Completed calls by time range"},
    {"id": "analytics_overview", "name": "Analytics overview", "source": "PostgreSQL", "purpose": "Trends, incident types, stations, and response times"},
    {"id": "discipline_activity", "name": "Discipline activity", "source": "PostgreSQL", "purpose": "Fire, EMS, and Law grouping"},
    {"id": "today_yesterday", "name": "Today versus yesterday", "source": "PostgreSQL", "purpose": "Same-time workload comparison"},
    {"id": "documentation_search", "name": "Knowledge search", "source": "Indexed manuals", "purpose": "Procedures and configuration"},
    {"id": "approved_memory", "name": "Approved local memory", "source": "Supervisor review", "purpose": "Local wording and approved guidance"},
]


def get_mae_tool_catalog() -> dict:
    return {
        "mode": "read-only",
        "write_tools_enabled": False,
        "tool_count": len(READ_ONLY_TOOLS),
        "tools": [dict(tool) for tool in READ_ONLY_TOOLS],
    }
