#!/bin/bash
# Table test for tg-away.py + tg-ping.sh (dry run, nothing is sent). Run: bash tests/test_away.sh
H="$(dirname "$0")/../plugins/telegram-hook/scripts/tg-away.py"
T=$(mktemp -d); export TELEGRAM_BOT_TOKEN=fake-token TELEGRAM_CHAT_ID=1 TMPDIR=$T TG_AWAY_FAKE_PLATFORM=darwin TG_AWAY_DELAY=2 TG_PING_DRYRUN_FILE=$T/sent CLAUDE_PROJECT_DIR=/x/myproj CLAUDE_CODE_ENTRYPOINT=cli
unset CLAUDE_CODE_HOST_SESSION_ID CLAUDE_CODE_REMOTE CONDUCTOR_WORKSPACE_NAME CLAUDE_PLUGIN_OPTION_AWAY_DELAY
pass=0; fail=0
tr_text() { jq -nc --arg t "$1" '{type:"assistant",message:{content:[{type:"text",text:$t}]}}' >"$T/$2.jsonl"; }
tr_ask()  { jq -nc --arg q "$1" '{type:"assistant",message:{content:[{type:"tool_use",name:"AskUserQuestion",input:{questions:[{question:$q,options:[{label:"Short",description:"5 min"},{label:"Long"}]}]}}]}}' >"$T/$2.jsonl"; }
tr_tool() { jq -nc '{type:"custom-title",customTitle:"My session"}' >"$T/$2.jsonl"
            jq -nc --arg c "$1" '{type:"assistant",message:{content:[{type:"tool_use",name:"Bash",input:{command:$c}}]}}' >>"$T/$2.jsonl"; }
fire() { jq -nc --arg k "$1" --arg s "$2" --arg p "$T/$2.jsonl" '{hook_event_name:"Notification",notification_type:$k,session_id:$s,transcript_path:$p,message:"Claude needs your permission"}' | python3 "$H"; }
check() { # name, session, expected regex (grep -E) or NONE; exactly one message expected otherwise
  got=$(grep -c "^===$" "$T/sent.$2" 2>/dev/null); got=${got:-0}
  if [ "$3" = NONE ]; then [ "$got" = 0 ] && ok=1 || ok=0; else grep -qE "$3" "$T/sent.$2" 2>/dev/null && [ "$got" = 1 ] && ok=1 || ok=0; fi
  if [ $ok = 1 ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: $(cat "$T/sent.$2" 2>/dev/null)"; fi
}
run() { # session, then commands; sent file per session
  TG_PING_DRYRUN_FILE=$T/sent.$1 "${@:2}"
}
export TG_AWAY_FAKE_IDLE=999
tr_text "Build finished." s1;           run s1 fire permission_prompt s1
tr_text "Build finished." s2;           run s2 fire idle_prompt s2
tr_text "Deploy to prod now?" s3;       run s3 fire idle_prompt s3
tr_ask  "Which delay?" s4;              run s4 fire elicitation_dialog s4
tr_text "Run migration?" s5;            run s5 fire permission_prompt s5; sleep 1; touch -t 203001010000 "$T/s5.jsonl"
tr_text "Twice?" s6;                    run s6 fire permission_prompt s6; sleep 1; run s6 fire permission_prompt s6
tr_text "At the Mac?" s7;               TG_AWAY_FAKE_IDLE=0 run s7 fire permission_prompt s7
tr_tool "npm run build" s8;             run s8 fire permission_prompt s8
tr_ask  "Which delay?" s9;              CLAUDE_CODE_ENTRYPOINT=claude-desktop CLAUDE_CODE_HOST_SESSION_ID=local_abc-123 run s9 fire elicitation_dialog s9
tr_text "Cloud?" s10;                   CLAUDE_CODE_REMOTE=true run s10 fire permission_prompt s10
tr_text "Use *bold* [x]?" s11;          run s11 fire permission_prompt s11
mkdir -p "$T/claude-telegram-hook/claims"; echo '{"until": 9999999999}' >"$T/claude-telegram-hook/claims/s13"
tr_tool "make deploy" s13;              run s13 fire permission_prompt s13
mkdir -p "$T/ds/a/b"; echo '{"title":"Desk title","remoteControlEnabled":true,"bridgeSessionIds":["session_01Abc"]}' >"$T/ds/a/b/local_rc-1.json"
tr_text "Remote?" s12;                  TG_AWAY_DESKTOP_SESSIONS=$T/ds CLAUDE_CODE_ENTRYPOINT=claude-desktop CLAUDE_CODE_HOST_SESSION_ID=local_rc-1 run s12 fire permission_prompt s12
U=0b5c0f6e-1111-2222-3333-444455556666
tr_text "Resume?" $U;                   run $U fire permission_prompt $U
sleep 5
check "heading, no title"         s1 '^### Claude is waiting for you$'
check "app line"                   s1 '^Terminal\\$'
check "project: local"             s1 '^local:myproj$'
check "text as quote"              s1 '^>Build finished\.$'
check "idle, no question: silent"  s2 NONE
check "idle_prompt: tg-stop owns it" s3 NONE
check "claimed by tg-ask: silent"  s13 NONE
check "question heading"           s4 '^### Claude has a question$'
check "AskUserQuestion text"       s4 '^\*\*Which delay\?\*\*$'
check "numbered options"           s4 '^1\. \*\*Short\*\* · 5 min$'
check "option without description" s4 '^2\. \*\*Long\*\*$'
check "answered in time: silent"   s5 NONE
check "second wait replaces first" s6 'Twice\?'
check "user at Mac: silent"        s7 NONE
check "permission heading"         s8 '^### Claude needs permission$'
check "session title line"         s8 '^My session\\$'
check "plain-English ask"          s8 '^\*\*Run a shell command\*\*$'
check "command folded in details"  s8 '^<details><summary>Details</summary>$'
check "command inside details"     s8 '^npm run build$'
check "desktop app name"           s9 '^Desktop app\\$'
check "desktop title from app"     s12 '^Desk title\\$'
check "Remote Control reply link"  s12 '^\[Reply in Claude\]\(https://claude\.ai/code/session_01Abc\)$'
check "cloud app name"             s10 '^Cloud session\\$'
check "markdown escaped"           s11 '^>Use \\\*bold\\\* \\\[x\\\]\?$'
echo "tg-away: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
