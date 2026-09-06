/**
 * Inline banner shown while the loss circuit breaker has delivery paused.
 *
 * This exists for a business reason as much as a technical one. Every signals
 * product has bad days; what costs trust is a bad day the product says nothing
 * about. A feed that visibly stands aside reads as risk management, while the same
 * empty feed with no explanation reads as something broken.
 *
 * Deliberately NOT dismissable, unlike WeekendNotice: a weekend is a known schedule
 * a user can acknowledge once, whereas this is live market state that changes what
 * they should expect right now. It disappears on its own when the breaker lifts.
 *
 * Renders nothing unless the breaker is actually active, so it costs nothing on a
 * normal day and nothing at all while the feature is unconfigured.
 */
export default function BreakerNotice({ breaker }) {
  if (!breaker?.active) return null;

  const classes = breaker.classes || [];
  // "Crypto and forex" / "Crypto" — say WHICH market, since a user watching both
  // needs to know only half their feed is affected.
  const label =
    classes.length === 2
      ? "Crypto and forex"
      : classes[0] === "forex"
        ? "Forex"
        : classes[0] === "crypto"
          ? "Crypto"
          : "Signal";

  let resumeText = null;
  if (breaker.resumes_at) {
    const when = new Date(breaker.resumes_at);
    // Guard against an unparseable timestamp rather than rendering "Invalid Date".
    if (!Number.isNaN(when.getTime())) {
      resumeText = when.toLocaleString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      });
    }
  }

  return (
    <div className="breaker-notice" role="status">
      <span className="breaker-dot" aria-hidden="true" />
      <div className="breaker-copy">
        <b>{label} signals paused — market conditions.</b>{" "}
        <span>
          An unusual number of recent setups have closed at their stop, so new
          signals are on hold while conditions settle.
          {resumeText ? ` Resuming around ${resumeText}.` : ""}
        </span>
        {/* Open trades keep being evaluated — only NEW delivery pauses. Saying so
            stops a user assuming their live positions are no longer tracked. */}
        <span className="breaker-sub">
          Trades already open are still being tracked as normal.
        </span>
      </div>
    </div>
  );
}
