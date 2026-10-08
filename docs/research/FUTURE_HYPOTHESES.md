# Future hypotheses (not tested, not tracked)

These ideas came up during Wave 2 implementation. **None of them is a Wave-2 candidate.** Each would need its own
pre-registration before any data is joined.

* **Scale drift of opponent-adjusted CFB features between box-score providers.** The 2026 production ESPN log gives
  the DSC-001 feature a mean of −0.51 (SD 1.25). The CFBD-era value was +0.14 (SD 1.42). A provider-level
  calibration study could ask whether Wave-1 thresholds transfer. This could only feed a *future* wave; Wave 2
  stays frozen.
* **Spread-ladder shape.** Some Kalshi CFB ladders are non-monotone between adjacent rungs (for example, a 5.5 mid
  above a 6.5 mid). A descriptive study of ladder coherence, and its relation to the moneyline, could inform a
  future market-center definition.
* **Closing move toward the side.** Wave 2 records the side-aware move from PRIMARY_60_180 to the 30-minute capture
  for both CFB streams. Whether that move predicts the residual is a separate question.
