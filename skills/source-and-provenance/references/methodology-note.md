# Methodology note template

Attach to every report or analysis. Keep it factual; one line per item where
possible. Write "none" rather than leaving a field out. The example values
describe a fictional online furniture shop and are invented.

```markdown
## Method and sources

**Report run:** report-2026-W40 (generated 2026-10-05 06:12 CEST)
**Coverage:** ISO weeks 36–40 2026 (31 Aug–4 Oct), Europe/Berlin

### Sources
| Dataset | System | Query / metric (version) | Retrieved | Rows |
|---|---|---|---|---|
| orders | order-service | weekly_paid_orders (v3) | 2026-10-05 06:00 CEST | 5 |
| ad spend | ad-platform exports (Google Ads, Meta) | channel_spend_daily (v2) | 2026-10-05 05:40 CEST | 70 |

### Definitions
- Paid order: an order whose payment cleared, counted in the week of payment.
- Conversion: paid orders / design consultations held, same ISO week.
- Ad spend: platform-reported cost, excl. VAT, 28-day click attribution.

### Steps
1. Pulled weekly aggregates from each source (queries above).
2. Excluded test orders and staff orders (flag `is_internal`).
3. Computed conversion from counts, not from per-channel rates.

### Exclusions and gaps
- Week 38 (14–20 Sep) orders missing: export failed 21 Sep; not imputed.

### Limitations
- Attribution window changed from 7 to 28 days on 14 Sep; ad metrics before
  and after are not comparable.
- Forecast is model output (orders-forecast v3, trained to 28 Sep), not data.

### Not checked
- Reconciliation of order revenue with the finance ledger for September.

### Artifacts
- Figures: viz-06f72957a73a2e86 (orders), … (manifests in the report store)
```
