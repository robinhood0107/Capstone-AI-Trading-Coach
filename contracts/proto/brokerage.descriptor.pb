
≠
brokerage.protocapstone.decision.v1"å
SubmitMockCashOrderRequest

request_id (	R	requestId
order_id (	RorderId

account_id (	R	accountId
symbol (	Rsymbol
side (	Rside

order_type (	R	orderType
quantity (Rquantity.
estimated_price_krw (RestimatedPriceKrw"¡
SubmitMockCashOrderResponse
order_id (	RorderId
accepted (Raccepted5
provider_order_ref_hash (	RproviderOrderRefHash
tr_id (	RtrId
received_at (	R
receivedAt"u
CancelMockCashOrderRequest

request_id (	R	requestId
order_id (	RorderId

account_id (	R	accountId"q
CancelMockCashOrderResponse
order_id (	RorderId
status (	Rstatus
received_at (	R
receivedAt"U
GetMockBalanceRequest

request_id (	R	requestId

account_id (	R	accountId"ö
MockBalancePosition
symbol (	Rsymbol
quantity (Rquantity(
market_value_krw (RmarketValueKrw%
is_gold_etf_etn (RisGoldEtfEtn"À
GetMockBalanceResponse

account_id (	R	accountId
cash_krw (RcashKrw0
portfolio_equity_krw (RportfolioEquityKrw4
margin_requirement_krw (RmarginRequirementKrwG
	positions (2).capstone.decision.v1.MockBalancePositionR	positions
observed_at (	R
observedAt%
source_version (	RsourceVersion"ù
GetMockBuyableRequest

request_id (	R	requestId

account_id (	R	accountId
symbol (	Rsymbol.
estimated_price_krw (RestimatedPriceKrw"ª
GetMockBuyableResponse

account_id (	R	accountId
symbol (	Rsymbol.
estimated_price_krw (RestimatedPriceKrw)
buyable_quantity (RbuyableQuantity,
buyable_amount_krw (RbuyableAmountKrw
cash_krw (RcashKrw
observed_at (	R
observedAt%
source_version (	RsourceVersion2‰
BrokerageServicez
SubmitMockCashOrder0.capstone.decision.v1.SubmitMockCashOrderRequest1.capstone.decision.v1.SubmitMockCashOrderResponsez
CancelMockCashOrder0.capstone.decision.v1.CancelMockCashOrderRequest1.capstone.decision.v1.CancelMockCashOrderResponsek
GetMockBalance+.capstone.decision.v1.GetMockBalanceRequest,.capstone.decision.v1.GetMockBalanceResponsek
GetMockBuyable+.capstone.decision.v1.GetMockBuyableRequest,.capstone.decision.v1.GetMockBuyableResponseB8
!com.capstone.decision.contract.v1BBrokerageContractPbproto3