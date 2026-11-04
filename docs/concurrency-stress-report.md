# LocaleGate: Repeated Barrier Concurrency Stress Experiment Report

**Document type:** Empirical Verification Report  
**Experiment:** Repeated Barrier-Synchronized Multi-Process Concurrency (P1.7 & Milestone 1.0)  
**Date:** 04 November 2026  
**Total Wall Clock Time:** 121.62s  
**Tested Concurrency Tiers:** 4, 8, 16 workers  
**Repetitions per Tier:** 100 repetitions  
**Total Contending Attempts:** 2,800 independent OS process executions  

---

## 1. Executive Summary & Verification of Invariants

To evaluate transactional isolation under simultaneous barrier-released contention, we executed **100 consecutive repetitions** across **4, 8, and 16 concurrent operating system processes** (300 total rounds; 2,800 total execution attempts).

All worker processes were synchronized via `multiprocessing.Barrier`, releasing all contending processes to immediately execute `confirm_and_execute` against disk-backed SQLite with `PRAGMA busy_timeout=5000` and `BEGIN IMMEDIATE` transaction acquisition.

### Invariant Verification Results

Across all 2,800 execution attempts across all 300 rounds:
1. **Initial Executions:** Exactly 1 initial execution per round (300 / 300 rounds, 100.0%).
2. **Idempotent Replays:** Exactly $W-1$ workers in each round were authenticated and returned as idempotent replays (2500 / 2500 replays, 100.0%).
3. **Database Records Committed:** Exactly 1 simulated row per round; **zero duplicate commits** observed across any process tier (0 duplicate mutations).
4. **Audit Chain Cryptographic Integrity:** 100.0% tamper-evident audit verification (`valid_chain=True`) with complete SHA-256 state tracking.
5. **Errors & Deadlocks:** 0 timeouts, 0 database lock exceptions, 0 unhandled errors.

---

## 2. Summary by Process Tier

| Worker Tier | Repetitions | Contending Attempts | Initial Executions | Idempotent Replays | Database Records | Duplicate Mutations | Audit Validity | Latency p50 (ms) | Latency p95 (ms) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **4 Workers** | 100 | 400 | **100** | 300 | 100 | **0** | 100% VALID | 89.7 | 107.5 |
| **8 Workers** | 100 | 800 | **100** | 700 | 100 | **0** | 100% VALID | 200.6 | 233.8 |
| **16 Workers** | 100 | 1,600 | **100** | 1,500 | 100 | **0** | 100% VALID | 921.1 | 948.6 |

---

## 3. Methodological Note on Operating System Scheduling

While `multiprocessing.Barrier` guarantees that all participating processes reach the synchronization point before any process is released, execution contention at the database layer is governed by the operating system kernel scheduler (`Windows NT kernel`). Describing this phenomenon as **simultaneous barrier-released contention** accurately reflects the physical execution model: processes contend for the exclusive `BEGIN IMMEDIATE` lock within millisecond windows as scheduled by the OS, and SQLite transaction serialization reliably enforces single-writer mutual exclusion with zero race conditions.
