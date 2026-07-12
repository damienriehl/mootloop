# MootLoop frontend developer targets.
.PHONY: install lint typecheck test check openapi-gen openapi-check build

install:
	npm install

lint:
	npm run lint

typecheck:
	npm run typecheck

test:
	npm test

build:
	npm run build

# Regenerate the typed contract from the committed openapi.json.
openapi-gen:
	npm run openapi:gen

# CI drift gate (FD-8): regenerate to a temp file and diff against the committed
# schema.d.ts — fails if the backend contract has drifted from the generated types.
openapi-check:
	@npx openapi-typescript ../openapi.json -o /tmp/mootloop-schema.check.d.ts >/dev/null
	@diff -u lib/api/schema.d.ts /tmp/mootloop-schema.check.d.ts \
		&& echo "openapi schema in sync" \
		|| (echo "ERROR: lib/api/schema.d.ts is stale — run 'make openapi-gen'"; exit 1)

# The full local gate.
check: lint typecheck test openapi-check
