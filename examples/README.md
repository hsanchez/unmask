# Python API examples

Run from the repository root with Python 3.14 or newer. Scripts resolve their
data relative to their own location or the installed package, so they also work
when launched from another directory using the project's Python environment.
They print results without writing files or starting the webapp.

## Regex bugs — local

```bash
uv run python examples/bugs.py
```

Uses `unmask.bugs` with the existing regex specification and taxonomy. The
examples intentionally demonstrate over-strictness, contradiction, and ambiguity.
No model provider or credentials are required.

## Natural-language bugs — model-assisted

```bash
uv sync --extra openai
uv run --env-file .env python examples/bugs_with_model.py
```

Set `OPENAI_API_KEY` in `.env` or the process environment. Omit `--env-file .env`
if credentials are already exported. This example sends the guardrail and
taxonomy to OpenAI and may incur provider charges; it does not run offline.
Edit `TargetConfig` in the script to select a different supported provider/model,
and install its corresponding extra. Extraction results may vary between calls.

## Protocol adherence — local

```bash
uv run python examples/adherence.py
```

Runs `unmask.adherence` twice: once with `VerificationCase.from_json` using the
studio's packaged example, and once with direct typed construction. It prints
detected findings separately from all checks, and always shows the overall
outcome. Both supplied cases have satisfied adherence under their annotations.

The annotations are supplied semantic assessments. Z3 checks the resulting
structured model; it does not generate annotations, interpret raw prose, or
establish hidden reasoning. An empty findings collection alone does not establish
adherence: inspect `result.status`, `result.overall`, checks, and limitations.
