
£
brokerage.protocapstone.decision.v1"˜
BoundMockCredentialEnvelope"
owner_user_id (	RownerUserId

account_id (	R	accountId
revision (Rrevision)
credential_state (	RcredentialState
kek_version (	R
kekVersion

wrap_nonce (R	wrapNonce
wrapped_dek (R
wrappedDek
wrap_tag (RwrapTag#
payload_nonce	 (RpayloadNonce-
payload_ciphertext
 (RpayloadCiphertext
payload_tag (R
payloadTag"ß
SubmitMockCashOrderRequest

request_id (	R	requestId
order_id (	RorderId

account_id (	R	accountId
symbol (	Rsymbol
side (	Rside

order_type (	R	orderType
quantity (Rquantity.
estimated_price_krw (RestimatedPriceKrwQ

credential	 (21.capstone.decision.v1.BoundMockCredentialEnvelopeR
credential"Á
SubmitMockCashOrderResponse
order_id (	RorderId
accepted (Raccepted5
provider_order_ref_hash (	RproviderOrderRefHash
tr_id (	RtrId
received_at (	R
receivedAt"È
CancelMockCashOrderRequest

request_id (	R	requestId
order_id (	RorderId

account_id (	R	accountIdQ

credential (21.capstone.decision.v1.BoundMockCredentialEnvelopeR
credential"q
CancelMockCashOrderResponse
order_id (	RorderId
status (	Rstatus
received_at (	R
receivedAt"¨
GetMockBalanceRequest

request_id (	R	requestId

account_id (	R	accountIdQ

credential (21.capstone.decision.v1.BoundMockCredentialEnvelopeR
credential"š
MockBalancePosition
symbol (	Rsymbol
quantity (Rquantity(
market_value_krw (RmarketValueKrw%
is_gold_etf_etn (RisGoldEtfEtn"Ë
GetMockBalanceResponse

account_id (	R	accountId
cash_krw (RcashKrw0
portfolio_equity_krw (RportfolioEquityKrw4
margin_requirement_krw (RmarginRequirementKrwG
	positions (2).capstone.decision.v1.MockBalancePositionR	positions
observed_at (	R
observedAt%
source_version (	RsourceVersion"ð
GetMockBuyableRequest

request_id (	R	requestId

account_id (	R	accountId
symbol (	Rsymbol.
estimated_price_krw (RestimatedPriceKrwQ

credential (21.capstone.decision.v1.BoundMockCredentialEnvelopeR
credential"»
GetMockBuyableResponse

account_id (	R	accountId
symbol (	Rsymbol.
estimated_price_krw (RestimatedPriceKrw)
buyable_quantity (RbuyableQuantity,
buyable_amount_krw (RbuyableAmountKrw
cash_krw (RcashKrw
observed_at (	R
observedAt%
source_version (	RsourceVersion"®
VerifyMockConnectionRequest

request_id (	R	requestId

account_id (	R	accountIdQ

credential (21.capstone.decision.v1.BoundMockCredentialEnvelopeR
credential"[
VerifyMockConnectionResponse

account_id (	R	accountId
	connected (R	connected2ã
BrokerageServicez
SubmitMockCashOrder0.capstone.decision.v1.SubmitMockCashOrderRequest1.capstone.decision.v1.SubmitMockCashOrderResponsez
CancelMockCashOrder0.capstone.decision.v1.CancelMockCashOrderRequest1.capstone.decision.v1.CancelMockCashOrderResponsek
GetMockBalance+.capstone.decision.v1.GetMockBalanceRequest,.capstone.decision.v1.GetMockBalanceResponsek
GetMockBuyable+.capstone.decision.v1.GetMockBuyableRequest,.capstone.decision.v1.GetMockBuyableResponse}
VerifyMockConnection1.capstone.decision.v1.VerifyMockConnectionRequest2.capstone.decision.v1.VerifyMockConnectionResponseB8
!com.capstone.decision.contract.v1BBrokerageContractPbproto3