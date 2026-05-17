#!/usr/bin/env bash
# Создаёт тестовый mkv с 2 аудиодорожками (рус + англ) на основе sample.mp4.
# sample.mp4 без аудио, поэтому генерим две тихие AAC-дорожки через anullsrc.
set -euo pipefail

SRC="tests/fixtures/sample.mp4"
OUT="tests/fixtures/multi_audio.mkv"

if [ ! -f "$SRC" ]; then
  echo "Missing $SRC"; exit 1
fi

ffmpeg -y -i "$SRC" \
  -f lavfi -t 5 -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -f lavfi -t 5 -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -map 0:v:0 -map 1:a:0 -map 2:a:0 \
  -c:v copy -c:a aac -b:a 96k \
  -metadata:s:a:0 language=rus -metadata:s:a:0 title="Дубляж" \
  -metadata:s:a:1 language=eng -metadata:s:a:1 title="Original" \
  "$OUT"

echo "Created $OUT"
