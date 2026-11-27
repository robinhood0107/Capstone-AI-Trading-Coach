# Issue 231 controlled replay protocol

Refs #225 and #231. Protocol `p1-issue-231-synthetic-replay.v1`, frozen before running the paired comparisons.

## Evidence at the dispatch baseline

- The historical owner projection used 104 context sessions for 31 assets. V127 set the visible window to 2026-08-18 through 2026-09-03, or 13 XKRX sessions. V154 derives the end from available data and preserves 13 as a minimum. Baseline follows the rule signal. Guide changes both entry and exit conditions using LSTM. Strict applies its gates to Guide's planned trades. The original counts therefore cannot supply a policy confusion matrix for a common Baseline/Guide candidate set. [Original 13-session results](../금융공학_공식_및_자동매매_로직_설명서.md#102-세-가지-시나리오-실측)
- The published Team B manifest is `p1-return-engine-artifact-manifest.v3` (Git seed 5087ffacf, 2026-09-03). Each signal parquet holds 31 rows for 2026-09-01 only. The equity log covers 104 sessions from 2026-04-02 through 2026-09-02. The backtest summary is `p1-return-backtest-result.v2` and contains one Guide arm. These files supply neither matched daily news/veto inputs nor independently labeled policy outcomes for the 13-session period. [Git seed](../../deploy/p1/seed/team-b/p1-return-engine-manifest.v3.json)
- The older long-run experiment reports 22 walk-forward test folds for 2005–2026. The 2026-09-03 record used 31 assets, 130,752 predictions, 35 bps in turnover costs and a point-in-time eligibility filter within today's fixed universe. Its published verdict was `BELOW_BASELINE`. Protocol v3, registered 2026-09-09 KST, refers to a locally preserved history hash and a different set of model arms. Both are historical research references. [Version 1 report](../../workspaces/decision-platform/research/p1-return-profit-verification/reports/profit-verification.v1.json) · [Version 3 protocol](../../workspaces/decision-platform/research/p1-return-profit-verification/reports/evaluation-protocol.v3.json)

## Frozen experiment

Use the corrected XKRX calendar pinned to exchange-calendars 4.13.2, 2026-08-18 through 2026-09-03 inclusive. Six fictitious instruments are eligible on every date. All candidate inputs carry the preceding XKRX close as their snapshot date. The first snapshot is 2026-08-14. The decision stage receives frozen BUY/HOLD/SELL and adverse-news inputs. The execution stage uses the synthetic close of the entry session and the synthetic close of the following session for exit. The final date supports exits only. There are 12 × 6 = 72 eligible candidate opportunities. Price basis is synthetic raw close in KRW, with no splits or dividends. Each trade plans 10 shares from a portfolio initialized at KRW 10,000,000 per arm. The replay charges 17.5 bps on each executed leg. Slippage, additional taxes and market impact are zero by construction.

| Fictitious instrument | Initial close (KRW) | Close change per session (KRW) | Rule | LSTM stand-in | Adverse news stand-in | Reference violation |
| --- | ---: | ---: | --- | --- | --- | --- | --- |
| SYN01 | 20,000 | +7 | BUY | BUY | Yes | No |
| SYN02 | 27,000 | −3 | BUY | SELL | No | No |
| SYN03 | 30,000 | −4 | BUY | HOLD | No | No |
| SYN04 | 32,500 | +5 | BUY | BUY | No | Yes |
| SYN05 | 37,500 | −6 | BUY | SELL | Yes | Yes |
| SYN06 | 22,000 | +2 | HOLD | BUY | No | No |

Reference labels encode the fixture's intended amount rule, which blocks amounts strictly above KRW 300,000. Exactly KRW 300,000 passes. Labels are declared in the fixture independently of replay outputs. All other policy limits are identical and chosen so they do not bind: 20% per asset and gold weight, −100% daily loss and drawdown, six entries per day, and the existing holdings and cash guards. The fixture has only non-gold instruments and holds each trade for one session. Each comparison starts with fresh cash and holdings.

- **Policy comparison** uses Rule BUY candidates in all arms. Change only the single-order cap from KRW 300,000 to 360,000 or 240,000.
- **Signal comparison** uses the KRW 300,000 cap in all arms. Compare Rule BUY against LSTM BUY, Rule BUY with an adverse-news filter, and Rule BUY with a veto on a frozen LSTM SELL. Every arm is paired directly with Rule BUY. The veto approximates the Guide entry condition. A common timed exit keeps the exit condition out of the comparison.

## Acceptance and scope

For each arm report the eligible universe, submitted candidates, expected-violation candidates and expected-normal candidates. Count missed blocks over expected violations, and excessive blocks over expected normal candidates. The denominator changes after signal filtering. Record symmetric differences in candidates and in simulated order legs by session, instrument, side and quantity relative to Rule BUY at KRW 300,000. Report modeled cost and terminal change before annualization. Confirm deterministic output, identical candidates across policy arms, at least one missed violation under the looser policy, at least one false block under the tighter policy, zero classification errors at the reference limit, and different candidate and order sets under fixed policy. Check the boundary trade passes at the reference cap. Repeated rows test the replay plumbing with a known oracle. The counts support no population inference.

Run serially with one numerical-library thread, the lockfile in frozen offline mode, and a 1 GiB process virtual-memory limit. The fixture has no external inputs. All orders in the output are simulated legs. This experiment measures the synthetic behavior of a single replay limit and the effect of predefined input changes. It does not estimate actual news or LSTM quality, full RiskEngine safety, live trading results, or long-run returns.
