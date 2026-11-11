#!/bin/bash
# Linux/Mac: 임시 PostgreSQL 클러스터를 띄워 백엔드 시험을 돌리고 정리한다.  사용: bash scripts/pg_test.sh
# (이미 PostgreSQL 이 있으면 그냥 TEST_DATABASE_URL=postgresql://.../churn_test python -m unittest discover -s tests -v)
set -e
B=${PG_BIN:-/usr/lib/postgresql/16/bin}; D=${PGTEST_DIR:-/tmp/pgbe_test}; PORT=${PGTEST_PORT:-5546}
rm -rf "$D"; mkdir -p "$D"; chown postgres "$D" 2>/dev/null || true
su postgres -c "$B/initdb -D $D/data -A trust >/dev/null && $B/pg_ctl -D $D/data -o '-p $PORT -k $D' -l $D/log start >/dev/null"
sleep 2; psql -h "$D" -p $PORT -U postgres -c "CREATE DATABASE churn_test" >/dev/null
TEST_DATABASE_URL="postgresql://postgres@/churn_test?host=$D&port=$PORT" python3 -m unittest discover -s tests "$@" || RC=$?
su postgres -c "$B/pg_ctl -D $D/data stop >/dev/null"; rm -rf "$D"; exit ${RC:-0}
