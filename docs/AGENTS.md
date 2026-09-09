# The agents, and the day they run to

Eight Claude agents, one deterministic routine, and one rule that never bends:
**no agent can ever increase risk.** Every agent may veto, shrink, tighten or
stand aside. None may size up, widen a stop, or open a trade the rules did not
propose. That constraint is what makes a language model safe inside a trading
loop, and it is enforced in code — the Risk Warden rejects any multiplier above
1.0 and journals it as an anomaly.

---

## The daily routine

The day is a fixed sequence of phases keyed to the London open. Phases decide
what is *permitted*; within a phase the engine is still event-driven. This buys
two things a purely reactive design cannot: a predictable number of agent calls
per day (the API bill no longer scales with how noisy the market is), and an
unambiguous answer to "what is the bot allowed to do right now" — computed from
the clock, so a bot restarted at 14:07 lands in exactly the phase it should.

All times are `sessions.timezone` (Europe/London) and follow BST automatically.

| Time | Phase | Who runs | New entries | Manage open |
|---|---|---|---|---|
| 07:30 | **Pre-flight** | Session Supervisor, News Scout, Regime Analyst | — | — |
| 08:00–12:00 | **Hunt** (London) | Strategy → Decision → Devil's Advocate → Warden; Trade Manager | ✅ | ✅ |
| 12:00–13:30 | **Hold** | Trade Manager | ✗ | ✅ |
| 13:30–17:00 | **Hunt** (NY overlap) | as above | ✅ | ✅ |
| 17:00–19:30 | **Wind-down** | Trade Manager, tighten only | ✗ | tighten / exit only |
| 19:30 | **Flatten** | Deterministic code | ✗ | close everything, confirm flat |
| 19:45 | **Debrief** | Day Auditor | — | — |
| 20:00 → 07:30 | **Closed** | nothing | ✗ | ✗ |

Every day ends flat. Nothing is held through the Asian session, where gold's
spread is widest and the bot is not watching, and each day's result stands on
its own — which is what lets the Day Auditor say something precise about it.

The phase clock is `goldbot/routine.py`. It is deterministic, shared by the live
engine and the backtester, and tested at every boundary from both sides.

---

## The eight agents

| # | Agent | Runs | Model / effort | Authority |
|---|---|---|---|---|
| 1 | **Session Supervisor** | Once, pre-flight | Opus 5 / xhigh | Sets the day's size multiplier ≤ 1.0. Can declare a no-trade day. **Cannot raise anything.** |
| 2 | **News Scout** | Pre-flight, then hourly in-session | Opus 5 / high | Advisory. Produces a `NewsBrief`; feeds 1, 4, 5, 6. |
| 3 | **Regime Analyst** | Pre-flight, refreshed every 4h | Opus 5 / high | Risk multiplier ≤ 1.0, permitted setups. **Reduce only.** |
| 4 | **Decision Agent** | Per candidate signal | Opus 5 / xhigh | `APPROVE` / `VETO` / `REDUCE` ≤ 1.0 |
| 5 | **Devil's Advocate** | Per candidate signal | Opus 5 / xhigh | Argues against the trade. A material objection vetoes or shrinks. Explicitly allowed to say "no material objection". |
| 6 | **Trade Manager** | Event-driven while a position is open | Opus 5 / high | `HOLD` / `TIGHTEN_STOP` / `PARTIAL_CLOSE` / `EXIT_NOW`. **Never widens, never adds.** |
| 7 | **Day Auditor** | Once, debrief | Opus 5 / high | **None.** Reconciles the day against the broker, writes the report. |
| 8 | **Performance Reviewer** | Weekly, Sunday | Opus 5 / xhigh | **None.** Proposes parameter changes; you approve them. |

Effort is `xhigh` on the four whose output is hardest to check after the fact —
the day plan, the two trade decisions, and the weekly proposals. The rest run at
`high`.

### What each one is for

**1. Session Supervisor.** Reads the news brief, the regime view, the account
state and yesterday's audit, and produces one thing: the plan for the day.
*Normal*, *reduced size* (with a multiplier), or *stand aside*. It exists so the
question "should we be trading today at all?" is asked once, deliberately, before
the first signal — not implicitly, by whether the strategy happens to fire.

**2. News Scout.** The agent you asked for first. Runs a server-side web search
over gold, USD and rates coverage and returns a strict `NewsBrief`: bias,
confidence, headlines with timestamps, risk flags, `stand_aside`. Runs out of
band, so a slow search never delays a trade decision. A stale brief is "no
view", never fresh-by-assumption.

**3. Regime Analyst.** Deterministic metrics first (ATR percentile, ADX, H4/D1
swing structure, DXY and yield correlation where the broker offers them), then a
Claude read of what they mean today: trending, ranging, or chaotic; which setups
are permitted; a risk multiplier. Cached for four hours.

