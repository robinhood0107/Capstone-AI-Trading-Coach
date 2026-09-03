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
_RAW_HISTORY_LIMIT = 40
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
    signal = "BUY" if ma5 > ma20 and rsi < 70 else "SELL" if ma5 < ma20 and rsi > 30 else "HOLD"
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
