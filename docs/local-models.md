# Local models

Knowledge Hubs supports exactly three LLMs, all downloaded and run locally by Ollama:

| Model | Ollama tag | Download | RAM |
|---|---|---|---|
| Llama 3.1 8B | `llama3.1:8b` | 4.7 GB | 8 GB+ |
| Mistral 7B | `mistral:7b` | 4.1 GB | 8 GB+ |
| GPT-OSS 20B | `gpt-oss:20b` | 13 GB | 16 GB+ |

The list lives in [`backend/app/services/llm_catalog.py`](../backend/app/services/llm_catalog.py). Other models Ollama may have are ignored.

- **Signing in needs one of the three.** The sign-in and onboarding screens first check what's installed:
  - **None installed:** you get a download screen. Pick a model; the one that suits your RAM is marked recommended. Sign-in only appears once the download finishes.
  - **One already installed:** you go straight to sign-in.
  - **Ollama not running:** the screen says so, with a Retry button.

  The server enforces the same rule: `POST /auth/token` returns 412 if no model is installed and 503 if Ollama is down.
- **Each user picks their model** on the **Models** page.
  - Your choice is saved on your account, so it applies to your future sessions.
  - It's used for every LLM call you make: extraction, summaries, GraphRAG, and connector syncs you start.
  - If your model disappears (removed on the Models page or with `ollama rm`), you're moved to another installed one.
- **Server admins download and remove models,** because every account on the machine shares the same Ollama. At least one model always stays installed.
