import { planSelected as ps } from "../analytics/events";
import { useTracked } from "@stapel/core";

export function Upsell() {
  const { tracked } = useTracked();
  return (
    <button onClick={tracked(ps, { plan: "pro", entry: "paywall" })}>Upgrade</button>
  );
}
