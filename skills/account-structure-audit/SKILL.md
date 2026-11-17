---
name: account-structure-audit
description: Run a structural audit of a B2C subscription or trial app's Meta ad account using RocketShip HQ's public audit repository. Excludes ROAS-led gaming, e-commerce, and B2B accounts.
---

# Meta Account Structure Audit

Use the public [meta-ads-account-audit](https://github.com/fortytwode/meta-ads-account-audit) repository as the authoritative implementation. This skill is a setup and workflow wrapper; do not copy or bundle its runtime.

## Scope

This cost-per-event audit is primarily for B2C subscription and trial-based apps. It can also analyze registration, purchase, or install funnels. It is not shaped for ROAS-led gaming/e-commerce or B2B/lead-generation accounts. The generated recommendations are drafts for human review.

## Run

Follow [SETUP.md](SETUP.md) to clone the verified public base into a new working folder, apply this review edition's included runtime maintenance patch, create a Python 3.10+ virtual environment, install its `requirements.txt`, and configure credentials locally. The existing public default branch has not received that patch; do not skip it or claim an unpatched checkout matches this edition.

From that repository root, run the documented two-command flow:

```bash
python run.py <label>
python assemble.py <label>
```

Use the same output label for both commands. `run.py` pulls data and writes an account map under `output/`; `assemble.py` renders the Markdown audit there. Read the generated audit and its methodology before summarizing it.

## Credentials and limits

The public code reads `FB_ACCESS_TOKEN` and `FACEBOOK_AD_ACCOUNT_ID` from environment variables or a `.env` file at the cloned repository root. The token needs `ads_read` on the selected account. Never ask the user to paste credentials into chat, print them, or copy them into this skill. Keep `.env` local and untracked.

With the included patch, the implementation uses Meta Marketing API `v25.0`. It limits lifetime monthly Insights to 36 months and requests bounded windows for other datasets. State those limits when relevant; do not describe the output as all-time history when the account is older or data is capped. Do not invent findings: report only what the generated audit supports and identify missing or limited data.

This workflow is read-only with respect to Meta campaigns. Do not edit, pause, create, or otherwise mutate campaigns. The audit is a local Markdown draft; do not publish it to Notion, Drive, or another service unless the user separately requests that action.

For a small illustrative example, see [examples/synthetic-findings.md](examples/synthetic-findings.md). Its numbers are synthetic and are not evidence from a real account or run.
