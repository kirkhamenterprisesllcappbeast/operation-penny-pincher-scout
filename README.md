# Penny Pincher Public Scout

Subscription-free, read-only discovery of advertised digital work. Runs hourly on
standard GitHub-hosted Linux runners in this public repository, or manually on an
existing Python 3.11+ computer. Uses only Python's standard library.

```
python3 -m unittest discover -s tests -v
python3 prototype/marketplace_scout.py --output scout-report.json --markdown scout-report.md
```

The latest report is in **Actions → Public Scout → latest completed run → Summary**
and the "Publish bounded public report" log. The workflow does not upload artifacts,
use caches, provision paid services, run larger runners, install packages or call
LLM APIs. No subscription or credential is required.

The hourly schedule is best-effort: GitHub may delay or disable scheduled runs.
This is recurring discovery, not an always-running AI worker. Public standard-runner
compute is free under GitHub's current rules:
https://docs.github.com/en/billing/concepts/product-billing/github-actions
The workflow refuses to run if this repository is private or copied to another
repository. Recheck billing before changing runner type or adding artifact storage.

## Sources and honest limitations

- Bounty: documented open-task API, queried without credentials. AUTH_REQUIRED is
  an access blocker, not a claim that the marketplace is empty.
- BasedAgents: public paid-task API. Escrow assertions are platform evidence only;
  a claim bond and any paid usage block zero-budget qualification.
- GitHub: three public searches for coding, documentation and research bounty
  signals. Up to 60 rows per scan, deduplicated by issue URL. Explicit reposts are
  excluded; this is a bounded sample, not an exhaustive search.
- Algora: publicly documented listing route probe. Access or contract failures are
  reported without invented tasks or private-endpoint workarounds.

Only an advertised positive amount, including $0.01, is a reward signal. $ alone is
not assumed to mean USD. Unknown effort, expense, eligibility, AI permission,
deadline and acceptance probability remain unknown. Every external recommendation
remains PRELIMINARY until each material claim has three independent credible
reviewed sources. Reposts and common publishers do not add corroboration.

No task automatically qualifies. Positive net-return arithmetic is a necessary
check, not source validation or authority to spend. Losses, break-even work, unknown
costs, fees, subscriptions and bond capital at risk are handled conservatively.

## Security and scope

No wallet settings, payment accounts, signing code, credentials or private
operational history are included. Bearer credentials are explicitly refused.
The runner only sends bounded GET requests to fixed HTTPS API endpoints, pins
public DNS results for TLS connections, refuses redirects, and stops on payment
challenges or rate limits. It never follows instructions or links in task bodies,
downloads task code, creates accounts, claims work, posts comments or submits work.

The report uses task IDs and validated source links, not arbitrary task text in
workflow commands or summaries. All paid activity requires a separate system and
specific human authorization. The received-money scoreboard stays $0.00 because
this runner has no receipt-verification integration. Advertised rewards are not
income. Tests use explicitly synthetic fixtures; only live scans produce discoveries.

Copyright (c) 2026 Kirkham Enterprises LLC. All rights reserved.
