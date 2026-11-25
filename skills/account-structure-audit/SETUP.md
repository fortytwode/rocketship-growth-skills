# Setup and usage

The audit implementation lives in the public repository [rocketshiphq/meta-ads-account-audit](https://github.com/rocketshiphq/meta-ads-account-audit). This review edition includes a small [runtime patch](references/runtime-update.patch) for its API-version pin and bounded request retries. The existing public default branch has not received that patch. Clone the verified base into a new working folder and apply the supplied patch before use:

```bash
git clone https://github.com/rocketshiphq/meta-ads-account-audit.git <working-folder>
cd <working-folder>
git checkout 4bb18ca5e3d95015ae2e5160e55b2d5a0a18f008
git apply /absolute/path/to/skills/account-structure-audit/references/runtime-update.patch
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Replace the illustrative paths with your actual folders. Apply only in that fresh checkout; do not discard changes or force the patch into a different source version. Use Python 3.10 or newer. The public `requirements.txt` currently lists `requests>=2.28` and `python-dotenv>=1.0`.

Configure these values locally, either in the repository's `.env` file or in the process environment:

- `FB_ACCESS_TOKEN`: Meta access token with `ads_read` for the target account.
- `FACEBOOK_AD_ACCOUNT_ID`: account ID, with or without the `act_` prefix.

Never paste credentials into chat or commit `.env`. The `label` below only names output files; it is not an account identifier or credential.

Run the two commands from the repository root, using the same label:

```bash
python run.py <label>
python assemble.py <label>
```

The first command retrieves data and creates `output/account_map_<label>_<date>.json`. The second renders the newest matching map into `output/audit_<label>_<date>.md`. Review the report's methodology and limitations before using its findings. With the supplied patch, the code uses Meta Marketing API `v25.0`; lifetime monthly Insights are capped at 36 months, and other pulls use bounded windows documented in the report/source. Offline request and assembly tests do not certify a live account's API access or completeness.
