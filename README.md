# D-GAY

**docxtpl General Acceptance Yield** is a local web app for checking Microsoft Word (`.docx`) Jinja templates. Upload a template, run **D-GAY Checker**, and the app lists every `{{ placeholder }}`, replaces it with the test value `GAY100%`, highlights that value in red, shows a preview, and lets you download the processed document.

The preview covers body paragraphs, tables, headers, and footers. Loops are rendered once so the tags inside them can be checked. A filter that rewrites the replacement text can remove the marker.

## Run locally

Use Python 3.10 or newer.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

On macOS or Linux, activate the environment with `source .venv/bin/activate` instead.

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The checker accepts a `.docx` file up to 16 MB.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Push to GitHub

The `.gitignore` already leaves out `.venv/` and `var/` (uploaded and processed files). From this folder:

```powershell
git init
git add .
git commit -m "Add the D-GAY template checker"
git branch -M main
git remote add origin https://github.com/<your-account>/D-GAY.git
git push -u origin main
```

Create an empty `D-GAY` repository on GitHub first, then replace `<your-account>` with your GitHub username. If GitHub shows a different remote URL, use that URL in `git remote add`.
