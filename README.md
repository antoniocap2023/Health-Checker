# Claude Chatbot (local)

A basic streaming chatbot: **React** frontend + **FastAPI** backend, powered by
Claude. Runs entirely on your machine.

```
health-checker/
├── backend/    # FastAPI API that talks to Claude
└── frontend/   # React chat UI
```

You'll run **two terminals** at the same time: one for the backend, one for the
frontend.

---

## One-time setup

### 1. Backend

```sh
cd backend
python3 -m venv venv            # create an isolated Python environment
source venv/bin/activate        # turn it on (you'll see "(venv)" in your prompt)
pip install -r requirements.txt # install FastAPI, the Claude SDK, etc.
cp .env.example .env            # create your secrets file
```

Now open `backend/.env` and paste your Anthropic API key after `ANTHROPIC_API_KEY=`.
(Get a key at https://console.anthropic.com → Settings → API Keys.)

### 2. Frontend

```sh
cd frontend
npm install
```

---

## Running it (every time)

**Terminal 1 — backend:**

```sh
cd backend
source venv/bin/activate
uvicorn main:app --reload
```

Leave it running. It serves the API at http://localhost:8000

**Terminal 2 — frontend:**

```sh
cd frontend
npm run dev
```

Then open the URL it prints (http://localhost:5173) in your browser and start chatting.

---

## How it fits together

1. You type a message in the React app.
2. The app sends the **whole conversation** to the backend at `POST /api/chat`.
   (The Claude API is stateless, so we resend the history each time.)
3. The backend calls Claude and **streams** the reply back word-by-word.
4. The app appends each piece to the chat so you see it appear live.

## Testing

The backend has a `pytest` suite in `backend/tests/`. Run it from the `backend/`
folder with the virtual environment active:

```sh
cd backend
source venv/bin/activate
pip install -r requirements.txt   # includes pytest + httpx (one-time)
pytest                            # fast unit tests only — the default
```

The tests come in three tiers, fastest/cheapest first:

| Tier | Command | Hits the network? | Costs money? |
|------|---------|-------------------|--------------|
| **Unit** (default) | `pytest` | No — clock, network, and Claude are all faked | No |
| **Integration** | `pytest --run-integration` | Yes — real PubMed/NCBI | No |
| **End-to-end** | `pytest --run-e2e` | Yes — real PubMed **and** real Claude | Yes (a few cents) |

```sh
pytest                                  # ~20 fast unit tests, no network (~1s)
pytest --run-integration                # + real PubMed calls
pytest --run-e2e                        # + one real Claude call (spends money)
pytest --run-integration --run-e2e      # run everything
```

Integration and e2e tests are **skipped by default** so a plain `pytest` stays
fast, free, and offline. (CI can enable them with the `RUN_INTEGRATION=1` /
`RUN_E2E=1` environment variables instead of the flags.) The e2e test also skips
itself if no real `ANTHROPIC_API_KEY` is set.

What each tier covers:

- **Unit** — the sliding-window rate limiter (including a real-threads test that
  proves the lock holds), the exponential-backoff retry logic, PubMed JSON/XML
  parsing, and the `/api/chat` agent loop (with Claude mocked).
- **Integration** — that our real NCBI requests and response parsing still work
  against live PubMed.
- **End-to-end** — the whole stack for real: HTTP → agent loop → Claude → PubMed
  → a grounded, PMID-cited answer.

> **Note:** all real PubMed traffic flows through one shared rate limiter
> (9 req/sec, under NCBI's 10/sec cap), so the tests can't get you rate-limited —
> **as long as you run them in a single process.** Don't run the integration/e2e
> tests with a parallel runner like `pytest -n` (pytest-xdist): each worker
> process gets its own limiter, so several workers could collectively exceed
> NCBI's limit.

## Where to tweak things

- **Bot personality:** `SYSTEM_PROMPT` in `backend/main.py`
- **Which model:** the `model=` line in `backend/main.py`
- **Look & feel:** `frontend/src/index.css`
- **UI behavior:** `frontend/src/App.jsx`

## Later (when you're ready)

- **Claude on AWS Bedrock:** swap the client in `main.py` and prefix the model id.
- **Save chat history / add users:** add DynamoDB + auth — the current structure
  doesn't need to change to support this, you just add to it.
