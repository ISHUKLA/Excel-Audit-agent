# Excel Audit Agent

Excel Audit Agent parses an actuarial or financial Excel workbook, independently reconstructs its formulas in Python to check them against the spreadsheet's own numbers, and optionally reconciles the result against a set of accounts figures you supply. Every finding and every reconciliation line is routed through a human reviewer at one of four sign-off gates before a PDF audit report is produced, so nothing in the final report is unreviewed. The tool never certifies a number on its own — it surfaces what it found, and a named human decides what to do with it.

## How to run locally

1. Install Python 3.11+ and the dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (used by Agent 4 to draft tab documentation).
3. Start the app:
   ```
   streamlit run app.py
   ```
4. Open the URL Streamlit prints (usually `http://localhost:8501`).

Run the test suite with:
```
pytest tests/
```

## How to run with Docker

1. Build the image:
   ```
   docker build -t excel-audit-agent .
   ```
2. Run the container, passing your API key through the environment:
   ```
   docker run -p 8501:8501 -e ANTHROPIC_API_KEY=your-key-here excel-audit-agent
   ```
3. Open `http://localhost:8501`.

## How to deploy to Streamlit Cloud

1. Push this repository to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), create a new app pointing at `app.py` on your branch.
3. In the app's **Settings → Secrets**, add your key using the same format as `.streamlit/secrets.toml.example`:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. `app.py` reads the key from Streamlit's secrets automatically — no code changes needed.

## The four human gates

The tool is built around four points where a named person, not the AI, has to make a decision before the pipeline continues. None of them can be skipped or merged.

1. **Context confirmation (Gate 1).** Before anything is parsed, you confirm that the file description you typed is accurate — and, if you supplied external accounts figures, that those are correct too. This is the tool checking it understood the assignment before doing any work.
2. **Findings review (Gate 2).** Every anomaly the tool flags (a hardcoded number buried in a formula, a suspicious skip in a `SUM` range, a circular reference between tabs, and so on) is shown to you individually. You confirm it's a real issue, override it with a reason, or dismiss it as a false positive with a reason. The pipeline will not proceed until every single finding has a decision attached.
3. **Reconciliation sign-off (Gate 3).** The tool shows you two independent comparisons side by side: its own Python recalculation against the spreadsheet's numbers ("internal consistency"), and — if you provided them — the spreadsheet's numbers against your accounts figures ("accounts reconciliation," for the CFO). You set the materiality threshold for each, and a genuine blocking discrepancy in either one stops the process cold until it's resolved.
4. **Final sign-off (Gate 4).** Once everything above is settled, a named person signs the report with their name and role. Only then is the PDF generated. The report's footer states outright that every finding was reviewed and approved by a named human — that sentence is only true because gates 1–4 actually happened.
