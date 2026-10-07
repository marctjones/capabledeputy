# Additional background app-restart acceptance

The synthetic accepted-email run passed with 97 foreground checks and no
keyboard/mouse events or app activation. It exercises summary, source selection,
saved subject/body edits, exact review, defer and deny without sending, one
approval/transport call, daemon restart, readable transcript, then an actual
test-app restart restoring the persisted transcript and source-session receipt
without another send. DEBUG restart setup selects an authorized daemon-listed
session via production `focusSession`; subsequent assertions use Accessibility.

Source changes are committed as `72a146e` (backend) and `b5c92ac` (native UI and
background test). This native run used the shared development build identified
by build-manifest.json. A separate clean snapshot containing only the selected
backend/frontend changes passed 71 Swift tests (one skipped, zero failures);
180 focused backend tests passed on the isolated backend patch. This evidence
does not establish live-provider delivery or general local-model quality.

Raw run: `/tmp/capdep-email-mvp-gui-reopen-v2`. These files preserve its result,
binary manifest and final Accessibility snapshots; trailing whitespace only
was normalized in text snapshots. All mail addresses/content are synthetic.
