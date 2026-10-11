# Supervised email MVP verification — 2026-10-07

The bounded supervised workflow implementation and synthetic acceptance for
milestone #51 are complete: one configured IMAP/SMTP account, one recipient and
plain-text replies. This does not establish stable full-product
acceptance, useful local-model quality, live-provider delivery, Windows support
or seven-day daily use. Those broader claims remain separately gated.

## Independent backend and regression checks

Run from the shared working tree in `/Users/marc/Documents/GitHub/capabledeputy`.
The full-suite results below concern that working tree, not an immutable release.
Default mail effects used only synthetic accounts and fake transports.

| Check | Result |
| --- | --- |
| Email acceptance, journal integration, reply contract, delivery uncertainty and approval identities | 107 passed, exit 0 |
| Production SMTP serialization boundary | 2 passed, exit 0 |
| Latest Swift package suite | 77 executed, 1 skipped, 0 failures; exit 0 |
| Unsigned Mac product app build | `BUILD SUCCEEDED`, exit 0; isolated temporary derived data |
| Actual cached Qwen3 8B model lane | Bounded synthetic rerun passed in 65.06 seconds; initial failure retained |
| Final Python regression suite | 3,648 passed, 15 skipped, 2 explicitly deselected, exit 0; 211.64 seconds |
| Ruff check and format | Passed; 810 files formatted |
| Pyright | 0 errors, 0 warnings |
| Coverage ratchet | Passed, 88 groups; total branch-aware coverage reported as 76% |
| Working-tree whitespace check | Passed |

The final full-suite command was:

```sh
UV_CACHE_DIR=/tmp/capdep-uv-cache \
UV_PYTHON_INSTALL_DIR=/tmp/capdep-uv-python \
.venv/bin/python -m pytest --cov-report=xml \
  -k 'not test_real_podman_round_trip_files and not test_real_podman_smoke'
```

It ran outside the restrictive sandbox to allow disposable subprocess cleanup
and host Metal initialization. The two excluded tests require a real Podman VM;
the configured VM socket was unavailable. No Podman containment claim follows
from mocked substrate tests. The new SMTP serialization tests were added after
full-suite collection and ran separately:

```sh
.venv/bin/python -m pytest --no-cov -q tests/test_email_mvp_smtp_boundary.py
```

Full-run and type-check logs are local temporary artifacts:
`/private/tmp/capdep-mvp-final-pytest-20261007.log` and
`/private/tmp/capdep-mvp-final-pyright-20261007.log`.

## Observed authorization and information-flow boundary

`tests/test_email_mvp_acceptance.py` runs the real Unix-socket daemon with a
scripted hostile planner. The planner actually attempts direct and base64
disclosure, forged consent/operator approval, recipient/body substitution,
expired/revoked/absent authority and stale/replayed reviews across restart.
Tests observe the fake transport independently of policy logs. A legitimate
human-reviewed reply succeeds beside blocked attacks.

The tests capture actual planner and extractor inputs: an attacker-controlled
projection reaches the planner, while a raw source canary reaches only the
tool-free extractor. Absent read authority causes no provider read and no
extractor call. These establish the tested boundary, not universal provider
blindness for every execution mode.

`tests/test_email_mvp_smtp_boundary.py` additionally restores production
`deliver()` and uses stdlib `SMTP.send_message()` to serialize MIME. Only the
connection's network operations are replaced. The observer captures actual
SMTP DATA bytes and the envelope sender/recipients. Decoded headers, threaded
message identity and multiline Unicode body match the daemon's frozen review;
the receipt reports `smtp_accepted`. Hostile direct/transformed disclosure
produces no DATA; replay adds no submission. SMTP acceptance is not evidence of
recipient inbox delivery.

The product app was built without launching or installing it:

```sh
xcodebuild -project apps/macos/CapDep/App/CapDep.xcodeproj -scheme CapDep \
  -configuration Debug -derivedDataPath /tmp/capdep-mvp-final-xcode \
  build CODE_SIGNING_ALLOWED=NO
```

Build log: `/private/tmp/capdep-mvp-final-xcode-20261007.log`. This verifies the
unsigned product target; it does not establish signed release/distribution
readiness or packaged live-account acceptance.

