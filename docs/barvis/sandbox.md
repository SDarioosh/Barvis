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
| Running the whole chain: seed data → barvis-sound → MCP → Barvis → eval | the score that counts (target ≥ 15/20) |
| Before/after comparisons on the same small model | real devices, real audio, Tailscale |
| Reproducing bugs in gating, routing, MCP, links | anything touching the owner's Odysseus install |

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

Paths below: `SBX` is a scratch directory; `SOUND` and `BARVIS` are the two clones.
Sandboxes are fresh containers, so redo this per session. Never commit `SBX` contents.

```bash
SBX=~/sbx; SOUND=<barvis-sound clone>; BARVIS=<Barvis clone>
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
  --alias qwen3-4b --host 127.0.0.1 --port 8082 --jinja -c 32768 -t 4 > $SBX/logs/llama.log 2>&1 < /dev/null &
setsid nohup $SBX/whisper.cpp/build/bin/whisper-server -m $SBX/models/ggml-base.en.bin \
  --host 127.0.0.1 --port 8178 -t 2 > $SBX/logs/whisper.log 2>&1 < /dev/null &
```

### barvis-sound

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

### Barvis

```bash
cd $BARVIS
uv venv -p 3.11 venv && uv pip install -p venv/bin/python -r requirements.txt -r requirements-dev.txt
mkdir -p data $SBX/barvis-data
echo sandbox-admin-password > $SBX/admin.txt
ODYSSEUS_DATA_DIR=$SBX/barvis-data ODYSSEUS_TRUSTED_READONLY_MCP=convo \
  setsid nohup venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7010 > $SBX/logs/barvis.log 2>&1 < /dev/null &
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
venv/bin/python scripts/barvis_setup.py --url http://$B --password-file $SBX/admin.txt
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
data, so they can be pasted into PRs. Barvis' log (`$SBX/logs/barvis.log`) shows each
tool call, approval and MCP error.

Sandbox baseline (2026-10-18, questions 1, 4, 16): raw 1/3, Barvis over HTTP 1/3. Audio
check: Silero VAD found 8.1 s of speech in the JFK clip and whisper.cpp transcribed it.

## Findings from the first sandbox run

These are real bugs, not sandbox quirks. 1–3 are Barvis bugs (tracked in
[roadmap.md](roadmap.md)); 4 is a barvis-sound bug (tracked in its `PROGRESS.md`).

1. **HTTP MCP servers lose their annotations.** `_connect_http` in `src/mcp_manager.py`
   stores each tool without `annotations` (the stdio and SSE paths keep them). So
   `src/mcp_trust.py` sees no `readOnlyHint`, and every read-only call over HTTP needs
   approval ("Exact approval required before tool start"). The owner's Docker setup
   uses HTTP, so this affects the server too. General fix, offer upstream.
2. **Stdio MCP servers added over the API fail on every call** in the sandbox:
   `MCP tool call failed: <name>:` with an empty message and `exit_code=1`, while the
   same server works from the same `mcp` client library outside Barvis. Use HTTP until
   this is understood.
3. **Empty answers:** some Barvis replies are empty after tool calls (question 16 in the
   HTTP run). That matches the "Done." replies in the server eval.
4. **barvis-sound: failed summaries are never retried.** If llama-server is down when a
   conversation is summarized, it is marked `failed`, and `convo-server summarize-pending`
   only retries `pending` conversations (`Pipeline.summarize_pending`). The conversation
   keeps its transcript but never gets a summary, title or action items.

## Server verification

The owner runs this on the server after the sandbox checks pass. Every PR that can change
answers, devices or deployment has a **Server verification** section listing:

- the commands to run, for example:
  - restart the isolated test instance (`~/.hermes/cache/scratch/barvis-run.sh`, port 7010);
  - `python scripts/barvis_setup.py --url http://127.0.0.1:7010 --password-file <file>`;
  - `python eval/bakeoff.py --frontend odysseus` in barvis-sound `server/`;
- the result to expect (e.g. "score ≥ previous 9/20");
- anything that needs a real device, with the steps.

Agents never run these themselves and never touch the owner's Odysseus install
(`/home/steam/models/odysseus`, port 7000). The owner pastes the result into the PR; the
agent continues from there.
