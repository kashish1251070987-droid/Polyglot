"""Deterministic sample data, integrity rules and database-independent integration."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import random

EVENT_TYPES = ('quiz', 'video', 'forum')


def normalize_id(value):
    # MongoDB aggregation returns BSON Int64, an int subclass. Normalize it
    # to a built-in int while still rejecting booleans and lossy float IDs.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return int(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise ValueError('Expected a positive integer or decimal string identifier')


def sample_data(student_count=120, events_per_enrollment=18):
    rng = random.Random(42)
    students = [dict(student_id=i, name=f'Student {i:03}', email=f'student{i:03}@example.edu')
                for i in range(1, student_count + 1)]
    courses = [dict(course_id=i, course_name=name, credits=credits) for i, name, credits in
               [(101, 'Database Management Systems', 4), (102, 'Data Structures', 4),
                (103, 'Computer Networks', 3)]]
    enrollments, events = [], []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for student in students:
        sid = student['student_id']
        for course in courses:
            cid = course['course_id']
            status = 'withdrawn' if sid % 17 == 0 else 'active'
            enrollments.append(dict(enrollment_id=len(enrollments) + 1, student_id=sid,
                                    course_id=cid, semester='2026-S1', status=status))
            # Include enrolled students with zero events to exercise a left join.
            if sid % 13 == 0:
                continue
            for n in range(events_per_enrollment):
                kind = EVENT_TYPES[n % 3]
                payload = ({'score': rng.randint(0, 100), 'max_score': 100} if kind == 'quiz'
                           else {'seconds_watched': rng.randint(30, 1200)} if kind == 'video'
                           else {'post_id': f'post-{len(events)+1}', 'word_count': rng.randint(10, 200)})
                events.append(dict(event_id=f'evt-{len(events)+1:07}',
                                   student_id=str(sid) if sid % 2 else sid, course_id=cid,
                                   event_type=kind, timestamp=start + timedelta(days=n % 30, hours=sid % 24),
                                   payload=payload))
    return dict(students=students, courses=courses, enrollments=enrollments, events=events)


def validate(data):
    """Validate the full snapshot before either query path can release a result."""
    issues = []
    def issue(source, record, message):
        issues.append(dict(source=source, record=str(record), issue=message))
    known = {}
    for table, key in [('students', 'student_id'), ('courses', 'course_id'),
                       ('enrollments', 'enrollment_id')]:
        seen = set()
        for row in data[table]:
            try:
                identity = normalize_id(row.get(key))
                if identity in seen:
                    issue(table, identity, f'Duplicate {key}')
                seen.add(identity)
            except ValueError:
                issue(table, row.get(key), f'Invalid {key}')
        known[table] = seen
    enrollment_pairs, unique_enrollments, emails = set(), set(), set()
    for row in data['students']:
        if not isinstance(row.get('name'), str) or not row['name'].strip() or not isinstance(row.get('email'), str) or '@' not in row['email']:
            issue('students', row.get('student_id'), 'Missing name or invalid email')
        if isinstance(row.get('email'), str):
            if row['email'] in emails:
                issue('students', row.get('student_id'), 'Duplicate email')
            emails.add(row['email'])
    for row in data['courses']:
        if not isinstance(row.get('course_name'), str) or not row['course_name'].strip() or type(row.get('credits')) is not int or row['credits'] <= 0:
            issue('courses', row.get('course_id'), 'Invalid name or credits')
    for row in data['enrollments']:
        try:
            sid, cid = normalize_id(row.get('student_id')), normalize_id(row.get('course_id'))
            if sid not in known['students'] or cid not in known['courses']:
                issue('enrollments', row.get('enrollment_id'), 'Orphan reference')
            enrollment_pairs.add((sid, cid))
            key = (sid, cid, str(row.get('semester')))
            if key in unique_enrollments:
                issue('enrollments', row.get('enrollment_id'), 'Duplicate enrollment')
            unique_enrollments.add(key)
        except ValueError:
            issue('enrollments', row.get('enrollment_id'), 'Invalid reference type')
        if not isinstance(row.get('semester'), str) or not row['semester'].strip() or row.get('status') not in ('active', 'completed', 'withdrawn'):
            issue('enrollments', row.get('enrollment_id'), 'Invalid semester or status')
    event_ids = set()
    for event in data['events']:
        eid = event.get('event_id')
        if not isinstance(eid, str) or not eid:
            issue('events', eid, 'Missing or invalid event_id')
        elif eid in event_ids:
            issue('events', eid, 'Duplicate event_id')
        else:
            event_ids.add(eid)
        try:
            sid, cid = normalize_id(event.get('student_id')), normalize_id(event.get('course_id'))
            if sid not in known['students'] or cid not in known['courses']:
                issue('events', eid, 'Orphan student or course')
            elif (sid, cid) not in enrollment_pairs:
                issue('events', eid, 'Student is not enrolled in this course')
        except ValueError:
            issue('events', eid, 'Invalid identifier type')
        ts = event.get('timestamp')
        if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
            issue('events', eid, 'Timestamp must be a timezone-aware datetime')
        kind, payload = event.get('event_type'), event.get('payload')
        if kind not in EVENT_TYPES:
            issue('events', eid, 'Unknown event type')
        if not isinstance(payload, dict):
            issue('events', eid, 'Missing or invalid payload')
            continue
        fields = {'quiz': ('score', 'max_score'), 'video': ('seconds_watched',),
                  'forum': ('word_count',)}.get(kind, ())
        valid = True
        for field in fields:
            value = payload.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                issue('events', eid, f'Invalid payload.{field}')
                valid = False
        if kind == 'quiz' and valid and (payload['max_score'] <= 0 or payload['score'] > payload['max_score']):
            issue('events', eid, 'Quiz score outside valid range')
        if kind == 'forum' and not isinstance(payload.get('post_id'), str):
            issue('events', eid, 'Missing or invalid payload.post_id')
    return issues


def enrolled_students(data, course_id):
    ids = {normalize_id(e['student_id']) for e in data['enrollments']
           if normalize_id(e['course_id']) == course_id and e['status'] == 'active'}
    return [s for s in data['students'] if normalize_id(s['student_id']) in ids]


def summarize(events):
    groups = defaultdict(lambda: dict(event_count=0, quiz_count=0, quiz_percent_sum=0,
                                      video_seconds=0, forum_posts=0))
    for e in events:
        g = groups[normalize_id(e['student_id'])]
        g['event_count'] += 1
        if e['event_type'] == 'quiz':
            g['quiz_count'] += 1
            g['quiz_percent_sum'] += e['payload']['score'] / e['payload']['max_score'] * 100
        elif e['event_type'] == 'video':
            g['video_seconds'] += e['payload']['seconds_watched']
        elif e['event_type'] == 'forum':
            g['forum_posts'] += 1
    return dict(groups)


def combine(students, groups):
    rows = []
    for student in students:
        sid = normalize_id(student['student_id'])
        g = groups.get(sid, {})
        count = g.get('quiz_count', 0)
        rows.append(dict(student_id=sid, name=student['name'], email=student['email'],
                         event_count=g.get('event_count', 0), quiz_attempts=count,
                         avg_quiz_percent=round(g['quiz_percent_sum'] / count, 2) if count else None,
                         video_minutes=round(g.get('video_seconds', 0) / 60, 2),
                         forum_posts=g.get('forum_posts', 0)))
    return sorted(rows, key=lambda row: row['student_id'])


def matches(event, course_id, start, end, event_type):
    return (normalize_id(event['course_id']) == course_id and start <= event['timestamp'] < end
            and (event_type == 'all' or event['event_type'] == event_type))


def query_snapshot(data, course_id, start, end, event_type='all', student_id=None):
    students = enrolled_students(data, course_id)
    if student_id is not None:
        students = [s for s in students if normalize_id(s['student_id']) == student_id]
    ids = {normalize_id(s['student_id']) for s in students}
    events = [e for e in data['events'] if normalize_id(e['student_id']) in ids
              and matches(e, course_id, start, end, event_type)]
    return combine(students, summarize(events))
