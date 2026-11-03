# Department Question Paper System

Prototype for department-managed question banks and rule-based internal assessment papers.

## Local development

1. Create a PostgreSQL database and set `DATABASE_URL` (the default is a local SQLite database for a no-setup demo).
2. Install the backend dependencies with `python -m pip install -r backend/requirements.txt`.
3. Start the API with `python -m uvicorn app.main:app --app-dir backend --reload`.
4. Install the web dependencies in `frontend` and start it with `pnpm dev`.

The first call to `POST /api/bootstrap` creates the initial Admin account. The Admin creates a department and its HOD. HODs create, deactivate, and reactivate their department's teacher accounts.

## Paper rules in this prototype

- Five compulsory parts, with one `OR` alternative per part.
- One complete question is selected from each part, each worth 10 marks, for 50 total.
- The default blueprint is 4+6 marks in Parts 1, 2, 4, and 5; Part 3 is 5+5.
- Each slot requires a configured CO and RBT level. The generator refuses to produce a paper if its bank has insufficient eligible questions.

For production, set `DATABASE_URL` to PostgreSQL and replace the demo session store with signed JWTs or the institution's SSO.
