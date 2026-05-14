# robomp — convenience targets.
SHELL := /bin/bash
PI_ROOT ?= /work/pi
STAGE ?= .pi-context

.PHONY: help stage build up down logs sh test clean

help:
	@echo "robomp targets:"
	@echo "  make stage       — rsync $$PI_ROOT into $(STAGE) (build context)"
	@echo "  make build       — stage + docker compose build"
	@echo "  make up          — bring the container up (foreground)"
	@echo "  make down        — tear down"
	@echo "  make logs        — follow container logs"
	@echo "  make sh          — exec a shell inside the running container"
	@echo "  make test        — run the unit test suite locally"
	@echo "  make clean       — drop $(STAGE)"

stage:
	PI_ROOT=$(PI_ROOT) ./bin/stage-pi.sh $(STAGE)

build: stage
	docker compose build

up:
	docker compose up -d
	docker compose logs -f

down:
	docker compose down

logs:
	docker compose logs -f

sh:
	docker compose exec robomp bash

test:
	pytest -x tests/

clean:
	rm -rf $(STAGE)
