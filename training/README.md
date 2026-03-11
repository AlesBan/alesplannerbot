# Training Question Bank

Use `training/question_bank.csv` to store your own training questions.

## Format

Required columns:
- `question`

Optional columns:
- `expected_intent` (for grading/tuning hints)
- `category` (your own grouping, not required by code)

## Notes

- `/training_on` automatically reads `training/question_bank.csv` if the file exists.
- If the file is missing, bot falls back to built-in template generation.
- You can keep adding rows without limit; bot will sample questions from this bank.
