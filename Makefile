PY := python3
export PYTHONPATH := src

.PHONY: demo fetch groundtruth rules llm report test clean

demo:
	$(PY) -m filingbench.cli --stage all --verbose

fetch:
	$(PY) -m filingbench.cli --stage fetch --verbose

groundtruth:
	$(PY) -m filingbench.cli --stage groundtruth --verbose

rules:
	$(PY) -m filingbench.cli --stage rules --verbose

llm:
	$(PY) -m filingbench.cli --stage llm --verbose

report:
	$(PY) -m filingbench.cli --stage report --verbose

test:
	$(PY) -m pytest tests -q

clean:
	rm -f results/*.csv results/*.json results/figures/*.png

package:
	./scripts/package.sh
