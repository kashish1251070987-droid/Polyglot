from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from core import EVENT_TYPES, sample_data, validate, enrolled_students, normalize_id
from service import benchmark, run_query, ValidationError

st.set_page_config(page_title='Polyglot Learning Lab', page_icon='🎓', layout='wide')
st.title('Polyglot Learning Lab')
st.caption('Academic records in MySQL. Learning activity in MongoDB. One view for faculty.')

mode = st.sidebar.radio('Data source', ['Demo', 'MySQL + MongoDB'])
st.sidebar.caption('Demo uses synthetic data in memory. Live mode reads your configured databases.')
page = st.sidebar.radio('Workspace', ['Learning dashboard', 'Data quality', 'Database explorer', 'Performance', 'Project guide'])


def get_snapshot():
    if mode == 'Demo':
        data = sample_data()
        fault = st.sidebar.selectbox('Validation demonstration',
                                    ['Clean data', 'Duplicate event', 'Orphan student', 'Missing field', 'Wrong type'])
        if fault != 'Clean data':
            data = deepcopy(data)
            if fault == 'Duplicate event':
                data['events'].append(deepcopy(data['events'][0]))
            elif fault == 'Orphan student':
                data['events'][0]['student_id'] = 999999
            elif fault == 'Missing field':
                del data['events'][0]['timestamp']
            elif fault == 'Wrong type':
                data['events'][0]['payload']['score'] = 'ninety'
        return data
    from storage import LiveStore
    with LiveStore() as store:
        return store.snapshot()


if page == 'Project guide':
    st.markdown('''
### What this project demonstrates
MySQL owns students, courses and enrollments, with primary keys and foreign keys.
MongoDB owns quiz, video and forum events. Each event includes student and course references.
Python checks integrity, normalizes IDs and joins native query results.

### Query workflow
1. Choose a course, time period and activity type.
2. Read and validate a complete source snapshot.
3. Select active enrolled students in MySQL.
4. Filter and aggregate events in MongoDB.
5. Join by student ID and retain students with zero activity.
6. Compare against the validated snapshot before releasing the report.

### Metrics
Quiz percentage is the arithmetic mean of each attempt's score / maximum score × 100.
Video minutes sum reported watch time, including repeated views.
Forum posts count forum events. Zero activity means no events within the selected filters.
It is not a prediction of student performance.

### Limits
Run this as a local classroom prototype with synthetic data. It has no login or access roles.
Full validation reads all source records. Benchmark query timings exclude this shared cost.
Pause external writes during validation and benchmarking: two separate databases do not provide
a shared transaction or atomic snapshot. A comparison detects report differences, but cannot
guarantee global consistency under concurrent writes.
''')
    st.stop()

try:
    data = get_snapshot()
except Exception:
    st.error('Could not read the databases. Check .env, start both database services, and run the seed command in README.md.')
    st.info('Switch to Demo to use the application without database services.')
    st.stop()

issues = validate(data)
st.sidebar.caption(f"{len(data['students'])} students · {len(data['events']):,} events")
if mode == 'Demo':
    st.info('Demo mode · synthetic records · no MySQL or MongoDB connection')

if page == 'Data quality':
    st.subheader('Integrity checks')
    if issues:
        st.error(f'{len(issues)} issues found. Integrated reports and benchmarks are blocked.')
        st.dataframe(pd.DataFrame(issues), hide_index=True, width='stretch')
        st.download_button('Download issues', pd.DataFrame(issues).to_csv(index=False), 'validation-issues.csv', 'text/csv')
    else:
        st.success('All integrity checks passed for this snapshot.')
    st.markdown('Checks cover keys, duplicate IDs and enrollments, references, required fields, timestamp types, and event payload ranges.')
    st.stop()

