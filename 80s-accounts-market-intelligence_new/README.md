# Thomas Scientific — 80s Accounts Market Intelligence

Automated market intelligence scanner for Thomas Scientific's top accounts (Super80 + Customer80).
Uses Gemini 2.5 Flash with Google Search grounding to surface real-time sales signals.

## Setup

```bash
cd backend/accounts-vertical
pip install -r requirements.txt
```

Add your Gemini API key to `.env`:
```
GEMINI_API_KEY=your_key_here
```

## Usage

See `backend/accounts-vertical/notes.txt` for all run commands.

## Triggers

| Trigger | What it finds |
|---|---|
| `pipeline` | New drug discoveries, clinical trials, FDA approvals, R&D breakthroughs |
| `expansion` | New facilities, plants, manufacturing scale-up, geographic expansion |
| `partnership` | Licensing deals, collaborations, M&A, joint ventures |
| `funding` | VC rounds, government contracts, grants, IPOs |
| `project` | New research programs, large-scale initiatives, contract wins |

## Account Tiers

- **Super80** — 7 highest-priority accounts
- **Customer80** — ~160 key pharma, biotech, diagnostics, and research accounts
