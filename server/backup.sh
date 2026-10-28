#!/bin/sh
# 每日备份 SQLite（在线备份，不停服务）。保留 14 天。
set -e
APP=/opt/studybot
OUT=$APP/backups
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
sqlite3 "$APP/data/studybot.db" ".backup '$OUT/studybot-$STAMP.db'"
find "$OUT" -name 'studybot-*.db' -mtime +14 -delete
echo "$(date '+%F %T') 备份完成 $OUT/studybot-$STAMP.db"
