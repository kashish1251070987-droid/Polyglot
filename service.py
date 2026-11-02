"""Validation gate and fair, repeatable query comparisons."""
from datetime import datetime, timezone
from statistics import median
from time import perf_counter
from core import combine, query_snapshot, validate


class ValidationError(ValueError):
    def __init__(self, issues):
        super().__init__(f'{len(issues)} integrity issues. Integrated output is blocked.')
        self.issues = issues


def checked_snapshot(data):
    started = perf_counter()
    issues = validate(data)
    elapsed = (perf_counter() - started) * 1000
    if issues:
        raise ValidationError(issues)
    return elapsed


def run_query(data, course_id, start, end, event_type='all', store=None, student_id=None):
    validation_ms = checked_snapshot(data)
    started = perf_counter()
    if store is None:
        rows = query_snapshot(data, course_id, start, end, event_type, student_id)
        transferred = None
    else:
        students, groups, transferred = store.optimized(course_id, start, end, event_type, student_id)
        rows = combine(students, groups)
        # Fail closed if the live sources changed since the validated snapshot.
        if rows != query_snapshot(data, course_id, start, end, event_type, student_id):
            raise ValueError('Sources changed during the query. Refresh and retry with writes paused.')
    return rows, dict(source='MySQL + MongoDB' if store else 'In-memory simulation',
                      generated_at=datetime.now(timezone.utc).isoformat(), course_id=course_id,
                      start_inclusive=start.isoformat(), end_exclusive=end.isoformat(),
                      event_type=event_type, student_id=student_id, enrollment_status='active',
                      validation_ms=round(validation_ms, 3),
                      query_ms=round((perf_counter() - started) * 1000, 3),
                      result_rows=len(rows), query_rows_transferred=transferred,
                      snapshot_records=sum(len(v) for v in data.values()))


def benchmark(data, course_id, start, end, event_type='all', store=None, repeats=5, student_id=None):
    validation_ms = checked_snapshot(data)
    # Both paths produce exactly the same report. Full validation happens once,
    # outside query-only timing; its cost is reported separately.
    measurements = {'Basic': [], 'Optimized': []}
    transfer = {}
    expected = None
    for i in range(repeats + 1):
        for path in (('Basic', 'Optimized') if i % 2 == 0 else ('Optimized', 'Basic')):
            started = perf_counter()
            if path == 'Basic':
                snapshot = store.snapshot() if store else data
                rows = query_snapshot(snapshot, course_id, start, end, event_type, student_id)
                count = sum(len(v) for v in snapshot.values()) if store else None
            elif store:
                students, groups, count = store.optimized(course_id, start, end, event_type, student_id)
                rows = combine(students, groups)
            else:
                # Same algorithm in demo mode; no fictitious database optimization.
                rows = query_snapshot(data, course_id, start, end, event_type, student_id)
                count = None
            elapsed = (perf_counter() - started) * 1000
            if expected is None:
                expected = query_snapshot(data, course_id, start, end, event_type, student_id)
            if rows != expected:
                raise ValueError('Result mismatch. Stop writes, refresh data, and repeat the comparison.')
            if i:
                measurements[path].append(elapsed)
            transfer[path] = count
    return [dict(path=path, median_ms=round(median(times), 3), min_ms=round(min(times), 3),
                 max_ms=round(max(times), 3), repeats=repeats, query_rows_transferred=transfer[path])
            for path, times in measurements.items()], validation_ms
