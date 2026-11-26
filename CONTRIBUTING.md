# Contributing

Thanks for helping improve Paracoat Dispatch Studio. Contributions should keep the project focused on a clear, dispatcher-reviewed workflow and must not present geometric estimates as cargo-safety certification.

## Before opening a pull request

1. Open an issue for a substantial feature or behavior change so the intended use case can be discussed.
2. Keep changes scoped and update the README or feature checklist when behavior, setup, or limitations change.
3. Add or update tests for changed behavior. Use synthetic fixtures; do not commit customer, shipment, credential, or other private data.
4. Run the checks below and include the results in your pull request.

## Local development

Python 3.11 is the supported development runtime used by CI.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest -q
streamlit run app.py
```

On Windows, create the environment with `py -3.11 -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`.

Set `PARACOAT_DB_PATH` to use a temporary or separate local database. SQLite files, virtual environments, Streamlit secrets, and environment files are ignored by Git.

## Pull request checklist

- [ ] The change addresses the linked issue or explains its use case.
- [ ] Tests cover changed logic and pass locally.
- [ ] User-facing documentation and `FEATURE_CHECKLIST.md` are current.
- [ ] No credentials, private records, local databases, or generated artifacts are included.
- [ ] Limitations and failure behavior are clear to dispatchers.
