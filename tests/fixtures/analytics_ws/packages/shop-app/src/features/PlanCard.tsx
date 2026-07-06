import { planSelected } from "../analytics/events";
import { useTracked } from "@stapel/core";
import { startCheckout, close, toggle } from "./actions";

export function PlanCard() {
  const { tracked, trackedSubmit } = useTracked();
  return (
    <div>
      <button onClick={tracked(planSelected, { plan: "pro", entry: "landing" }, startCheckout)}>
        Pick pro
      </button>
      <form onSubmit={trackedSubmit(planSelected, { plan: "free", entry: "settings" })}>
        Choose free
      </form>
      {/* eslint-disable-next-line stapel/clickable-needs-event -- legacy widget, tracked upstream */}
      <span onClick={close} data-analytics="flow">
        step the checkout machine
      </span>
      <div
        onClick={toggle}
        data-analytics="none"
        data-analytics-reason="visual accordion toggle, not a funnel step"
      >
        details
      </div>
    </div>
  );
}
