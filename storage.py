"""Live connectors. No database connection is shared across Streamlit sessions."""
import os
from pathlib import Path
from core import normalize_id


def mysql_connection():
    import mysql.connector
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
    return mysql.connector.connect(
        host=os.getenv('MYSQL_HOST', '127.0.0.1'), port=int(os.getenv('MYSQL_PORT', '3307')),
        user=os.getenv('MYSQL_USER', 'cp_user'), password=os.getenv('MYSQL_PASSWORD', 'local_cp_password'),
        database=os.getenv('MYSQL_DATABASE', 'polyglot_cp'), connection_timeout=5)


def mongo_client():
    from pymongo import MongoClient
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
    return MongoClient(os.getenv('MONGO_URI',
                       'mongodb://cp_admin:local_mongo_password@127.0.0.1:27018/?authSource=admin'),
                       serverSelectionTimeoutMS=5000, tz_aware=True)


def mongo_collection(client):
    return client[os.getenv('MONGO_DATABASE', 'polyglot_cp')]['learning_events']


def read_sql(connection, query, params=()):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()


class LiveStore:
    def __enter__(self):
        self.sql = mysql_connection()
        try:
            self.mongo = mongo_client()
            self.mongo.admin.command('ping')
        except Exception:
            self.sql.close()
            if hasattr(self, 'mongo'):
                self.mongo.close()
            raise
        self.events = mongo_collection(self.mongo)
        return self

    def __exit__(self, *_):
        self.sql.close()
        self.mongo.close()

    def snapshot(self):
        return dict(students=read_sql(self.sql, 'SELECT * FROM students'),
                    courses=read_sql(self.sql, 'SELECT * FROM courses'),
                    enrollments=read_sql(self.sql, 'SELECT * FROM enrollments'),
                    events=list(self.events.find({}, {'_id': 0})))

    def optimized(self, course_id, start, end, event_type='all', student_id=None):
        query = '''SELECT DISTINCT s.student_id, s.name, s.email
            FROM students s JOIN enrollments e ON s.student_id = e.student_id
            WHERE e.course_id = %s AND e.status = %s'''
        params = (course_id, 'active')
        if student_id is not None:
            query += ' AND s.student_id = %s'
            params += (student_id,)
        students = read_sql(self.sql, query, params)
        if not students:
            return students, {}, 0
        ids = [normalize_id(s['student_id']) for s in students]
        # Support canonical integer IDs and decimal strings, including leading zeros.
        # Normalization in the pipeline handles all representations accepted by validation.
        criteria = {'timestamp': {'$gte': start, '$lt': end}}
        if event_type != 'all':
            criteria['event_type'] = event_type
        pipeline = [
            {'$match': criteria},
            {'$set': {'normalized_student': {'$convert': {'input': '$student_id', 'to': 'long',
                                                        'onError': None, 'onNull': None}},
                      'normalized_course': {'$convert': {'input': '$course_id', 'to': 'long',
                                                       'onError': None, 'onNull': None}}}},
            {'$match': {'normalized_course': course_id, 'normalized_student': {'$in': ids}}},
            {'$group': {
                '_id': '$normalized_student', 'event_count': {'$sum': 1},
                'quiz_count': {'$sum': {'$cond': [{'$eq': ['$event_type', 'quiz']}, 1, 0]}},
                'quiz_percent_sum': {'$sum': {'$cond': [{'$eq': ['$event_type', 'quiz']},
                    {'$multiply': [{'$divide': ['$payload.score', '$payload.max_score']}, 100]}, 0]}},
                'video_seconds': {'$sum': {'$cond': [{'$eq': ['$event_type', 'video']},
                                                     '$payload.seconds_watched', 0]}},
                'forum_posts': {'$sum': {'$cond': [{'$eq': ['$event_type', 'forum']}, 1, 0]}}
            }}]
        groups = list(self.events.aggregate(pipeline))
        return students, {normalize_id(g['_id']): g for g in groups}, len(students) + len(groups)


def seed_databases():
    """Idempotent demo upserts. Intended only for a dedicated CP database."""
    from pymongo import ReplaceOne
    from core import sample_data, validate
    data = sample_data()
    assert not validate(data)
    sql = mysql_connection()
    try:
        cursor = sql.cursor()
        try:
            for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
                if statement.strip():
                    cursor.execute(statement)
            for table, columns in [('students', ('student_id', 'name', 'email')),
                                   ('courses', ('course_id', 'course_name', 'credits')),
                                   ('enrollments', ('enrollment_id', 'student_id', 'course_id', 'semester', 'status'))]:
                updates = ','.join(f'{col}=VALUES({col})' for col in columns[1:])
                placeholders = ','.join(['%s'] * len(columns))
                cursor.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({placeholders}) '
                                   f'ON DUPLICATE KEY UPDATE {updates}',
                                   [tuple(row[col] for col in columns) for row in data[table]])
            sql.commit()
        except Exception:
            sql.rollback()
            raise
        finally:
            cursor.close()
    finally:
        sql.close()
    with mongo_client() as client:
        collection = mongo_collection(client)
        collection.create_index('event_id', unique=True)
        collection.create_index([('timestamp', 1), ('event_type', 1)])
        collection.create_index([('student_id', 1), ('course_id', 1)])
        collection.bulk_write([ReplaceOne({'event_id': e['event_id']}, e, upsert=True)
                               for e in data['events']], ordered=False)
    return {key: len(value) for key, value in data.items()}


if __name__ == '__main__':
    print('Seeding the configured CP databases. Existing demo IDs will be updated.')
    print(seed_databases())
