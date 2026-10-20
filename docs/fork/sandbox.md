# Sandbox stack

Coding agents work in a **cloud sandbox**, not on the owner's server. The sandbox can
build and run the whole system with small CPU models and fictional data. The owner runs
the final check (real model, real devices) on the server; see
[Server verification](#server-verification).

Verified 2026-10-18 in a Claude Code cloud sandbox: 4 CPUs, 15 GB RAM, no GPU; PyPI,
Hugging Face, npm and GitHub reachable; `cmake`, `gcc`, `ffmpeg`, Node 22, Python
3.11/3.12 and `uv` installed.

## What it is for

| Use the sandbox for | Leave to the owner's server |
|---|---|
| Unit tests, lint, everything with fakes | the real model (`qwen3.5-35b` on the RTX 3060) |
| Running the whole chain: seed data → odysseus-sound → MCP → Odysseus → eval | the score that counts (target ≥ 15/20) |
| Before/after comparisons on the same small model | real devices, real audio, Tailscale |
| Reproducing bugs in gating, routing, MCP, links | anything touching production (port 7000) |

Small-model scores are **not comparable** to the server scores (3/20, 9/20, 15/20). Compare
sandbox runs only with other sandbox runs.

## Models

- LLM: **Qwen3-4B-Instruct-2507, Q4_K_M** (~2.5 GB), the same family as production,
  without thinking mode.
- STT: whisper.cpp `ggml-base.en.bin` (~148 MB).

Speed on 4 CPUs: ~60 tokens/s prompt processing, ~7 tokens/s generation. Seeding the 8
demo conversations takes ~8 min. One eval question takes ~10–110 s with
`--frontend raw` and ~1.5–6 min with `--frontend odysseus`. Run a few questions, not all
20.

## Build and start

Paths below: `SBX` is a scratch directory; `SOUND` and `FORK` are the two clones.
Sandboxes are fresh containers, so redo this per session. Never commit `SBX` contents.

```bash
SBX=~/sbx; SOUND=<odysseus-sound clone>; FORK=<Odysseus fork clone>
mkdir -p $SBX/models $SBX/logs && cd $SBX

# llama.cpp and whisper.cpp, CPU builds (~10 min, can run in parallel)
git clone --depth 1 https://github.com/ggml-org/llama.cpp
(cd llama.cpp && cmake -B build -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release \
   && cmake --build build -j4 --target llama-server)
git clone --depth 1 https://github.com/ggml-org/whisper.cpp
(cd whisper.cpp && cmake -B build -DCMAKE_BUILD_TYPE=Release \
   && cmake --build build -j4 --target whisper-server)

curl -sSL -o models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
curl -sSL -o models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

# Same ports as production. Start long-running services with setsid so they survive
# the shell that started them (plain `cmd &` inside a compound command can die with it).
setsid nohup $SBX/llama.cpp/build/bin/llama-server -m $SBX/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  --alias qwen3-4b --host 127.0.0.1 --port 8082 --jinja -c 32768 -t 4 --cache-ram 2048 > $SBX/logs/llama.log 2>&1 < /dev/null &
setsid nohup $SBX/whisper.cpp/build/bin/whisper-server -m $SBX/models/ggml-base.en.bin \
  --host 127.0.0.1 --port 8178 -t 2 > $SBX/logs/whisper.log 2>&1 < /dev/null &
```

### odysseus-sound

```bash
cat > $SBX/convo.env <<'EOF'
export CONVO_INGEST_TOKEN=sandbox-ingest-token-0123456789abcdef
export CONVO_DATA_DIR=$HOME/sbx/convo-data
export CONVO_LLM_BASE_URL=http://127.0.0.1:8082/v1
export CONVO_LLM_MODEL=qwen3-4b
export CONVO_WHISPER_SERVER_URL=http://127.0.0.1:8178
export CONVO_DIARIZATION=false
export CONVO_PUBLIC_URL=http://127.0.0.1:8000
export CONVO_MCP_TOKEN=sandbox-mcp-token-0123456789abcdef
EOF
cd $SOUND/server
uv venv -p 3.11 .venv
# The vad extra pulls in torch; use the CPU wheel index (the default wheel is the CUDA build)
uv pip install -p .venv/bin/python --index-url https://download.pytorch.org/whl/cpu \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match -e ".[vad,dev]"
. $SBX/convo.env && mkdir -p $CONVO_DATA_DIR
.venv/bin/python scripts/seed_demo.py                      # 8 fictional conversations
setsid nohup .venv/bin/convo-server serve > $SBX/logs/convo.log 2>&1 < /dev/null &          # :8000
setsid nohup .venv/bin/convo-server mcp --http > $SBX/logs/mcp-http.log 2>&1 < /dev/null &  # :8001
```

Audio path check (public-domain JFK clip shipped with whisper.cpp; diarization is off in
the sandbox because it needs the SpeechBrain model):

```bash
.venv/bin/python scripts/smoke_upload.py --token "$CONVO_INGEST_TOKEN" --device smoke \
  --session smoke-jfk $SBX/whisper.cpp/samples/jfk.wav
# then: GET /v1/conversations, and /v1/conversations/<id>/transcript shows the text
```

The tokens are sandbox-only placeholders; never reuse them anywhere real.

### Odysseus

```bash
cd $FORK
uv venv -p 3.11 venv && uv pip install -p venv/bin/python -r requirements.txt -r requirements-dev.txt
mkdir -p data $SBX/odysseus-data
echo sandbox-admin-password > $SBX/admin.txt
ODYSSEUS_DATA_DIR=$SBX/odysseus-data ODYSSEUS_TRUSTED_READONLY_MCP=convo \
  setsid nohup venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7010 > $SBX/logs/odysseus.log 2>&1 < /dev/null &
```

ChromaDB isn't running, so the log shows `VectorRAG init failed` and
`MemoryVectorStore init failed`. That's expected; chat and MCP work without it.

Configure it over the API (no browser needed):

```bash
B=127.0.0.1:7010; J=$SBX/cookies.txt; . $SBX/convo.env
curl -s -X POST $B/api/auth/setup -H 'content-type: application/json' \
  -d '{"username":"admin","password":"sandbox-admin-password"}'
curl -s -c $J -X POST $B/api/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"sandbox-admin-password","remember":true}'
# The first endpoint becomes the default chat model.
curl -s -b $J -X POST $B/api/model-endpoints -F name=sandbox-llama \
  -F base_url=http://127.0.0.1:8082/v1 -F supports_tools=true
curl -s -b $J -X POST $B/api/mcp/servers -F name=convo -F transport=http \
  -F "url=http://127.0.0.1:8001/mcp?access_token=$CONVO_MCP_TOKEN"
# --trim: the tool set of all earlier sandbox runs; see "All Odysseus tools on" for why
venv/bin/python scripts/modules_setup.py --url http://$B --password-file $SBX/admin.txt --trim
INSTR=$(cd $SOUND/server && .venv/bin/python -c \
  "import json; from convo_server.mcp_server import INSTRUCTIONS; print(json.dumps(INSTRUCTIONS))")
curl -s -b $J -X POST $B/api/presets/custom -H 'content-type: application/json' \
  -d "{\"system_prompt\": $INSTR, \"temperature\": 0.3, \"max_tokens\": 2048}"
```

## Run the eval

```bash
cd $SOUND/server && . $SBX/convo.env
BAKEOFF_MODEL=qwen3-4b .venv/bin/python eval/bakeoff.py --frontend raw --only 1 4 16
ODYSSEUS_URL=http://127.0.0.1:7010 ODYSSEUS_PASSWORD_FILE=$SBX/admin.txt \
  .venv/bin/python eval/bakeoff.py --frontend odysseus --only 1 4 16
```

Results go to `server/eval/results/<frontend>.jsonl`. They hold only fictional demo
data, so they can be pasted into PRs. Odysseus' log (`$SBX/logs/odysseus.log`) shows each
tool call, approval and MCP error.

Sandbox baseline (2026-10-18, questions 1, 4, 16): raw 1/3, Odysseus over HTTP 1/3. Audio
check: Silero VAD found 8.1 s of speech in the JFK clip and whisper.cpp transcribed it.

whisper-server is only needed for the audio check: `seed_demo.py` inserts transcripts
directly, so an eval-only session can skip building whisper.cpp.

Count approvals in a run with `grep -c "Exact approval required" $SBX/logs/odysseus.log`;
`bakeoff.py` also prints `(odysseus: N tool approval(s) auto-accepted)` per question.

### HTTP annotation fix (finding 1), 2026-10-18

Same stack, same seed, questions 1, 4, 16, Odysseus restarted between the runs:

| | score | approval prompts | `Exact approval required` lines | tool calls | time |
|---|---|---|---|---|---|
| before | 2/3 | 2 | 2 | 2 | 563 s |
| after | 2/3 | 0 | 0 | 2 | 399 s |

The score doesn't move here because `bakeoff.py` auto-accepts approvals. Each approval
made Odysseus redo the model round that produced the call (~1–2 min on CPU), which is where
the time went. Question 16 fails in both runs: the 4B model answers without calling a
tool. Small runs vary: the same questions scored 1/3 in the first baseline.

### All Odysseus tools on, 2026-10-18

Same stack and seed, questions 1, 4, 16, one Odysseus instance; only `modules_setup.py`
changed between runs. 89 built-ins plus the built-in MCP servers and `convo`.

| Setup | score | convo tools called | approvals | time | what happened |
|---|---|---|---|---|---|
| A: `--trim`, "Regular tools" (old default) | 1/3 | Q1, Q4 | 0 | 683 s | Q16: no tool call (as before) |
| B: every tool on, "Regular tools" | 0/3 | none | 0 | 2 s | every request was 34.7–36.8k tokens, over the 32k context: llama-server returned HTTP 400 before the model ran (132 tool schemas sent) |
| C: every tool on, "Auto" (new default) | 0/3 | none | 1 | 299 s | per-turn selection offered `ask_user`/`python`/`read_file` (Q1, Q16) and `manage_calendar` (Q4); no convo tool was offered |

So with every tool on, module tools don't reach the model in either mode. `/sound`
and `/context` fix that per message; see [Slash skills](#slash-skills-2026-10-18). `modules_setup.py` now defaults to "Auto" (B can't
work at 32k), and `--trim` keeps the setup that answers recording questions.

### Slash skills, 2026-10-18

`/sound` and `/context` (SDarioosh/Barvis#9). Same stack and seed, one Odysseus instance,
questions 1, 4, 16 plus a person + relative date question (21: "What did Maria ask me
to do on Friday?", answer in the 2026-10-16 `maria-oct` conversation). C, D and E ran
with every tool on and "Auto"; only the message changed. D and E were sent through
`scripts/module_slash_eval.py` (the composed `/invoke` message, through bakeoff's
odysseus frontend). `tools_sent` is the `[agent-debug]` count on the first round.

| Setup | score | convo tools called | approvals | `tools_sent` | time |
|---|---|---|---|---|---|
| A: `--trim`, "Regular tools" | 3/4 | all 4 | 0 | 11 | 887 s |
| C: all tools, Auto, plain question | 0/4 | none | 2 | 2–3 | 1016 s |
| D: all tools, Auto, `/sound` | **4/4** | all 4 | 0 | 11 | 564 s |
| E: all tools, Auto, `/context` | 1/4 | Q1, Q4, Q21 | 0 | 11 | 551 s |

- C: Q1 and Q16 got `ask_user`/`python`/`read_file` and answered "no access"; Q4 went
  to `manage_calendar`, Q21 to `manage_tasks` (one approval each), both empty.
- D: every question searched the conversations and cited the right link. Q21 resolved
  "Friday" to 2026-10-16.
- E: Q16 made no tool call; Q4 searched but answered "not found"; Q21 cited the right
  conversation but left out the keyword (Rosa/train). Same tools as D, different
  procedure text (`/context`'s generic one vs odysseus-sound's `INSTRUCTIONS`), so with
  a 4B model the wording matters; small runs also vary (Q1 here started with "Model
  request failed", finding 5, and still passed).
- A first ran right after the others and failed in ~1 s per question: llama-server had
  been OOM-killed (13 GB resident; its prompt cache grows across long runs). The row
  above is the rerun on 2026-10-19 after restarting it with `--cache-ram 2048`; use
  that flag for long eval sessions in the sandbox.
- Date: the `/sound` request to llama-server (captured through a logging proxy)
  contains Odysseus' "Current date and time" message ("Today is Monday, October 19,
  2026 (2026-10-19)") and exactly the 11 tools.
- Step 1 (before the change), how a slash skill's `requires_toolsets` was applied in
  "Auto": only when the SKILL.md text named the tool; see the PR.

Rerun D:

```bash
cd $SOUND/server && . $SBX/convo.env
ODYSSEUS_URL=http://127.0.0.1:7010 ODYSSEUS_PASSWORD_FILE=$SBX/admin.txt \
  .venv/bin/python $FORK/scripts/module_slash_eval.py --skill sound \
  --log $SBX/logs/odysseus.log --extra -- --only 1 4 16 21
```

### Plain module questions, 2026-10-20

Setup C (every tool on, "Auto", no slash command), questions 1, 4, 13, 16 and 20 (the
"Mars" question must answer without a citation). Run 1 had the fork routing
(`src/module_turn_scope.py`) plus a fork "Sources:" footer; run 2 dropped the footer and
added one sentence to odysseus-sound's `INSTRUCTIONS` (search before saying "I don't
know"; Custom preset updated). Chat mode = `ODYSSEUS_MODE=chat`, what the web UI sends with
the agent toggle off.

| Run | agent mode | chat mode | notes |
|---|---|---|---|
| Before (C above, Q1 4 16 21) | 0/4 | — | no convo tool offered |
| 1: routing + footer | 2/5 | 4/5 | Q16 offered the tools but answered "shall I search?"; the footer fired once, on the Mars "nothing found" answer, and failed it |
| 2: routing + module text | **5/5** | **4/5** | chat Q16 searched conversations and missed the allergy |

Every passing answer cited the right transcript link; 0 approvals. The footer was removed
(it never added a passing citation), so the fork part is only new files.

```bash
cd $SOUND/server && . $SBX/convo.env
cd $FORK && venv/bin/python scripts/modules_setup.py --url http://127.0.0.1:7010 \
  --password-file $SBX/admin.txt --untrim
cd $SOUND/server && for m in agent chat; do ODYSSEUS_MODE=$m ODYSSEUS_URL=http://127.0.0.1:7010 \
  ODYSSEUS_PASSWORD_FILE=$SBX/admin.txt .venv/bin/python eval/bakeoff.py --frontend odysseus \
  --only 1 4 13 16 20; done
```

## Findings from the first sandbox run

These are real bugs, not sandbox quirks. 1–3 and 5 are bugs in Odysseus or the fork (tracked in
[roadmap.md](roadmap.md)); 4 is an odysseus-sound bug (tracked in its `PROGRESS.md`).

1. **Fixed: HTTP MCP servers lose their annotations.** `_connect_http` in
   `src/mcp_manager.py` stored each tool without `annotations` (the stdio and SSE paths
   keep them). So `src/mcp_trust.py` saw no `readOnlyHint`, and every read-only call over
   HTTP needed approval ("Exact approval required before tool start"). The owner's Docker
   setup uses HTTP, so this affected the server too. Fixed with one line; results in
   [HTTP annotation fix](#http-annotation-fix-finding-1-2026-10-18). Upstream issue to
   file.
2. **Fixed: stdio MCP servers added over the API fail on every call**:
   `MCP tool call failed: <name>:` with an empty message and `exit_code=1`, while the
   same server works from the same `mcp` client library outside Odysseus. Cause: the
   server was connected inside the HTTP request, and Starlette's `BaseHTTPMiddleware`
   cancels its task group once the response is sent, which also cancelled the stdio
   transport's reader/writer tasks (the empty message is anyio's `ClosedResourceError`).
   The call inside the adding request worked; every later one failed. Built-in servers
   were not affected (they hold their connection in their own task). Fixed by opening
   every stdio connection in a long-lived owner task (`src/mcp_stdio_owner.py`, FORK.md).
   Reproduced and checked under uvicorn with a one-tool FastMCP server; regression test
   `tests/test_mcp_stdio_owner.py`. Upstream issue to file.
3. **Empty answers:** some Odysseus replies are empty after tool calls (question 16 in the
   HTTP run). That matches the "Done." replies in the server eval.
4. **odysseus-sound: failed summaries are never retried.** If llama-server is down when a
   conversation is summarized, it is marked `failed`, and `convo-server summarize-pending`
   only retries `pending` conversations (`Pipeline.summarize_pending`). The conversation
   keeps its transcript but never gets a summary, title or action items.
5. **Repeating output becomes "Model request failed".** When the model repeats itself,
   Odysseus aborts the stream (`[degenerate-stream] aborting … repeated an exact 69-token
   block twice` in the log) and the answer starts with "The task is incomplete: Model
   request failed." (question 4, first run). Seen once, with the 4B model; not yet
   checked whether it happens with the server model.

## Server verification

The owner runs this on the server after the sandbox checks pass. Every PR that can change
answers, devices or deployment has a **Server verification** section listing:

- the commands to run, for example:
  - restart the isolated test instance (`~/.hermes/cache/scratch/barvis-run.sh`, port 7010);
  - `python scripts/modules_setup.py --url http://127.0.0.1:7010 --password-file <file>`;
  - `python eval/bakeoff.py --frontend odysseus` in odysseus-sound `server/`;
- the result to expect (e.g. "score ≥ previous 9/20");
- anything that needs a real device, with the steps.

Agents never run these themselves. Production (`/home/steam/models/odysseus`, port
7000) is changed only by the owner with `scripts/fork_deploy.sh`
([deploy.md](deploy.md)); a Server verification may end with that deploy step. The owner
pastes the result into the PR; the agent continues from there.
