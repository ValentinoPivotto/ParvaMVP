# Atajos equivalentes a los scripts de npm que había antes.
# Todos se pueden correr también a mano: `python3 -m backend.<módulo>`.
PY ?= python3

.PHONY: start dev seed reset link-phone check-meta tunnel test

start:                ## levanta la web app + el webhook
	$(PY) -m backend.handler.server

dev:                  ## igual que start, pero recarga al guardar
	$(PY) -m backend.scripts.dev

seed:                 ## recarga los datos de ejemplo
	$(PY) -m backend.repository.seed

reset:                ## borra y recarga la base
	$(PY) -m backend.repository.seed --reset

link-phone:           ## vincula un teléfono: make link-phone ARGS="1 +54911..."
	$(PY) -m backend.scripts.link_phone $(ARGS)

check-meta:           ## diagnostica el setup de Meta: make check-meta ARGS="<WABA_ID>"
	$(PY) -m backend.scripts.check_meta $(ARGS)

tunnel:               ## abre el túnel ngrok hacia el puerto del .env
	$(PY) -m backend.scripts.tunnel

test:                 ## todos los tests: backend (Python) y frontend (Node)
	$(PY) -m unittest discover -s test -t .
	node --test test/