## Corrections made while validating

The regression gate exposed stale expectations after the security and email
changes. Tests now expect redacted mail arguments in relaxation-refusal audit
events, recognize `planner_result.withheld`, use actual migration backup paths
and verify preserved noncolliding snapshots. Test typing and the GUI probe's
artifact-list fixture were corrected. The client parity manifest lists the
new email RPCs and accurately distinguishes bespoke client support from generic
RPC availability. The offline narrated email demo now distinguishes an
authorized outbox record from SMTP delivery and retains its unsent draft.

Only the new mail-summary coverage group was added, at its measured 79.31%
floor. Existing coverage floors were not lowered. Twenty-seven groups remain
below the 85% target; passing the ratchet is not attainment of that target.

## Native and model acceptance

The implementation chat owns native GUI changes and isolated background
Accessibility runs. The latest accepted-submission run passed with 92
foreground checks, and the definite pre-submission failure run passed with 91.
Both used no keyboard/mouse events and no foreground activation. Each observed
exactly one fake SMTP attempt, retained the honest outcome after daemon restart
without duplication, and checked transcript paragraph/list/quote/code boundaries.
Their result files are `/tmp/capdep-email-mvp-gui-accepted-v2/result.json` and
`/tmp/capdep-email-mvp-gui-failed-v2/result.json`.

The corrected uncertain-outcome run passed with 110 foreground checks. It
retained the unknown receipt through restart, required deliberate provider
verification, retained original submission history and made no retry. It also
passed transcript readability checks. Earlier failures locating the control
were resolved by distinct background-panel titles; failed runs are not counted
as passes. The final result is
`/tmp/capdep-email-mvp-gui-unknown-v3/result.json`.

The three PASS results, binary hashes, exact-review and recovery Accessibility
snapshots are preserved under [evidence/email-mvp-2026-10-07](evidence/email-mvp-2026-10-07).
Their build manifests explicitly record dirty working trees; differing binary
hashes retain the exact tested native artifact identity. These binaries are not
asserted to be builds of an immutable release tag.

An additional actual app-restart run passed with 97 background checks. The
process reopened its saved conversation and receipt without another SMTP
attempt. DEBUG setup selected a daemon-authorized session via the production
focus method; subsequent checks used native Accessibility. Its PASS and
reopened transcript/receipt snapshots are preserved under
`evidence/email-mvp-2026-10-07/reopen/`.

Independent verification/contract corrections are committed as `a9e4320` and
the initial evidence archive as `09e235e`. The implementation chat subsequently
committed the selected backend as `72a146e` and native UI/acceptance driver as
`b5c92ac`, preserving unrelated working-tree changes.

Before committing, it applied the selected patches to an isolated archive:
180 focused backend tests passed, and the selected Swift snapshot executed 71
tests with one skip and no failures. The corrected mail-summary code, scope
tests and local-model lane matched current source byte for byte. These focused
snapshot checks support the selected implementation; they do not reattribute
the earlier broad working-tree test run to an immutable release. Signed
distribution and full-product release acceptance remain separate.

The initial cached local 8B model lane exposed an invented date filter and
unsupported attachment reference. The implementation chat corrected neutral
recent-mail routing using the trusted latest user request: unrequested
date/query/folder filters are removed while explicit requested filters remain.
Thirty focused scope tests passed; the later mail/SMTP regression aggregate
passed 66 tests. This correction followed the full regression run above.

The actual cached Qwen3-8B-4bit-mlx rerun passed in 65.06 seconds: both synthetic
topics/dates were preserved, no unrequested date filter executed and the human
edited source-bound reply reached fake SMTP. The shipped composition path
starts with a blank draft for human editing; supplemental tool-free AI reply
composition is separately recorded and passed its corpus-specific factual
checks. It is not the shipped default or release authority.

Both the initial failure and successful rerun are preserved under
[evidence/email-mvp-2026-10-07/model](evidence/email-mvp-2026-10-07/model).
Their synthetic records establish one fixed-corpus result, not generalized
model quality or daily-driver promotion. Live-account quality and daily-use
acceptance remain #479 / milestone #54.
