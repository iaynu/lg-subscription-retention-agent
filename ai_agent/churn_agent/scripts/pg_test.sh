#!/bin/bash
# 임시 PostgreSQL 16 클러스터를 띄워 전체 테스트(PG 통합 포함)를 실행하고 정리한다.
# 사용: bash scripts/pg_test.sh [pytest 옵션]   (postgres 사용자로 initdb를 실행할 수 있는 리눅스 환경 기준)
set -e
B=/usr/lib/postgresql/16/bin
D=${PGTEST_DIR:-/tmp/pgt}; rm -rf "$D"; mkdir -p "$D"; chown postgres "$D" 2>/dev/null || true
su postgres -c "$B/initdb -D $D/data -A trust >/dev/null && $B/pg_ctl -D $D/data -o '-p 5544 -k $D' -l $D/log start >/dev/null"
sleep 2; psql -h "$D" -p 5544 -U postgres -c "CREATE DATABASE agent_test" >/dev/null
TEST_DATABASE_URL="postgresql+psycopg2://postgres@/agent_test?host=$D&port=5544" python3 -m pytest -q tests "$@" || RC=$?
su postgres -c "$B/pg_ctl -D $D/data stop >/dev/null"; rm -rf "$D"; exit ${RC:-0}
