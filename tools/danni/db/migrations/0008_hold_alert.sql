-- A held answer becomes a problem once its source has had the chance to confirm it. That is a day for a source
-- read every day, and longer for one read once a week: until now it was a day for all, so such a source alerted
-- daily for a hold nothing could yet confirm.
ALTER TABLE ops.source ADD COLUMN hold_alert_after interval NOT NULL DEFAULT '1 day';
COMMENT ON COLUMN ops.source.hold_alert_after IS 'След колко време задържан отговор на този източник е проблем. По-дълго от ритъма на четене, за да има кога второто четене да го потвърди.';
