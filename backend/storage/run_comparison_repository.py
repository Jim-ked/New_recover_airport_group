from dataclasses import asdict

from backend.domain.run_comparison import RunComparisonRecord, canonical_json
from backend.storage.database import connect


class ComparisonInvariantError(RuntimeError):
    pass


class RunComparisonRepository:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def insert_or_get_existing(self, record: RunComparisonRecord, *, source_check=None):
        with connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if source_check:
                source_check(conn)
            values = asdict(record)
            columns = ','.join(values)
            inserted = conn.execute(
                f'INSERT INTO assessment_comparison ({columns}) VALUES ({",".join("?" for _ in values)}) ON CONFLICT(idempotency_key) DO NOTHING',
                list(values.values()),
            ).rowcount == 1
            row = conn.execute('SELECT * FROM assessment_comparison WHERE idempotency_key=?', (record.idempotency_key,)).fetchone()
            try:
                existing = RunComparisonRecord(**dict(row))
            except (ValueError, TypeError) as exc:
                raise ComparisonInvariantError('existing comparison record violates the saved contract') from exc
            # Labels/context may change. Identical mathematical identity must never change facts.
            old, new = existing.to_dict()['result_json'], record.to_dict()['result_json']
            if any(canonical_json(old[key]) != canonical_json(new[key]) for key in ('focus_comparison', 'pair_facts')):
                raise ComparisonInvariantError('same comparison identity produced different pair facts')
            return existing, inserted

    def get(self, record_id, *, owner_user_id):
        clause = '' if owner_user_id is None else ' AND created_by_user_id=?'
        params = (record_id,) if owner_user_id is None else (record_id, owner_user_id)
        with connect(self.db_path) as conn:
            row = conn.execute('SELECT * FROM assessment_comparison WHERE id=?'+clause, params).fetchone()
        # Return raw storage too: the read service must retain unreadable historical rows.
        return dict(row) if row else None

    def list(self, *, owner_user_id, limit=50, offset=0):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('limit must be in [1,500]')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('offset must be nonnegative')
        with connect(self.db_path) as conn:
            clause = '' if owner_user_id is None else ' WHERE created_by_user_id=?'
            scope = () if owner_user_id is None else (owner_user_id,)
            total = conn.execute('SELECT COUNT(*) FROM assessment_comparison'+clause, scope).fetchone()[0]
            rows = conn.execute('SELECT * FROM assessment_comparison'+clause+' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?',
                                (*scope, limit, offset)).fetchall()
        return [dict(row) for row in rows], total
