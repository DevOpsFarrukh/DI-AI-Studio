#!/usr/bin/env bash
# Check a running deployment end to end: health, UI, voices/emotions, speech, audio, auth.
#
#   deploy/smoke-test.sh http://127.0.0.1:8000
#   deploy/smoke-test.sh https://tts.example.com "$DI_TTS_API_KEY"
#
# Exit code 0 = all checks passed. Needs curl and python3.
set -uo pipefail
URL="${1:?usage: smoke-test.sh BASE_URL [API_KEY]}"; URL="${URL%/}"
KEY="${2:-}"
HDR=(); [ -n "$KEY" ] && HDR=(-H "X-API-Key: $KEY")
fail=0
ok()  { echo "  ok    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "Smoke test: $URL"

code=$(curl -s -o /tmp/smoke_health.json -w '%{http_code}' "$URL/api/health")
[ "$code" = 200 ] && ok "health 200 ($(json 'd.get("voices"), "voices,", d.get("emotions", "?"), "emotions"' < /tmp/smoke_health.json))" \
                  || bad "health returned $code: $(cat /tmp/smoke_health.json)"

code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/")
[ "$code" = 200 ] && ok "web UI 200" || bad "web UI returned $code"

if [ -n "$KEY" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/voices")
  [ "$code" = 401 ] && ok "request without API key rejected (401)" || bad "request without API key returned $code (expected 401)"
fi

voices=$(curl -s "${HDR[@]}" "$URL/api/voices")
n=$(echo "$voices" | json 'len(d["emotion_details"])' 2>/dev/null) && ok "voices endpoint: $n emotions" || bad "voices endpoint: $voices"
voice=$(echo "$voices" | json 'd["voices"][0]["id"]' 2>/dev/null || echo narrator)

for emo in neutral sad excited whisper; do
  r=$(curl -s "${HDR[@]}" -H 'Content-Type: application/json' -X POST "$URL/api/say" \
      -d "{\"text\":\"Smoke test, one two three.\",\"voice\":\"$voice\",\"emotion\":\"$emo\"}")
  audio=$(echo "$r" | json 'd["audio_url"]' 2>/dev/null) || { bad "say ($emo): $r"; continue; }
  warns=$(echo "$r" | json 'len(d["warnings"])')
  magic=$(curl -s "$URL$audio" | head -c 4)
  if [ "$magic" = "RIFF" ] && [ "$warns" = 0 ]; then
    ok "say $voice/$emo -> WAV, $(echo "$r" | json 'd["duration_s"]')s, rendered in $(echo "$r" | json 'd["render_s"]')s"
  else
    bad "say $voice/$emo: magic=$magic warnings=$(echo "$r" | json 'd["warnings"]')"
  fi
done

r=$(curl -s "${HDR[@]}" -H 'Content-Type: application/json' -X POST "$URL/api/render" \
    -d '{"script":"NARRATOR (serious:0.8): The city was silent.\nALICE (curious): Did you hear that?\nBOB (fearful:0.7): I did."}')
lines=$(echo "$r" | json 'len(d["timeline"])' 2>/dev/null) && [ "$lines" = 3 ] && ok "script render: 3 lines, $(echo "$r" | json 'd["duration_s"]')s" \
  || bad "script render: $r"

[ $fail = 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $fail
