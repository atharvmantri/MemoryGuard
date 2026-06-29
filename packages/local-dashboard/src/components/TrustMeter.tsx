import {
  clampTrustScore,
  trustLevel,
  trustPercentLabel,
} from "../lib/trust";

export interface TrustMeterProps {
  /** Trust score in the range [0, 1]. Values outside the range are clamped. */
  trustScore: number;
  /** Optional descriptive prefix for the accessible label (e.g. memory id). */
  label?: string;
  /** Hide the numeric value text (the aria-label still carries it). */
  hideValue?: boolean;
}

/**
 * Renders a trust score as a colored meter.
 *
 * Requirement 19.3: high (>=0.7) Circuit Blue / Signal Lime, medium amber,
 * low red. The component exposes an accessible `meter` role with the numeric
 * score so assistive technology can read the value (Requirement 19.x a11y).
 */
export function TrustMeter({
  trustScore,
  label,
  hideValue = false,
}: TrustMeterProps) {
  const score = clampTrustScore(trustScore);
  const level = trustLevel(score);
  const percent = trustPercentLabel(score);
  const prefix = label ? `${label}: ` : "";
  const ariaLabel = `${prefix}trust score ${score.toFixed(2)} (${level})`;

  return (
    <div
      className="mg-trustmeter"
      role="meter"
      aria-valuenow={score}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuetext={percent}
      aria-label={ariaLabel}
      data-trust-level={level}
    >
      <div className="mg-trustmeter__track">
        <div
          className={`mg-trustmeter__fill mg-trustmeter__fill--${level}`}
          style={{ width: `${score * 100}%` }}
        />
      </div>
      {!hideValue && (
        <span className="mg-trustmeter__value" aria-hidden="true">
          {percent}
        </span>
      )}
    </div>
  );
}

export default TrustMeter;
