# Methodology note template

Attach to every report or analysis. Keep it factual; one line per item where
possible. Write "none" rather than leaving a field out.

```markdown
## Method and sources

**Report run:** report-2026-W40 (generated 2026-10-05 06:12 EEST)
**Coverage:** ISO weeks 36–40 2026 (31 Aug–4 Oct), Europe/Helsinki

### Sources
| Dataset | System | Query / metric (version) | Retrieved | Rows |
|---|---|---|---|---|
| bookings | booking-service | weekly_confirmed_bookings (v3) | 2026-10-05 06:00 EEST | 5 |
| ad spend | AdIntel (Google Ads, Meta) | channel_spend_daily (v2) | 2026-10-05 05:40 EEST | 70 |

### Definitions
- Confirmed booking: a quote accepted and paid deposit, counted in the week of
  acceptance.
- Conversion: confirmed bookings / quotes created, same ISO week.
- Ad spend: platform-reported cost, excl. VAT, 28-day click attribution.

### Steps
1. Pulled weekly aggregates from each source (queries above).
2. Excluded test bookings and internal moves (flag `is_internal`).
3. Computed conversion from counts, not from per-channel rates.

### Exclusions and gaps
- Week 38 (14–20 Sep) bookings missing: export failed 21 Sep; not imputed.

### Limitations
- Attribution window changed from 7 to 28 days on 14 Sep; ad metrics before
  and after are not comparable.
- Forecast is model output (bookings-forecast v3, trained to 28 Sep), not data.

### Not checked
- Reconciliation of booking revenue with finance ledger for September.

### Artifacts
- Figures: viz-06f72957a73a2e86 (bookings), … (manifests in the report store)
```
