# {{SLUG}} mobile app (stapel-kmp). Run from mobile/.
.PHONY: check walk contracts generate desktop android ios

# Every proof gate this machine can run (scripts/mobile-check.sh prints PASS / FAIL / NOT RUN).
check:
	scripts/mobile-check.sh

# The sign-in walk against a running backend: make walk WALK_BASE_URL=http://127.0.0.1:{{DEV_PORT}}/
walk:
	WALK_BASE_URL="$(WALK_BASE_URL)" scripts/mobile-check.sh

# The project's own backend modules -> contracts/<module>/ -> generated clients (commit both).
# Run after the backend gains endpoints.
contracts:
{{CONTRACT_RECIPES}}	./gradlew --console=plain :shared:stapelGenerate

generate:
	./gradlew --console=plain :shared:stapelGenerate

desktop:
	./gradlew --console=plain :desktopApp:run

android:
	./gradlew --console=plain :androidApp:assembleDebug

ios:
	scripts/ios-smoke.sh
