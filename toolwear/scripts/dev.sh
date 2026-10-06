#!/usr/bin/env bash
# 一键启动演示环境：PostgreSQL + FastAPI + Vite
set -euo pipefail

PGHOME="${PGHOME:-$HOME/pgsql}"
PGDATA="${PGDATA:-$HOME/pgdata}"
export PATH="$PGHOME/bin:$PATH"
cd "$(dirname "$0")/.."

# 1) PostgreSQL（首次运行需先解压二进制，见 README）
if [ -x "$PGHOME/bin/pg_ctl" ]; then
  "$PGHOME/bin/pg_ctl" -D "$PGDATA" -l /tmp/toolwear-pg.log -o "-p 5432 -k /tmp" \
    -w -t 10 start 2>/dev/null || true
fi

# 2) 后端
cd backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACK_PID=$!
cd ..

# 3) 前端
cd frontend
npm run dev &
FRONT_PID=$!
cd ..

trap 'kill $BACK_PID $FRONT_PID 2>/dev/null || true' EXIT
echo "后端  http://localhost:8000/api/health"
echo "前端  http://localhost:5173"
wait
