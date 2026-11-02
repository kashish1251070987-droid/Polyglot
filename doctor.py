"""Read-only local readiness checks. Never print connection strings or passwords."""
import argparse
import json
import urllib.request


def dashboard_check():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8501/_stcore/health', timeout=3) as response:
            body = response.read(128).decode().strip()
            ready = response.status == 200 and body == 'ok'
        return {'service': 'Dashboard', 'ready': ready, 'message': 'Responding' if ready else 'Unexpected health response'}
    except Exception as error:
        return {'service': 'Dashboard', 'ready': False, 'message': f'Not responding ({type(error).__name__})'}


def database_checks():
    from storage import mysql_connection, read_sql, mongo_client, mongo_collection
    results = []
    connection = None
    try:
        connection = mysql_connection()
        counts = {table: int(read_sql(connection, f'SELECT COUNT(*) AS n FROM {table}')[0]['n'])
                  for table in ('students', 'courses', 'enrollments')}
        results.append({'service': 'MySQL', 'ready': all(counts.values()), 'counts': counts,
                        'message': 'Academic tables available' if all(counts.values()) else 'Load sample data with storage.py'})
    except Exception as error:
        results.append({'service': 'MySQL', 'ready': False,
                        'message': f'Unavailable or schema missing ({type(error).__name__})'})
    finally:
        if connection is not None:
            connection.close()
    try:
        with mongo_client() as client:
            client.admin.command('ping')
            count = mongo_collection(client).count_documents({})
        results.append({'service': 'MongoDB', 'ready': count > 0, 'event_count': count,
                        'message': 'Learning events available' if count else 'No events; load sample data with storage.py'})
    except Exception as error:
        results.append({'service': 'MongoDB', 'ready': False,
                        'message': f'Unavailable ({type(error).__name__})'})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-only', action='store_true')
    parser.add_argument('--dashboard-only', action='store_true')
    args = parser.parse_args()
    if args.database_only and args.dashboard_only:
        parser.error('Choose at most one check scope')
    checks = [] if args.dashboard_only else database_checks()
    if not args.database_only:
        checks.append(dashboard_check())
    ready = all(item['ready'] for item in checks)
    print(json.dumps({'ready': ready, 'checks': checks}, indent=2))
    return 0 if ready else 1


if __name__ == '__main__':
    raise SystemExit(main())
