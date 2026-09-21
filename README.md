# JALRAKSHYAK AI — Backend Prototype

AI Disaster Response Copilot. Yantra Business Cup — SOFTBOTS AI Hackathon 2026.
Team: Nep AI Solutions Pvt. Ltd.

## What's here

The core `Perceive -> Reason -> Act` loop from the proposal, working end to end:

- `app/models.py` — data models: Report, Unit, Shelter
- `app/scoring.py` — rule-based severity scoring (CRITICAL / HIGH / MODERATE) with plain-language reasons
- `app/matching.py` — nearest-available-unit matching
- `app/explain.py` — LLM layer that turns reasons into a one-sentence explanation (falls back to a template if no API key is set)
- `app/main.py` — FastAPI endpoints: intake, ranked dashboard list, approve/modify/reject, lifecycle status

Not yet built: React dashboard, offline queue, voice input, shelter panel UI, multi-channel demo trigger. See "Next steps" below.

## Run it

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# optional — enables real LLM explanations instead of the template fallback
export ANTHROPIC_API_KEY=your_key_here

uvicorn app.main:app --reload
```

Then open **http://localhost:8000/docs** — FastAPI's auto-generated interactive docs. You can submit a report, see it scored and matched, and hit approve/modify/reject, all from the browser, before any frontend exists.

## Try the flow with curl

```bash
curl -X POST http://localhost:8000/reports -H "Content-Type: application/json" -d '{
  "location_label": "Thapathali, near Bagmati riverbank",
  "lat": 27.6944, "lng": 85.3197,
  "channel": "SMS",
  "victim_count": 6,
  "injuries": true,
  "children_present": true,
  "trapped": true,
  "water_rising": true,
  "notes": "family stuck on roof"
}'

curl http://localhost:8000/incidents
```

## Next steps (in order)

1. **React dashboard** — card-per-incident, ranked by severity, Approve/Modify/Reject buttons, reason text inline. Consume `/incidents`, `/units`, `/shelters`.
2. **Offline queue** — on the frontend: try the POST, on failure push to a `localStorage` array, flush on reconnect.
3. **Voice input** — Web Speech API (`webkitSpeechRecognition`) wired to the `notes` field of the intake form.
4. **Shelter panel** — simple capacity/occupancy display from `/shelters`.
5. **Multi-channel demo trigger** — a button that fires a simulated "incoming SMS report" POST for the live demo.

## Notes

- SQLite file (`backend/jalrakshyak.db`) is gitignored — it's regenerated with demo seed data (5 units, 2 shelters around Kathmandu) on first run.
- Scoring is intentionally simple rule-based logic — see `scoring.py` if you want to tune thresholds before the demo.
