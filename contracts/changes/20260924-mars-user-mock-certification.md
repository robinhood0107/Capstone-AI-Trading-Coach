# MARS owner-bound KIS_MOCK certification

The full product adds `POST /api/v1/brokerage/mock/credential/certify`. It accepts no
body or query fields and derives owner, opaque account ID, and credential revision from
the authenticated user's current row. Internal gRPC carries the sealed envelope; the
provider process rechecks owner/account/revision and accepts only `CONNECTED` credentials.

The certification code fixes the test to one `005930` KRX limit BUY share at the current
lower limit. During an XKRX session from 09:10 through 15:00 KST it reads the lower limit,
checks buyable cash, submits once, requests full cancellation once, then checks the exact
execution, open-order result, and unchanged pre/post balance digest. The browser requires
an explicit confirmation and warns that a lower-limit order could fill. No request field
can alter symbol, side, quantity, price, or brokerage mode. PASS requires exactly one quote,
seven brokerage requests, at most one token request, cancelled-unfilled execution, no
remaining open order, and equal balance digests.

Only a PASS receipt bound to the owner's opaque account and current credential revision
changes `user_broker_credentials.credential_state` to `CERTIFIED`. The certification
attempt record contains opaque attempt/account IDs, status, receipt digest, session date,
safe failure code, and bounded call counts; raw account number, KIS order number, response
body, and credentials are not stored. Credential rotation/deletion is refused while an
attempt is running or has uncertain provider outcome. Recovery reuses the same attempt and
deterministic encrypted Redis order reference, so it cannot submit a duplicate test order.
If the provider outcome remains uncertain, the authenticated owner can confirm they checked
and resolved the test order in KIS with `POST
/api/v1/brokerage/mock/credential/certify/recovery-confirm`. This clears only the recovery
lock; it does not certify the credential or arm automation.
KIS_LIVE is not exposed.
