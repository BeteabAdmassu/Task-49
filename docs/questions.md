# Business Logic Questions Log

1. [Impossible speed jump interpretation]
   - **Question**: Is `>85 mph between consecutive pings` based on inferred geospatial movement or speed deltas in telemetry?
   - **My Understanding**: In an offline local deployment, pings may already contain computed speed. Detecting abrupt speed behavior is acceptable when based on consecutive ping speed values and elapsed time.
   - **Solution**: On CSV ingest, compare current speed and previous speed over elapsed hours; if abnormal effective jump exceeds threshold, write `impossible_speed_jump` risk event.

2. [Commuter bundle minimum stay equivalent]
   - **Question**: How should minimum-length equivalent apply to non-lodging passes?
   - **My Understanding**: Treat pass duration as `bundle_days` and enforce a minimum day count.
   - **Solution**: For `product_type=commuter_bundle`, enforce `bundle_days >= 3` before seat hold is created.

3. [Offline ranking metrics source]
   - **Question**: Should ranking metrics be computed live from recommendations or imported snapshots?
   - **My Understanding**: Analysts can use locally computed offline samples; pre-aggregated rows are acceptable for an MVP dashboard.
   - **Solution**: Added `ranking_samples` table and dashboard aggregation for Precision, Recall, NDCG, Coverage, and Diversity.
