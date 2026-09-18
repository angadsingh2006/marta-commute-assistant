build-CheckCommuteFunction: build-lambda-package
build-StatusFunction: build-lambda-package

# Wheels must match the Lambda runtime (python3.13, x86_64), not the host.
build-lambda-package:
	python3 -m pip install -r requirements-lambda.txt -t "$(ARTIFACTS_DIR)" \
		--platform manylinux2014_x86_64 \
		--python-version 3.13 \
		--implementation cp \
		--only-binary=:all:
	cp -r app "$(ARTIFACTS_DIR)/app"
	find "$(ARTIFACTS_DIR)/app" -name __pycache__ -type d -exec rm -rf {} +
