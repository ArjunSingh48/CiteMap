# CiteMap — run from the repo root (e.g. on your Mac: ~/hacknation/citemap)
PY ?= python3

all:            ## extract rules -> lookups -> changes
	$(PY) -m citemap.cli all

rerun: all      ## same as all (picks up data/new_laws and data/supplement automatically)

test:           ## full test suite
	$(PY) -m pytest -q tests

stats:
	$(PY) -m citemap.cli stats

clean:
	rm -rf outputs/*.json audit/*.jsonl cache/llm

.PHONY: all rerun test stats clean stress

stress:         ## randomized attack tests: 100 -> 250 -> 1000 (+UI); each round must stay under 1% failures
	$(PY) tests/stress/run_stress.py --n 100 --seed 1 --ui
	$(PY) tests/stress/run_stress.py --n 250 --seed 2 --ui
	$(PY) tests/stress/run_stress.py --n 1000 --seed 3 --ui
