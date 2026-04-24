# Product Discovery

Agent-based product roadmap planning tool — OKR to Product Backlog.  
Django SPA with HTMX, SCSS, and MongoDB (PyMongo).

---

## Quick Start (Local — .venv)

```bash
# 1. Create and activate virtual environment
python -m venv .venv

# Windows PowerShell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
#    Edit .env with your MongoDB URI and admin secret key

# 4. Run the development server (ASGI — required for SSE streaming)
uvicorn config.asgi:application --reload --port 8000
```

Open **http://127.0.0.1:8000** in your browser.

> `python manage.py runserver` also works but is single-threaded; SSE runs will block all other requests while an agent is executing.

---

## Docker

```bash
# Build
docker build -t product-discovery .

# Run (uses .env file for configuration)
docker run -p 8000:8000 --env-file .env product-discovery
```

The container runs `uvicorn` (ASGI) by default, which is required for SSE streaming.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `APP_SECRET_KEY` | Admin password for write access | *(required)* |
| `MONGODB_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGODB_NAME` | MongoDB database name | `product_discovery` |
| `DEBUG` | Django debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `OPENAI_API_KEY` | API key for direct OpenAI models | *(required for `openai` models)* |
| `OPENAI_API_URL` | Endpoint fallback for `openai` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `ANTHROPIC_API_KEY` | API key for direct Anthropic models | *(required for `anthropic` models)* |
| `ANTHROPIC_API_URL` | Endpoint fallback for `anthropic` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `GOOGLE_API_KEY` | API key for direct Google Gemini models | *(required for `google` models)* |
| `GOOGLE_API_URL` | Endpoint fallback for `google` models when `endpoint` is omitted in `agent_models.json` | *(optional)* |
| `AZURE_OPENAI_API_KEY` | API key for Azure AI Foundry OpenAI deployments | *(required for `azure_openai` models)* |
| `AZURE_OPENAI_API_URL` | Endpoint fallback for `azure_openai` models when `endpoint` is omitted in `agent_models.json` | *(required if JSON `endpoint` is missing)* |
| `AZURE_ANTHROPIC_API_KEY` | API key for Azure AI Foundry Anthropic deployments | *(required for `azure_anthropic` models)* |
| `AZURE_ANTHROPIC_API_URL` | Endpoint fallback for `azure_anthropic` models when `endpoint` is omitted in `agent_models.json` | *(required if JSON `endpoint` is missing)* |

---

## Project Structure

See [AGENTS.md](AGENTS.md) for full architecture and development instructions.

## Configuration Notes

- Supported agent models are defined in `agent_models.json` at the repository root.
- The runtime resolves model endpoint as: JSON `endpoint` first, then `{PROVIDER_UPPER}_API_URL` env var fallback.
- For Azure models, the model key is the deployment name. Azure endpoints remain required, but may now come from either JSON `endpoint` or `AZURE_OPENAI_API_URL` / `AZURE_ANTHROPIC_API_URL`.
- The AutoGen runtime lives in the root `agents/` package, separate from the Django `server/` app.
- Agent execution is streamed over SSE (`/chat/sessions/<id>/run/`). The server **must** run under ASGI (`uvicorn`) for real-time streaming; WSGI will buffer the entire response before sending.
