.PHONY: api client integration-test ui-check ui-browser-install ui-staging-qa ui-profile-matrix ui-repair-check ui-autofix ui-smart-tests ui-visual-propose ui-visual-accept ui-visual-update production-ui-smoke

api:
	bash scripts/dev-api.sh

client:
	cd apps/client && npm install && npm run dev

integration-test:
	bash scripts/test-integration.sh

ui-check:
	bash scripts/cam-ui-gate.sh

ui-browser-install:
	bash scripts/cam-install-ui-browser.sh

ui-staging-qa:
	bash scripts/cam-ui-staging-qa.sh

ui-profile-matrix:
	bash scripts/cam-ui-profile-matrix.sh

ui-repair-check:
	bash scripts/cam-ui-repair-check.sh

ui-autofix:
	bash scripts/cam-ui-autofix.sh

ui-smart-tests:
	bash scripts/cam-ui-run-smart-tests.sh

ui-visual-propose:
	bash scripts/cam-ui-baseline-propose.sh

ui-visual-accept:
	bash scripts/cam-ui-baseline-accept.sh

ui-visual-update:
	@echo "Direct baseline writes are disabled; creating a governed proposal instead."
	bash scripts/cam-ui-baseline-propose.sh

production-ui-smoke:
	bash scripts/cam-production-ui-smoke.sh
