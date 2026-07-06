import { defineEvent, prop } from "@stapel/core";

export const planSelected = defineEvent({
  name: "pricing.plan.selected",
  description: "User picked a pricing plan",
  props: {
    plan: prop.oneOf(["free", "pro"], "Plan code"),
    entry: prop.string("Entry point"),
  },
  flow: "billing.checkout",
});

export const appOpened = defineEvent({
  name: "app.opened",
  description: "App shell mounted",
  props: {},
});
