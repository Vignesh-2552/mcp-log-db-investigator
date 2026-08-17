import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.redaction import redact_log_event
from tools.cloudwatch import utils


@mcp.tool()
def cw_describe_log_fields(log_group: str) -> dict:
    """Sample recent events from a log group and report discovered JSON shapes
    (clustered by top-level key set, since one log group can hold structurally
    different event types — e.g. access logs vs. app-error logs) with each
    shape's own field-frequency table, to ground Logs Insights query
    generation. Boosts the sample with error/fatal-level events so rare-but-
    important shapes aren't drowned out by a purely random sample."""
    settings = get_settings()
    try:
        utils.validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
        client = utils.get_logs_client(settings)
        now = datetime.now(UTC)
        start_time = utils.epoch_millis(now - timedelta(hours=1))

        random_events = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            limit=settings.cw_describe_fields_sample_size,
        ).get("events", [])
        error_events = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            filterPattern=utils.ERROR_BOOST_FILTER_PATTERN,
            limit=settings.cw_describe_fields_error_boost_size,
        ).get("events", [])

        seen_ids = {e.get("eventId") for e in random_events}
        tagged_events = [(e, "random") for e in random_events]
        for e in error_events:
            event_id = e.get("eventId")
            if event_id not in seen_ids:
                seen_ids.add(event_id)
                tagged_events.append((e, "error_boosted"))

        clusters: dict[frozenset, dict[str, Any]] = {}
        parsed = 0
        for event, origin in tagged_events:
            try:
                obj = json.loads(event.get("message", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            parsed += 1
            sig = frozenset(obj.keys())
            cluster = clusters.setdefault(
                sig,
                {
                    "top_level_keys": sorted(sig),
                    "row_count": 0,
                    "composition": {"random": 0, "error_boosted": 0},
                    "field_counts": defaultdict(int),
                    "example_event": event.get("message", ""),
                },
            )
            cluster["row_count"] += 1
            cluster["composition"][origin] += 1
            for path in utils.flatten_field_paths(obj):
                cluster["field_counts"][path] += 1

        shapes = []
        for _, cluster in sorted(clusters.items(), key=lambda kv: -kv[1]["row_count"]):
            shape_id = hashlib.sha1(",".join(cluster["top_level_keys"]).encode()).hexdigest()[:8]
            fields = [
                {"field": k, "frequency": v}
                for k, v in sorted(cluster["field_counts"].items(), key=lambda kv: -kv[1])
            ]
            shapes.append(
                {
                    "shape_id": shape_id,
                    "top_level_keys": cluster["top_level_keys"],
                    "row_count": cluster["row_count"],
                    "sample_composition": cluster["composition"],
                    "fields": fields,
                    "example_event": redact_log_event(cluster["example_event"], settings),
                    "correlation_id_candidates": sorted(
                        f for f in cluster["field_counts"] if utils.CORRELATION_ID_RE.search(f)
                    ),
                }
            )

        total_events = len(tagged_events)
        note = None
        if total_events and parsed == 0:
            note = "Sampled events do not appear to be JSON-structured; field discovery is limited."
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return utils.aws_error_response(e)

    return {
        "ok": True,
        "data": {
            "log_group": log_group,
            "sampled_events": total_events,
            "parsed_as_json": parsed,
            "sample_composition": {
                "random": len(random_events),
                "error_boosted": total_events - len(random_events),
            },
            "shapes": shapes,
            "note": note,
        },
        "meta": {"shape_count": len(shapes), "sampled_events": total_events},
    }
