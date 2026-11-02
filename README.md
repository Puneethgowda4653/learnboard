# LearnHub
Flask + SQLite exam platform (Maths, Science, English; Classes 1–10; NCERT/CBSE, ICSE, IGCSE, State).
```
pip install -r requirements.txt
python seed.py          # sample ORIGINAL questions/tests
python app.py           # http://localhost:5000
```
First run prints the admin login (admin@learnhub.local). Set ADMIN_PASSWORD / SECRET_KEY env vars for real use.
Admin → Questions → Bulk import CSV loads real content (past papers you hold rights to).
