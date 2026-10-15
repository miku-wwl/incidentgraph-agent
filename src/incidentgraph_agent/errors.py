class IncidentGraphError(Exception):
    """Base class for safe, user-facing application errors."""

    code = "incidentgraph_error"


class InvalidInput(IncidentGraphError):
    code = "invalid_input"


class NotFound(IncidentGraphError):
    code = "not_found"


class DatabaseUnavailable(IncidentGraphError):
    code = "database_unavailable"


class GraphQueryError(IncidentGraphError):
    code = "graph_query_failed"


class ConfigurationError(IncidentGraphError):
    code = "configuration_error"