if page == 'Database explorer':
    st.subheader('Source records')
    table = st.selectbox('Dataset', list(data), key='source_table')
    st.caption('Events belong to MongoDB. The remaining tables belong to MySQL.')
    if table == 'events':
        st.json(data[table][:5], expanded=False)
        st.caption('First five event documents shown.')
    else:
        st.dataframe(pd.DataFrame(data[table]), hide_index=True, width='stretch')
    st.code(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'), language='sql')
    st.stop()

if issues:
    st.error(f'{len(issues)} integrity issues detected. Open Data quality to inspect them. Integrated output is blocked.')
    st.stop()
if not data['courses']:
    st.warning('No courses available. Seed your databases first.')
    st.stop()

course_names = {int(c['course_id']): c['course_name'] for c in data['courses']}
col1, col2, col3 = st.columns([2, 2, 1])
course_id = col1.selectbox('Course', list(course_names), format_func=course_names.get, key='course_filter')
date_range = col2.date_input('Activity dates (UTC)',
                             (datetime(2026, 8, 1).date(), datetime(2026, 8, 31).date()))
event_type = col3.selectbox('Activity', ['all', *EVENT_TYPES], key='activity_filter')
student_names = {normalize_id(s['student_id']): s['name'] for s in enrolled_students(data, course_id)}
student_id = st.selectbox('Student', [None, *sorted(student_names)],
                          format_func=lambda sid: 'All active students' if sid is None else f'{sid} · {student_names[sid]}',
                          key=f'student_filter_{course_id}')
if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
    st.info('Select a start date and an end date.')
    st.stop()
if date_range[0] > date_range[1]:
    st.warning('The start date must be on or before the end date.')
    st.stop()
start = datetime.combine(date_range[0], time.min, tzinfo=timezone.utc)
end = datetime.combine(date_range[1] + timedelta(days=1), time.min, tzinfo=timezone.utc)
st.caption('Only active enrollments are included. The complete end date is included in UTC.')

try:
    if page == 'Performance':
        st.subheader('Basic vs optimized queries')
        st.write('Basic reads all source records and filters in Python. Live optimized queries filter students in MySQL and aggregate events in MongoDB.')
        if mode == 'Demo':
            st.warning('Demo runs the same in-memory algorithm twice to check the benchmark flow. It cannot measure database speedups or transfer reduction.')
        if st.button('Run comparison', type='primary'):
            with st.spinner('Checking results and measuring five runs per path…'):
                if mode == 'Demo':
                    results, validation_ms = benchmark(data, course_id, start, end, event_type, student_id=student_id)
                else:
                    from storage import LiveStore
                    with LiveStore() as store:
                        results, validation_ms = benchmark(data, course_id, start, end, event_type, store, student_id=student_id)
            frame = pd.DataFrame(results)
            st.success('Both paths returned identical results in every run.')
            st.dataframe(frame, hide_index=True, width='stretch')
            st.bar_chart(frame.set_index('path')['median_ms'])
            st.caption(f'Validation: {validation_ms:.2f} ms, excluding initial snapshot loading. Query timings include transfer and combination. One warm-up per path is excluded. Run order alternates. Rows transferred counts documents/rows, not bytes or peak memory.')
            st.download_button('Download measurements', frame.to_csv(index=False), 'benchmark.csv', 'text/csv')
        st.stop()
    if mode == 'Demo':
        rows, provenance = run_query(data, course_id, start, end, event_type, student_id=student_id)
    else:
        from storage import LiveStore
        with LiveStore() as store:
            rows, provenance = run_query(data, course_id, start, end, event_type, store, student_id=student_id)
    if not rows:
        st.info('No active students are enrolled in this course.')
        st.stop()
    frame = pd.DataFrame(rows)
    cols = st.columns(4)
    cols[0].metric('Enrolled students', len(frame))
    cols[1].metric('Learning events', int(frame.event_count.sum()))
    cols[2].metric('No activity in period', int((frame.event_count == 0).sum()))
    cols[3].metric('Video minutes', f'{frame.video_minutes.sum():,.1f}')
    st.subheader('Integrated student report')
    st.dataframe(frame, hide_index=True, width='stretch')
    st.caption('A blank quiz average means no quiz attempts in the selected period.')
    st.download_button('Download report', frame.to_csv(index=False), 'student-learning-report.csv', 'text/csv')
    with st.expander('Report provenance'):
        st.json(provenance)
        st.download_button('Download provenance', json.dumps(provenance, indent=2), 'report-provenance.json', 'application/json')
    with st.expander('Activity by student'):
        st.bar_chart(frame.set_index('name')['event_count'])
except ValidationError as exc:
    st.error(str(exc))
except Exception as exc:
    if isinstance(exc, ValueError):
        st.error(str(exc))
    else:
        st.error('Query failed. Check database availability and configuration, then refresh. Connection credentials are not displayed here.')
