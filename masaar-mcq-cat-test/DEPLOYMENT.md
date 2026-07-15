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
OPENAI_API_KEY = "sk-proj-..."
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_TIMEOUT = "30"
LANGFUSE_SECRET_KEY = "sk-lf-..."
LANGFUSE_PUBLIC_KEY = "pk-lf-..."
LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
CAT_APPROACH_ID = "approach-2-llm-math-code-pick"
CAT_MATH_ACTOR = "llm"
CAT_SELECTION_ACTOR = "code"
```

For local runs:

```bash
cd masaar-mcq-cat-test
streamlit run streamlit_app.py
```
