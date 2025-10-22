# String Analyzer API — HNG Stage 1

## Description
A RESTful API that analyzes strings and stores their computed properties such as length, palindrome check, unique characters, and SHA-256 hash.

## Setup (Run Locally)
```bash
git clone <repo-link>
cd string-analyzer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
