# Ultimate Repair Center (URC)

URC ist ein **restore-only** Incident-Framework fuer den laufenden Betrieb.
Ziel ist nicht Redesign, sondern die schnelle Rueckkehr in den freigegebenen IST-Zustand.

## Zielbild
- Tickets automatisch erfassen, priorisieren und in Queues legen.
- Pro Worker nur **ein Ticket gleichzeitig** verarbeiten.
- Diagnosen mit Cloud-Modellen erzeugen.
- Nur freigegebene Restore-Aktionen durch den Executor erlauben.
- Ergebnis als `done` oder `failed` nachvollziehbar ablegen.

## Was URC bewusst nicht macht
- Keine Knowledgebase-Module und keine KB-Datenpflege.
- Keine Security-/Compliance-Automation.
- Keine Firewall-/Policy-Aenderungen und kein Netzwerk-Redesign.

## Architektur in 30 Sekunden
1. Ein Plan kommt ueber die Control API (`POST /v1/plan`) rein.
2. URC erzeugt daraus Agent-Tickets in `runtime/queues/<agent>/inbox/`.
3. Worker holen Tickets nach Prioritaet (`critical` -> `low`).
4. Orchestrator waehlt Modellkette und Funktionsschema.
5. Ergebnis landet in `runtime/queues/<agent>/done/` sowie global in `runtime/done/`.
6. Fehler landen in `runtime/queues/<agent>/failed/` und `runtime/failed/`.

## Repository-Struktur
- `src/urc/`: Runtime-Code (`control_api`, `worker`, `orchestrator`, `ollama_client`, `executor`, `cli`)
- `configs/`: Agent-, Issue-, Modell- und Antwortschemas
- `scripts/`: Operator-Helfer (`submit_plan.sh`)
- `ops/systemd/`: Service-Templates fuer API und Worker
- `runtime/`: Laufzeitdaten (Queues, Plans, Done/Failed, Heartbeat)

## Konfigurationsmodell
- `configs/agent_profiles.json`:
  - Rollen, Modell-Aliase, Token-Limits, allowed actions (Executor)
- `configs/issue_profiles.json`:
  - Issue-Typ -> Agenten, Modellstrategie, vorgeschlagene Executor-Aktion
- `configs/model_policy.json`:
  - Alias -> konkretes Cloud-Modell
- `configs/ollama_endpoints.json`:
  - Endpoint-Fallbacks inkl. `OLLAMA_API_KEY`
- `configs/response_schemas.json`:
  - Erwartete JSON-Struktur je Agent-Funktion

## Lokaler Start (manuell)
Control API:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.control_api --base-dir /root/ultimate-repair-center --bind 127.0.0.1 --port 8765
```

Worker (Beispiel):
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.worker --agent sre_diagnoser --base-dir /root/ultimate-repair-center --interval-sec 10
```

Weiteren Worker starten:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.worker --agent performance_analyst --base-dir /root/ultimate-repair-center --interval-sec 10
```

## Ticket einreichen
Per Helper-Script:
```bash
cd /root/ultimate-repair-center
URC_API_URL=http://127.0.0.1:8765 ISSUE_TYPE=manual_plan ./scripts/submit_plan.sh "Test restore plan"
```

Per CLI:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.cli submit \
  --url http://127.0.0.1:8765 \
  --type tailnet_degraded \
  --summary "Tailnet route unstable" \
  --priority high \
  --target-agents sre_diagnoser,performance_analyst,documentarian
```

## API-Endpunkte
- `GET /healthz`: Basis-Health der Control API
- `GET /v1/status`: Queue- und Agent-Status
- `POST /v1/plan`: Plan erstellen und Tickets verteilen

## Systemd-Betrieb
Templates:
- `ops/systemd/urc-control-api.service`
- `ops/systemd/urc-worker@.service`

Typischer Betrieb:
```bash
sudo cp ops/systemd/urc-control-api.service /etc/systemd/system/
sudo cp ops/systemd/urc-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now urc-control-api.service
sudo systemctl enable --now urc-worker@sre_diagnoser.service
sudo systemctl enable --now urc-worker@performance_analyst.service
sudo systemctl enable --now urc-worker@documentarian.service
```

## Executor-Regeln
- Executor fuehrt nur Aktionen aus, die in `allowed_actions` stehen.
- Executor-Aktion wird ueber Issue-Profile vorgeschlagen.
- Apply-Flag ist explizit und darf nicht implizit aktiviert werden.
- Betriebsprinzip bleibt: restore-only statt redesign.

## Betriebsdaten und Monitoring
- Offene Tickets: `runtime/queues/*/inbox/*.json`
- Erledigt: `runtime/done/*.json`
- Fehler: `runtime/failed/*.json`
- Heartbeat: `runtime/heartbeat/*.json`

## Release- und Update-Regel
Alle Aenderungen werden in zwei Stellen gepflegt:
- `CHANGELOG.md`
- Git-Historie auf `main`
