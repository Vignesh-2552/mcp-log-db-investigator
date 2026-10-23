# Query cookbook

Table and column names below are examples. Confirm real names with `db_describe_table`,
`cw_describe_log_fields` or `nr_describe_log_fields` first.

## SQL

Failed rows in a window:
```sql
SELECT id, user_id, status, error_code, created_at
FROM public.orders
WHERE created_at BETWEEN :start AND :end AND status = 'FAILED'
ORDER BY created_at DESC LIMIT 100;
```

Failure counts grouped by provider:
```sql
SELECT provider, count(*) AS failures
FROM public.payments
WHERE status = 'FAILED' AND created_at BETWEEN :start AND :end
GROUP BY provider ORDER BY failures DESC;
```

## CloudWatch Logs Insights

Prefer `cw_get_trace_events(log_group, field, value, start, end)` for single-field traces. Raw form:
```
fields @timestamp, @message
| filter request_id = "req-9f2c1a2b3c4d"
| sort @timestamp asc
| limit 200
```

Error rate over time:
```
filter level = "ERROR"
| stats count() as errors by bin(5m), error_code
| sort errors desc
```

p95 latency over time:
```
filter ispresent(@duration)
| stats pct(@duration, 95) as p95_ms, pct(@duration, 50) as p50_ms by bin(5m)
| sort bin(5m) asc
```

Top error messages:
```
filter level = "ERROR"
| stats count() as occurrences by @message
| sort occurrences desc
| limit 20
```

## New Relic NRQL

Sanity check:
```
SELECT timestamp, message FROM Log SINCE 1 hour ago LIMIT 10
```

Trace by correlation id (run `nr_describe_log_fields` first; the real attribute may be `trace.id`):
```
SELECT timestamp, message FROM Log WHERE request_id = 'req-9f2c1a2b3c4d' SINCE 1 day ago LIMIT 200
```
If the id is not a parsed attribute, fall back to `WHERE message LIKE '%<id>%'`.

Error rate over time:
```
SELECT count(*) FROM Log WHERE level = 'ERROR' FACET error_code TIMESERIES 5 minutes SINCE 3 hours ago
```

Top error messages:
```
SELECT count(*) FROM Log WHERE level = 'ERROR' FACET message SINCE 1 day ago LIMIT 20
```
