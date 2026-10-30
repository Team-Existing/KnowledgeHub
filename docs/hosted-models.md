# Hosted models

Besides the [local models](local-models.md), a server can offer the models of one **OpenAI-compatible API**. That can be a cloud service (OpenAI, Azure OpenAI's v1 endpoint, OpenRouter, Groq, Together, Mistral and others) or a server you run yourself (vLLM, LM Studio, LiteLLM). Hosted models are much faster than an 8B model on a CPU, and usually more accurate.

## What leaves the server

When a user's chosen model is hosted, these are sent to that service:

- the text of the sources they add, for extraction and summaries
- their GraphRAG questions, the recent chat turns, and the knowledge items and source passages used to answer them

Embeddings, search and storage always stay on the server. Users who need everything kept on the server choose a local model.

The app says where text goes in these places:
- sign-in and onboarding
- the Models page, where each hosted model is labeled *Text is sent to …*
- the provider badge (*OpenAI · gpt-4.1-mini* instead of *Local · llama3.1:8b*)
- the transcript form and GraphRAG
- the privacy page

## Configuration

Nothing is sent anywhere unless the URL and the model list are both set. The app deliberately ignores `OPENAI_API_KEY`, so a key that happens to be in the environment can't start sending your knowledge base to a provider.

| Variable | Example | Meaning |
|---|---|---|
| `LLM_API_BASE_URL` | `https://api.openai.com/v1` | The API's base URL. The app calls `<base>/chat/completions` |
| `LLM_API_KEY` | `sk-…` | Sent as `Authorization: Bearer …`. Leave it empty for a server that needs no key |
| `LLM_API_MODELS` | `gpt-4.1-mini,gpt-4.1` | Comma-separated model names users may choose. Only these are offered |
| `LLM_API_NAME` | `OpenAI` | What users see, for example *Text is sent to OpenAI*. Defaults to the URL's host name |
| `LLM_API_TIMEOUT_SECONDS` | `120` | How long to wait for a reply |
| `OLLAMA_ENABLED` | `true` | `false` means no local models at all. Sign-in, onboarding and the Models page then only offer the hosted models, and nothing contacts Ollama |
| `LLM_DEFAULT_MODEL` | `api/gpt-4.1-mini` | The model for users who haven't chosen one, and for work done on nobody's behalf. Defaults to `OLLAMA_MODEL`, or to the first hosted model when Ollama is off |

Hosted models get the model id `api/<name>`, for example `api/gpt-4.1-mini`. That id is what's stored on a user's account.

## How it behaves

- **Sign-in:** a hosted model is enough to sign in, even when Ollama is down or no local model is installed. A user whose local model is no longer available is moved to a usable model, which may be a hosted one.
- **Structured output:**
  - Extraction asks for a JSON schema (`response_format: json_schema`).
  - A server that rejects that gets a plain JSON object request (`json_object`), and from then on that model is always sent the simpler form.
- **Newer OpenAI models:** a model that rejects `max_tokens` or `temperature` (OpenAI's reasoning models) is sent `max_completion_tokens` without a temperature from then on.
- **Rate limits and outages:** HTTP 429 and 5xx are retried twice. The app waits as long as `Retry-After` asks, up to 10 seconds. Other errors fail the call the same way an unreachable Ollama does: extraction falls back to rule-based extraction, and GraphRAG lists the retrieved knowledge instead of an answer.
- **Logs:** each call logs the model, the service, the time taken and the token counts. A reply cut off by its token limit is logged as a warning. The key is never logged.

## Limits

- One API per server. Organizations bringing their own key will come with organization support.
- There is no per-user or per-organization usage cap yet. The logs record token counts for each call.
