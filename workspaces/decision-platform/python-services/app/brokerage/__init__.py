"""S3 Brokerage mock adapters.

기본 경계는 fixture/offline이며, online adapter는 닫힌 KIS_MOCK gate와 bounded operator
approval에서만 사용한다. provider 1회 검증은 별도의 exact packet을 요구하며 KIS_LIVE
실계좌 주문 권한은 이 패키지에 없다.
"""
