from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class BlackScholesRequest(_message.Message):
    __slots__ = ("option_right", "spot", "strike", "time_to_maturity_years", "volatility", "risk_free_rate", "dividend_yield")
    OPTION_RIGHT_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    TIME_TO_MATURITY_YEARS_FIELD_NUMBER: _ClassVar[int]
    VOLATILITY_FIELD_NUMBER: _ClassVar[int]
    RISK_FREE_RATE_FIELD_NUMBER: _ClassVar[int]
    DIVIDEND_YIELD_FIELD_NUMBER: _ClassVar[int]
    option_right: str
    spot: float
    strike: float
    time_to_maturity_years: float
    volatility: float
    risk_free_rate: float
    dividend_yield: float
    def __init__(self, option_right: _Optional[str] = ..., spot: _Optional[float] = ..., strike: _Optional[float] = ..., time_to_maturity_years: _Optional[float] = ..., volatility: _Optional[float] = ..., risk_free_rate: _Optional[float] = ..., dividend_yield: _Optional[float] = ...) -> None: ...

class BlackScholesResponse(_message.Message):
    __slots__ = ("discounted_value",)
    DISCOUNTED_VALUE_FIELD_NUMBER: _ClassVar[int]
    discounted_value: float
    def __init__(self, discounted_value: _Optional[float] = ...) -> None: ...

class GreeksRequest(_message.Message):
    __slots__ = ("option_right", "spot", "strike", "time_to_maturity_years", "volatility", "risk_free_rate", "dividend_yield")
    OPTION_RIGHT_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    TIME_TO_MATURITY_YEARS_FIELD_NUMBER: _ClassVar[int]
    VOLATILITY_FIELD_NUMBER: _ClassVar[int]
    RISK_FREE_RATE_FIELD_NUMBER: _ClassVar[int]
    DIVIDEND_YIELD_FIELD_NUMBER: _ClassVar[int]
    option_right: str
    spot: float
    strike: float
    time_to_maturity_years: float
    volatility: float
    risk_free_rate: float
    dividend_yield: float
    def __init__(self, option_right: _Optional[str] = ..., spot: _Optional[float] = ..., strike: _Optional[float] = ..., time_to_maturity_years: _Optional[float] = ..., volatility: _Optional[float] = ..., risk_free_rate: _Optional[float] = ..., dividend_yield: _Optional[float] = ...) -> None: ...

class GreeksResponse(_message.Message):
    __slots__ = ("delta", "gamma", "vega_per_unit_volatility", "vega_per_vol_point", "calendar_theta_per_year", "calendar_theta_per_day", "rho_per_unit_rate", "rho_per_rate_point")
    DELTA_FIELD_NUMBER: _ClassVar[int]
    GAMMA_FIELD_NUMBER: _ClassVar[int]
    VEGA_PER_UNIT_VOLATILITY_FIELD_NUMBER: _ClassVar[int]
    VEGA_PER_VOL_POINT_FIELD_NUMBER: _ClassVar[int]
    CALENDAR_THETA_PER_YEAR_FIELD_NUMBER: _ClassVar[int]
    CALENDAR_THETA_PER_DAY_FIELD_NUMBER: _ClassVar[int]
    RHO_PER_UNIT_RATE_FIELD_NUMBER: _ClassVar[int]
    RHO_PER_RATE_POINT_FIELD_NUMBER: _ClassVar[int]
    delta: float
    gamma: float
    vega_per_unit_volatility: float
    vega_per_vol_point: float
    calendar_theta_per_year: float
    calendar_theta_per_day: float
    rho_per_unit_rate: float
    rho_per_rate_point: float
    def __init__(self, delta: _Optional[float] = ..., gamma: _Optional[float] = ..., vega_per_unit_volatility: _Optional[float] = ..., vega_per_vol_point: _Optional[float] = ..., calendar_theta_per_year: _Optional[float] = ..., calendar_theta_per_day: _Optional[float] = ..., rho_per_unit_rate: _Optional[float] = ..., rho_per_rate_point: _Optional[float] = ...) -> None: ...

class ImpliedVolatilityRequest(_message.Message):
    __slots__ = ("option_right", "spot", "strike", "time_to_maturity_years", "risk_free_rate", "dividend_yield", "market_price", "max_iterations")
    OPTION_RIGHT_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    TIME_TO_MATURITY_YEARS_FIELD_NUMBER: _ClassVar[int]
    RISK_FREE_RATE_FIELD_NUMBER: _ClassVar[int]
    DIVIDEND_YIELD_FIELD_NUMBER: _ClassVar[int]
    MARKET_PRICE_FIELD_NUMBER: _ClassVar[int]
    MAX_ITERATIONS_FIELD_NUMBER: _ClassVar[int]
    option_right: str
    spot: float
    strike: float
    time_to_maturity_years: float
    risk_free_rate: float
    dividend_yield: float
    market_price: float
    max_iterations: int
    def __init__(self, option_right: _Optional[str] = ..., spot: _Optional[float] = ..., strike: _Optional[float] = ..., time_to_maturity_years: _Optional[float] = ..., risk_free_rate: _Optional[float] = ..., dividend_yield: _Optional[float] = ..., market_price: _Optional[float] = ..., max_iterations: _Optional[int] = ...) -> None: ...

class ImpliedVolatilityResponse(_message.Message):
    __slots__ = ("implied_volatility",)
    IMPLIED_VOLATILITY_FIELD_NUMBER: _ClassVar[int]
    implied_volatility: float
    def __init__(self, implied_volatility: _Optional[float] = ...) -> None: ...