**4. Decision Agent.** The main call. Gets the rule signal, the brief, the regime,
upcoming calendar events, spread, exposure and the day's P&L, and returns a
structured verdict with a written rationale that is journalled next to the trade.

**5. Devil's Advocate.** Runs on every proposal the Decision Agent approves and
builds the strongest case against it. Resolution is asymmetric on purpose: an
objection vetoes or shrinks; agreement changes nothing. So no third arbiter is
needed, and a challenger that is *forced* to disagree — which only produces
noise — is avoided by letting it say "no material objection".

**6. Trade Manager.** Runs while a position is open, triggered by events (price
moving ≥ 0.5R, a news flag, a phase change) with `min_interval_s` as the cost
backstop. It is the agent most retail systems lack, and exits are where much of
retail money is lost. Structurally incapable of widening a stop or adding size —
the executor rejects any such instruction and journals it.

**7. Day Auditor.** Runs after the flatten, once the fills have settled. Confirms
the bot is flat and that its records match the broker's. Then: did the plan
survive contact? What did each veto cost or save? Which phase made or lost the
money? It writes the journal's day record and a short Telegram summary. Closing
the loop daily instead of weekly matters on a small account, where a week is a
long time to not notice something.

**8. Performance Reviewer.** The weekly version, over the full journal: per-setup
and per-session expectancy, veto accuracy, drawdown, agent cost, weekly return
against your 3–5% benchmark, and *proposed* parameter changes. It has no write
access to config. Every change is yours to approve.

---

## What is deliberately NOT an agent

The test applied to every component: *does it require judgement over ambiguous
information?* If yes, it's an agent. If it's arithmetic, timing or execution,
it's code — because code gives the same answer every time and can be tested
against hand-computed numbers.

| Component | Why code |
|---|---|
| **Risk Warden** — sizing, every hard veto | Last line of defence on money. 58 tests. An LLM in front of it adds a component that can be talked out of things, plus cost and latency — not safety. |
| **Executor** — orders, retries, filling modes | Retcode-driven, millisecond-sensitive, zero ambiguity. |
| **Routine / session gate** — phase boundaries, flatten times | A known time is a lookup, not a judgement. |
| **Calendar blackouts** — NFP, CPI, FOMC | Same. |
| **Watchdog** — stale ticks, broker rejections, position reconciliation | Must work *especially* when everything else is failing. |
| **Strategy signal generation** | Must be backtestable bar-for-bar. |

You asked whether more agents should be added for safety and risk. The honest
answer is that the real safety gaps in a system like this are watchdog problems
— a stale price feed, a broker rejecting orders, the bot's position list
disagreeing with the broker's — and those want code. The Session Supervisor and
the Day Auditor are the two agent-shaped additions that genuinely help, because
they add judgement at the two moments the day needs it: before, and after.

---

## Adding an agent later

Yes, at any time, and the architecture is built for it. Each agent is:

1. an entry in `config.agents` (model, effort, timeout, cadence, TTL),
2. a module in `goldbot/agents/` with a typed output schema, and
3. a hook into one of the routine's phases.

The authority rule makes this safe: a new agent can only veto, shrink or advise,
so adding one cannot make the system less safe — only slower and more expensive.
Each call costs money, so every agent has to earn its place against the stage
below it (see rollout). If it can't be shown to improve on the system without
it, it comes out.

---

## Fail-closed, always

API error, timeout, malformed output, or a `refusal` stop reason from any agent
means **no trade**. Missing a trade is free. An unreviewed trade is not.

Once `daily_cost_limit_usd` trips, no agent answers, and therefore no new trade
is approved for the rest of the day. That is the safe failure, but it is still
a stop — set the cap with the arithmetic in `config.example.yaml` in front of
you.

---

## Rollout — staged, so results can be attributed

All eight get built. They go live in stages, and the journal records the
counterfactual at every stage so each agent's contribution is measurable.

1. **Stage A** — deterministic core with the routine: strategy, Risk Warden,
   executor, calendar, phase clock, daily flatten. Backtested, then dry-run.
   The baseline.
2. **Stage B** — **Session Supervisor** + **Day Auditor**. The day gains a
   beginning and an end. Cheapest agents to run (two calls a day) and the
   first to give you something to read.
3. **Stage C** — **News Scout** + **Decision Agent** + **Devil's Advocate**.
   Every veto journalled with what the trade would have done.
4. **Stage D** — **Regime Analyst** + **Trade Manager**. Exits compared against
   what the fixed rules would have done.
5. **Stage E** — **Performance Reviewer** weekly. Then the dashboard.

If an agent can't be shown to improve on the stage below it, it is cut. That is
the whole point of the staging.
