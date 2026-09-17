# Troubleshooting: Report Stuck at Gate 3

**Audience:** Actuarial reviewers and team leads. No technical background required.

---

## 1. Symptoms

You're likely on this page because you're seeing one or more of:

- The Gate 3 (Reconciliation Sign-off) screen is up, but the **"Next" button is disabled or grayed out**.
- The reconciliation verdict shown reads **"BLOCK"** or **"INCOMPLETE"** for the internal or external comparison.
- The screen looks the same after refreshing or re-entering values.

None of this means the tool is broken. Gate 3 is designed to stop the pipeline whenever the reconciliation isn't clean — that's the gate doing its job, not a bug.

---

## 2. Immediate Checks

### a) Is the block intentional?

Gate 3 shows **two separate verdicts** side by side — internal (the workbook checking itself) and external (the workbook against your accounts figures). Either one can independently show `pass`, `warn`, `block`, `incomplete`, or `not_performed`. A `block` on either one is enough to disable "Next."

Example of what you might see:

```
Internal verdict:  warn
External verdict:  block
```

The three most common reasons for a `block` or `incomplete`:

1. **Context mismatch** — the currency, entity, or period on your reference figures doesn't match the workbook (e.g., the workbook is in GBP but the accounts file you uploaded is in EUR). This alone forces the external verdict to `block`, because a comparison across currencies or periods isn't meaningful.
2. **Unmapped reference lines or outputs** — one or more lines from your accounts figures, or one or more figures the tool produced, haven't been matched to anything on the other side. This forces the verdict to at least `incomplete`.
3. **Unapproved mappings** — the tool proposed a match between a workbook figure and an accounts line, but no one has approved it yet. A proposed mapping is not a decision; it doesn't count until a named person approves it.

Check which of these applies to you before doing anything else — it tells you whether you need to fix data, approve something, or whether the block is correct and the run should stop here.

### b) Check the materiality thresholds

Materiality thresholds control how large a difference has to be before it's flagged as a discrepancy rather than accepted as a rounding/immaterial variance.

- You'll find the threshold fields on the Gate 3 screen itself, above the reconciliation tables — one for percentage difference, one for absolute difference.
- These start at a sensible default, but **you set the actual value** — the tool will not silently pick one for you.
- A threshold set too tight will flag differences that aren't actually meaningful, driving verdicts toward `warn` or `block` unnecessarily. If your verdicts look worse than you expect, check whether the threshold is unusually low before assuming the underlying numbers are wrong.

### c) Check for unmatched lines and unapproved mappings

This is the most common cause of a stuck Gate 3.

1. Scroll to the bottom of the Gate 3 screen.
2. Look for sections labeled **"Unmapped reference lines"** and **"Unmapped Python outputs."** Anything listed here has not been reconciled against anything on the other side.
3. Just above that, look for any **proposed mappings awaiting approval**. These are the tool's suggestions for which accounts line corresponds to which workbook figure — shown with a confidence level, never pre-approved.
4. For each proposed mapping that looks correct, approve it under your own name. For anything genuinely unmatched (a line that has no counterpart), you don't need to force a match — an honest "not traceable" or "unmapped" entry is the correct outcome and will be reflected in the report as such, rather than hidden.

Once every mapping that should exist has been approved and true unmatched items are the only thing remaining, re-check the verdicts — they may still show `incomplete` by design if there is a genuine gap, which is expected and not something you can dismiss from this screen.

### d) Refresh the page

If you've made changes (approved a mapping, adjusted a threshold) and the screen doesn't seem to reflect it, press **F5** to reload. Gate 3 recalculates verdicts from the current state each time it loads, so a stale view is usually just a rendering lag, not a data problem.

---

## 3. If Still Stuck

If you've worked through Section 2 and the gate still won't advance, or the screen behaves unexpectedly (blank, error message, spinner that never resolves):

**Check the application logs** (if your deployment runs in Docker):

```bash
docker logs excel-audit-agent | tail -50
```

Look for a Python error or traceback near the bottom — that tells whoever supports the tool where to look, even if it doesn't mean anything to you directly.

**Query the audit log directly** to see the last recorded state for your report (you'll need your report ID, shown at the top of the Gate 3 screen):

```bash
sqlite3 audit.db "SELECT event_type, actor, created_at FROM log_rows WHERE report_id = '<your-report-id>' ORDER BY created_at DESC LIMIT 10;"
```

This shows the most recent events recorded for your report — useful for confirming whether your last action (a mapping approval, a threshold change) was actually saved.

**Last resort — resume in a fresh tab:**

1. Close the browser tab.
2. Open a new tab and navigate back to the app.
3. Use the resume/reload option to pick up your report by its ID.

This does not lose your work — every gate decision is saved to the audit log as it happens, so resuming reloads the last confirmed state rather than starting over.

If none of this resolves it, contact whoever administers the tool with your report ID and the log output above — that's enough for them to diagnose it without needing to reproduce the problem from scratch.

---

## 4. Prevention

- **Approve all mappings at Gate 2** before moving on, rather than leaving them for later. A mapping proposal that's still unapproved when you reach Gate 3 is one of the most common causes of an unexpected `incomplete` verdict.
- **Before uploading your accounts reference figures, double-check they match the workbook** on entity, period, and currency. A mismatch on any of these is caught automatically and will block the external comparison — catching it before upload saves a round trip back through the pipeline.

---

*This runbook describes expected behavior of the reconciliation gate, not a defect. A `block` or `incomplete` verdict that reflects a genuine data gap is the tool working correctly — the fix is in the underlying data or mappings, not in bypassing the gate.*
