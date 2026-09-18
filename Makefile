build-CheckCommuteFunction: build-lambda-package
build-StatusFunction: build-lambda-package

build-lambda-package:
	python3 -m pip install -r requirements-lambda.txt -t "$(ARTIFACTS_DIR)"
	cp -r app "$(ARTIFACTS_DIR)/app"
	find "$(ARTIFACTS_DIR)/app" -name __pycache__ -type d -exec rm -rf {} +
