from dataclasses import dataclass
from typing import Optional, Any

@dataclass
class BronzeEnvelopeV1:
    schema_version: int  # must be 1
    run_id: str          # UUID
    source: str          # "reddit"
    entity_type: str     # "post" | "comment" | "subreddit" | "user"
    source_id: str       # Reddit ID
    fetched_at: float    # Unix timestamp
    payload_sha256: str  # hex SHA-256 of payload JSON bytes
    payload: dict        # raw source data

@dataclass
class BronzeObject:
    object_id: str       # deterministic: SHA-256 of (source/entity/run/path/hash)
    run_id: str
    entity_type: str
    relative_path: str   # relative to bronze root
    sha256: str          # SHA-256 of the part file
    row_count: int
    min_fetched_at: float
    max_fetched_at: float
    transform_status: str  # "pending" | "running" | "success" | "failed"
    transformed_at: Optional[float]

@dataclass
class RunResult:
    run_id: str
    pipeline: str
    status: str          # "running" | "success" | "failed"
    started_at: float
    finished_at: Optional[float]
    input_count: int
    output_count: int
    errors: list[str]    # sanitized error strings only

@dataclass
class ServingState:
    singleton_id: str    # always "current"
    current_publish_id: Optional[str]
    published_at: Optional[float]
    source_run_id: Optional[str]

def validate_bronze_envelope(data: dict) -> BronzeEnvelopeV1:
    if data.get('schema_version') != 1:
        raise ValueError("Invalid schema_version")
    if not isinstance(data.get('run_id'), str):
        raise ValueError("Invalid run_id")
    if data.get('source') != "reddit":
        raise ValueError("Invalid source")
    if data.get('entity_type') not in ("post", "comment", "subreddit", "user"):
        raise ValueError("Invalid entity_type")
    if not isinstance(data.get('source_id'), str):
        raise ValueError("Invalid source_id")
    if not isinstance(data.get('fetched_at'), (int, float)):
        raise ValueError("Invalid fetched_at")
    if not isinstance(data.get('payload_sha256'), str):
        raise ValueError("Invalid payload_sha256")
    if not isinstance(data.get('payload'), dict):
        raise ValueError("Invalid payload")
    
    return BronzeEnvelopeV1(
        schema_version=data['schema_version'],
        run_id=data['run_id'],
        source=data['source'],
        entity_type=data['entity_type'],
        source_id=data['source_id'],
        fetched_at=float(data['fetched_at']),
        payload_sha256=data['payload_sha256'],
        payload=data['payload']
    )
