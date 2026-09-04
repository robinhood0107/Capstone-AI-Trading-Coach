"""Exact-31 daily Rule+LSTM materialization before an automation session claim."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import grpc
import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.inference_grpc_server import METHOD_PATH

_MAX_PACKET_BYTES = 512 * 1024
# 장기 추세를 보려면 그만큼 읽어야 한다. V120 이 리더 상한을 400 으로 올렸고 여기서
# 요청하는 값이 실제 조회 깊이를 정한다. 그 종목에 있는 만큼만 돌아오므로 이력이 짧은
# 종목에서도 실패하지 않는다.
_RAW_HISTORY_LIMIT = 260
# 장기 추세 창의 상한과 하한. 상한 200 은 문헌 표준이고(Faber 2007 의 10개월 SMA, Brock·
# Lakonishok·LeBaron 1992 의 1/200 VMA) 하한 20 은 평균이 의미를 갖는 최소 표본이다.
# 종목마다 min(200, 가진 이력) 을 쓰므로 우리 표본에 맞춘 상수를 새로 들이지 않는다.
_MA_TREND_MAX = 200
_MA_TREND_MIN = 20
_WINDOW_SIZE = 20
# MA20 이 생기려면 그 앞에 19세션이 있어야 하고, 모델은 feature 행 _WINDOW_SIZE 개를
# 받는다. 그래서 필요한 최소 이력은 19 + _WINDOW_SIZE 다. 리터럴로 두면 window 나 MA
# 기간이 바뀔 때 조용히 어긋나므로 파생값으로 둔다 - 다른 종목·다른 설정에도 그대로
# 맞아야 한다.
_MA_LONG_WARMUP = 19
_MIN_HISTORY = _MA_LONG_WARMUP + _WINDOW_SIZE


class DailyInferenceError(RuntimeError):
    """Daily materialization cannot publish a complete current batch."""


@dataclass(frozen=True, slots=True)
class DailyInferenceResult:
    outcome: str
    target_session: date
    batch_sha256: str | None = None


class DailySignalRepository:
    """Function-only database boundary for daily inference input and publication."""

    def __init__(self, database_dsn: str) -> None:
        try:
            parsed = conninfo_to_dict(database_dsn)
        except psycopg.Error as error:
            raise DailyInferenceError("DAILY_INFERENCE_DSN_INVALID") from error
        if (
            parsed.get("user") != "decision_automation_runtime"
            or parsed.get("host") not in {"postgres", "127.0.0.1", "localhost"}
            or not parsed.get("dbname")
        ):
            raise DailyInferenceError("DAILY_INFERENCE_DSN_ROLE_INVALID")
        self._database_dsn = database_dsn

    def context(self, target_session: date) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select p1_read_daily_inference_context_v1(%s)", (target_session,))
            row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        try:
            value = json.loads(str(row[0]))
        except json.JSONDecodeError as error:
            raise DailyInferenceError("DAILY_INFERENCE_CONTEXT_INVALID") from error
        if not isinstance(value, dict):
            raise DailyInferenceError("DAILY_INFERENCE_CONTEXT_INVALID")
        return cast(dict[str, Any], value)

    def history(self, symbol: str, target_session: date) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from p1_read_automation_atr_bars_v1(%s,%s,%s)",
                (symbol, target_session, _RAW_HISTORY_LIMIT),
            )
            rows = cursor.fetchall()
        return [
            {
                "close": int(row[5]),
                "high": int(row[3]),
                "low": int(row[4]),
                "open": int(row[2]),
                "sessionDate": row[1].isoformat(),
                "volume": int(row[6]),
            }
            for row in rows
        ]

    def commit(self, packet: dict[str, Any]) -> tuple[str, str]:
        packet_bytes = canonical_json_bytes(packet)
        if len(packet_bytes) > _MAX_PACKET_BYTES:
            raise DailyInferenceError("DAILY_INFERENCE_PACKET_TOO_LARGE")
        packet_sha = _sha(packet_bytes)
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select outcome,batch_sha256 from p1_commit_daily_signal_batch_v1(%s,%s)",
                    (packet_bytes.decode("utf-8"), packet_sha),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DailyInferenceError("DAILY_INFERENCE_COMMIT_EMPTY")
                connection.commit()
        except psycopg.Error as error:
            raise DailyInferenceError("DAILY_INFERENCE_COMMIT_FAILED") from error
        return str(row[0]), str(row[1])

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._database_dsn, connect_timeout=2, autocommit=False)


class ReturnInferenceClient:
    """One loopback request plus at most one identical retry."""

    def __init__(self, target: str, shared_secret: str) -> None:
        if not target.startswith(("127.0.0.1:", "[::1]:")) or len(shared_secret) < 32:
            raise DailyInferenceError("DAILY_INFERENCE_LOOPBACK_CONFIG_INVALID")
        self._channel = grpc.insecure_channel(target)
        self._call = self._channel.unary_unary(
            METHOD_PATH,
            request_serializer=lambda value: value,
            response_deserializer=lambda value: value,
        )
        self._metadata = (("x-return-inference-auth", shared_secret),)

    def infer(self, request_bytes: bytes) -> bytes:
        for attempt in range(2):
            try:
                return self._call(request_bytes, timeout=5, metadata=self._metadata)
            except grpc.RpcError as error:
                if attempt == 1:
                    raise DailyInferenceError("DAILY_INFERENCE_MODEL_FAILED") from error
        raise AssertionError("bounded inference retry loop fell through")

    def close(self) -> None:
        self._channel.close()


class DailyInferenceService:
    """Build exact-31 features, call existing inference, and atomically publish Rule+LSTM."""

    def __init__(self, repository: DailySignalRepository, client: ReturnInferenceClient) -> None:
        self._repository = repository
        self._client = client

    @classmethod
    def from_environment(cls) -> DailyInferenceService:
        dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
        target = os.environ.get("RETURN_INFERENCE_GRPC_TARGET", "127.0.0.1:50057").strip()
        secret = os.environ.get("RETURN_INFERENCE_GRPC_SHARED_SECRET", "").strip()
        return cls(DailySignalRepository(dsn), ReturnInferenceClient(target, secret))

    def ensure_daily_signals(self, target_session: date) -> DailyInferenceResult:
        context = self._repository.context(target_session)
        if context is None:
            return DailyInferenceResult("MODEL_OR_MARKET_DATA_UNAVAILABLE", target_session)
        if context.get("outcome") == "REPLAYED":
            batch = context.get("batchSha256")
            return DailyInferenceResult(
                "REPLAYED",
                target_session,
                str(batch) if isinstance(batch, str) else None,
            )
        symbols = context.get("symbols")
        if (
            not isinstance(symbols, list)
            or len(symbols) != 31
            or len(set(symbols)) != 31
            or symbols.count("132030") != 1
            or not all(isinstance(item, str) for item in symbols)
        ):
            raise DailyInferenceError("DAILY_INFERENCE_UNIVERSE_INVALID")
        feature_rows: list[dict[str, Any]] = []
        rule_rows: list[dict[str, Any]] = []
        source_session = str(context.get("sourceSession"))
        for symbol in cast(list[str], symbols):
            history = self._repository.history(symbol, target_session)
            try:
                features, rule_signal = _features_and_rule(history, source_session)
            except DailyInferenceError as error:
                # 종목을 붙여 다시 던진다. 31종목 루프 안에서 실패하면 어느 종목이 막혔는지가
                # 곧 대처 방법이다 - 신규 상장인지, 수집이 밀린 것인지.
                raise DailyInferenceError(f"{error} symbol={symbol}") from None
            feature_rows.append(
                {
                    "currentClose": features[-1][FEATURE_ORDER.index("raw_close")],
                    "features": features,
                    "sessionDate": target_session.isoformat(),
                    "symbol": symbol,
                }
            )
            rule_rows.append(
                {
                    "expectedReturn": 0.0,
                    "producer": "RULE_BASELINE",
                    "signal": rule_signal,
                    "symbol": symbol,
                }
            )
        request = {
            "artifactId": context["artifactId"],
            "bundleSha256": context["bundleSha256"],
            "contractId": "p1-return-inference-request.v1",
            "rows": feature_rows,
            "sessionDate": target_session.isoformat(),
        }
        request_bytes = canonical_json_bytes(request)
        response_bytes = self._client.infer(request_bytes)
        response = _validated_response(response_bytes, request, cast(list[str], symbols))
        lstm_rows = [
            {
                "expectedReturn": item["expectedReturn"],
                "producer": "LSTM",
                "signal": item["signal"],
                "symbol": item["symbol"],
            }
            for item in cast(list[dict[str, Any]], response["predictions"])
        ]
        signals = sorted(
            (*lstm_rows, *rule_rows), key=lambda item: (item["producer"], item["symbol"])
        )
        packet = {
            "artifactId": context["artifactId"],
            "bundleSha256": context["bundleSha256"],
            "contractId": "p1-return-daily-signal-batch.v1",
            "inferenceRequestSha256": _sha(request_bytes),
            "inferenceResponseSha256": _sha(response_bytes),
            "marketManifestSha256": context["marketManifestSha256"],
            "modelSha256": context["modelSha256"],
            "signals": signals,
            "sourceSession": source_session,
            "targetSession": target_session.isoformat(),
        }
        outcome, batch_sha = self._repository.commit(packet)
        return DailyInferenceResult(outcome, target_session, batch_sha)

    def close(self) -> None:
        self._client.close()


def _features_and_rule(
    history: list[dict[str, Any]], expected_source_session: str
) -> tuple[list[list[float]], str]:
    # 이력이 짧은 것과 소스 세션이 어긋난 것은 원인도 대처도 다르므로 따로 말한다.
    # 앞은 시간이 지나면 해결되고, 뒤는 시장데이터 수집이 밀린 것이다.
    if len(history) < _MIN_HISTORY:
        raise DailyInferenceError(
            f"DAILY_INFERENCE_HISTORY_TOO_SHORT sessions={len(history)} required={_MIN_HISTORY}"
        )
    observed_source = history[-1].get("sessionDate")
    if observed_source != expected_source_session:
        raise DailyInferenceError(
            "DAILY_INFERENCE_HISTORY_INCOMPLETE "
            f"observedSource={observed_source} expectedSource={expected_source_session}"
        )
    closes = [float(item["close"]) for item in history]
    feature_rows: list[list[float]] = []
    ma5_values: list[float | None] = []
    ma20_values: list[float | None] = []
    rsi_values: list[float | None] = []
    for index, item in enumerate(history):
        ma5 = sum(closes[index - 4 : index + 1]) / 5 if index >= 4 else None
        ma20 = sum(closes[index - 19 : index + 1]) / 20 if index >= 19 else None
        rsi = _rsi14(closes, index)
        ma5_values.append(ma5)
        ma20_values.append(ma20)
        rsi_values.append(rsi)
        if index < 19 or rsi is None:
            continue
        values = [
            float(item["open"]),
            float(item["high"]),
            float(item["low"]),
            closes[index],
            float(item["volume"]),
            closes[index] / closes[index - 1] - 1.0,
            cast(float, ma5),
            cast(float, ma20),
            rsi,
        ]
        if not all(math.isfinite(value) for value in values):
            raise DailyInferenceError("DAILY_INFERENCE_FEATURE_NON_FINITE")
        feature_rows.append(values)
    if len(feature_rows) < _WINDOW_SIZE:
        raise DailyInferenceError("DAILY_INFERENCE_FEATURE_WINDOW_INCOMPLETE")
    latest = len(history) - 1
    ma5 = cast(float, ma5_values[latest])
    ma20 = cast(float, ma20_values[latest])
    rsi = cast(float, rsi_values[latest])
    # 그 종목이 보여줄 수 있는 최대 이력으로 장기 추세를 만든다. 200세션이 있으면 200을
    # 쓰고, 상장 이력이 그보다 짧으면 있는 만큼으로 계산한다. _MIN_HISTORY(39)가 이미
    # _MA_TREND_MIN(20)보다 크므로 여기서 새 최소 이력 요건이 생기지 않는다 - 즉 이 변경으로
    # 배제되는 종목이 없다.
    trend_window = min(_MA_TREND_MAX, len(closes))
    if trend_window < _MA_TREND_MIN:
        raise DailyInferenceError(
            f"DAILY_INFERENCE_TREND_WINDOW_TOO_SHORT sessions={len(closes)}"
            f" required={_MA_TREND_MIN}"
        )
    ma_trend = sum(closes[-trend_window:]) / trend_window
    close = closes[latest]
    # 추세를 상태로 판정한다. 교차가 일어난 그 날만 보는 것이 아니다.
    #
    # 이전 판정은 prior_ma5 <= prior_ma20 을 함께 요구해 "그 날 정확히 골든크로스"만 BUY 로
    # 인정했다. 교차는 종목당 연 5~12회뿐이라 31종목이어도 하루 평균 1종목이고, ADR-039 의
    # 2-of-2 합의를 곱하면 평균 10거래일에 한 번만 매수 후보가 생겼다.
    #
    # 실측 - exact-31 의 26년 PIT 패널(158,336관측 / 6,646세션 / 2000-02~2026-09)에서
    # Fama-MacBeth (1973) 로 쟀다. 날짜별로 "신호 종목 등가중 - 유니버스 등가중"을 만들고 그
    # 시계열을 Newey-West (1987) 5래그로 검정한다. 같은 날 종목은 시장 성분을 공유해 강하게
    # 상관되므로 (날짜,종목) 관측을 pooled 로 검정하면 t 가 크게 부풀려진다. 그리고 시장
    # 성분이 매일 상쇄되므로 폭등장/급락장 편향이 구조적으로 없다.
    #
    #   event  BUY 0.73종목/일  후보 0인 날 55.8%  초과 -0.0751%p/일  NW t -2.02
    #   state  BUY 9.17종목/일  후보 0인 날  1.9%  초과 -0.0143%p/일  NW t -1.11
    #
    # event 는 10년 구간 셋 모두와 하락 연도 6개 중 5개에서 음수로 일관되게 나쁘다. state 는
    # 다중검정 보정(시행 4회, |t|>2.5) 후 0과 구분되지 않는다 - 알파도 해도 없는 중립 필터다.
    # 그것이 문헌이 추세 필터에 기대하는 역할이다(알파가 아니라 위험 통제).
    #
    # 후보 0인 날이 1.9% 남는 것은 결함이 아니다. 유니버스 전 종목이 하락 추세인 날 매수
    # 후보가 없는 것이 정상이다. 반대로 event 의 55.8% 는 규칙이 사건을 요구해서 생긴 것이고
    # 짧은 표본의 산물이 아니다 - 26년에서도 같은 값이다.
    #
    # 밴드 변형(ma5 > ma20*1.01)은 채택하지 않았다. 26년 수치가 state 와 구분되지 않는데
    # (-0.0149%p, t -1.00) 우리 표본에 맞춘 상수를 하나 더 들여와 범용성만 떨어진다.
    #
    # 상태 판정이 문헌 표준이기도 하다 - Brock, Lakonishok & LeBaron (1992, JoF) 의 이동평균
    # 규칙은 빠른 MA 가 느린 MA 위에 있는 상태(VMA)로 판정하고, 교차 직후만 보는 것은 그들의
    # "fixed" 변형이다. Faber (2007) 도 price > SMA 상태로 보유를 정한다.
    #
    # 합의는 그대로 둔다. 앙상블이 정확도를 올리려면 각 투표자가 50% 보다 나아야 하는데
    # (Condorcet; Hansen & Salamon 1990) LSTM dir_acc 0.498, rule 승률 47% 로 둘 다 아래다.
    # 2-of-2 의 역할은 통계가 아니라 한 생산자의 오작동을 막는 fail-safe 다.
    #
    # 여기서 다시 바꾼 이유 - 한 달 안쪽 추세가 아니라 전체 동향을 보게 한다.
    #
    # MA5/MA20 은 한 달 안쪽만 본다. 문헌의 추세 정의는 더 길다 - Faber (2007) 는 10개월(약
    # 200세션) SMA 대비 가격 상태로 보유를 정하고, Brock·Lakonishok·LeBaron (1992) 의 VMA
    # 규칙 집합에는 1/150 · 1/200 이 들어 있다.
    #
    # 같은 26년 패널에서 사전 확정 변형 7개를 쟀다(시행 7회 -> Bonferroni |t|>2.50).
    #
    #   state (직전)   BUY  9.17종목/일  0인날 1.9%  초과 -0.0143%p  t -1.11
    #   trend_only     BUY 10.84종목/일  0인날 1.4%  초과 -0.0010%p  t -0.08
    #   state_trend    BUY  6.12종목/일  0인날 4.3%  초과 -0.0124%p  t -0.79
    #   state_matrend  BUY  5.64종목/일  0인날 5.5%  초과 -0.0103%p  t -0.63
    #
    # 임계를 넘는 변형은 없다 - 어느 추세 규칙에도 알파가 없고 그것이 문헌이 예측하는 바다.
    # 그래서 채택은 사전 확정한 다른 축으로 가르고 trend_only 가 넷 모두에서 최선이다.
    # 후보가 가장 많고(10.84/일), 후보 0인 날이 가장 적고(1.4%), 드리프트가 사실상 0 이며
    # (-0.0010%p, 직전의 1/14), 최근 20년 두 구간 모두 양수인 유일한 변형이다
    # (-0.0218 / +0.0040 / +0.0063). 하락 연도도 6개 중 4개에서 양수다(직전은 2개).
    #
    # MA5/MA20 조건을 함께 걸면(state_trend) 후보가 6.12/일로 줄고 초과수익은 오히려 나빠진다.
    # 두 필터를 곱해서 얻는 것이 없으므로 곱하지 않는다.
    #
    # 종목 커버리지 - 31종목 전부가 신호를 내고 발생률 최소 37.6% / 중위 46.1% / 최대 54.8%,
    # 0% 종목이 없다. 2021~2022 상장 종목도 워밍업 22세션만 잃는다.
    #
    # MA5/MA20/RSI 는 계약된 모델 feature 로 그대로 남는다. 바뀌는 것은 RULE_BASELINE 의
    # 판정 하나뿐이고 텐서 집합과 feature 순서는 건드리지 않는다.
    signal = (
        "BUY"
        if close > ma_trend and rsi < 70
        else "SELL"
        if close < ma_trend and rsi > 30
        else "HOLD"
    )
    return feature_rows[-_WINDOW_SIZE:], signal


def _rsi14(closes: list[float], index: int) -> float | None:
    if index < 14:
        return None
    deltas = [closes[position] - closes[position - 1] for position in range(index - 13, index + 1)]
    average_gain = sum(max(value, 0.0) for value in deltas) / 14
    average_loss = sum(max(-value, 0.0) for value in deltas) / 14
    if average_gain == average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _validated_response(
    content: bytes, request: dict[str, Any], symbols: list[str]
) -> dict[str, Any]:
    if not content or len(content) > 64 * 1024:
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_SIZE_INVALID")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_INVALID") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_INVALID")
    if set(value) != {
        "artifactId",
        "bundleSha256",
        "contractId",
        "orderAuthority",
        "predictions",
        "providerCalls",
        "sessionDate",
    }:
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_FIELDS_INVALID")
    if (
        value.get("artifactId") != request["artifactId"]
        or value.get("bundleSha256") != request["bundleSha256"]
        or value.get("sessionDate") != request["sessionDate"]
        or value.get("contractId") != "p1-return-inference-response.v1"
        or value.get("providerCalls") != 0
        or value.get("orderAuthority") != "NONE"
    ):
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_BINDING_INVALID")
    predictions = value.get("predictions")
    if not isinstance(predictions, list) or len(predictions) != 31:
        raise DailyInferenceError("DAILY_INFERENCE_RESPONSE_EXACT31_INVALID")
    observed: set[str] = set()
    for item in predictions:
        if not isinstance(item, dict) or set(item) != {
            "expectedReturn",
            "forecastClose",
            "signal",
            "symbol",
        }:
            raise DailyInferenceError("DAILY_INFERENCE_PREDICTION_FIELDS_INVALID")
        symbol = item.get("symbol")
        if (
            symbol not in symbols
            or symbol in observed
            or item.get("signal") not in {"BUY", "HOLD", "SELL"}
            or not _finite(item.get("expectedReturn"))
            or not _finite(item.get("forecastClose"))
        ):
            raise DailyInferenceError("DAILY_INFERENCE_PREDICTION_INVALID")
        observed.add(cast(str, symbol))
    return cast(dict[str, Any], value)


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
