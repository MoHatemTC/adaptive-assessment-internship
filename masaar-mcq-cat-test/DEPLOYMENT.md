# Streamlit deployment

Deploy this branch as a separate Streamlit app.

## Approach

- Branch: `approach-2-llm-math-code-pick`
- Math actor: LLM
- Next-question actor: code
- Rephrasing: not enabled, because the LLM does not pick the next question in this approach.

## Streamlit settings

- Main file path: `masaar-mcq-cat-test/streamlit_app.py`
- Python requirements: root `requirements.txt` forwards to `masaar-mcq-cat-test/requirements.txt`

## Secrets

Set these in Streamlit secrets or environment variables:

```toml
# --- LLM backend: configure ONE. -------------------------------------------
# With both present the LiteLLM gateway wins; force either with LLM_PROVIDER.

# Option A — LiteLLM gateway. BOTH lines are required: a key with no base URL is
# ignored and the app silently falls through to OpenAI.
LITELLM_API_KEY = "sk-..."
LITELLM_BASE_URL = "https://learner-os.sprints.ai/litellm/v1"
LITELLM_MODEL = "kimi-k2.6"
# kimi is a reasoning model — ~20s per CAT step. The OpenAI-tuned 30s default
# turns merely-slow calls into "the model failed".
LITELLM_TIMEOUT = "180"

# Option B — OpenAI direct.
# OPENAI_API_KEY = "sk-proj-..."
# OPENAI_MODEL = "gpt-4o-mini"
# OPENAI_TIMEOUT = "30"

# LLM_PROVIDER = "litellm"   # or "openai", to override the precedence above

# Optional: price a model the built-in table does not cover (e.g. kimi). Both or
# neither — the cost panel reports metered tokens and says "unpriced model"
# rather than inventing a rate.
# LLM_PRICE_INPUT_PER_1M = "0.60"
# LLM_PRICE_OUTPUT_PER_1M = "2.50"

LANGFUSE_SECRET_KEY = "sk-lf-..."
LANGFUSE_PUBLIC_KEY = "pk-lf-..."
LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
CAT_APPROACH_ID = "approach-2-llm-math-code-pick"
CAT_MATH_ACTOR = "llm"
CAT_SELECTION_ACTOR = "code"
```

`config_env.py` also accepts these grouped under a TOML table, so
`[litellm]`, `[llm]`, `[openai]`, `[langfuse]` or `[cat]` sections work too.


For local runs:

```bash
cd masaar-mcq-cat-test
streamlit run streamlit_app.py
```
