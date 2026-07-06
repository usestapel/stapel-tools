import { appOpened } from "./analytics/events";
import { analytics } from "@stapel/core";

export function App() {
  analytics.track(appOpened, {});
  return null;
}
